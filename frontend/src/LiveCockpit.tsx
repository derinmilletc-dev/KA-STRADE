import {useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode} from 'react'
import './copy-phrase.css'
import {Bell, Check, ChevronDown, Coins, Layers, MessageCircle, Power, RefreshCw, ShieldCheck, SlidersHorizontal, TrendingUp, TriangleAlert, Wallet, X} from 'lucide-react'
import type {SharedLiveStatus} from './LiveTradingPanel'
import {
  GUIDE_STEPS, RISK_PRESETS, dailySummary, duration, liveGuide, matchPreset, positionNote, retryDelay, riskSummary,
  smartAlerts, whyNoTrade, rejectionBreakdown, statusHelp, nextScan, reconnectingGuide, capacityPreview, marginModePreview, marginSimulation, openMarginTotal, clampCustomPercent, CUSTOM_MARGIN_MIN_PERCENT, type GuideInput, type GuideNotice,
} from './live-guide'
import {askKais} from '../../kais-ask'
import {NOTIFY_KINDS, browserNotificationState, loadNotifyPrefs} from '../../notification-prefs'
import '../../live-cockpit.css'

/** Canlı İşlem kokpiti (Master Trade › Canlı).
 *  Kural 1: ekrandaki her rakam sunucudan gelen doğrulanmış durumdan gelir; yoksa "—" ya da "doğrulanamadı" yazar.
 *  Kural 2: yalnız OKUMA kendiliğinden tekrarlanır. Onay, başlatma, durdurma gibi değiştiren istekler tek sefer ve
 *  müşterinin düğmesiyle gider; sonucu sunucu yanıtıyla doğrulanır, sonra durum yeniden okunur. */

type Plan = NonNullable<SharedLiveStatus['plans']>[number]
type Position = Record<string, unknown>
type Request = (path: string, body?: Record<string, unknown>) => Promise<unknown>
type Props = {
  status: SharedLiveStatus | null
  verified: boolean
  loading: boolean
  guideInput: GuideInput
  wallet: number | null
  availableBalance: number | null
  marketBanSeconds: number
  externalBusy: boolean
  userId: string | null
  request: Request
  refresh: () => Promise<unknown>
  findPlan: (position: Position) => Plan | undefined
  extras?: ReactNode
}

// Sunucunun beklediği sabit onay ifadesi; kullanıcı artık yazmıyor, kutuyu işaretliyor.
const FILL_LOCK_PHRASE = 'DOLUM RİSKİNİ İNCELEDİM'
type Sheet = null | 'approvals' | 'start' | 'stop' | 'close-all' | 'close-position' | 'fill-lock' | 'emergency-clear' | 'recovery' | 'extend' | 'why'
type CloseTarget = {planId: string; symbol: string; direction: string; entry: string; pnl: number | null}
type ItemState = 'done' | 'todo' | 'working' | 'error'

const HIGH_RISK_PHRASE = 'YÜKSEK RİSKİ KABUL EDİYORUM'
const AGGRESSIVE_PHRASE = 'AGRESİF MODU KABUL EDİYORUM'
const MODE_TEXT: Record<string, string> = {guvenli: 'Daha az teminat, daha küçük işlem. Bugünkü ayar.', orta: 'Daha büyük işlem; kayıp ve kazanç büyür.', agresif: 'En büyük işlem; kayıp ve kazanç en yüksek.'}
const CLOSE_ALL_PHRASE = 'CANLI ACİL DURDUR'
// TP1'de kapanan oran planın kendisinden okunur; alanı olmayan eski planlar %60 gösterilir.
const tp1Percent = (plan: Record<string, unknown> | undefined) => {
  const value = Number(plan?.tp1_fraction)
  return Number.isFinite(value) && value > 0 && value < 1 ? Math.round(value * 100) : 60
}
// Sunucu bu cümleyi ister; tek pozisyonu kapatırken kullanıcıya yazdırmayız, onay penceresindeki "Evet, kapat" gönderir.
const CLOSE_POSITION_PHRASE = 'CANLI POZİSYONU KAPAT'
const usdt = (value: number | null | undefined, signed = false) => typeof value === 'number' && Number.isFinite(value)
  ? `${signed && value > 0 ? '+' : ''}${value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USDT` : '—'
const pct = (value: number) => `%${value.toLocaleString('tr-TR', {maximumFractionDigits: 2})}`
const finite = (value: unknown): number | null => {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN
  return Number.isFinite(parsed) ? parsed : null
}
const clock = (value: unknown) => {
  const time = Date.parse(String(value || ''))
  return Number.isFinite(time) ? new Date(time).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : '—'
}
const dateTime = (value: number) => new Date(value).toLocaleString('tr-TR', {day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit'})
const errorText = (error: unknown, fallback: string) => {
  const raw = error instanceof Error ? error.message : fallback
  // Sunucunun teknik ön ekini ("LIVE KİLİTLİ / GATE BLOCKED: ...") müşteriye göstermeyiz; açıklamanın kendisi kalır.
  return raw.replace(/^(LIVE KİLİTLİ \/ GATE BLOCKED|BELİRSİZ YÜRÜTME \/ MANUEL İNCELEME GEREKLİ|BAKIM MODU \/ YENİ GİRİŞLER KAPALI|LIVE HTTP \d+):\s*/, '') || fallback
}
const goTab = (tab: string) => {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  window.history.pushState(window.history.state, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
const readStored = (key: string | null, allowed: (value: number) => boolean): number | null => {
  if (!key) return null
  try {
    const value = Number(localStorage.getItem(key))
    return Number.isFinite(value) && allowed(value) ? value : null
  } catch { return null }
}
const writeStored = (key: string | null, value: number) => {
  if (!key) return
  try { localStorage.setItem(key, String(value)) } catch { /* tarayıcı kaydı kapalı olabilir; seçim bu sayfada kalır */ }
}
const validCoins = (value: number) => Number.isInteger(value) && value >= 10 && value <= 100
const validHours = (value: number) => [1, 4, 12, 24].includes(value)
const NEAR_TEXT: Record<string, string> = {
  confidence_low: 'güven skoru düşük', signal_conditions: 'trend/sinyal oluşmadı', stop_too_wide: 'stop %6 sınırından geniş', spread_wide: 'alış-satış farkı geniş',
  market_volume: 'hacim düşük', market_move: 'fiyat hareketi yetersiz', minimum_trade_size: 'Binance minimum tutarı', minimum_margin: 'teminat sınırı', position_limit: 'pozisyon sınırı dolu',
}
const riskTier = (risk: number) => risk > 5 ? 'red' : risk > 2 ? 'orange' : risk > 1 ? 'yellow' : 'normal'

const STALE_LIMIT_MS = 30 * 60 * 1000

export default function LiveCockpit(props: Props) {
  const {loading, availableBalance, marketBanSeconds, userId, request, refresh, findPlan, extras} = props
  // Bağlantı geçici koparsa (zaman aşımı, sunucu yeniden başlıyor) son DOĞRULANAN görünüm 30 dk'ya kadar soluk ve
  // "x önce" etiketiyle gösterilir; rehber ilerlemeyi sıfırlamaz. Bu sırada değiştiren istekler kapalıdır (durdurma hariç).
  const lastGood = useRef<{status: SharedLiveStatus; input: GuideInput; wallet: number | null; at: number} | null>(null)
  if (props.verified && props.status && props.guideInput.connected) lastGood.current = {status: props.status, input: props.guideInput, wallet: props.wallet, at: Date.now()}
  const staleSnapshot = !props.verified && !props.loading && lastGood.current && Date.now() - lastGood.current.at < STALE_LIMIT_MS ? lastGood.current : null
  const stale = staleSnapshot !== null
  const status = staleSnapshot ? staleSnapshot.status : props.status
  const guideInput = staleSnapshot ? staleSnapshot.input : props.guideInput
  const wallet = staleSnapshot ? staleSnapshot.wallet : props.wallet
  const verified = props.verified || stale
  const externalBusy = props.externalBusy || stale
  const staleAge = staleSnapshot ? Math.max(0, Math.round((Date.now() - staleSnapshot.at) / 1000)) : 0
  const original = status?.original_v2_live
  // Risk adımı: sunucuda risk revizyonu varsa tamam. Eski kayıtlarda revizyon olmayabilir (aynı değer yeniden
  // kaydedilince sunucu yeni revizyon yazmaz); bu durumda bu tarayıcıda sunucunun DOĞRULADIĞI son kayıt
  // (aynı yüzdeler) adımı tamamlar. Başlatma isteği riski sunucuda yeniden doğrular.
  const confirmKey = userId ? `kais-live-risk-confirmed:${userId}` : null
  const [riskConfirmed, setRiskConfirmed] = useState<string | null>(() => { try { return confirmKey ? localStorage.getItem(confirmKey) : null } catch { return null } })
  const riskSignature = original ? `${original.risk_percent}|${original.daily_loss_percent}` : null
  const riskConfirmedHere = riskSignature !== null && riskConfirmed === riskSignature
  const cycleAt = Date.parse(String(original?.scanner?.last_cycle_at || ''))
  const effectiveInput: GuideInput = {...guideInput, riskApproved: guideInput.riskApproved || riskConfirmedHere,
    lastCycleAgeSeconds: Number.isFinite(cycleAt) ? Math.max(0, (Date.now() - cycleAt) / 1000) : null}
  const guide = staleSnapshot ? reconnectingGuide(effectiveInput, staleAge, props.guideInput.statusError) : liveGuide(effectiveInput)
  const help = guide.phase === 'problem' && guide.action === 'retry' && !staleSnapshot ? statusHelp(props.guideInput.statusError) : null
  const [sheet, setSheet] = useState<Sheet>(null)
  const [working, setWorking] = useState('')
  const flight = useRef(false)
  const [flash, setFlash] = useState<{tone: 'ok' | 'warn' | 'stop'; text: string} | null>(null)
  const [whyOpen, setWhyOpen] = useState(false)
  const [msgOpen, setMsgOpen] = useState(false)
  const busy = Boolean(working) || externalBusy

  // ---- Strateji ayarı: risk yüzdeleri sunucuda saklanır; coin sayısı ve süre başlatma parametresidir (tarayıcıda hatırlanır).
  const coinsKey = userId ? `protrebot-original-live-coins:${userId}` : null
  const hoursKey = userId ? `protrebot-original-live-hours:${userId}` : null
  const savedRisk = original?.risk_percent ?? 1
  const savedDaily = original?.daily_loss_percent ?? 3
  const riskSaved = (original?.risk_revision ?? 0) > 0 || riskConfirmedHere
  const [riskError, setRiskError] = useState('')
  const [sheetError, setSheetError] = useState('')
  const [draft, setDraft] = useState(() => ({
    risk: savedRisk, daily: savedDaily,
    coins: readStored(coinsKey, validCoins) ?? original?.universe?.requested_count ?? 40,
    hours: readStored(hoursKey, validHours) ?? original?.session?.hours ?? 12,
  }))
  const [custom, setCustom] = useState(false)
  const [understood, setUnderstood] = useState(false)
  const [riskAck, setRiskAck] = useState(false)
  const [phrase, setPhrase] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  useEffect(() => { setDraft(current => ({...current, risk: savedRisk, daily: savedDaily})); setUnderstood(false); setRiskAck(false); setPhrase('') }, [savedRisk, savedDaily, original?.risk_revision])
  useEffect(() => {
    setDraft(current => ({...current, coins: readStored(coinsKey, validCoins) ?? current.coins, hours: readStored(hoursKey, validHours) ?? current.hours}))
  }, [coinsKey, hoursKey])
  const setStartParam = (key: 'coins' | 'hours', value: number) => {
    setDraft(current => ({...current, [key]: value}))
    writeStored(key === 'coins' ? coinsKey : hoursKey, value)
  }
  const riskDirty = draft.risk !== savedRisk || draft.daily !== savedDaily
  const draftValid = Number.isFinite(draft.risk) && draft.risk >= 0.5 && draft.risk <= 10 && Number.isFinite(draft.daily)
    && draft.daily >= 1 && draft.daily <= 25 && draft.daily >= draft.risk && validCoins(draft.coins) && validHours(draft.hours)
  const tierReady = (risk: number, ack: boolean, typed: string) => risk > 5 ? typed === HIGH_RISK_PHRASE : risk > 2 ? ack : true
  const activePreset = matchPreset({risk_percent: draft.risk, daily_loss_percent: draft.daily, symbol_count: draft.coins, session_hours: draft.hours})
  const caps = original?.risk_limits
  const choosePreset = (id: string) => {
    const preset = RISK_PRESETS.find(item => item.id === id)
    if (!preset) return
    setCustom(false)
    setDraft({risk: preset.risk_percent, daily: preset.daily_loss_percent, coins: preset.symbol_count, hours: preset.session_hours})
    writeStored(coinsKey, preset.symbol_count); writeStored(hoursKey, preset.session_hours)
    setRiskAck(false); setPhrase('')
  }

  // ---- Tek uçuşlu istek yürütücü: aynı anda ikinci değiştiren istek gitmez.
  const perform = async (key: string, action: () => Promise<void>, success?: string, onError?: (message: string) => void) => {
    if (flight.current) return false
    flight.current = true
    setWorking(key)
    try {
      await action()
      if (success) setFlash({tone: 'ok', text: success})
      return true
    } catch (error) {
      const message = errorText(error, 'İşlem tamamlanamadı.')
      setFlash({tone: 'stop', text: message})
      onError?.(message)
      return false
    } finally {
      try { await refresh() } catch { /* yenileme hatası rehberde "doğrulanamadı" olarak görünür */ }
      flight.current = false
      setWorking('')
    }
  }

  const saveRisk = () => perform('risk', async () => {
    setRiskError('')
    if (wallet === null) throw new Error('Güncel Binance bakiyesi doğrulanamadı; risk ayarı kaydedilmedi.')
    const payload = await request('/original-v2/risk-settings', {
      risk_percent: draft.risk, daily_loss_percent: draft.daily, risk_acknowledged: draft.risk > 2 && riskAck,
      high_risk_confirmation: draft.risk > 5 ? phrase : '', warning_balance_usdt: wallet,
      expected_risk_revision: original?.risk_revision ?? 0, understood,
    }) as {risk_percent?: unknown; daily_loss_percent?: unknown; risk_revision?: unknown} | null
    if (!payload || payload.risk_percent !== draft.risk || payload.daily_loss_percent !== draft.daily || !Number.isSafeInteger(payload.risk_revision))
      throw new Error('Risk ayarının kaydı doğrulanamadı; güncel ayarları kontrol et.')
    const signature = `${draft.risk}|${draft.daily}`
    setRiskConfirmed(signature)
    try { if (confirmKey) localStorage.setItem(confirmKey, signature) } catch { /* kayıt kapalıysa bu sayfa açıkken geçerli */ }
    setSettingsOpen(false)
  }, 'Risk ayarın kaydedildi. Yalnız yeni işlemlere uygulanır; açık pozisyonlar değişmez.', setRiskError)

  // ---- Teminat modu: yalnız bu hesap; yalnız yeni girişlere uygulanır.
  const savedMode = original?.margin_mode ?? 'guvenli'
  const [modePick, setModePick] = useState<string | null>(null)
  const [modeUnderstood, setModeUnderstood] = useState(false)
  const [modeAck, setModeAck] = useState(false)
  const [modePhrase, setModePhrase] = useState('')
  const [modeError, setModeError] = useState('')
  const [modeHelpOpen, setModeHelpOpen] = useState(false)
  const [customOn, setCustomOn] = useState(false)
  const [customRaw, setCustomRaw] = useState('40')
  const [extendHours, setExtendHours] = useState(12)
  const [extendUnderstood, setExtendUnderstood] = useState(false)
  const extendSession = () => perform('extend', async () => {
    const before = original?.session?.expires_at ? Date.parse(original.session.expires_at) : 0
    const payload = await request('/original-v2/extend-session', {session_hours: extendHours, understood: extendUnderstood}) as {session?: {expires_at?: string | null}} | null
    const after = payload?.session?.expires_at ? Date.parse(payload.session.expires_at) : NaN
    if (!Number.isFinite(after) || after <= before) throw new Error('Oturum uzatması doğrulanamadı; güncel süreyi kontrol et.')
    setSheet(null); setExtendUnderstood(false)
  }, 'Oturum uzatıldı. Açık pozisyonlar ve ayarlar değişmedi.', setSheetError)
  const chosenMode = modePick ?? savedMode
  const modeReady = chosenMode !== savedMode && modeUnderstood && (chosenMode === 'guvenli' || modeAck) && (chosenMode !== 'agresif' || modePhrase.trim() === AGGRESSIVE_PHRASE)
  const pickMode = (mode: string) => { setModePick(mode === savedMode ? null : mode); setModeAck(false); setModePhrase(''); setModeError('') }
  const saveMode = () => perform('mode', async () => {
    setModeError('')
    const payload = await request('/original-v2/margin-mode', {
      mode: chosenMode, acknowledged: chosenMode !== 'guvenli' && modeAck,
      confirmation: chosenMode === 'agresif' ? modePhrase.trim() : '', understood: modeUnderstood,
    }) as {margin_mode?: unknown} | null
    if (!payload || payload.margin_mode !== chosenMode) throw new Error('Teminat modunun kaydı doğrulanamadı; güncel modu kontrol et.')
    setModePick(null); setModeUnderstood(false); setModeAck(false); setModePhrase('')
  }, 'Teminat modun kaydedildi. Yalnız yeni işlemlere uygulanır; açık pozisyonlar değişmez.', setModeError)

  // ---- Onaylar: sırayla, her birinin sonucu ayrı gösterilir.
  const approvalDone = {consent: guideInput.authorizationValid, policy: guideInput.policyAcknowledged, arm: guideInput.armed}
  const [approvalState, setApprovalState] = useState<Record<string, {state: ItemState; error?: string}>>({})
  const [approvalAccepted, setApprovalAccepted] = useState(false)
  const [openItem, setOpenItem] = useState<string | null>('consent')
  const approvalSteps = [
    {key: 'consent' as const, path: '/consent'},
    {key: 'policy' as const, path: '/policy/acknowledge'},
    {key: 'arm' as const, path: '/arm'},
  ]
  const runApprovals = () => perform('approvals', async () => {
    for (const step of approvalSteps) {
      if (approvalDone[step.key]) continue
      setApprovalState(current => ({...current, [step.key]: {state: 'working'}}))
      try {
        await request(step.path, {understood: true})
        setApprovalState(current => ({...current, [step.key]: {state: 'done'}}))
      } catch (error) {
        const message = errorText(error, 'Onay verilemedi.')
        setApprovalState(current => ({...current, [step.key]: {state: 'error', error: message}}))
        setOpenItem(step.key)
        throw new Error(message)
      }
    }
    setSheet(null)
  }, 'Güvenlik onayları tamam. Sıradaki adım: son kontrol ve başlatma.')
  const itemState = (key: 'consent' | 'policy' | 'arm'): ItemState => approvalState[key]?.state === 'working' || approvalState[key]?.state === 'error'
    ? approvalState[key].state : approvalDone[key] ? 'done' : 'todo'

  // ---- Başlat: özet ekranından; risk yüzdeleri sunucuda kayıtlı değerlerdir.
  const [startUnderstood, setStartUnderstood] = useState(false)
  const [startAck, setStartAck] = useState(false)
  const [startPhrase, setStartPhrase] = useState('')
  const summary = wallet !== null ? riskSummary(wallet, savedRisk, savedDaily, caps) : null
  const startReady = wallet !== null && startUnderstood && tierReady(savedRisk, startAck, startPhrase) && validCoins(draft.coins) && validHours(draft.hours)
  const consentExpiresMs = status?.authorization?.expires_at ? Date.parse(status.authorization.expires_at) : NaN
  const consentLeftMs = Number.isFinite(consentExpiresMs) ? consentExpiresMs - Date.now() : 0
  const consentShorterThanSession = guideInput.authorizationValid && consentLeftMs < draft.hours * 3600 * 1000
  const start = () => perform('start', async () => {
    if (wallet === null) throw new Error('Güncel Binance bakiyesi doğrulanamadı; strateji başlatılmadı.')
    // Oturum, 24 saatlik risk izninin kalan süresiyle sınırlanır; seçilen süre izinden uzunsa izni yenile (yenileme ARM'ı sıfırlar).
    const renewConsent = !guideInput.authorizationValid || consentLeftMs < draft.hours * 3600 * 1000
    if (renewConsent) await request('/consent', {understood: true})
    if (!guideInput.policyAcknowledged) await request('/policy/acknowledge', {understood: true})
    if (renewConsent || !guideInput.armed) await request('/arm', {understood: true})
    let payload: {original_v2_live?: {enabled?: unknown}; enabled?: unknown} | null
    try { payload = await request('/original-v2/start', {
      risk_percent: savedRisk, daily_loss_percent: savedDaily, risk_acknowledged: savedRisk > 2 && startAck,
      high_risk_confirmation: savedRisk > 5 ? startPhrase : '', warning_balance_usdt: wallet,
      expected_risk_revision: original?.risk_revision ?? 0, understood: true,
      symbol_count: draft.coins, session_hours: draft.hours,
    }) as typeof payload } catch (error) { await settleAfterTimeout(error, true); payload = {enabled: true} }
    const enabled = payload && typeof payload === 'object' ? (payload.original_v2_live?.enabled ?? payload.enabled) : undefined
    if (enabled !== true) throw new Error('Başlatma sonucu doğrulanamadı; güncel durumu kontrol et.')
    setSheet(null); setStartUnderstood(false); setStartAck(false); setStartPhrase('')
  }, `Otomatik işlem başladı · ${draft.hours} saat · ${draft.coins} coin taranıyor.`, setSheetError)

  // ---- Durdur: yalnız yeni girişleri kapatır; açık pozisyonların Binance'teki korumaları yerinde kalır.
  const running = guideInput.running
  const runningRef = useRef(running)
  runningRef.current = running
  /** Yanıt gecikince (zaman aşımı) istek sunucuda yine de tamamlanmış olabilir: tekrar GÖNDERMEDEN durumu birkaç kez
   *  okuyup gerçek sonucu buluruz. İstenen duruma ulaşıldıysa başarı sayılır; ulaşılmadıysa hata açıkça söylenir. */
  const settleAfterTimeout = async (error: unknown, wantRunning: boolean) => {
    if (!/zaman aşımı|timeout|aborted/i.test(error instanceof Error ? error.message : '')) throw error
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 4000))
      try { await refresh() } catch { /* bir sonraki denemede tekrar okunur */ }
      await new Promise(resolve => window.setTimeout(resolve, 50))
      if (runningRef.current === wantRunning) return
    }
    throw new Error(wantRunning
      ? 'Sunucu yanıtı gecikti; durum kontrol edildi ve strateji başlamamış görünüyor. Birkaç saniye sonra tekrar başlatabilirsin.'
      : 'Sunucu yanıtı gecikti; strateji hâlâ çalışıyor görünüyor. Tekrar durdur.')
  }
  const stop = () => perform('stop', async () => {
    if (original?.enabled) {
      let payload: {original_v2_live?: {enabled?: unknown}; enabled?: unknown} | null
      try { payload = await request('/original-v2/stop') as typeof payload } catch (error) { await settleAfterTimeout(error, false); payload = {enabled: false} }
      const enabled = payload && typeof payload === 'object' ? (payload.original_v2_live?.enabled ?? payload.enabled) : undefined
      if (enabled !== false) throw new Error('Durdurma sonucu doğrulanamadı; güncel durumu kontrol et.')
    } else {
      await request('/auto/stop')
    }
    setSheet(null)
  }, 'Durduruldu. Yeni işlem açılmaz; açık pozisyonların stop ve hedefleri Binance’te duruyor.', setSheetError)
  const [closePhrase, setClosePhrase] = useState('')
  // ---- Tek pozisyonu kapat: yalnız o sembolü piyasa fiyatından reduce-only emirle kapatır; Stop/TP temizliğini sunucu yapar.
  const [closeTarget, setCloseTarget] = useState<CloseTarget | null>(null)
  const askClosePosition = (target: CloseTarget) => { setSheetError(''); setCloseTarget(target); setSheet('close-position') }
  const closePosition = () => {
    const target = closeTarget
    if (!target) return Promise.resolve(false)
    return perform('close-position', async () => {
      await request('/position/close', {plan_id: target.planId, confirmation: CLOSE_POSITION_PHRASE})
      setSheet(null); setCloseTarget(null)
    }, `${target.symbol.replace('USDT', '')} kapanış emri gönderildi. Pozisyon kapanınca Stop/TP emirleri otomatik temizlenir.`, setSheetError)
  }
  const closeAll = () => perform('close-all', async () => {
    await request('/emergency', {confirmation: CLOSE_ALL_PHRASE, close_tracked_positions: true})
    setSheet(null); setClosePhrase('')
  }, 'Acil durdurma gönderildi: bota ait emirler iptal ediliyor, pozisyonlar reduce-only emirle kapatılıyor.', setSheetError)

  // ---- 2FA isteyen işlemler.
  const [code, setCode] = useState('')
  const [secureAccepted, setSecureAccepted] = useState(false)
  const closeSheet = () => { if (!flight.current) { setSheet(null); setCode(''); setSecureAccepted(false); setClosePhrase(''); setCloseTarget(null) } }
  const secure = (key: 'emergency-clear' | 'recovery' | 'fill-lock') => perform(key, async () => {
    if (key === 'emergency-clear') await request('/emergency/clear', {confirmation: 'ACİL DURDURMAYI KALDIRIYORUM', totp_code: code})
    else if (key === 'recovery') await request('/recovery/check', {confirmation: 'LIVE RECOVERY CHECK', totp_code: code})
    else {
      const payload = await request('/original-v2/fill-lock/approve', {lock_id: original?.fill_lock?.id, confirmation: FILL_LOCK_PHRASE}) as {fill_lock?: {active?: unknown}} | null
      if (payload?.fill_lock?.active !== false) throw new Error('Dolum kilidi onayı doğrulanamadı; güncel durumu kontrol et.')
    }
    setSheet(null); setCode(''); setSecureAccepted(false)
  }, key === 'emergency-clear' ? 'Acil durdurma kaldırıldı. Yeniden başlatmak için rehberdeki adımı izle.'
    : key === 'recovery' ? 'Kurtarma kontrolü tamamlandı.' : 'Dolum kilidi kaldırıldı; otomasyon kendiliğinden başlamaz.', setSheetError)

  // ---- Kendiliğinden düzelme: yalnız durum okuması, artan aralıklarla.
  const [retry, setRetry] = useState({attempt: 0, at: 0})
  const [now, setNow] = useState(() => Date.now())
  const wantsRetry = guide.action === 'retry' && !loading && !working
  useEffect(() => {
    if (!wantsRetry) { setRetry(current => current.attempt === 0 && current.at === 0 ? current : {attempt: 0, at: 0}); return }
    const at = Date.now() + retryDelay(retry.attempt) * 1000
    setRetry(current => ({...current, at}))
    const timer = window.setTimeout(() => {
      void Promise.resolve(refresh()).catch(() => undefined).finally(() => setRetry(current => ({attempt: current.attempt + 1, at: 0})))
    }, at - Date.now())
    return () => window.clearTimeout(timer)
  }, [wantsRetry, retry.attempt])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!(wantsRetry && retry.at) && !running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [wantsRetry, retry.at, running])
  const retryIn = wantsRetry && retry.at ? Math.max(0, Math.ceil((retry.at - now) / 1000)) : null

  // ---- Oturum geri sayımı (sunucudan gelen kalan süreden, yerel saatle ilerletilir).
  const [sessionMark, setSessionMark] = useState({received: Date.now(), remaining: original?.session?.remaining_seconds ?? 0})
  useEffect(() => setSessionMark({received: Date.now(), remaining: original?.session?.remaining_seconds ?? 0}), [original?.session?.remaining_seconds, original?.session?.expires_at, original?.enabled])
  const sessionLeft = running ? Math.max(0, Math.ceil(sessionMark.remaining - Math.max(0, now - sessionMark.received) / 1000)) : 0

  // ---- Adım geçişi: biten adımı ve sıradakini söyler.
  const previousStep = useRef<{phase: string; step: number} | null>(null)
  const [advanced, setAdvanced] = useState(0)
  useEffect(() => {
    const prev = previousStep.current
    previousStep.current = {phase: guide.phase, step: guide.step}
    if (!prev || prev.phase === 'loading') return
    if (guide.phase === 'running' && prev.phase !== 'running') { setAdvanced(value => value + 1); return }
    if (guide.phase === 'setup' && guide.step > prev.step && (prev.phase === 'setup' || prev.phase === 'problem')) {
      setAdvanced(value => value + 1)
      setFlash({tone: 'ok', text: `${prev.step}. adım tamamlandı · Sıradaki: ${GUIDE_STEPS[guide.step - 1]}`})
    }
  }, [guide.phase, guide.step])
  useEffect(() => {
    if (!flash) return
    const timer = window.setTimeout(() => setFlash(null), flash.tone === 'stop' ? 9000 : 5000)
    return () => window.clearTimeout(timer)
  }, [flash])

  useEffect(() => { if (guide.action === 'choose-risk') setSettingsOpen(true) }, [guide.action])
  const act = (action = guide.action) => {
    if (action === 'retry') return void perform('retry', async () => undefined)
    if (action === 'open-connection') return goTab('baglanti')
    if (action === 'upgrade') { window.location.href = '/pricing'; return }
    if (action === 'choose-risk') { setSettingsOpen(true); window.setTimeout(() => document.getElementById('lc-strategy')?.scrollIntoView({behavior: 'smooth', block: 'center'}), 30); return }
    const sheets: Record<string, Sheet> = {approve: 'approvals', start: 'start', stop: 'stop', 'clear-emergency': 'emergency-clear', recovery: 'recovery', 'review-fill-lock': 'fill-lock'}
    if (action && sheets[action]) { setApprovalState({}); setApprovalAccepted(false); setSheetError(''); setSheet(sheets[action]) }
  }

  // ---- Görünen veriler.
  // Hesap okunamıyorsa metrik ve pozisyon gösterilmez ("—"): eski ya da boş veri gerçekmiş gibi görünmesin.
  const live = verified && guideInput.connected
  const positions = (live ? status?.account?.positions : null) ?? []
  const day = original?.daily
  const dayCurrent = day?.day === new Date().toISOString().slice(0, 10)
  const limitCandidates = [day?.limit, caps?.daily_loss_limit_usdt, status?.policy?.daily_loss_limit].map(finite).filter((value): value is number => value !== null && value > 0)
  const dailyLimit = live && dayCurrent && finite(day?.limit) ? Math.min(...limitCandidates) : null
  const dailyLoss = live && dayCurrent ? finite(day?.loss) : null
  const protectedCount = positions.filter(position => {
    const plan = findPlan(position)
    return plan?.provenance_state === 'CONFIRMED' && plan.exchange_protection?.stop === true
  }).length
  const alerts: GuideNotice[] = [...guide.notices, ...(live ? smartAlerts({
    dailyLoss, dailyLimit, dailyLocked: guideInput.dailyLocked, availableBalance, minMargin: caps?.min_margin_usdt ?? null,
    marketBanSeconds, openPositions: positions.length, protectedPositions: protectedCount,
  }) : [])]
  const why = running ? whyNoTrade(original?.scanner) : null
  const today = live ? dailySummary({realizedPnl: status?.daily?.realized_pnl, entries: status?.daily?.entries, last24h: original?.scanner?.last_24h, dailyLoss, dailyLimit}) : null
  const eventTone = (kind: unknown) => NOTIFY_KINDS.find(item => item.pattern.test(String(kind || '')))?.tone ?? 'mute'
  const notify = useMemo(() => {
    try { const prefs = loadNotifyPrefs(); return prefs.browser && browserNotificationState() === 'granted' } catch { return false }
  }, [])
  const scanStage: Record<string, string> = {
    UNIVERSE_PENDING: 'Coin listesi hazırlanıyor', UNIVERSE_REFRESH: 'Coin listesi yenileniyor', SCANNING: 'Taranıyor',
    SCAN_COMPLETE: 'Tarama tamam · sıradaki mum bekleniyor', WAITING_NEXT_CANDLE: 'Sıradaki 15 dk mum bekleniyor', WAITING_RISK_DATA: 'Günlük risk verisi bekleniyor',
    RATE_LIMIT_WAIT: 'Binance sınırı bekleniyor', NO_ELIGIBLE_SYMBOLS: 'Filtreye uyan coin yok', BLOCKED: 'Yeni girişler engelli',
  }
  // Sorun varken ilerleme, gerçekten tamamlanmış adımlardan sayılır (sorunun kendisi adım değildir).
  const completed = [guideInput.connected && guideInput.balance !== null, effectiveInput.riskApproved, guideInput.authorizationValid && guideInput.policyAcknowledged]
  const progressDone = guide.phase === 'running' ? GUIDE_STEPS.length : guide.phase === 'problem' ? Math.min(completed.indexOf(false) === -1 ? 3 : completed.indexOf(false), guide.step - 1 >= 0 && guide.step > 1 ? guide.step - 1 : 3) : guide.step - 1
  const currentIndex = guide.phase === 'problem' ? progressDone : guide.step - 1
  const kaisQuestion = running && why ? 'Canlı stratejim son taramada neden işlem açmadı? Kısaca açıkla.'
    : running ? 'Canlı stratejim şu an ne yapıyor? Kısaca özetle.'
    : `Canlı işlem kurulumunda şu adımdayım: "${guide.title}". Ne yapmam gerektiğini kısaca anlatır mısın?`

  const actionIcon = guide.action === 'stop' ? <Power aria-hidden="true"/> : guide.action === 'retry' ? <RefreshCw aria-hidden="true" className={working === 'retry' ? 'lcSpin' : ''}/> : null
  const presetCards = <div className="lcPresets" role="radiogroup" aria-label="Risk seçeneği">
    {RISK_PRESETS.map(preset => {
      const numbers = wallet !== null ? riskSummary(wallet, preset.risk_percent, preset.daily_loss_percent, caps) : null
      const selected = !custom && activePreset?.id === preset.id
      return <button type="button" role="radio" aria-checked={selected} key={preset.id} className="lcPreset" data-id={preset.id} data-selected={selected || undefined}
        disabled={busy} onClick={() => choosePreset(preset.id)}>
        <span className="lcPresetHead"><b>{preset.label}</b>{preset.id === 'dengeli' && <em>Önerilen</em>}</span>
        <span className="lcPresetLine">İşlem başı <b>{numbers ? usdt(numbers.perTrade) : pct(preset.risk_percent)}</b>{numbers?.perTradeCapped && <em>sunucu tavanı</em>}</span>
        <span className="lcPresetLine">Günde en fazla <b>{numbers ? usdt(numbers.perDay) : pct(preset.daily_loss_percent)}</b>{numbers?.perDayCapped && <em>sunucu tavanı</em>}</span>
        <small>{preset.symbol_count} coin · {preset.session_hours} saat · {preset.note}</small>
      </button>
    })}
    <button type="button" role="radio" aria-checked={custom || (!activePreset)} className="lcPreset lcPresetCustom" data-selected={custom || !activePreset || undefined} disabled={busy} onClick={() => setCustom(true)}>
      <span className="lcPresetHead"><b>Kendim ayarlayayım</b></span>
      <small>{!activePreset && !custom ? `Şu an: %${draft.risk} / %${draft.daily} · ${draft.coins} coin · ${draft.hours} saat` : 'Risk, günlük sınır, coin sayısı ve süre'}</small>
    </button>
  </div>
  const tier = riskTier(draft.risk)
  // Dürüst risk: seçilen yüzde HEDEFTİR; gerçek risk stop mesafesi ve teminat sınırlarıyla her işlemde ayrı hesaplanır.
  const preview = wallet !== null ? capacityPreview(wallet, original?.risk_limits) : null
  const worstRisk = preview ? Math.min(draft.risk, preview.atMaxStop.percent) : draft.risk
  const editorOpen = settingsOpen && verified && Boolean(original) && guideInput.connected && !guideInput.emergency
  const honestTarget = editorOpen && draftValid ? draft.risk : riskSaved ? savedRisk : null
  const unreachableAtMax = preview !== null && honestTarget !== null && honestTarget > preview.atMaxStop.percent + 1e-9
  const lastAdj = original?.last_risk_adjustment ?? null
  const lastBreakdown = lastAdj?.breakdown ?? null
  const honestBlock = <>
    {honestTarget !== null && <div className="lcHonest" data-warn={unreachableAtMax || preview === null || undefined}>
      <p className="lcHonestHead"><b>Seçtiğin {pct(honestTarget)} bir hedeftir, gerçek risk değil.</b> Gerçek kayıp, stop mesafesine göre işlem başına hesaplanır.</p>
      {preview ? <>
        <ul className="lcHonestRows">{preview.rows.map(row => <li key={row.stopPct}><span>Stop %{row.stopPct} uzaktaysa</span><b>en fazla ≈ {usdt(row.loss)} ({pct(row.percent)})</b></li>)}</ul>
        {unreachableAtMax && <p className="lcHint warn" role="alert">Seçtiğin {pct(honestTarget)} bu bakiye ve {original?.risk_limits?.leverage}x kaldıraçla hiçbir işlemde tam uygulanamaz. En iyi durumda (stop %{preview.atMaxStop.stopPct} uzakta) bile en fazla {pct(preview.atMaxStop.percent)} olur. Bunun sebebi işlem başı teminat sınırıdır.</p>}
      </> : <p className="lcHint warn" role="alert">Bakiye veya sınırlar doğrulanamadı; gerçekte ulaşılabilecek risk hesaplanamıyor. Seçtiğin değer yalnızca bir hedeftir.</p>}
      <details className="lcFine"><summary>Hesap nasıl yapılıyor?</summary><small>Tahmindir: ücret ve kayma tahmini dahil; stop'un boşlukla aşılması ve fonlama hariç. Açık pozisyonlar ve anlık kullanılabilir bakiye işlem anında bu sınırı daha da düşürebilir. Likidasyon payı ayrıca kontrol edilir.</small></details>
    </div>}
    {lastAdj && <div className="lcHonest" data-last>
      <p className="lcHonestHead"><b>Son işlem adayı · {lastAdj.symbol}</b></p>
      <ul className="lcHonestRows">
        <li><span>Hedef risk</span><b>{usdt(lastAdj.requested_loss_usdt)} ({pct(lastAdj.selected_percent)})</b></li>
        <li><span>Uygulanabilir en fazla</span><b>{lastBreakdown && lastBreakdown.status !== 'unavailable' && typeof lastBreakdown.max_applicable_loss_usdt === 'number' ? `${usdt(lastBreakdown.max_applicable_loss_usdt)} (${pct(lastBreakdown.max_applicable_percent ?? 0)})` : 'hesaplanamadı'}</b></li>
        <li><span>Gerçekleşecek (tahmini)</span><b>{usdt(lastAdj.effective_loss_usdt)} ({pct(lastAdj.effective_percent)})</b></li>
      </ul>
      {lastBreakdown?.reachable === false && <p className="lcHint warn">Hedefe ulaşılamadı{lastBreakdown.unreached_reason ? `: ${lastBreakdown.unreached_reason}` : ''}.</p>}
      {lastBreakdown?.status === 'partial' && <p className="lcHint">Doğrulanamayan: {(lastBreakdown.unverified ?? []).join(', ')}; bu yüzden uygulanabilir en fazla değer eksik olabilir.</p>}
      {(!lastBreakdown || lastBreakdown.status === 'unavailable') && <p className="lcHint warn">Hedef ile uygulanabilir risk karşılaştırması hesaplanamadı. Gerçekleşecek değer yine botun hesapladığı stop kaybıdır.</p>}
      {lastAdj.reasons.length > 0 && <small>Düşürme sebepleri: {lastAdj.reasons.join(' · ')}.</small>}
    </div>}
  </>
  const modeRows = original?.margin_modes ?? []
  const customMax = modeRows.reduce((max, row) => Math.max(max, row.per_trade_percent), 0)
  const customValue = customOn ? clampCustomPercent(customRaw, customMax) : null
  const savedRow = modeRows.find(row => row.mode === savedMode), chosenRow = modeRows.find(row => row.mode === chosenMode)
  const simPercent = customOn ? customValue : chosenRow?.per_trade_percent ?? null
  const simContext = {openMargin: openMarginTotal(positions as Array<Record<string, unknown>>), dailyLimit: original?.daily?.limit ?? null, dailyLoss: original?.daily?.loss ?? null}
  const sim = wallet !== null && simPercent !== null ? marginSimulation(wallet, caps, simPercent, simContext) : null
  const oldSim = wallet !== null && savedRow ? marginSimulation(wallet, caps, savedRow.per_trade_percent, simContext) : null
  const simChanged = simPercent !== null && savedRow ? simPercent !== savedRow.per_trade_percent : false
  const num = (value: number | null | undefined) => usdt(value).replace(' USDT', '')
  const riskyPick = simPercent !== null && simPercent >= 50
  const nearestMode = customValue === null ? null : modeRows.reduce<typeof modeRows[number] | null>((best, row) => !best || Math.abs(row.per_trade_percent - customValue) < Math.abs(best.per_trade_percent - customValue) ? row : best, null)
  const modeBlock = verified && original && guideInput.connected && !guideInput.emergency && modeRows.length > 0 ? <div className="lcMode" id="lc-mode">
    <p className="lcHonestHead"><b>Teminat modu</b> · Her işleme ne kadar teminat ayrılabileceğini sen seçersin. Sadece senin hesabında geçerli; istediğin zaman değiştirebilirsin. <button type="button" className="lcLink lcWhatIs" aria-expanded={modeHelpOpen} onClick={() => setModeHelpOpen(value => !value)}>Bu ne?</button></p>
    {modeHelpOpen && <p className="lcWhy">Teminat, her işleme bakiyenden ayırdığın paradır. Modu yükseltirsen işlem büyür: kazanç da kayıp da büyür. Stop-loss her zaman çalışır ve zarar o seviyede durur. Toplam teminat sınırı her modda aynıdır.</p>}
    <div className="lcModes" role="radiogroup" aria-label="Teminat modu">
      {modeRows.map(row => {
        const view = wallet !== null ? marginModePreview(wallet, caps, row.per_trade_percent) : null
        const rowSim = wallet !== null ? marginSimulation(wallet, caps, row.per_trade_percent, simContext) : null
        const selected = !customOn && chosenMode === row.mode
        return <button type="button" role="radio" aria-checked={selected} key={row.mode} className="lcPreset lcModeCard" data-id={row.mode} data-selected={selected || undefined} disabled={busy} onClick={() => { setCustomOn(false); pickMode(row.mode) }}>
          <span className="lcPresetHead"><b>{row.label}</b>{savedMode === row.mode && <em>AKTİF</em>}</span>
          <span className="lcModePct"><strong>%{row.per_trade_percent}</strong><small>bakiye / işlem</small></span>
          <span className="lcMeter" aria-hidden="true"><i style={{width: `${Math.min(100, row.per_trade_percent)}%`}}/></span>
          <small className="lcModeNote">{MODE_TEXT[row.mode]}</small>
          <span className="lcKV"><em>Teminat</em><b>{view ? usdt(view.marginUsdt) : '—'}</b></span>
          <span className="lcKV"><em>Pozisyon (en fazla)</em><b>{rowSim?.notional != null ? usdt(rowSim.notional) : '—'}</b></span>
          <span className="lcKV"><em>{view ? `Stop %${view.atThreeStop?.stopPct ?? 3} kaybı` : 'Stop kaybı'}</em><b className="lcLoss">{view ? `≈ ${usdt((view.atThreeStop ?? view.atMaxStop).loss)} · ${pct((view.atThreeStop ?? view.atMaxStop).percent)}` : '—'}</b></span>
        </button>
      })}
    </div>
    {customMax >= CUSTOM_MARGIN_MIN_PERCENT && <div className="lcCustomMargin">
      <button type="button" className="lcLink" aria-expanded={customOn} disabled={busy} onClick={() => { setCustomOn(value => !value); setModePick(null); setModeAck(false); setModePhrase('') }}>{customOn ? 'Özel yüzde denemesini kapat' : 'Özel yüzde dene (kaydedilmez)'}</button>
      {customOn && <div className="lcCustomBox">
        <label>İşlem başı teminat (%{CUSTOM_MARGIN_MIN_PERCENT}–%{customMax})
          <input type="range" min={CUSTOM_MARGIN_MIN_PERCENT} max={customMax} step="1" value={customValue ?? CUSTOM_MARGIN_MIN_PERCENT} onChange={event => setCustomRaw(event.target.value)}/>
        </label>
        <label>Yüzde
          <input type="number" inputMode="decimal" min={CUSTOM_MARGIN_MIN_PERCENT} max={customMax} step="1" value={customRaw} onChange={event => setCustomRaw(event.target.value)}/>
        </label>
        {customValue === null
          ? <p className="lcHint warn" role="alert">Geçersiz değer. %{CUSTOM_MARGIN_MIN_PERCENT} ile %{customMax} arasında bir sayı yaz.</p>
          : <p className="lcHint">Bu sadece bir deneme: kaydedilmez ve emir açmaz. Sunucu şu an yalnız Güvenli, Orta ve Agresif modları kabul eder{nearestMode ? `; senin denemene en yakını ${nearestMode.label} (%${nearestMode.per_trade_percent})` : ''}.</p>}
      </div>}
    </div>}
    {sim && <div className="lcSim" aria-label="Tahmini değerler">
      <p className="lcSimHead"><b>{customOn ? `Özel deneme · %${sim.percent}` : `${chosenRow?.label ?? ''} · %${sim.percent}`}</b> <small>Tahminidir; gerçekleşen değer Binance minimum/adım kuralları, bakiye ve açık pozisyonlara göre daha düşük olabilir.</small></p>
      <dl className="lcSimGrid">
        <div><dt>İşlem başı teminat (en fazla)</dt><dd>{usdt(sim.marginUsdt)}{sim.capped ? ' · sistem tavanıyla kırpıldı' : ''}</dd></div>
        <div><dt>Tahmini pozisyon büyüklüğü</dt><dd>{sim.notional !== null ? usdt(sim.notional) : '—'}</dd></div>
        <div><dt>Stop seviyesinde tahmini kayıp</dt><dd>{sim.lossAtThree ? `≈ ${usdt(sim.lossAtThree.loss)} (${pct(sim.lossAtThree.percent)})` : '—'}</dd></div>
        <div><dt>Bakiyeye oran</dt><dd>%{sim.walletShare.toFixed(1)}</dd></div>
        <div><dt>Toplam teminat sınırı</dt><dd>{sim.totalLimit !== null ? usdt(sim.totalLimit) : '—'}</dd></div>
        <div><dt>Açık teminat{sim.openEstimated ? ' (tahmini)' : ''}</dt><dd>{sim.openMargin !== null ? usdt(sim.openMargin) : 'doğrulanamadı'}</dd></div>
        <div><dt>Kalan teminat kapasitesi</dt><dd>{sim.room !== null ? `${usdt(sim.room)}${sim.tradesFit !== null ? ` · ≈ ${sim.tradesFit} yeni işlem` : ''}` : '—'}</dd></div>
        <div><dt>Günlük zarar limiti</dt><dd>{sim.dailyLimit !== null ? `${usdt(sim.dailyLimit)}${sim.dailyLeft !== null ? ` · kalan ${usdt(sim.dailyLeft)}` : ''}` : '—'}</dd></div>
        <div><dt>Açık pozisyon</dt><dd>{positions.length}{finite(status?.policy?.max_positions) ? ` / ${status?.policy?.max_positions}` : ''}</dd></div>
      </dl>
      {riskyPick && <p className="lcHint warn" role="note">Dikkat: işlem başına bakiyenin yarısından fazlası teminat olarak ayrılabilir. Kazanç da, kayıp da büyür; stop-loss yine çalışır ama zarar büyük olabilir.</p>}
      {simChanged && oldSim && <table className="lcCompare"><caption>Şimdiki mod ({savedRow?.label}) ile karşılaştırma (USDT)</caption>
        <thead><tr><th/><th>Şimdi</th><th>Yeni</th></tr></thead>
        <tbody>
          <tr><th>Teminat / işlem</th><td>{num(oldSim.marginUsdt)}</td><td>{num(sim.marginUsdt)}</td></tr>
          <tr><th>Pozisyon</th><td>{oldSim.notional !== null ? num(oldSim.notional) : '—'}</td><td>{sim.notional !== null ? num(sim.notional) : '—'}</td></tr>
          <tr><th>Stop kaybı (en fazla)</th><td>{oldSim.lossAtThree ? num(oldSim.lossAtThree.loss) : '—'}</td><td>{sim.lossAtThree ? num(sim.lossAtThree.loss) : '—'}</td></tr>
        </tbody></table>}
    </div>}
    <details className="lcFine"><summary>Her modda değişmeyenler</summary><small>Toplam teminat sınırı (%{modeRows[0].total_percent}), her işlemin stop-loss'u, günlük kayıp sınırı ve diğer korumalar her modda aynen çalışır. Seçtiğin risk bir hedeftir: sistem karşılayamazsa işlem yine açılabilir ama gerçek risk daha düşük olur. Bunu "Son işlem adayı" kutusunda ve olay kaydında açıkça görürsün; sessizce düşürülmez.</small></details>
    {!customOn && chosenMode !== savedMode && <div className="lcModeConfirm">
      <label className="lcCheck"><input type="checkbox" checked={modeUnderstood} onChange={event => setModeUnderstood(event.target.checked)}/><span>Gerçek parayla işlem açılacağını ve kayıp olabileceğini anladım.</span></label>
      {chosenMode !== 'guvenli' && <label className="lcCheck"><input type="checkbox" checked={modeAck} onChange={event => setModeAck(event.target.checked)}/><span>Bu modda her işleme daha çok teminat ayrılacağını ve zararın büyüyebileceğini kabul ediyorum.</span></label>}
      {chosenMode === 'agresif' && <label className="lcField">Onay için şunu yaz: <code>{AGGRESSIVE_PHRASE}</code><input type="text" value={modePhrase} autoComplete="off" onChange={event => setModePhrase(event.target.value)}/></label>}
      {modeError && <p className="lcHint warn" role="alert">{modeError}</p>}
      <button type="button" className="lcBtn lcPrimary" disabled={busy || !modeReady} onClick={() => void saveMode()}>{working === 'mode' ? 'Kaydediliyor…' : `${modeRows.find(row => row.mode === chosenMode)?.label ?? ''} modunu uygula`}</button>
    </div>}
  </div> : null
  const strategyCard = <section className="lcCard lcStrategy" id="lc-strategy" aria-label="Strateji ve risk">
    <header className="lcCardHead"><div><small>STRATEJİ</small><h3>Kais Original V2 · risk ayarı</h3></div>
      {verified && guideInput.connected && !guideInput.emergency && <button type="button" className="lcLink" aria-expanded={editorOpen} onClick={() => setSettingsOpen(value => !value)}>{editorOpen ? 'Kapat' : riskSaved ? 'Değiştir' : 'Seç'}<ChevronDown aria-hidden="true" data-open={editorOpen || undefined}/></button>}</header>
    <div className="lcStrategyNow">
      <span><small>İşlem başı hedef risk</small><b>{riskSaved ? pct(savedRisk) : '—'}</b>{riskSaved && wallet !== null ? <em>≈ {usdt(riskSummary(wallet, savedRisk, savedDaily, caps)?.perTrade)}</em> : !riskSaved && <em>seçilmedi</em>}</span>
      <span><small>Günlük kayıp sınırı</small><b>{riskSaved ? pct(savedDaily) : '—'}</b>{riskSaved && wallet !== null && <em>≈ {usdt(riskSummary(wallet, savedRisk, savedDaily, caps)?.perDay)}</em>}</span>
      <span><small>Tarama</small><b>{draft.coins} coin</b><em>{draft.hours} saatlik oturum</em></span>
    </div>
    {honestBlock}
    {modeBlock}
    {editorOpen && <div className="lcStrategyEdit">
      {wallet === null && <p className="lcHint warn">Bakiye doğrulanınca her seçeneğin USDT karşılığı burada görünür.</p>}
      {presetCards}
      {custom && <div className="lcCustom">
        <label>İşlem başı risk (%)<input type="number" min="0.5" max="10" step="0.1" value={draft.risk} disabled={busy} onChange={event => { setDraft(current => ({...current, risk: Number(event.target.value)})); setRiskAck(false); setPhrase('') }}/></label>
        <label>Günlük kayıp sınırı (%)<input type="number" min="1" max="25" step="0.5" value={draft.daily} disabled={busy} onChange={event => setDraft(current => ({...current, daily: Number(event.target.value)}))}/></label>
        <label>Taranacak coin (10–100)<input type="number" min="10" max="100" step="5" value={draft.coins} disabled={busy || running} onChange={event => setStartParam('coins', Number(event.target.value))}/></label>
        <div className="lcSegWrap" role="group" aria-label="Oturum süresi"><span>Oturum süresi</span>
          <div className="lcSeg">{[1, 4, 12, 24].map(value => <button type="button" key={value} aria-pressed={draft.hours === value} disabled={busy || running} onClick={() => setStartParam('hours', value)}>{value} saat</button>)}</div></div>
      </div>}
      {!draftValid && <p className="lcHint stop" role="alert">İşlem riski %0,5–10, günlük sınır %1–25 ve işlem riskinden küçük olmamalı; coin sayısı 10–100.</p>}
      {draftValid && tier !== 'normal' && <p className="lcTier" data-tier={tier}>{tier === 'yellow' ? 'Ortalamanın üstünde risk.' : tier === 'orange' ? 'Yüksek risk: üst üste birkaç kayıp bakiyeni belirgin düşürür.' : 'Çok yüksek risk: ani hareketlerde kayıp daha büyük olabilir.'}
        {wallet !== null && <> {worstRisk < draft.risk ? 'Gerçekte uygulanabilen riskle, en kötü durumda' : ''} 10 kayıp üst üste gelirse bakiye ≈ <b>{usdt(wallet * (1 - worstRisk / 100) ** 10)}</b> (−%{Math.round((1 - (1 - worstRisk / 100) ** 10) * 100)}) olur.</>}</p>}
      {running && <p className="lcHint">Çalışırken yalnız risk yüzdeleri değişir ve yeni işlemlere uygulanır; coin sayısı ve süre bir sonraki başlatmada geçerli olur.</p>}
      {riskDirty && draftValid && <div className="lcConfirmBox">
        {draft.risk > 2 && draft.risk <= 5 && <label className="lcCheck"><input type="checkbox" checked={riskAck} onChange={event => setRiskAck(event.target.checked)}/><span>%{draft.risk} işlem riskinin yüksek olduğunu anladım.</span></label>}
        {draft.risk > 5 && <><label className="lcField">Onay için <b className="selectablePhrase">{HIGH_RISK_PHRASE}</b> yaz<input value={phrase} autoComplete="off" onChange={event => setPhrase(event.target.value)}/></label></>}
        <label className="lcCheck"><input type="checkbox" checked={understood} onChange={event => setUnderstood(event.target.checked)}/><span>Gerçek parayla işlem açılacağını ve kayıp olabileceğini anladım.</span></label>
        <button type="button" className="lcBtn lcPrimary" disabled={busy || wallet === null || !understood || !tierReady(draft.risk, riskAck, phrase)} onClick={() => void saveRisk()}>{working === 'risk' ? 'Kaydediliyor…' : 'Bu ayarı kaydet'}</button>
        {riskError && <p className="lcHint stop" role="alert" data-testid="live-risk-error">Kaydedilemedi: {riskError}</p>}
      </div>}
      {!riskDirty && <p className="lcHint">{riskSaved ? 'Kayıtlı risk ayarın bu. Coin sayısı ve süre seçimin bu tarayıcıda hatırlanır.' : 'Bir seçenek seç ve kaydet; rehber seni sıradaki adıma geçirir.'}</p>}
      {!riskSaved && !riskDirty && draftValid && <div className="lcConfirmBox">
        <label className="lcCheck"><input type="checkbox" checked={understood} onChange={event => setUnderstood(event.target.checked)}/><span>Gerçek parayla işlem açılacağını ve kayıp olabileceğini anladım.</span></label>
        <button type="button" className="lcBtn lcPrimary" disabled={busy || wallet === null || !understood || !tierReady(draft.risk, riskAck, phrase)} onClick={() => void saveRisk()}>{working === 'risk' ? 'Kaydediliyor…' : 'Bu ayarı kaydet'}</button>
        {riskError && <p className="lcHint stop" role="alert" data-testid="live-risk-error">Kaydedilemedi: {riskError}</p>}
      </div>}
    </div>}
  </section>

  // ---- Alt pencereler.
  const policy = status?.policy ?? {}
  const policyRows = ([
    ['İşlem başı en fazla kayıp', policy.max_loss_per_trade, 'usdt'], ['Günlük kayıp sınırı', policy.daily_loss_limit, 'usdt'],
    ['Aynı anda en fazla pozisyon', policy.max_positions, 'count'], ['Günlük en fazla işlem', policy.daily_trade_limit, 'count'],
    ['Üst üste kayıp sınırı', policy.consecutive_loss_limit, 'count'], ['En yüksek kaldıraç', policy.max_leverage, 'x'],
  ] as const).map(([label, value, kind]) => [label, finite(value), kind] as const).filter(([, value]) => value !== null && value > 0)
  const consentEnds = status?.authorization?.expires_at ? Date.parse(status.authorization.expires_at) : Date.now() + 24 * 3600 * 1000
  const approvalItems = [
    {key: 'consent' as const, title: '24 saatlik risk izni', short: 'Gerçek parayla emir açılmasına 24 saatlik izin.',
      details: [<>Sistem senin adına Binance Futures’ta <b>gerçek parayla</b> emir açabilir; kayıp olabilir.</>,
        <>İzin {guideInput.authorizationValid ? 'şu tarihte biter' : 'verildikten 24 saat sonra, yaklaşık'}: <b>{dateTime(consentEnds)}</b>. Kendiliğinden yenilenmez.</>,
        <>Yalnız bu hesaba ve kayıtlı Binance anahtarına bağlıdır; anahtar değişirse izin geçersiz olur.</>,
        <>Para çekme yetkisi kullanılmaz; anahtarında para çekme izni kapalı olmalı.</>]},
    {key: 'policy' as const, title: 'Hesap risk sınırları', short: 'Sunucunun değiştirilemez güvenlik sınırlarını onaylarsın.',
      details: [...policyRows.map(([label, value, kind]) => <>{label}: <b>{kind === 'usdt' ? usdt(value) : kind === 'x' ? `${value}x` : value}</b></>),
        <>Bu sınırlar senin seçtiğin yüzdelerin üstünde ikinci bir emniyettir; hangisi daha düşükse o uygulanır.</>,
        <>Sınırlar değişirse bu onay kendiliğinden geçersiz olur ve yeniden sorulur.</>]},
    {key: 'arm' as const, title: 'Canlı emir izni (ARM)', short: 'Otomatik işlemin emir gönderebilmesi için son anahtar.',
      details: [<>Gerçek işlem kilidini 24 saat için açar; tek başına emir açmaz, stratejiyi de başlatmaz.</>,
        <>Durdurduğunda ya da acil durdurmada kendiliğinden kapanır.</>,
        <>Açılabilmesi için sunucudaki hazırlık kontrollerinin hepsi geçmiş olmalı.</>]},
  ]
  const approvalsLeft = approvalItems.filter(item => !approvalDone[item.key]).length
  const stateLabel: Record<ItemState, string> = {done: 'Tamam', todo: 'Bekliyor', working: 'Veriliyor…', error: 'Verilemedi'}

  const sheetView = sheet === 'approvals' ? <>
    <h2 id="lc-sheet-title">Neye onay veriyorsun?</h2>
    <p className="lcSheetLead">Üç ayrı güvenlik onayı. Hiçbiri tek başına emir açmaz; ayrıntısına basıp neye izin verdiğini görebilirsin.</p>
    <ol className="lcApprovals">{approvalItems.map((item, index) => {
      const state = itemState(item.key)
      const open = openItem === item.key
      return <li key={item.key} data-state={state}>
        <button type="button" className="lcApprovalHead" aria-expanded={open} onClick={() => setOpenItem(open ? null : item.key)}>
          <span className="lcApprovalNo" aria-hidden="true">{state === 'done' ? <Check/> : index + 1}</span>
          <span className="lcApprovalTitle"><b>{item.title}</b><small>{item.short}</small></span>
          <span className="lcChip" data-state={state}>{stateLabel[state]}</span>
          <ChevronDown aria-hidden="true" data-open={open || undefined}/>
        </button>
        {open && <ul className="lcApprovalBody">{item.details.map((line, row) => <li key={row}>{line}</li>)}</ul>}
        {approvalState[item.key]?.error && <p className="lcHint stop" role="alert">{approvalState[item.key].error}</p>}
      </li>
    })}</ol>
    {approvalsLeft > 0 ? <>
      <label className="lcCheck"><input type="checkbox" checked={approvalAccepted} onChange={event => setApprovalAccepted(event.target.checked)}/><span>Yukarıdakileri okudum; gerçek parayla işlem riskini kabul ediyorum.</span></label>
      <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
        <button type="button" className="lcBtn lcPrimary" disabled={!approvalAccepted || busy} onClick={() => void runApprovals()}>{working === 'approvals' ? 'Onaylar veriliyor…' : `${approvalsLeft} onayı ver ve devam et`}</button></div>
    </> : <div className="lcSheetActions"><button type="button" className="lcBtn lcPrimary" onClick={closeSheet}>Tamam</button></div>}
  </> : sheet === 'start' ? <>
    <h2 id="lc-sheet-title">Başlatmadan önce son kontrol</h2>
    <ul className="lcChecklist" aria-label="Hazırlık kontrol listesi">
      {([
        [guideInput.connected, 'Binance bağlantısı', guideInput.connected ? 'Bağlı, emir göndermeye hazır' : 'Bağlantı doğrulanamadı'],
        [wallet !== null, 'Bakiye', wallet !== null ? `${usdt(wallet)} doğrulandı` : 'Okunamadı'],
        [riskSaved, 'Risk ayarı', riskSaved ? `Seçildi · hedef ${pct(savedRisk)} · yalnız yeni işlemlere` : 'Henüz seçilmedi'],
        [true, 'Teminat modu', `${original?.margin_modes?.find(row => row.mode === savedMode)?.label ?? 'Güvenli'} · işlem başı en çok %${original?.margin_modes?.find(row => row.mode === savedMode)?.per_trade_percent ?? 30}`],
        [guideInput.authorizationValid && guideInput.policyAcknowledged, 'Güvenlik onayları', guideInput.authorizationValid && guideInput.policyAcknowledged ? 'Tamam' : 'Başlatınca eksik olanlar verilir'],
      ] as [boolean, string, string][]).map(([ok, title, note]) => <li key={title} data-ok={ok || undefined}><i aria-hidden="true">{ok ? <Check/> : null}</i><span><b>{title}</b><small>{note}</small></span></li>)}
    </ul>
    {summary ? <div className="lcRiskGrid">
      <div><small>Bir işlemde en fazla kayıp</small><b>{usdt(summary.perTrade)}</b>{summary.perTradeCapped && <em>sunucu tavanı uygulandı</em>}</div>
      <div><small>Kötü bir günde en fazla</small><b>{usdt(summary.perDay)}</b>{summary.perDayCapped && <em>sunucu tavanı uygulandı</em>}</div>
      <div><small>Bakiyene oranı (günlük)</small><b>{pct(summary.dayShare)}</b><em>bakiye {usdt(wallet)}</em></div>
      <div><small>Oturum</small><b>{draft.hours} saat</b><em>{draft.coins} coin taranır</em></div>
    </div> : <p className="lcHint stop" role="alert">Güncel bakiye doğrulanamadı; kayıp tutarları hesaplanamıyor, başlatma kapalı.</p>}
    <div className="lcSheetNote"><ShieldCheck aria-hidden="true"/><p>Her işlem stop-loss ve hedefleriyle birlikte Binance’e yerleştirilir; korumasız pozisyon bırakılmaz. Sınıra ulaşılırsa o gün yeni işlem açılmaz.</p></div>
    {consentShorterThanSession && <p className="lcHint">Risk iznin {draft.hours} saatten önce bitiyor; başlatınca izin kendiliğinden yenilenir, oturum tam {draft.hours} saat sürer.</p>}
    {(!guideInput.authorizationValid || !guideInput.policyAcknowledged || !guideInput.armed) && <p className="lcHint">Başlatınca eksik onay{[!guideInput.authorizationValid, !guideInput.policyAcknowledged, !guideInput.armed].filter(Boolean).length > 1 ? 'lar' : ''} ({[!guideInput.authorizationValid && '24 saatlik risk izni', !guideInput.policyAcknowledged && 'risk sınırları', !guideInput.armed && 'canlı emir izni (ARM)'].filter(Boolean).join(', ')}) önce verilir.</p>}
    {riskDirty && <p className="lcHint warn">Kaydedilmemiş risk değişikliğin var; başlatma kayıtlı ayarla (%{savedRisk} / %{savedDaily}) yapılır.</p>}
    {savedRisk > 2 && savedRisk <= 5 && <label className="lcCheck"><input type="checkbox" checked={startAck} onChange={event => setStartAck(event.target.checked)}/><span>%{savedRisk} işlem riskinin yüksek olduğunu anladım.</span></label>}
    {savedRisk > 5 && <><label className="lcField">Onay için <b className="selectablePhrase">{HIGH_RISK_PHRASE}</b> yaz<input value={startPhrase} autoComplete="off" onChange={event => setStartPhrase(event.target.value)}/></label></>}
    <label className="lcCheck"><input type="checkbox" checked={startUnderstood} onChange={event => setStartUnderstood(event.target.checked)}/><span>Bu tutarları gördüm; gerçek parayla işlem açılacağını anladım.</span></label>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={() => { closeSheet(); setSettingsOpen(true); window.setTimeout(() => (document.querySelector('#lc-strategy .lcStrategyEdit') || document.getElementById('lc-strategy') || document.getElementById('lc-drawer'))?.scrollIntoView({behavior: 'smooth', block: 'start'}), 120) }} disabled={Boolean(working)}>Ayarları değiştir</button>
      <button type="button" className="lcBtn lcPrimary" disabled={!startReady || busy} onClick={() => void start()}>{working === 'start' ? 'Başlatılıyor…' : 'Otomatik işlemi başlat'}</button></div>
  </> : sheet === 'extend' ? <>
    <h2 id="lc-sheet-title">Oturumu uzat</h2>
    <div className="lcSheetNote"><ShieldCheck aria-hidden="true"/><p>{sessionLeft > 0 ? `Oturumun ${duration(sessionLeft)} sonra bitiyor. ` : ''}Seçtiğin süre <b>şu andan itibaren</b> sayılır. Açık pozisyonlar, risk ayarı ve teminat modu değişmez. Süre, canlı risk izninin bitişini aşamaz.</p></div>
    <div className="lcPresets" role="radiogroup" aria-label="Uzatma süresi">
      {[4, 12, 24].map(hours => <button type="button" role="radio" aria-checked={extendHours === hours} key={hours} className="lcPreset" data-selected={extendHours === hours || undefined} disabled={Boolean(working)} onClick={() => setExtendHours(hours)}><span className="lcPresetHead"><b>{hours} saat</b></span></button>)}
    </div>
    <label className="lcCheck"><input type="checkbox" checked={extendUnderstood} onChange={event => setExtendUnderstood(event.target.checked)}/><span>Gerçek parayla işlemin bu süre boyunca açılmaya devam edeceğini anladım.</span></label>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcPrimary" disabled={!extendUnderstood || busy} onClick={() => void extendSession()}>{working === 'extend' ? 'Uzatılıyor…' : `${extendHours} saat uzat`}</button></div>
  </> : sheet === 'stop' ? <>
    <h2 id="lc-sheet-title">Otomatik işlemi durdur</h2>
    <ul className="lcStopList">
      <li data-tone="ok"><Check aria-hidden="true"/><span><b>Yeni işlem açılmaz.</b> Tarama hemen durur.</span></li>
      <li data-tone="ok"><ShieldCheck aria-hidden="true"/><span><b>{positions.length ? `${positions.length} açık pozisyon kapatılmaz.` : 'Açık pozisyon yok.'}</b>{positions.length ? ' Stop ve hedefleri Binance’te kalır; oraya ulaşınca kendiliğinden kapanır.' : ''}</span></li>
      <li data-tone="mute"><Power aria-hidden="true"/><span>Canlı emir izni (ARM) kapanır. Yeniden başlatmak istediğinde rehber seni tek adımla geri götürür.</span></li>
    </ul>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcDanger" disabled={Boolean(working)} onClick={() => void stop()}>{working === 'stop' ? 'Durduruluyor…' : 'Yeni işlemleri durdur'}</button></div>
    {positions.length > 0 && <button type="button" className="lcLink lcCloseAll" onClick={() => setSheet('close-all')} disabled={Boolean(working)}>Pozisyonları da hemen kapatmam gerekiyor →</button>}
  </> : sheet === 'close-all' ? <>
    <h2 id="lc-sheet-title">Acil durdurma · pozisyonları kapat</h2>
    <p className="lcSheetLead">Bota ait giriş ve koruma emirleri iptal edilir, doğrulanmış bot pozisyonları <b>piyasa fiyatından reduce-only</b> emirle kapatılır. Kayıp kesinleşebilir. Sonrasında yeniden başlatmak 2FA ister.</p>
    <label className="lcField">Onay için <b className="selectablePhrase">{CLOSE_ALL_PHRASE}</b> yaz<input value={closePhrase} autoComplete="off" onChange={event => setClosePhrase(event.target.value)}/></label>
        <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={() => setSheet('stop')} disabled={Boolean(working)}>Geri</button>
      <button type="button" className="lcBtn lcDanger" disabled={closePhrase.trim().toLocaleUpperCase('tr-TR') !== CLOSE_ALL_PHRASE || Boolean(working)} onClick={() => void closeAll()}>{working === 'close-all' ? 'Gönderiliyor…' : 'Acil durdur ve kapat'}</button></div>
  </> : sheet === 'close-position' && closeTarget ? <>
    <h2 id="lc-sheet-title">{closeTarget.symbol.replace('USDT', '')} pozisyonunu kapat</h2>
    <p className="lcSheetLead">{closeTarget.direction} · giriş {closeTarget.entry}. Pozisyon <b>piyasa fiyatından, reduce-only</b> emirle hemen kapanır; ters yönde yeni pozisyon açılmaz. Kayıp kesinleşebilir. Diğer pozisyonlara dokunulmaz.</p>
    <ul className="lcStopList">
      <li data-tone={closeTarget.pnl !== null && closeTarget.pnl < 0 ? 'warn' : 'ok'}><Check aria-hidden="true"/><span><b>Şu anki sonuç: {usdt(closeTarget.pnl, true)}</b> (kesin sonuç emir fiyatına göre değişir).</span></li>
      <li data-tone="mute"><ShieldCheck aria-hidden="true"/><span>Pozisyon kapanınca bota ait Stop/TP emirleri otomatik iptal edilir; birkaç saniye sürebilir.</span></li>
    </ul>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcDanger" disabled={Boolean(working)} onClick={() => void closePosition()}>{working === 'close-position' ? 'Gönderiliyor…' : 'Evet, kapat'}</button></div>
  </> : sheet === 'fill-lock' ? <>
    <h2 id="lc-sheet-title">Dolum güvenlik kilidi{original?.fill_lock?.symbol ? ` · ${original.fill_lock.symbol}` : ''}</h2>
    <p className="lcSheetLead">Bir emir beklenenden farklı doldu, bu yüzden yeni işlemler durduruldu. Açık pozisyon değiştirilmedi.</p>
    <ul className="lcFacts">
      {(original?.fill_lock?.violations ?? []).map(item => <li key={item}>{item}</li>)}
      <li>Stop mesafesi: <b>{original?.fill_lock?.evidence?.stop_percent ?? '—'}%</b> · Risk: <b>{original?.fill_lock?.evidence?.risk_usdt ?? '—'} / {original?.fill_lock?.evidence?.risk_budget_usdt ?? '—'} USDT</b> · Teminat: <b>{original?.fill_lock?.evidence?.margin_usdt ?? '—'} USDT</b></li>
    </ul>
    <p className="lcHint warn">Kilidi kaldırmak pozisyonun riskini düzeltmez ve stratejiyi başlatmaz. Gerekirse pozisyonu Binance’ten kontrol et.</p>
    <label className="lcCheck"><input type="checkbox" checked={secureAccepted} onChange={event => setSecureAccepted(event.target.checked)}/><span>Dolum riskini inceledim ve onaylıyorum.</span></label>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcPrimary" disabled={!secureAccepted || Boolean(working)} onClick={() => void secure('fill-lock')}>{working === 'fill-lock' ? 'Kaldırılıyor…' : 'Kilidi kaldır'}</button></div>
  </> : sheet === 'emergency-clear' || sheet === 'recovery' ? <>
    <h2 id="lc-sheet-title">{sheet === 'recovery' ? 'Kurtarma kontrolü' : 'Acil durdurmayı kaldır'}</h2>
    <p className="lcSheetLead">{sheet === 'recovery'
      ? 'Binance’teki emir ve pozisyonların sistem kaydıyla eşleştiği yeniden doğrulanır. Emir gönderilmez.'
      : 'Kilit kalkar ama işlem kendiliğinden başlamaz; rehber seni onay ve başlatma adımlarına götürür. Açık pozisyonların korumaları değişmez.'}</p>
    <label className="lcCheck"><input type="checkbox" checked={secureAccepted} onChange={event => setSecureAccepted(event.target.checked)}/><span>{sheet === 'recovery' ? 'Kontrolün yapılmasını onaylıyorum.' : 'Acil durdurma kilidinin kaldırılmasını onaylıyorum.'}</span></label>
    <label className="lcField">2FA kodu<input type="password" inputMode="numeric" autoComplete="one-time-code" data-private="true" maxLength={32} value={code} onChange={event => setCode(event.target.value.trim())}/></label>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcPrimary" disabled={!secureAccepted || code.length < 6 || Boolean(working)} onClick={() => void secure(sheet)}>{working === sheet ? 'Doğrulanıyor…' : sheet === 'recovery' ? 'Kontrolü yap' : 'Kilidi kaldır'}</button></div>
  </> : null

  // ---- Durum çubuğu, tarama kartı ve akış için türetilen değerler.
  const pnlToday = live ? finite(status?.daily?.realized_pnl) : null
  const scanner = original?.scanner
  const banWaits = marketBanSeconds > 0 && !guideInput.scanningContinues
  const upcoming = running ? nextScan(now, {waitUntilMs: banWaits ? now + marketBanSeconds * 1000 : null}) : null
  const lastScanMs = Date.parse(String(scanner?.last_scan_at || ''))
  const ago = (ms: number) => Number.isFinite(ms) ? `${duration(Math.max(0, (now - ms) / 1000))} önce` : ''
  const sessionEndsAt = Date.parse(String(original?.session?.expires_at || ''))
  const sessionStartedAt = Date.parse(String(original?.session?.started_at || ''))
  const barState = stale ? {tone: 'wait', label: 'Bağlantı yok · yeniden bağlanıyor'}
    : guide.phase === 'loading' ? {tone: 'wait', label: 'Durum okunuyor'}
    : guide.phase === 'running' ? guide.tone === 'stop' ? {tone: 'stop', label: 'Çalışıyor · döngü yanıt vermiyor'} : guide.tone === 'warn' ? {tone: 'warn', label: 'Çalışıyor · girişler beklemede'} : {tone: 'ok', label: 'Çalışıyor'}
    : guide.phase === 'problem' ? {tone: guide.tone === 'stop' ? 'stop' : 'warn', label: 'Dikkat gerekiyor'}
    : guideInput.paused ? {tone: 'mute', label: 'Durdu'} : {tone: 'go', label: `Kurulum · Adım ${guide.step}/${GUIDE_STEPS.length}`}
  const [eventFilter, setEventFilter] = useState<'all' | 'trade' | 'warn' | 'system'>('all')
  const eventGroup = (kind: unknown): 'trade' | 'warn' | 'system' => {
    const match = NOTIFY_KINDS.find(item => item.pattern.test(String(kind || '')))
    return !match ? 'system' : match.id === 'entry' || match.id === 'close' ? 'trade' : 'warn'
  }
  const allEvents = status?.events ?? []
  const shownEvents = allEvents.filter(event => eventFilter === 'all' || eventGroup(event.kind) === eventFilter).slice(0, 14)
  const nearMisses = (scanner?.near_misses ?? []).slice(0, 3)
  const breakdown = rejectionBreakdown(scanner?.rejections_24h, Date.now())
  // Çalışırken bağlantı koparsa da koç görünümü kalır (adım çubuğuna geri dönülmez).
  const coach = guide.phase === 'running' || (stale && guideInput.running)
  const timeline = coach ? [
    {label: 'Başladı', value: Number.isFinite(sessionStartedAt) ? clock(original?.session?.started_at) : '—', state: 'done'},
    {label: 'Son tarama', value: Number.isFinite(lastScanMs) ? clock(scanner?.last_scan_at) : 'bekleniyor', state: 'done'},
    {label: 'Sıradaki tarama', value: upcoming ? `${new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})} · ${duration(upcoming.inSeconds)}` : '—', state: 'now'},
    {label: 'Oturum biter', value: Number.isFinite(sessionEndsAt) ? clock(original?.session?.expires_at) : '—', state: 'todo'},
  ] : []
  const settingsDrawerOpen = settingsOpen && coach
  const ringPct = (() => {
    if (!upcoming || !Number.isFinite(lastScanMs)) return 0
    const elapsed = Math.max(0, (now - lastScanMs) / 1000)
    const total = elapsed + Math.max(0, upcoming.inSeconds)
    return total > 0 ? Math.min(100, Math.max(0, Math.round(elapsed / total * 100))) : 0
  })()
  const minMargin = finite(caps?.min_margin_usdt)
  const coachChecks: {ok: boolean; text: string}[] = coach ? [
    {ok: Boolean(guideInput.connected), text: guideInput.connected ? 'Binance bağlı' : 'Binance bağlantısı yok'},
    {ok: availableBalance !== null && (minMargin === null || availableBalance >= minMargin), text: availableBalance === null ? 'Bakiye okunuyor' : minMargin !== null && availableBalance < minMargin ? `Bakiye düşük · ${usdt(availableBalance)}` : `Bakiye yeterli · ${usdt(availableBalance)}`},
    {ok: !guideInput.dailyLocked, text: guideInput.dailyLocked ? 'Günlük kayıp sınırı doldu' : dailyLoss !== null && dailyLimit !== null ? `Günlük kayıp ${usdt(dailyLoss)} / ${usdt(dailyLimit)}` : 'Günlük sınır açık'},
    {ok: sessionLeft <= 0 || sessionLeft > 3600, text: sessionLeft > 0 ? sessionLeft <= 3600 ? `Oturum ${duration(sessionLeft)} sonra bitiyor` : `Oturum ${duration(sessionLeft)} kaldı` : 'Oturum süresi okunuyor'},
  ] : []
  const coachNote = coach && running ? [why, breakdown?.headline ? `Son 24 saat: ${breakdown.headline}` : null].filter(Boolean).join(' ') : ''

  return <section className="lcRoot originalLiveWorkspace" aria-label="Canlı İşlem" data-phase={guide.phase} data-stale={stale || undefined}>
    <header className="lcStatusBar" data-tone={barState.tone}>
      <div className="lcStatusMain"><span className="lcStatusDot" aria-hidden="true"/><div><small>CANLI İŞLEM · KAIS ORIGINAL V2</small><b>{barState.label}</b></div></div>
      <dl className="lcStatusStats">
        {running && <div><dt>Oturum</dt><dd data-testid="original-session-remaining">{sessionLeft > 0 ? `${duration(sessionLeft)} kaldı` : '—'}</dd></div>}
        {running && <div><dt>Son tarama</dt><dd>{Number.isFinite(lastScanMs) ? `${clock(scanner?.last_scan_at)} · ${scanner?.scanned_symbol_count ?? 0} coin` : 'bekleniyor'}</dd></div>}
        {upcoming && <div><dt>Sonraki tarama</dt><dd data-testid="live-next-scan">{new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})} · {duration(upcoming.inSeconds)}</dd></div>}
        <div><dt>Bakiye</dt><dd>{verified && wallet !== null ? usdt(wallet) : '—'}</dd></div>
        <div><dt>Bugün</dt><dd data-sign={pnlToday === null || pnlToday === 0 ? undefined : pnlToday < 0 ? 'down' : 'up'}>{pnlToday !== null ? usdt(pnlToday, true) : '—'}</dd></div>
      </dl>
      <div className="lcStatusLinks">
        <span className="lcUpdated"><small>Son güncelleme</small><b>{lastGood.current ? new Date(lastGood.current.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '—'}</b></span>
        <button type="button" className="lcRefresh" aria-label="Yenile" title="Yenile" onClick={() => { void Promise.resolve(refresh()).catch(() => undefined) }}><RefreshCw aria-hidden="true"/></button>
        {live && original?.margin_modes && <span className="lcModeChip" data-mode={savedMode} title="Teminat modu: her işleme ayrılabilecek en çok teminat">Teminat · <b>{original.margin_modes.find(row => row.mode === savedMode)?.label ?? 'Güvenli'} %{original.margin_modes.find(row => row.mode === savedMode)?.per_trade_percent ?? 30}</b></span>}
        <a className="lcPill lcPillLink" href="/profile#notify-settings" title="Bildirim ayarları"><Bell aria-hidden="true"/>{notify ? 'Bildirimler açık' : 'Bildirimleri aç'}</a>
        <a className="lcPill lcPillLink" href="/master-trade?tab=baglanti" onClick={event => { event.preventDefault(); goTab('baglanti') }}>Bağlantı →</a>
        {running && <button type="button" className="lcBtn lcDanger lcBarStop" disabled={working === 'stop'} onClick={() => act('stop')}><Power aria-hidden="true"/>Durdur</button>}
      </div>
    </header>
    <div className="lcSummary" aria-label="Hesap özeti">
      <article><i aria-hidden="true"><Wallet/></i><span><small>Toplam bakiye</small><b>{verified && wallet !== null ? usdt(wallet) : '—'}</b></span></article>
      <article><i aria-hidden="true"><TrendingUp/></i><span><small>Bugünkü kâr/zarar</small><b data-sign={pnlToday === null || pnlToday === 0 ? undefined : pnlToday < 0 ? 'down' : 'up'}>{pnlToday !== null ? usdt(pnlToday, true) : '—'}</b></span></article>
      <article><i aria-hidden="true"><Coins/></i><span><small>Kullanılabilir bakiye</small><b>{verified && availableBalance !== null ? usdt(availableBalance) : '—'}</b></span></article>
      <article><i aria-hidden="true"><Layers/></i><span><small>Açık pozisyon</small><b>{live ? `${positions.length}${finite(policy.max_positions) ? ` / ${policy.max_positions}` : ''}` : '—'}</b></span></article>
    </div>
    {stale && <p className="lcStaleNote" role="status">Bağlantı kesildi; aşağıdaki bilgiler {duration(staleAge)} önce doğrulandı. Yeniden bağlanınca kendiliğinden güncellenir.</p>}

    <div className="lcGrid" data-coach={coach || undefined} data-noscan={!(running || scanner?.last_scan_at) || undefined}>
      <div className="lcMain">
        <section className="lcGuide" data-tone={guide.tone} data-phase={guide.phase} data-testid="live-guide" aria-live="polite" key={advanced}>
          <header className="lcGuideHead">
            <span className="lcEye" aria-hidden="true" data-tone={guide.tone}/>
            <div><b>{coach ? 'KAİS KOÇ' : 'KAİS REHBER'}</b><small>{stale ? 'Bağlantı bekleniyor · ilerlemen korunuyor' : coach ? 'Kurulum tamam · seni bilgilendiriyorum' : guide.phase === 'loading' ? 'Hazırlanıyor' : guide.phase === 'problem' ? 'Önce bunu çözelim · ilerlemen korunuyor' : `Adım ${guide.step} / ${GUIDE_STEPS.length} · ${GUIDE_STEPS[guide.step - 1]}`}</small></div>
            <button type="button" className="lcAsk" onClick={() => askKais(kaisQuestion)}><MessageCircle aria-hidden="true"/>Kais’e sor</button>
          </header>
          {!coach && <ol className="lcSteps" aria-label="Kurulum adımları">{GUIDE_STEPS.map((label, index) => {
            const state = index < progressDone ? 'done' : index === currentIndex && guide.phase !== 'loading' ? guide.phase === 'problem' ? 'problem' : 'current' : 'todo'
            return <li key={label} data-state={state} aria-current={state === 'current' ? 'step' : undefined}><i/><span>{label}</span></li>
          })}</ol>}
          {guide.done && <p className="lcDone"><span aria-hidden="true"><Check/></span>{guide.done}</p>}
          <h2 className="lcGuideTitle">{guide.title}</h2>
          <p className="lcGuideMsg" data-open={msgOpen || undefined}>{guide.message}</p>
          {String(guide.message || '').length > 90 && <button type="button" className="lcMsgToggle" aria-expanded={msgOpen} onClick={() => setMsgOpen(value => !value)}>{msgOpen ? 'Daha az' : 'Devamını göster'}</button>}
          {help && <p className="lcHelp"><b>Ne yapmalıyım?</b> {help.text}{help.href && <> <a href={help.href}>{help.linkLabel} →</a></>}</p>}
          {coach && <ol className="lcTimeline" aria-label="Oturum zaman çizelgesi">{timeline.map(item => <li key={item.label} data-state={item.state}><i aria-hidden="true"/><small>{item.label}</small><b>{item.value}</b></li>)}</ol>}
          {coach && !stale && <p className="lcCoachLine" data-testid="original-entry-status">{guideInput.entryBlocker ? `Yeni giriş beklemede: ${guideInput.entryBlocker}` : 'Yeni giriş açık · kurallara uyan fırsat çıkınca stop ve hedefleriyle emir açılır.'}</p>}
          {coach && !stale && <div className="lcCoachDeck" data-testid="coach-deck" style={{'--p': `${ringPct}%`} as CSSProperties}>
            <div className="lcCoachRing" role="img" aria-label={upcoming ? `Sonraki taramaya ${duration(upcoming.inSeconds)} kaldı` : 'Sonraki tarama yok'} >
              <div><b>{upcoming ? duration(upcoming.inSeconds) : '—'}</b><small>sonraki tarama</small></div></div>
            <div className="lcCoachTimes" aria-label="Tarama saatleri">
              <span><small>Son tarama</small><b>{Number.isFinite(lastScanMs) ? clock(scanner?.last_scan_at) : '—'}</b></span>
              <span><small>Sonraki tarama</small><b>{upcoming ? new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : '—'}</b></span>
              <i className="lcCoachBar" aria-hidden="true"/>
            </div>
            <div className="lcCoachBody">
              <small className="lcCoachCap">BAŞLAMADAN ÖNCE KONTROL</small>
              <ul className="lcCoachChecks">{coachChecks.map(item => <li key={item.text} data-ok={item.ok || undefined}><i aria-hidden="true">{item.ok ? '✓' : '!'}</i>{item.text}</li>)}</ul>
              {coachNote && <p className="lcCoachNote"><small>KOÇ YORUMU</small>{coachNote}</p>}
            </div>
          </div>}
          <div className="lcGuideActions">
            {guide.action && guide.actionLabel && guide.action !== 'stop' && (guide.action === 'open-connection'
              ? <a className="lcBtn lcPrimary" href="/master-trade?tab=baglanti" data-testid="live-guide-action" onClick={event => { event.preventDefault(); goTab('baglanti') }}>{guide.actionLabel}</a>
              : <button type="button" className="lcBtn lcPrimary" data-testid="live-guide-action" disabled={busy && guide.action !== 'retry'} onClick={() => act()}>{actionIcon}{guide.action === 'retry' && working === 'retry' ? 'Okunuyor…' : guide.actionLabel}</button>)}
            {guide.why && <button type="button" className="lcBtn lcGhost" aria-expanded={whyOpen} onClick={() => setWhyOpen(value => !value)}>Neden gerekli?</button>}
            {coach && running && !stale && <button type="button" className="lcBtn lcPrimary lcExtend" data-urgent={(sessionLeft > 0 && sessionLeft <= 3600) || undefined} disabled={busy} onClick={() => { setExtendUnderstood(false); setSheetError(''); setSheet('extend') }}>Oturumu uzat</button>}
            {coach && <button type="button" className="lcBtn lcGhost" onClick={() => { setSettingsOpen(true); window.setTimeout(() => document.getElementById('lc-drawer')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 30) }}>Risk ayarını gör</button>}
          </div>
          {whyOpen && guide.why && <p className="lcWhy">{guide.why}</p>}
          {retryIn !== null && <p className="lcRetry" role="status"><RefreshCw aria-hidden="true" className="lcSpin"/>{retryIn > 0 ? `${retryIn} sn sonra kendiliğinden tekrar denenecek` : 'Tekrar deneniyor…'}{retry.attempt > 0 ? ` · ${retry.attempt + 1}. deneme` : ''}</p>}
        </section>

        {alerts.length > 0 && <div className="lcAlerts">{alerts.map((item, index) => <p key={index} className="lcAlert" data-tone={item.tone}>{item.tone === 'ok' ? <ShieldCheck aria-hidden="true"/> : <TriangleAlert aria-hidden="true"/>}{item.text}</p>)}</div>}

        {(running || scanner?.last_scan_at) && <section className="lcCard lcScan" aria-label="Canlı tarama">
          <header className="lcCardHead"><div><small>CANLI TARAMA</small><h3>{scanStage[scanner?.stage || ''] || (running ? 'İlk tarama bekleniyor' : 'Strateji durdu')}</h3></div>
            <button type="button" className="lcAsk" onClick={() => askKais('Canlı stratejim son taramada neden işlem açmadı? Kısaca açıkla.')}><MessageCircle aria-hidden="true"/>Kais’e sor</button></header>
          <div className="lcScanStats">
            <span><small>Son tarama</small><b>{Number.isFinite(lastScanMs) ? clock(scanner?.last_scan_at) : '—'}</b><em>{Number.isFinite(lastScanMs) ? ago(lastScanMs) : 'henüz yok'}</em></span>
            <span data-accent><small>Sonraki tarama</small><b>{upcoming ? new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : '—'}</b><em>{upcoming ? banWaits ? 'Binance engeli bitince' : `${duration(upcoming.inSeconds)} sonra` : 'strateji çalışmıyor'}</em></span>
            <span><small>Taranan</small><b>{scanner?.scanned_symbol_count ?? 0} coin</b><em>{scanner?.candidate_count ? `${scanner.candidate_count} aday` : 'aday yok'}</em></span>
            <span><small>Son 24 saat</small><b>{scanner?.last_24h?.scans ?? 0} tarama</b><em>{scanner?.last_24h?.candidates ?? 0} aday · {scanner?.last_24h?.trades ?? 0} işlem</em></span>
          </div>
          {why && <div className="lcWhyNo"><small>NEDEN İŞLEM AÇILMADI?</small><p>{why}</p></div>}
          {nearMisses.length > 0 && <ul className="lcNear" aria-label="Sinyale en yakın coinler">{nearMisses.map(item => {
            const conf = finite(item.confidence), need = finite(item.confidence_threshold)
            return <li key={item.symbol}><b>{item.symbol.replace('USDT', '')}</b><span>{NEAR_TEXT[item.reason_code] ?? item.reason ?? 'kurallara uymadı'}</span>
              {conf !== null && need !== null && <i className="lcConf" title={`Güven %${conf}, gereken %${need}`}><em style={{width: `${Math.min(100, Math.max(0, conf))}%`}} data-pass={conf >= need || undefined}/><u style={{left: `${Math.min(100, Math.max(0, need))}%`}}/></i>}
              <small>{conf !== null ? `güven %${conf}${need !== null ? ` / %${need}` : ''}` : ''}</small></li>
          })}</ul>}
          {breakdown && <details className="lcRej"><summary><span><small>SON 24 SAAT · NEREDE ELENDİ?</small><b>{breakdown.headline}</b></span><ChevronDown aria-hidden="true"/></summary>
            <div className="lcRejBody">
              <div><h4>Elenme sebebi</h4><ul>{breakdown.reasons.map(row => <li key={row.key}><span>{row.label}</span><i><em style={{width: `${row.bar}%`}}/></i><b>{row.percent < 1 ? '<%1' : `%${row.percent}`}</b></li>)}</ul></div>
              {breakdown.gates.length > 0 && <div><h4>Hangi sinyal kapısı tutmadı</h4><ul>{breakdown.gates.map(row => <li key={row.key}><span>{row.label}</span><i><em style={{width: `${row.bar}%`}}/></i><b>{row.count}</b></li>)}</ul></div>}
              <p>Sayılar coin × tarama olarak toplanır; bir coin birden fazla kapıda takılabilir. Bu bilgi yalnızca açıklama içindir, kuralları değiştirmez.{breakdown.thin ? ' Henüz az tarama var; döküm biriktikçe netleşir.' : ''}</p>
            </div></details>}
        </section>}

        <section className="lcCard" aria-label="Açık pozisyonlar">
          <header className="lcCardHead"><div><small>POZİSYONLAR</small><h3>Açık pozisyonlar{live ? ` · ${positions.length}${finite(policy.max_positions) ? ` / ${policy.max_positions}` : ''}` : ''}</h3></div><small>{live ? stale ? `${duration(staleAge)} önce` : 'Binance verisi' : 'Doğrulama bekleniyor'}</small></header>
          {positions.length === 0 ? <p className="lcEmpty">{live ? running ? 'Şu an açık pozisyon yok. Uygun fırsat çıkınca stop ve hedefleriyle burada görünür.' : 'Açık pozisyon yok.' : 'Güncel pozisyonlar doğrulanamadı.'}</p>
            : <div className="lcPositions">{positions.map((position, index) => {
              const plan = findPlan(position)
              const external = !plan || plan.provenance_state === 'READ_ONLY_EXTERNAL' || plan.source === 'READ_ONLY_EXTERNAL'
              const protection = !external && plan?.provenance_state === 'CONFIRMED' ? plan.exchange_protection : undefined
              const note = external ? null : positionNote(position, plan as Parameters<typeof positionNote>[1], now)
              const pnl = finite(position.unrealized_pnl)
              // Yalnız sahipliği doğrulanmış bot pozisyonu kapatılabilir (sunucu da aynısını ister); dış pozisyona dokunulmaz.
              const planId = plan && typeof plan.id === 'string' ? plan.id : ''
              const closable = !external && plan?.provenance_state === 'CONFIRMED' && planId.length >= 6 && live
              const flag = (ok: boolean | undefined) => <i data-ok={ok === true || undefined}>{ok === true ? 'Binance’te ✓' : 'doğrulanamadı'}</i>
              return <article className="lcPosition" key={`${String(position.symbol)}-${index}`}>
                <header><b>{String(position.symbol || '—').replace('USDT', '')}<span data-dir={String(position.direction || '').toUpperCase()}>{String(position.direction || '—')}</span></b>
                  <strong data-sign={pnl === null ? undefined : pnl < 0 ? 'down' : 'up'}>{usdt(pnl, true)}</strong></header>
                <div className="lcLevels">
                  <span><small>Giriş</small><b>{String(position.entry_price ?? '—')}</b></span>
                  <span><small>Fiyat</small><b>{String(position.mark_price ?? '—')}</b></span>
                  <span><small>Stop</small><b>{String(plan?.stop_loss ?? '—')}</b>{!external && flag(protection?.stop)}</span>
                  <span><small>TP1 (%{tp1Percent(plan)})</small><b>{String(plan?.targets?.[0] ?? '—')}</b>{!external && flag(protection?.tp1)}</span>
                  <span><small>TP3</small><b>{String(plan?.targets?.[2] ?? '—')}</b>{!external && flag(protection?.tp3)}</span>
                </div>
                {note && <p className="lcNote" data-tone={note.tone}>{note.text}</p>}
                {closable && <button type="button" className="lcPosClose" disabled={busy}
                  onClick={() => askClosePosition({planId, symbol: String(position.symbol || ''), direction: String(position.direction || ''), entry: String(position.entry_price ?? '—'), pnl})}>
                  <X aria-hidden="true"/>Pozisyonu kapat</button>}
                {external && <p className="lcNote" data-tone="mute">Dış pozisyon · bot tarafından yönetilmiyor.</p>}
                {!external && plan?.provenance_state !== 'CONFIRMED' && <p className="lcNote" data-tone="warn">Plan sahipliği doğrulanamadı.</p>}
              </article>
            })}</div>}
        </section>
        {original && !coach && strategyCard}
      </div>

      <aside className="lcSide">
        <section className="lcCard lcToday" aria-label="Gün özeti">
          <header className="lcCardHead"><div><small>GÜN ÖZETİ</small><h3>Bugün</h3></div></header>
          <p>{today ? today.text : 'Hesap doğrulanınca bugünün özeti burada görünür.'}</p>
          <div className="lcDayLimit"><span><small>Günlük kayıp sınırı</small><b>{dailyLoss !== null && dailyLimit !== null ? `${usdt(dailyLoss)} / ${usdt(dailyLimit)}` : '—'}</b></span>
            {dailyLoss !== null && dailyLimit !== null && <div className="lcBar" role="progressbar" aria-label="Günlük kayıp sınırı kullanımı" aria-valuemin={0} aria-valuemax={dailyLimit} aria-valuenow={Math.min(dailyLimit, Math.max(0, dailyLoss))} data-high={dailyLoss / dailyLimit >= 0.8 || undefined}><i style={{width: `${Math.min(100, Math.max(0, dailyLoss / dailyLimit * 100))}%`}}/></div>}</div>
        </section>
        <section className="lcCard lcEvents" aria-label="Son olaylar">
          <header className="lcCardHead"><div><small>AKIŞ</small><h3>Son olaylar</h3></div></header>
          <div className="lcChips" role="tablist" aria-label="Olay filtresi">{([['all', 'Tümü'], ['trade', 'İşlemler'], ['warn', 'Uyarılar'], ['system', 'Sistem']] as const).map(([id, label]) =>
            <button type="button" role="tab" key={id} aria-selected={eventFilter === id} data-on={eventFilter === id || undefined} onClick={() => setEventFilter(id)}>{label}</button>)}</div>
          {!verified && allEvents.length > 0 && <p className="lcHint">Son görülen kayıtlar; güncel durum doğrulanamadı.</p>}
          {shownEvents.length === 0 ? <p className="lcEmpty">{allEvents.length ? 'Bu filtrede olay yok.' : 'Henüz olay yok.'}</p> : <ul>{shownEvents.map((event, index) => <li key={`${event.created_at}-${index}`} data-tone={eventTone(event.kind)}>
            <time>{clock(event.created_at)}</time><span>{event.message ?? String(event.kind || '').replaceAll('_', ' ')}</span></li>)}</ul>}
        </section>
      </aside>
    </div>

    <details className="lcDrawer" id="lc-drawer" open={settingsDrawerOpen || undefined} onToggle={event => { const open = (event.currentTarget as HTMLDetailsElement).open; if (coach) setSettingsOpen(open) }}>
      <summary><span className="lcDrawerIcon"><SlidersHorizontal aria-hidden="true"/></span><span className="lcDrawerTitle"><b>Ayarlar ve gelişmiş</b><small>Risk ayarı, manuel emir, kurtarma ve acil durdurma tek yerde.</small></span><span className="lcDrawerChip" data-tone={guideInput.emergency ? 'stop' : 'ok'}><i aria-hidden="true"/>{guideInput.emergency ? 'Acil durdurma kilidi var' : 'Her şey yolunda'}</span><ChevronDown aria-hidden="true"/></summary>
      <div className="lcDrawerBody">
        {original && coach && strategyCard}
        {extras}
      </div>
    </details>

    {flash && <div className="lcToast" data-tone={flash.tone} role="status"><i aria-hidden="true"/><span>{flash.text}</span><button type="button" aria-label="Kapat" onClick={() => setFlash(null)}><X aria-hidden="true"/></button></div>}
    {sheetView && <div className="lcBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) closeSheet() }}>
      <section className="lcSheet" role="dialog" aria-modal="true" aria-labelledby="lc-sheet-title" onKeyDown={event => { if (event.key === 'Escape') closeSheet() }}>
        <button type="button" className="lcSheetClose" aria-label="Kapat" onClick={closeSheet} disabled={Boolean(working)}><X aria-hidden="true"/></button>
        {sheetView}
        {sheetError && sheet !== 'approvals' && <p className="lcHint stop" role="alert" data-testid="live-sheet-error">İşlem tamamlanamadı: {sheetError}</p>}
      </section>
    </div>}
  </section>
}

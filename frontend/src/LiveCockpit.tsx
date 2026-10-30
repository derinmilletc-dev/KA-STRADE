import {useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode} from 'react'
import './copy-phrase.css'
import {Bell, Check, ChevronDown, ChevronRight, Clock, Coins, FileCheck, Layers, MessageCircle, Power, RefreshCw, ShieldCheck, SlidersHorizontal, TrendingUp, TriangleAlert, Wallet, X, Zap} from 'lucide-react'
import type {SharedLiveStatus} from './LiveTradingPanel'
import {
  GUIDE_STEPS, RISK_PRESETS, shadowNote, dailySummary, duration, liveGuide, matchPreset, positionNote, retryDelay, riskSummary,
  smartAlerts, whyNoTrade, rejectionBreakdown, statusHelp, nextScan, reconnectingGuide, capacityPreview, marginModePreview, marginSimulation, openMarginTotal, clampCustomPercent, CUSTOM_MARGIN_MIN_PERCENT, type GuideInput, type GuideNotice,
} from './live-guide'
import {askKais} from '../../kais-ask'
import {MANUAL_CLOSE_EVENT, MANUAL_INTENT_EVENT, takeIntentForOpen} from '../../trade-intent'
import SpotHint from '../../SpotHint'
import {NOTIFY_KINDS, browserNotificationState, loadNotifyPrefs} from '../../notification-prefs'
import '../../live-cockpit.css'
import '../../live-cockpit-mobile.css'
import '../../live-cockpit-coach-mobile.css'

const LAUNCH_TARGETS = ['[data-testid="launch-open-mobile"]', '[data-testid="launch-open"]']

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
  /** Demo ikizi: aynı kokpit, Binance Futures Demo motoruna bağlı; gerçek para yok. */
  demo?: boolean
  /** Demo'da sekme değişimi URL yerine üst bileşene bildirilir. */
  onNavigate?: (tab: string) => void
  /** Demo'da "Manuel işlem aç" demo emir kartını açar. */
  onOpenManual?: () => void
}

// Sunucunun beklediği sabit onay ifadesi; kullanıcı artık yazmıyor, kutuyu işaretliyor.
const FILL_LOCK_PHRASE = 'DOLUM RİSKİNİ İNCELEDİM'
type Sheet = null | 'approvals' | 'start' | 'stop' | 'close-all' | 'close-position' | 'edit-protection' | 'reduce-position' | 'fill-lock' | 'emergency-clear' | 'recovery' | 'extend' | 'why' | 'wizard' | 'summary' | 'launch'
type CloseTarget = {planId: string; symbol: string; direction: string; entry: string; pnl: number | null}
// Manuel pozisyon: stop/hedef değiştirme ve kısmi kapatma hedefi (yalnız MANUEL planlar).
type ManualTarget = CloseTarget & {stop: string; targets: [string, string, string]; mark: string}
type ItemState = 'done' | 'todo' | 'working' | 'error'

const HIGH_RISK_PHRASE = 'YÜKSEK RİSKİ KABUL EDİYORUM'
// Risk paketi adımı (Kais Koç sihirbazı): "Kendim ayarlarım" kaydırıcısının durakları. Sınırlar sunucuyla aynı (%0,5–15).
const RISK_STEPS = [0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 15]
const RISK_TICKS = [0.5, 2, 5, 10, 15]
const PRESET_APPROVE: Record<string, string> = {temkinli: "Temkinli'yi onayla ✓", dengeli: "Dengeli'yi onayla ✓", agresif: "Agresif'i onayla ✓", maksimum: "Maksimum'u onayla ✓"}
const riskToneOf = (risk: number) => risk <= 1 ? 'low' : risk <= 2 ? 'mid' : risk <= 5 ? 'warn' : 'hot'
/** Üst üste 5 kayıpta bakiyenin eriyen payı (0–1). Yalnız gösterim. */
const streakLoss = (riskPercent: number) => 1 - (1 - riskPercent / 100) ** 5
const money = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: 2})
const AGGRESSIVE_PHRASE = 'AGRESİF MODU KABUL EDİYORUM'
const MODE_TEXT: Record<string, string> = {guvenli: 'Daha az teminat, daha küçük işlem. Bugünkü ayar.', orta: 'Daha büyük işlem; kayıp ve kazanç büyür.', agresif: 'En büyük işlem; kayıp ve kazanç en yüksek.'}
const CLOSE_ALL_PHRASE = 'CANLI ACİL DURDUR'
// TP1'de kapanan oran planın kendisinden okunur; alanı olmayan eski planlar %60 gösterilir.
const tp1Percent = (plan: Record<string, unknown> | undefined) => {
  const value = Number(plan?.tp1_fraction)
  return Number.isFinite(value) && value > 0 && value < 1 ? Math.round(value * 100) : 60
}
// Manuel TP2 payı (yoksa %20).
const tp2Percent = (plan: Record<string, unknown> | undefined) => {
  const value = Number(plan?.tp2_fraction)
  return Number.isFinite(value) && value > 0 && value < 1 ? Math.round(value * 100) : 20
}
// Sunucu bu cümleyi ister; tek pozisyonu kapatırken kullanıcıya yazdırmayız, onay penceresindeki "Evet, kapat" gönderir.
const CLOSE_POSITION_PHRASE = 'CANLI POZİSYONU KAPAT'
const PROTECTION_PHRASE = 'KORUMAYI GÜNCELLE'
const REDUCE_PHRASE = 'KISMİ KAPAT'
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
const goTabUrl = (tab: string) => {
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
const ALL_LAUNCH_STEPS = [
  {id: 'conn', title: 'Binance bağlantısı', op: ''},
  {id: 'risk', title: 'Risk paketi', op: 'risk'},
  {id: 'daily', title: 'Günlük sınır', op: 'risk'},
  {id: 'mode', title: 'Teminat modu', op: 'mode'},
  {id: 'smart', title: 'Akıllı koruma', op: 'smart'},
  {id: 'consent', title: '24 saatlik risk izni', op: 'consent'},
  {id: 'policy', title: 'Risk limitleri onayı', op: 'policy'},
  {id: 'arm', title: 'Canlı emir izni (ARM)', op: 'arm'},
] as const
type LaunchStepId = typeof ALL_LAUNCH_STEPS[number]['id']
// Demo motorunda teminat modu, akıllı koruma, 24 saatlik izin ve risk onayı yok; emir izni (ARM) başlatırken sunucuda açılır.
const DEMO_STEP_IDS: readonly LaunchStepId[] = ['conn', 'risk', 'daily', 'arm']
const DEMO_TEXT: ReadonlyArray<readonly [RegExp, string]> = [
  [/gerçek parayla/g, 'demo parayla'], [/Gerçek parayla/g, 'Demo parayla'], [/Canlı emir izni \(ARM\)/g, 'Demo emir izni'],
  [/Canlıya hazırlık/g, 'Demo\'ya hazırlık'], [/CANLI İŞLEM/g, 'DEMO İŞLEM'], [/CANLI TARAMA/g, 'DEMO TARAMA'], [/Canlı İşlem/g, 'Demo İşlem'],
  [/Canlı tarama/g, 'Demo tarama'], [/Canlı stratejim/g, 'Demo stratejim'], [/Canlı işlem/g, 'Demo işlem'],
]
const demoText = (text: string, demo: boolean | undefined) => demo ? DEMO_TEXT.reduce((out, [from, to]) => out.replace(from, to), text) : text
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
  const demo = props.demo === true
  const goTab = (tab: string) => { if (props.onNavigate) props.onNavigate(tab); else goTabUrl(tab) }
  const LAUNCH_STEPS = demo ? ALL_LAUNCH_STEPS.filter(step => DEMO_STEP_IDS.includes(step.id)) : ALL_LAUNCH_STEPS
  const dt = (text: string) => demoText(text, demo)
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
  const setupPhase = !stale && !guideInput.running && ['choose-risk', 'approve', 'start'].includes(guide.action ?? '')
  const help = guide.phase === 'problem' && guide.action === 'retry' && !staleSnapshot ? statusHelp(props.guideInput.statusError) : null
  const [sheet, setSheet] = useState<Sheet>(null)
  const [smartArming, setSmartArming] = useState(false)
  const [smartUnderstood, setSmartUnderstood] = useState(false)
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
  const [wizCustom, setWizCustom] = useState(false)
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
  const draftValid = Number.isFinite(draft.risk) && draft.risk >= 0.5 && draft.risk <= 15 && Number.isFinite(draft.daily)
    && draft.daily >= 1 && draft.daily <= 25 && draft.daily >= draft.risk && validCoins(draft.coins) && validHours(draft.hours)
  const tierReady = (risk: number, ack: boolean, typed: string) => risk > 5 ? typed === HIGH_RISK_PHRASE : risk > 2 ? ack : true
  const activePreset = matchPreset({risk_percent: draft.risk, daily_loss_percent: draft.daily, symbol_count: draft.coins, session_hours: draft.hours})
  const caps = original?.risk_limits
  const choosePreset = (id: string) => {
    const preset = RISK_PRESETS.find(item => item.id === id)
    if (!preset) return
    setCustom(false); setWizCustom(false)
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

  // ---- Tek sayfa "Hepsini hazırla ve başlat": her adım tek tek tik alır; onay yazısıyla çalışır; hata olursa o satırda durur.
  type LaunchOp = {state: 'working' | 'done' | 'error'; error?: string; sig?: string; verified?: boolean}
  const [launchApprovals, setLaunchApprovals] = useState<Record<string, string>>({})
  const [launchOps, setLaunchOps] = useState<Record<string, LaunchOp>>({})
  const [launchPhrase, setLaunchPhrase] = useState('')
  const [dailyText, setDailyText] = useState<string | null>(null)
  const openLaunch = () => {
    setLaunchApprovals({}); setLaunchOps({}); setLaunchPhrase(''); setDailyText(null); setSheetError(''); setRiskError(''); setModeError('')
    setCustom(false); setCustomOn(false); setUnderstood(false); setRiskAck(false); setPhrase(''); setModePick(null)
    setModeUnderstood(false); setModeAck(false); setModePhrase(''); setSmartPick('keep'); setSmartWizAck(false); setWizCustom(false)
    setSheet('launch')
  }
  const openWizard = () => {
    setWizStep(0); setSmartPick('keep'); setSmartWizAck(false); setWizResumed(false); setSheetError(''); setRiskError(''); setModeError(''); setCustom(false); setCustomOn(false)
    setUnderstood(false); setRiskAck(false); setPhrase(''); setModePick(null); setModeUnderstood(false); setModeAck(false); setModePhrase('')
    try {
      const saved = wizKey ? JSON.parse(localStorage.getItem(wizKey) ?? 'null') : null
      if (saved && typeof saved === 'object' && Date.now() - Number(saved.at) < 7 * 86400000 && Number.isFinite(saved.risk) && Number.isFinite(saved.daily)
        && saved.risk >= 0.5 && saved.risk <= 15 && saved.daily >= 1 && saved.daily <= 25 && saved.daily >= saved.risk && Number.isInteger(saved.step) && saved.step >= 0 && saved.step <= 4
        && (saved.risk !== savedRisk || saved.daily !== savedDaily || saved.step > 0 || saved.smartPick === 'on')) {
        setDraft(current => ({...current, risk: saved.risk, daily: saved.daily, coins: validCoins(saved.coins) ? saved.coins : current.coins, hours: validHours(saved.hours) ? saved.hours : current.hours}))
        setWizStep(saved.step)
        setSmartPick(saved.smartPick === 'on' || saved.smartPick === 'off' ? saved.smartPick : 'keep')
        if ([60, 65, 70, 75, 80].includes(saved.thr)) setSmartWizThreshold(saved.thr)
        if ([5, 10, 15, 20, 30].includes(saved.wait)) setSmartWizWait(saved.wait)
        setWizResumed(true)
      }
    } catch { /* bozuk kayıt: baştan başla */ }
    setSheet('wizard')
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
  const [wizStep, setWizStep] = useState(0)
  const [moreOpen, setMoreOpen] = useState(false)
  const [eventsOpen, setEventsOpen] = useState(false)
  const [smartPick, setSmartPick] = useState<'keep' | 'on' | 'off'>('keep')
  const [smartWizThreshold, setSmartWizThreshold] = useState(70)
  const [smartWizWait, setSmartWizWait] = useState(10)
  const [smartWizAck, setSmartWizAck] = useState(false)
  const [wizResumed, setWizResumed] = useState(false)
  const wizKey = userId ? `protrebot-wizard-draft:${userId}` : null
  useEffect(() => {  // yarım kalan rehber seçimleri bu tarayıcıda saklanır (onay kutuları ASLA saklanmaz)
    if (sheet !== 'wizard' || !wizKey) return
    try { localStorage.setItem(wizKey, JSON.stringify({step: wizStep, risk: draft.risk, daily: draft.daily, coins: draft.coins, hours: draft.hours, smartPick, thr: smartWizThreshold, wait: smartWizWait, at: Date.now()})) } catch { /* saklama yoksa sorun değil */ }
  }, [sheet, wizKey, wizStep, draft.risk, draft.daily, draft.coins, draft.hours, smartPick, smartWizThreshold, smartWizWait])
  const clearWizDraft = () => { try { if (wizKey) localStorage.removeItem(wizKey) } catch { /* yok say */ } }
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

  // Her adımın üç ayrı durumu vardır: Onay (senin kararın), Doğrulama (sistem kontrolü), İşlem (sistemin yaptığı).
  // İşlem kaydı girdi imzasıyla tutulur: aynı girdiyle başarıyla bitmiş işlem tekrar çalıştırılmaz.
  const launchSetOp = (key: string, op: LaunchOp) => setLaunchOps(current => ({...current, [key]: op}))
  const launchNeedsRisk = riskDirty || !riskSaved
  const runLaunch = () => perform('launch', async () => {
    if (wallet === null) throw new Error('Güncel Binance bakiyesi doğrulanamadı; hiçbir adım çalıştırılmadı.')
    setSheetError('')
    let revision = original?.risk_revision ?? 0
    let renewed = false
    let armedNow = guideInput.armed
    const op = async (key: string, sig: string, work: () => Promise<boolean>) => {
      const prior = launchOps[key]
      if (prior?.state === 'done' && prior.sig === sig) return
      launchSetOp(key, {state: 'working', sig})
      try {
        const verified = await work()
        launchSetOp(key, {state: 'done', sig, verified})
      } catch (error) {
        const message = errorText(error, 'İşlem tamamlanamadı.')
        launchSetOp(key, {state: 'error', sig, error: message})
        throw new Error(message)
      }
    }
    await op('risk', launchOpSig.risk, async () => {
      if (!launchNeedsRisk) return true
      const payload = await request('/original-v2/risk-settings', {
        risk_percent: draft.risk, daily_loss_percent: draft.daily, risk_acknowledged: draft.risk > 2 && riskAck,
        high_risk_confirmation: draft.risk > 5 ? phrase : '', warning_balance_usdt: wallet,
        expected_risk_revision: revision, understood: true,
      }) as {risk_percent?: unknown; daily_loss_percent?: unknown; risk_revision?: unknown} | null
      if (!payload || payload.risk_percent !== draft.risk || payload.daily_loss_percent !== draft.daily || !Number.isSafeInteger(payload.risk_revision))
        throw new Error('Risk ayarının kaydı sunucuda doğrulanamadı.')
      revision = payload.risk_revision as number
      const signature = `${draft.risk}|${draft.daily}`
      setRiskConfirmed(signature)
      try { if (confirmKey) localStorage.setItem(confirmKey, signature) } catch { /* kayıt kapalıysa sorun değil */ }
      return true
    })
    await op('mode', launchOpSig.mode, async () => {
      if (chosenMode === savedMode) return true
      const payload = await request('/original-v2/margin-mode', {
        mode: chosenMode, acknowledged: chosenMode !== 'guvenli' && modeAck,
        confirmation: chosenMode === 'agresif' ? modePhrase.trim() : '', understood: true,
      }) as {margin_mode?: unknown} | null
      if (!payload || payload.margin_mode !== chosenMode) throw new Error('Teminat modunun kaydı sunucuda doğrulanamadı.')
      return true
    })
    await op('smart', launchOpSig.smart, async () => {
      if (!smartChangeWanted) return true
      const payload = await request('/original-v2/smart-profit', smartPick === 'on'
        ? {threshold: smartWizThreshold, wait_minutes: smartWizWait, enabled: true, understood: true} : {enabled: false}) as {settings?: {enabled?: unknown}} | null
      if (payload?.settings?.enabled !== (smartPick === 'on')) throw new Error('Akıllı koruma ayarının kaydı sunucuda doğrulanamadı.')
      return true
    })
    const renewConsent = !guideInput.authorizationValid || consentLeftMs < draft.hours * 3600 * 1000
    await op('consent', launchOpSig.consent, async () => {
      if (!renewConsent) return true
      const payload = await request('/consent', {understood: true}) as {authorization?: {valid?: unknown}} | null
      if (payload?.authorization?.valid !== true) throw new Error('24 saatlik risk izni sunucuda doğrulanamadı.')
      renewed = true
      return true
    })
    await op('policy', launchOpSig.policy, async () => {
      if (guideInput.policyAcknowledged) return true
      const payload = await request('/policy/acknowledge', {understood: true}) as {policy_acknowledged?: unknown} | null
      if (payload?.policy_acknowledged !== true) throw new Error('Risk limitleri onayı sunucuda doğrulanamadı.')
      return true
    })
    armedNow = armedNow && !renewed
    await op('arm', launchOpSig.arm, async () => {
      if (armedNow) return true
      const payload = await request('/arm', {understood: true}) as {armed?: unknown} | null
      if (payload?.armed !== true) throw new Error('Canlı emir izni (ARM) sunucuda doğrulanamadı.')
      armedNow = true
      return true
    })
    // ARM ve başlatma ayrı işlemlerdir: başlatma ARM'ı kendiliğinden vermez.
    await op('start', launchOpSig.start, async () => {
      if (!armedNow) throw new Error('Canlı emir izni (ARM) açık değil; bot başlatılmadı. ARM adımı yeniden denenecek.')
      let payload: {original_v2_live?: {enabled?: unknown}; enabled?: unknown} | null
      try { payload = await request('/original-v2/start', {
        risk_percent: draft.risk, daily_loss_percent: draft.daily, risk_acknowledged: draft.risk > 2 && riskAck,
        high_risk_confirmation: draft.risk > 5 ? phrase : '', warning_balance_usdt: wallet,
        expected_risk_revision: revision, understood: true, symbol_count: draft.coins, session_hours: draft.hours,
      }) as typeof payload } catch (error) { await settleAfterTimeout(error, true); payload = {enabled: true} }
      const enabled = payload && typeof payload === 'object' ? (payload.original_v2_live?.enabled ?? payload.enabled) : undefined
      if (enabled !== true) throw new Error('Başlatma sonucu doğrulanamadı; güncel durumu kontrol et.')
      return true
    })
    clearWizDraft()
    await new Promise(resolve => window.setTimeout(resolve, 1600))  // son durumlar görünsün
    setSheet(null); setLaunchPhrase('')
  }, `Otomatik işlem başladı · ${draft.hours} saat · ${draft.coins} coin taranıyor.`, setSheetError)

  // Manuel işlem: yeni bir emir yolu açmaz. Mevcut "Manuel emir · gerçek para" formunu (tek örnek, aynı durum) yarı saydam bir panel olarak gösterir.
  // Dry-run kontrolü, ARM, kilitler ve son onay o formda aynen geçerlidir; kapatınca bölümler önceki hâline döner.
  const [manualModal, setManualModal] = useState(false)
  const manualPrev = useRef<{drawer: boolean; manual: boolean} | null>(null)
  const openManual = () => {
    const drawer = document.getElementById('lc-drawer') as HTMLDetailsElement | null
    const manual = document.querySelector<HTMLDetailsElement>('.originalManualOrder')
    if (!manual) { setFlash({tone: 'warn', text: 'Manuel emir bölümü şu an açılamadı. Bağlantı doğrulanınca tekrar dene.'}); return }
    manualPrev.current = {drawer: drawer?.open ?? false, manual: manual.open}
    if (drawer) drawer.open = true
    manual.open = true
    setManualModal(true)
  }
  const closeManual = useCallback(() => {
    setManualModal(false)
    const prev = manualPrev.current
    manualPrev.current = null
    if (!prev) return
    const drawer = document.getElementById('lc-drawer') as HTMLDetailsElement | null
    const manual = document.querySelector<HTMLDetailsElement>('.originalManualOrder')
    if (manual) manual.open = prev.manual
    if (drawer) drawer.open = prev.drawer
  }, [])
  useEffect(() => {
    if (!manualModal) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeManual() }
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow }
  }, [manualModal, closeManual])
  // Analiz stüdyosundaki "İşlem aç": aynı Manuel işlem panelini açar (form hazır olunca, en çok ~10 sn bekler).
  const openManualRef = useRef(openManual)
  openManualRef.current = openManual
  useEffect(() => {
    if (demo) return undefined  // Demo'da "İşlem aç"ı demo emir kartı karşılar; niyeti burada tüketme.
    let timer = 0
    const tryOpen = (attempt = 0) => {
      if (document.querySelector('.originalManualOrder')) { if (takeIntentForOpen()) openManualRef.current(); return }
      if (attempt < 50) timer = window.setTimeout(() => tryOpen(attempt + 1), 200)
      else if (takeIntentForOpen()) setFlash({tone: 'warn', text: 'Manuel işlem ekranı açılamadı: canlı bağlantı doğrulanmamış görünüyor. Bağlantı sekmesini kontrol edip tekrar dene.'})
    }
    const onIntent = () => { window.clearTimeout(timer); tryOpen() }
    const onClose = () => closeManual()
    tryOpen()
    window.addEventListener(MANUAL_INTENT_EVENT, onIntent)
    window.addEventListener(MANUAL_CLOSE_EVENT, onClose)
    return () => { window.clearTimeout(timer); window.removeEventListener(MANUAL_INTENT_EVENT, onIntent); window.removeEventListener(MANUAL_CLOSE_EVENT, onClose) }
  }, [closeManual, demo])

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
  // ---- Manuel pozisyon: stop/hedef değiştir ve kısmi kapat. Sunucu her şeyi yeniden doğrular (yön, risk sınırı,
  // açık hedef); stop doğrulanmadan pozisyon korumasız sayılmaz. Yalnız değiştirilen alanlar gönderilir.
  const [manualTarget, setManualTarget] = useState<ManualTarget | null>(null)
  const [editLevels, setEditLevels] = useState({stop: '', tp1: '', tp2: '', tp3: ''})
  const [reducePct, setReducePct] = useState<25 | 50 | 75>(50)
  const askEditProtection = (target: ManualTarget) => {
    setSheetError(''); setManualTarget(target)
    setEditLevels({stop: target.stop, tp1: target.targets[0], tp2: target.targets[1], tp3: target.targets[2]})
    setSheet('edit-protection')
  }
  const askReduce = (target: ManualTarget) => { setSheetError(''); setManualTarget(target); setReducePct(50); setSheet('reduce-position') }
  const changedLevel = (value: string, original: string) => {
    const next = Number(value.replace(',', '.')), before = Number(original)
    return Number.isFinite(next) && next > 0 && next !== before ? next : undefined
  }
  const saveProtection = () => {
    const target = manualTarget
    if (!target) return Promise.resolve(false)
    const body = {
      plan_id: target.planId, confirmation: PROTECTION_PHRASE,
      stop_loss: changedLevel(editLevels.stop, target.stop), tp1: changedLevel(editLevels.tp1, target.targets[0]),
      tp2: changedLevel(editLevels.tp2, target.targets[1]), tp3: changedLevel(editLevels.tp3, target.targets[2]),
    }
    if ([body.stop_loss, body.tp1, body.tp2, body.tp3].every(value => value === undefined)) { setSheetError('Değişen bir seviye yok.'); return Promise.resolve(false) }
    return perform('edit-protection', async () => {
      await request('/position/protection', body)
      setSheet(null); setManualTarget(null)
    }, `${target.symbol.replace('USDT', '')} stop/hedefler güncellendi; yeni stop Binance'te doğrulandı.`, setSheetError)
  }
  const reducePosition = () => {
    const target = manualTarget
    if (!target) return Promise.resolve(false)
    const percent = reducePct
    return perform('reduce-position', async () => {
      await request('/position/reduce', {plan_id: target.planId, percent, confirmation: REDUCE_PHRASE})
      setSheet(null); setManualTarget(null)
    }, `${target.symbol.replace('USDT', '')} pozisyonun %${percent}'i kapatıldı; kalan kısım stopla korunuyor.`, setSheetError)
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
    if (key === 'emergency-clear') {
      try { await request('/emergency/clear', {confirmation: 'ACİL DURDURMAYI KALDIRIYORUM', totp_code: code}) }
      catch (error) {
        // Sunucu kilidi zaten kendiliğinden kaldırmış olabilir (geçici kilitler hesap doğrulanınca düşer); ekran eski kalmış demektir.
        if (!/Aktif emergency kilidi yok/i.test(errorText(error, ''))) throw error
      }
    }
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
  // Kais Koç sayacı (telefon): halkaya dokununca 'sonraki tarama' ile 'oturum' arasında geçer.
  const [koView, setKoView] = useState<'scan' | 'session'>('scan')
  const sessionTotal = useRef(0)
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
    if (action === 'choose-risk') { openWizard(); return }
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
  const kaisQuestion = running && why ? dt('Canlı stratejim son taramada neden işlem açmadı? Kısaca açıkla.')
    : running ? dt('Canlı stratejim şu an ne yapıyor? Kısaca özetle.')
    : `${dt('Canlı işlem')} kurulumunda şu adımdayım: "${guide.title}". Ne yapmam gerektiğini kısaca anlatır mısın?`

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
  // ---- Akıllı Kâr Koruma: puan düşünce hemen satmaz; kapanmış 15 dk mum + sayaç + net kâr şartı.
  const smart = original?.smart_profit
  const smartSettings = smart?.settings
  const saveSmart = (changes: Record<string, boolean | number | string>, success: string) => perform('smart', async () => {
    const payload = await request('/original-v2/smart-profit', changes) as {settings?: Record<string, unknown>} | null
    if (!payload?.settings || Object.entries(changes).some(([key, value]) => key !== 'understood' && payload.settings?.[key] !== value))
      throw new Error('Akıllı Kâr Koruma ayarının kaydı doğrulanamadı; güncel ayarı kontrol et.')
  }, success)
  const SMART_MODE: Record<string, string> = {NORMAL: 'Normal', KAR_KORUMA: 'Kâr koruma', CIKISA_HAZIR: 'Çıkışa hazır'}
  const smartTimer = (end?: number | null) => {
    if (!end) return '—'
    const left = Math.max(0, Math.round(end - Date.now() / 1000))
    return left > 0 ? `${Math.ceil(left / 60)} dk kaldı` : 'doldu'
  }
  const smartSwitch = (key: 'enabled' | 'protect_stop_enabled' | 'smart_exit_enabled', label: string, hint: string) => smartSettings && <button type="button" role="switch" aria-checked={smartSettings[key]} className="lcSmartSwitch" data-on={smartSettings[key] || undefined} disabled={busy || smartSettings.emergency_off} onClick={() => key === 'enabled' && !smartSettings.enabled ? setSmartArming(true) : void saveSmart({[key]: !smartSettings[key]}, `${label} ${smartSettings[key] ? 'kapatıldı' : 'açıldı'}. Orijinal Stop ve TP aynen duruyor.`)}>
    <span><b>{label}</b><small>{hint}</small></span><i>{smartSettings[key] ? 'AÇIK' : 'KAPALI'}</i>
  </button>
  const smartBlock = verified && original && guideInput.connected && smart && smartSettings ? <div className="lcSmart" id="lc-smart">
    <h3>Akıllı Kâr Koruma</h3>
    <p className="lcHint">Güven puanı %{smartSettings.threshold}'in altına düşünce pozisyonu hemen satmaz. {smartSettings.wait_minutes} dakika bekler; sonraki kapanmış 15 dakikalık mumda puan hâlâ düşükse ve ücretler düşüldükten sonra kârdaysa kapatır. <b>Zarardaysa asla kapatmaz.</b> Puan yalnızca 15 dakikalık mum kapanışında değişir; karar da buna göre verilir.</p>
    {smartSettings.emergency_off && <p className="lcHint warn" role="alert">Acil durdurma AÇIK: Akıllı Kâr Koruma tamamen devre dışı. Orijinal Stop ve TP çalışıyor.</p>}
    {!smartSettings.enabled && !smartSettings.emergency_off && <p className="lcHint" role="status"><b>Kapalı — sen açana kadar hiçbir emir gönderilmez.</b> Tercihler hazır; açmadan önce istersen değiştir.</p>}
    {smartArming && !smartSettings.enabled && <div className="lcHint warn" role="group" aria-label="Açma onayı">
      <label><input type="checkbox" checked={smartUnderstood} onChange={event => setSmartUnderstood(event.target.checked)} /> Bu sistem açıkken gerçek Binance emri (geçici stop ve kapatma) gönderebileceğini anlıyorum.</label>
      <button type="button" className="lcBtn" disabled={busy || !smartUnderstood} onClick={() => void saveSmart({enabled: true, understood: true}, 'Akıllı Kâr Koruma açıldı. Orijinal Stop ve TP aynen duruyor.').then(ok => { if (ok) { setSmartArming(false); setSmartUnderstood(false) } })}>Aç</button>
      <button type="button" className="lcBtn" disabled={busy} onClick={() => { setSmartArming(false); setSmartUnderstood(false) }}>Vazgeç</button>
    </div>}
    <div className="lcSmartSwitches">
      {smartSwitch('enabled', 'Sistem', 'Puan takibi ve koruma')}
      {smartSwitch('protect_stop_enabled', 'Kâr koruma stopu', 'Kârdayken geçici koruma emri')}
      {smartSwitch('smart_exit_enabled', 'Akıllı çıkış', 'Gerçek kapatma emri')}
    </div>
    <div className="lcSmartPick">
      <label>Puan eşiği<select value={smartSettings.threshold} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({threshold: Number(event.target.value)}, 'Puan eşiği kaydedildi.')}>{[60, 65, 70, 75, 80].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>Bekleme süresi<select value={smartSettings.wait_minutes} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({wait_minutes: Number(event.target.value)}, 'Bekleme süresi kaydedildi.')}>{[5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} dk</option>)}</select></label>
    </div>
    <div className="lcSmartPick">
      <label>Ters yön sinyali<select value={smartSettings.direction_mismatch_action ?? 'record_only'} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({direction_mismatch_action: event.target.value}, 'Ters yön tercihi kaydedildi.')}><option value="record_only">Sadece kaydet</option><option value="count_as_low">Düşük puan gibi say</option></select></label>
    </div>
    <button type="button" className="lcBtn lcDanger" disabled={busy} onClick={() => void saveSmart({emergency_off: !smartSettings.emergency_off}, smartSettings.emergency_off ? 'Acil durdurma kaldırıldı.' : 'Akıllı Kâr Koruma acil durduruldu; geçici stoplar kaldırılıyor. Orijinal Stop ve TP duruyor.')}>{smartSettings.emergency_off ? 'Acil durdurmayı kaldır' : 'Akıllı korumayı ACİL DURDUR'}</button>
    {smart.positions.length > 0 && <ul className="lcSmartList" aria-label="Pozisyon koruma durumu">
      {smart.positions.map(item => <li key={item.plan_id ?? item.symbol} data-mode={item.mode}>
        <b>{item.symbol}</b><span>{SMART_MODE[item.mode ?? 'NORMAL'] ?? item.mode}</span>
        <small>{item.direction_state === 'MISMATCH' ? `Ters yön sinyali (karşı güven ${item.opposite_confidence ?? '—'})` : `Puan ${item.last_score ?? '—'}`} · Sayaç {item.mode === 'NORMAL' ? '—' : smartTimer(item.timer_end)}{item.temp_stop_price ? ` · Geçici stop ${item.temp_stop_price}` : ''}{item.waiting_profit ? ' · net kâr bekleniyor' : ''}</small>
      </li>)}
    </ul>}
  </div> : null
  const smartAdvanced = smartSettings ? <details className="lcWizAdv"><summary>Gelişmiş</summary>
    <div className="lcWizAdvBody">
      {smartSwitch('protect_stop_enabled', 'Kâr koruma stopu', 'Kârdayken geçici koruma emri')}
      {smartSwitch('smart_exit_enabled', 'Akıllı çıkış', 'Gerçek kapatma emri')}
      <div className="lcSmartPick">
        <label>Puan eşiği<select value={smartSettings.threshold} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({threshold: Number(event.target.value)}, 'Puan eşiği kaydedildi.')}>{[60, 65, 70, 75, 80].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Bekleme süresi<select value={smartSettings.wait_minutes} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({wait_minutes: Number(event.target.value)}, 'Bekleme süresi kaydedildi.')}>{[5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} dk</option>)}</select></label>
      </div>
      <div className="lcSmartPick">
        <label>Ters yön sinyali<select value={smartSettings.direction_mismatch_action ?? 'record_only'} disabled={busy || smartSettings.emergency_off} onChange={event => void saveSmart({direction_mismatch_action: event.target.value}, 'Ters yön tercihi kaydedildi.')}><option value="record_only">Sadece kaydet</option><option value="count_as_low">Düşük puan gibi say</option></select></label>
      </div>
      <p className="lcHint">Bu ayarlar seçer seçmez kaydedilir.</p>
    </div>
  </details> : null
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
      {chosenMode === 'agresif' && <label className="lcField">Onay için şunu yaz: <code translate="no">{AGGRESSIVE_PHRASE}</code><input type="text" value={modePhrase} autoComplete="off" onChange={event => setModePhrase(event.target.value)}/></label>}
      {modeError && <p className="lcHint warn" role="alert">{modeError}</p>}
      <button type="button" className="lcBtn lcPrimary" disabled={busy || !modeReady} onClick={() => void saveMode()}>{working === 'mode' ? 'Kaydediliyor…' : `${modeRows.find(row => row.mode === chosenMode)?.label ?? ''} modunu uygula`}</button>
    </div>}
  </div> : null
  const strategyCard = <section className="lcCard lcStrategy" id="lc-strategy" aria-label="Strateji ve risk">
    <header className="lcCardHead"><div><small>STRATEJİ</small><h3>KAİS LONG · risk ayarı</h3></div>
      {verified && guideInput.connected && !guideInput.emergency && <button type="button" className="lcLink" aria-expanded={editorOpen} onClick={() => setSettingsOpen(value => !value)}>{editorOpen ? 'Kapat' : riskSaved ? 'Değiştir' : 'Seç'}<ChevronDown aria-hidden="true" data-open={editorOpen || undefined}/></button>}</header>
    <div className="lcStrategyNow">
      <span><small>İşlem başı hedef risk</small><b>{riskSaved ? pct(savedRisk) : '—'}</b>{riskSaved && wallet !== null ? <em>≈ {usdt(riskSummary(wallet, savedRisk, savedDaily, caps)?.perTrade)}</em> : !riskSaved && <em>seçilmedi</em>}</span>
      <span><small>Günlük kayıp sınırı</small><b>{riskSaved ? pct(savedDaily) : '—'}</b>{riskSaved && wallet !== null && <em>≈ {usdt(riskSummary(wallet, savedRisk, savedDaily, caps)?.perDay)}</em>}</span>
      <span><small>Tarama</small><b>{draft.coins} coin</b><em>{draft.hours} saatlik oturum</em></span>
    </div>
    {honestBlock}
    {modeBlock}
    {smartBlock}
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
      {!draftValid && <p className="lcHint stop" role="alert">İşlem riski %0,5–15, günlük sınır %1–25 ve işlem riskinden küçük olmamalı; coin sayısı 10–100.</p>}
      {draftValid && tier !== 'normal' && <p className="lcTier" data-tier={tier}>{tier === 'yellow' ? 'Ortalamanın üstünde risk.' : tier === 'orange' ? 'Yüksek risk: üst üste birkaç kayıp bakiyeni belirgin düşürür.' : 'Çok yüksek risk: ani hareketlerde kayıp daha büyük olabilir.'}
        {wallet !== null && <> {worstRisk < draft.risk ? 'Gerçekte uygulanabilen riskle, en kötü durumda' : ''} 10 kayıp üst üste gelirse bakiye ≈ <b>{usdt(wallet * (1 - worstRisk / 100) ** 10)}</b> (−%{Math.round((1 - (1 - worstRisk / 100) ** 10) * 100)}) olur.</>}</p>}
      {running && <p className="lcHint">Çalışırken yalnız risk yüzdeleri değişir ve yeni işlemlere uygulanır; coin sayısı ve süre bir sonraki başlatmada geçerli olur.</p>}
      {riskDirty && draftValid && <div className="lcConfirmBox">
        {draft.risk > 2 && draft.risk <= 5 && <label className="lcCheck"><input type="checkbox" checked={riskAck} onChange={event => setRiskAck(event.target.checked)}/><span>%{draft.risk} işlem riskinin yüksek olduğunu anladım.</span></label>}
        {draft.risk > 5 && <><label className="lcField">Onay için <b className="selectablePhrase" translate="no">{HIGH_RISK_PHRASE}</b> yaz<input value={phrase} autoComplete="off" onChange={event => setPhrase(event.target.value)}/></label></>}
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

  // ---- Kais Koç kurulum sihirbazı: mevcut kaydetme akışlarını (risk, günlük sınır, teminat modu) sırayla kullanır; yeni kural eklemez.
  const WIZ_STEPS = ['Risk', 'Günlük sınır', 'Teminat', 'Akıllı koruma', 'Onay']
  const WIZ_COACH = [
    'Önce her işlemde ne kadar riske girmek istediğini seç. Emin değilsen Dengeli iyi bir başlangıçtır.',
    'Şimdi günlük sınırı seç: o gün toplam kaybın bu rakama ulaşırsa bot yeni işlem açmaz.',
    'Son olarak her işleme ne kadar teminat ayrılabileceğini seç. Teminatı Binance ayırır; paran Binance hesabında kalır.',
    'İstersen akıllı koruma ekle: puan düşünce kârdaki pozisyonu korur. Varsayılan KAPALI; açmak tamamen senin kararın.',
    'Hepsi hazır. Gözden geçir, onayla ve kaydet.',
  ]
  const DAILY_CHOICES = [2, 3, 5, 6, 10]
  const wizModeChanged = chosenMode !== savedMode
  const wizRiskNums = wallet !== null ? riskSummary(wallet, draft.risk, draft.daily, caps) : null
  const wizDailyOk = Number.isFinite(draft.daily) && draft.daily >= 1 && draft.daily <= 25 && draft.daily >= draft.risk
  const smartCurrentlyOn = smartSettings?.enabled === true
  const smartChangeWanted = smartSettings !== undefined && ((smartPick === 'on' && !smartCurrentlyOn) || (smartPick === 'off' && smartCurrentlyOn))
  const wizAnythingChanged = riskDirty || wizModeChanged || smartChangeWanted
  const wizReady = wizAnythingChanged && (!(riskDirty || wizModeChanged) || (draftValid && wizDailyOk && wallet !== null)) && (!smartChangeWanted || smartPick === 'off' || smartWizAck)
    && (!riskDirty || (understood && tierReady(draft.risk, riskAck, phrase)))
    && (!wizModeChanged || (modeUnderstood && (chosenMode === 'guvenli' || modeAck) && (chosenMode !== 'agresif' || modePhrase.trim() === AGGRESSIVE_PHRASE)))
  const wizUnderstood = riskDirty ? understood : modeUnderstood
  const setWizUnderstood = (value: boolean) => { setUnderstood(value); setModeUnderstood(value) }
  const saveWizard = async () => {
    if (riskDirty && !(await saveRisk())) return
    if (wizModeChanged && !(await saveMode())) return
    if (smartChangeWanted) {
      const smartOk = await saveSmart(smartPick === 'on'
        ? {threshold: smartWizThreshold, wait_minutes: smartWizWait, enabled: true, understood: true}
        : {enabled: false}, smartPick === 'on' ? 'Akıllı Kâr Koruma açıldı. Orijinal Stop ve TP aynen duruyor.' : 'Akıllı Kâr Koruma kapatıldı.')
      if (!smartOk) return
    }
    clearWizDraft()
    setSheet(null)
  }
  const wizTrades = wizRiskNums && wizRiskNums.perTrade > 0 ? Math.max(1, Math.floor(wizRiskNums.perDay / wizRiskNums.perTrade)) : null
  const wizMargin = wallet !== null && chosenRow ? marginModePreview(wallet, caps, chosenRow.per_trade_percent)?.marginUsdt ?? null : null
  const WIZ_WHY = [
    `Bir işlemde en fazla ne kadar kaybetmeyi göze aldığını belirler. ${wizRiskNums ? `Şu seçimle bir işlemde hedef kayıp yaklaşık ${usdt(wizRiskNums.perTrade)}.` : ''} Stop-loss bu seviyede çalışır.`,
    `Bir günde toplam kaybın bu sınıra gelirse bot o gün yeni işlem açmaz. ${wizRiskNums && wizTrades ? `Örnek: sınır ${usdt(wizRiskNums.perDay)}, işlem başı ${usdt(wizRiskNums.perTrade)}; yaklaşık ${wizTrades} zararlı işlemden sonra durur.` : ''}`,
    'Teminat, her işlem için bakiyenden ayrılan paradır. Yükselttikçe işlem büyür; kazanç da kayıp da büyür. Stop-loss her zaman çalışır.',
    'Puan düşünce pozisyon hemen satılmaz; 15 dakikalık mum kapanışları beklenir. Zarardaysa asla kapatılmaz. Orijinal Stop ve TP hiçbir zaman silinmez.',
    'Burada hiçbir şey emir açmaz. Kaydedince yalnız yeni işlemler etkilenir; açık pozisyonlar değişmez.',
  ]
  const wizardView = <>
    <h2 id="lc-sheet-title">Kais Koç ile ayarla</h2>
    <ol className="lcWizDots" aria-label="Adımlar">{WIZ_STEPS.map((label, index) => <li key={label} data-state={index < wizStep ? 'done' : index === wizStep ? 'now' : 'next'} aria-current={index === wizStep ? 'step' : undefined}><i aria-hidden="true">{index < wizStep ? '✓' : index + 1}</i><span>{label}</span></li>)}</ol>
    <div className="lcWizBar" role="progressbar" aria-valuemin={1} aria-valuemax={WIZ_STEPS.length} aria-valuenow={wizStep + 1} aria-label={`Adım ${wizStep + 1} / ${WIZ_STEPS.length}`}><i style={{width: `${((wizStep + 1) / WIZ_STEPS.length) * 100}%`}}/></div>
    {wizResumed && <p className="lcWizResume" role="status">Kaldığın yerden devam ediyorsun. <button type="button" className="lcLink" onClick={() => { clearWizDraft(); openWizard() }}>Baştan başla</button></p>}
    <p className="lcWizCoach"><b>KAİS KOÇ</b>{WIZ_COACH[wizStep]}</p>
    <div className="lcWizLive" aria-label="Seçimine göre tahmini rakamlar">
      <div><small>İşlem başı risk</small><b>{wizRiskNums ? usdt(wizRiskNums.perTrade) : '—'}</b></div>
      <div><small>Günlük en çok kayıp</small><b>{wizRiskNums ? usdt(wizRiskNums.perDay) : '—'}</b></div>
      <div><small>İşlem başı teminat</small><b>{wizMargin !== null ? `≈ ${usdt(wizMargin)}` : '—'}</b></div>
      <p>Bunlar hedef ve tavan rakamlarıdır: tahmin, geçmiş sonuç ya da kazanç/kayıp garantisi değildir.{wizTrades ? ` Günlük sınır ≈ ${wizTrades} zararlı işleme denk gelir.` : ''}</p>
    </div>
    <details className="lcWizWhy"><summary>Bu ne işe yarar?</summary><p>{WIZ_WHY[wizStep]}</p></details>
    {wizStep === 0 && <div className="lcWizList" role="radiogroup" aria-label="İşlem başı risk">
      {RISK_PRESETS.map(preset => {
        const numbers = wallet !== null ? riskSummary(wallet, preset.risk_percent, preset.daily_loss_percent, caps) : null
        const selected = draft.risk === preset.risk_percent
        return <button type="button" role="radio" aria-checked={selected} key={preset.id} className="lcWizOption" data-selected={selected || undefined} disabled={busy} onClick={() => choosePreset(preset.id)}>
          <span className="lcWizHead"><b>{preset.label}</b>{preset.id === 'dengeli' && <em>Önerilen</em>}</span>
          <span className="lcWizBig">{numbers ? usdt(numbers.perTrade) : pct(preset.risk_percent)}<small> / işlem (hedef)</small></span>
          <small>{preset.note}</small>
        </button>
      })}
      <p className="lcHint">Seçtiğin rakam bir hedeftir, garanti edilen gerçek kayıp değildir. Farklı bir yüzde için Ayarlar’daki “Kendim ayarlayayım” bölümünü kullanabilirsin.</p>
      <button type="button" className="lcBtn lcWizQuick" disabled={busy || !draftValid || !wizDailyOk} onClick={() => setWizStep(4)}>Hızlı: bu paketle özete geç →</button>
      <p className="lcHint">Paket yalnız işlem riskini ve günlük sınırı seçer. Teminat modu ve Akıllı koruma olduğu gibi kalır; hiçbiri kendiliğinden açılmaz.</p>
    </div>}
    {wizStep === 1 && <div className="lcWizList">
      <div className="lcWizChips" role="radiogroup" aria-label="Günlük kayıp sınırı">
        {DAILY_CHOICES.filter(value => value >= draft.risk).map(value => {
          const numbers = wallet !== null ? riskSummary(wallet, draft.risk, value, caps) : null
          return <button type="button" role="radio" aria-checked={draft.daily === value} key={value} className="lcWizChip" data-selected={draft.daily === value || undefined} disabled={busy} onClick={() => setDraft(current => ({...current, daily: value}))}>
            <b>%{value}</b><small>{numbers ? usdt(numbers.perDay) : ''}</small>
          </button>
        })}
      </div>
      <label className="lcWizField">Kendi sınırın (%1–%25)
        <input type="number" inputMode="decimal" min="1" max="25" step="0.5" value={draft.daily} disabled={busy} onChange={event => setDraft(current => ({...current, daily: Number(event.target.value)}))}/>
      </label>
      {wizDailyOk
        ? <p className="lcHint">Günde en fazla <b>{wizRiskNums ? usdt(wizRiskNums.perDay) : pct(draft.daily)}</b> kaybedebilirsin{wizRiskNums?.perDayCapped ? ' (sunucu tavanı bunu düşürdü)' : ''}. Bu sınıra gelince bot o gün yeni işlem açmaz.</p>
        : <p className="lcHint stop" role="alert">Günlük sınır %1–%25 arasında ve işlem riskinden (%{draft.risk}) küçük olmamalı.</p>}
    </div>}
    {wizStep === 2 && <div className="lcWizList" role="radiogroup" aria-label="Teminat modu">
      {modeRows.map(row => {
        const view = wallet !== null ? marginModePreview(wallet, caps, row.per_trade_percent) : null
        const selected = chosenMode === row.mode
        return <button type="button" role="radio" aria-checked={selected} key={row.mode} className="lcWizOption" data-selected={selected || undefined} disabled={busy} onClick={() => { setCustomOn(false); pickMode(row.mode) }}>
          <span className="lcWizHead"><b>{row.label}</b>{savedMode === row.mode && <em>Şu anki</em>}</span>
          <span className="lcWizBig">%{row.per_trade_percent}<small> bakiye / işlem · ≈ {view ? usdt(view.marginUsdt) : '—'}</small></span>
          <small>{MODE_TEXT[row.mode]}</small>
        </button>
      })}
      <p className="lcHint">Teminatı Binance senin Binance hesabından ayırır; KaisTrade almaz. Bu seçim sadece her işleme en çok ne kadar ayrılabileceğini belirler.</p>
    </div>}
    {wizStep === 3 && <div className="lcWizList">
      {smartSettings === undefined ? <p className="lcHint">Akıllı koruma bilgisi şu an alınamadı; bu adımı atlayabilirsin, Ayarlar’dan sonra da açabilirsin.</p> : <>
        <ol className="lcFlow" aria-label="Akıllı koruma nasıl çalışır">
          <li><i>1</i><b>Puan düşer</b><small>Eşiğin altına iner</small></li>
          <li><i>2</i><b>Sayaç</b><small>{smartWizWait} dk bekler</small></li>
          <li><i>3</i><b>Yeni mum</b><small>Hâlâ düşükse devam</small></li>
          <li><i>4</i><b>Kârdaysa çık</b><small>Zarardaysa bekler</small></li>
        </ol>
        <p className="lcHint">Güven puanı eşiğin altına düşünce pozisyonu hemen satmaz: bekler, sonraki kapanmış 15 dakikalık mumda da puan düşükse ve ücretler düşüldükten sonra kârdaysa kapatır. <b>Zarardaysa asla kapatmaz.</b> Orijinal Stop ve TP aynen çalışır.</p>
        <div className="lcWizList" role="radiogroup" aria-label="Akıllı koruma">
          {([['keep', smartCurrentlyOn ? 'Şu an AÇIK, böyle kalsın' : 'Şu an KAPALI, böyle kalsın', smartCurrentlyOn ? 'Mevcut ayarlar korunur.' : 'Hiçbir akıllı emir gönderilmez.'],
             ...(smartCurrentlyOn ? [['off', 'Kapat', 'Geçici stoplar kaldırılır; orijinal Stop/TP durur.']] : [['on', 'Aç', 'Açıkken gerçek Binance emri (geçici stop ve kapatma) gönderebilir.']])] as string[][]).map(([id, title, note]) =>
            <button type="button" role="radio" aria-checked={smartPick === id} key={id} className="lcWizOption" data-selected={smartPick === id || undefined} disabled={busy} onClick={() => setSmartPick(id as 'keep' | 'on' | 'off')}>
              <span className="lcWizHead"><b>{title}</b>{id === 'keep' && !smartCurrentlyOn && <em>Önerilen şimdilik</em>}</span><small>{note}</small>
            </button>)}
        </div>
        {smartAdvanced}
        {smartPick === 'on' && <div className="lcSmartPick">
          <label>Puan eşiği<select value={smartWizThreshold} disabled={busy} onChange={event => setSmartWizThreshold(Number(event.target.value))}>{[60, 65, 70, 75, 80].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
          <label>Bekleme süresi<select value={smartWizWait} disabled={busy} onChange={event => setSmartWizWait(Number(event.target.value))}>{[5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} dk</option>)}</select></label>
          <label className="lcCheck"><input type="checkbox" checked={smartWizAck} onChange={event => setSmartWizAck(event.target.checked)}/><span>Akıllı koruma açıkken gerçek Binance emri gönderebileceğini anlıyorum.</span></label>
        </div>}
      </>}
    </div>}
    {wizStep === 4 && <div className="lcWizList">
      <dl className="lcWizSummary">
        <div><dt>İşlem başı risk</dt><dd>{wizRiskNums ? usdt(wizRiskNums.perTrade) : pct(draft.risk)} <small>(%{draft.risk}){riskDirty && draft.risk !== savedRisk ? ` · şimdi %${savedRisk}` : ''}</small></dd></div>
        <div><dt>Günlük kayıp sınırı</dt><dd>{wizRiskNums ? usdt(wizRiskNums.perDay) : pct(draft.daily)} <small>(%{draft.daily}){draft.daily !== savedDaily ? ` · şimdi %${savedDaily}` : ''}</small></dd></div>
        <div><dt>Teminat modu</dt><dd>{chosenRow?.label ?? '—'} <small>(%{chosenRow?.per_trade_percent ?? '—'}){wizModeChanged ? ` · şimdi ${savedRow?.label ?? '—'}` : ''}</small></dd></div>
        <div><dt>Akıllı koruma</dt><dd>{smartPick === 'on' ? `AÇILACAK (eşik ${smartWizThreshold}, ${smartWizWait} dk)` : smartPick === 'off' ? 'KAPATILACAK' : smartCurrentlyOn ? 'Açık (değişmiyor)' : 'Kapalı (değişmiyor)'}</dd></div>
      </dl>
      {!wizAnythingChanged && <p className="lcHint">Seçtiklerin zaten kayıtlı ayarların. Değiştirmek için geri dönüp başka bir seçenek seç.</p>}
      {wizAnythingChanged && <div className="lcConfirmBox">
        {riskDirty && draft.risk > 2 && draft.risk <= 5 && <label className="lcCheck"><input type="checkbox" checked={riskAck} onChange={event => setRiskAck(event.target.checked)}/><span>%{draft.risk} işlem riskinin yüksek olduğunu anladım.</span></label>}
        {riskDirty && draft.risk > 5 && <label className="lcField">Onay için <b className="selectablePhrase" translate="no">{HIGH_RISK_PHRASE}</b> yaz<input value={phrase} autoComplete="off" onChange={event => setPhrase(event.target.value)}/></label>}
        {wizModeChanged && chosenMode !== 'guvenli' && <label className="lcCheck"><input type="checkbox" checked={modeAck} onChange={event => setModeAck(event.target.checked)}/><span>Bu modda her işleme daha çok teminat ayrılacağını ve zararın büyüyebileceğini kabul ediyorum.</span></label>}
        {wizModeChanged && chosenMode === 'agresif' && <label className="lcField">Onay için şunu yaz: <code translate="no">{AGGRESSIVE_PHRASE}</code><input type="text" value={modePhrase} autoComplete="off" onChange={event => setModePhrase(event.target.value)}/></label>}
        <label className="lcCheck"><input type="checkbox" checked={wizUnderstood} onChange={event => setWizUnderstood(event.target.checked)}/><span>Gerçek parayla işlem açılacağını ve kayıp olabileceğini anladım.</span></label>
        <p className="lcHint">Kaydedince yalnız yeni işlemlere uygulanır; açık pozisyonlar değişmez. Hiçbir adım tek başına emir açmaz.</p>
      </div>}
      {riskError && <p className="lcHint stop" role="alert">Risk ayarı kaydedilemedi: {riskError}</p>}
      {modeError && <p className="lcHint stop" role="alert">Teminat modu kaydedilemedi: {modeError}</p>}
    </div>}
    <div className="lcSheetActions lcWizActions">
      <button type="button" className="lcBtn" disabled={busy || wizStep === 0} onClick={() => setWizStep(step => Math.max(0, step - 1))}>Geri</button>
      {wizStep < 4
        ? <button type="button" className="lcBtn lcPrimary" disabled={busy || (wizStep === 1 && !wizDailyOk)} onClick={() => setWizStep(step => Math.min(4, step + 1))}>Devam</button>
        : <button type="button" className="lcBtn lcPrimary" data-testid="koc-wizard-save" disabled={busy || !wizReady} onClick={() => void saveWizard()}>{working === 'risk' || working === 'mode' ? 'Kaydediliyor…' : 'Hepsini kaydet'}</button>}
    </div>
  </>
  const launchPhraseOk = launchPhrase.trim().toLocaleLowerCase('tr-TR').replace(/ı/g, 'i') === 'onayliyorum'
  const launchRiskValid = Number.isFinite(draft.risk) && draft.risk >= 0.5 && draft.risk <= 15 && validCoins(draft.coins) && validHours(draft.hours)
  const launchModeOk = chosenMode === savedMode || ((chosenMode === 'guvenli' || modeAck) && (chosenMode !== 'agresif' || modePhrase.trim() === AGGRESSIVE_PHRASE))
  const launchConnected = guideInput.connected && guideInput.credentialsConfigured && wallet !== null
  // İşlem imzaları: aynı girdiyle bitmiş işlem tekrar çalışmaz (runLaunch da bunları kullanır).
  const launchCore = `${draft.risk}|${draft.daily}|${chosenMode}|${smartPick}|${smartWizThreshold}|${smartWizWait}|${draft.coins}|${draft.hours}`
  const launchOpSig = {
    risk: `risk|${draft.risk}|${draft.daily}`, mode: `mode|${chosenMode}`, smart: `smart|${smartPick}|${smartWizThreshold}|${smartWizWait}`,
    consent: `consent|${draft.hours}`, policy: 'policy', arm: `arm|${draft.hours}`, start: `start|${launchCore}`,
  }
  // Onay imzaları: önceki bir seçim değişirse ona bağlı onaylar kendiliğinden geçersiz olur.
  const launchSig: Record<LaunchStepId, string> = {
    conn: 'conn',
    risk: `${draft.risk}|${draft.coins}|${draft.hours}|${riskAck}|${draft.risk > 5 ? phrase === HIGH_RISK_PHRASE : ''}`,
    daily: `${draft.risk}|${draft.daily}`,
    mode: `${chosenMode}|${modeAck}|${modePhrase.trim() === AGGRESSIVE_PHRASE}`,
    smart: `${smartPick}|${smartWizThreshold}|${smartWizWait}|${smartWizAck}`,
    consent: `${draft.risk}|${draft.daily}|${draft.hours}`,
    policy: `${draft.risk}|${draft.daily}`,
    arm: launchCore,
  }
  const launchValid: Record<LaunchStepId, boolean> = {
    conn: launchConnected,
    risk: launchRiskValid && tierReady(draft.risk, riskAck, phrase),
    daily: wizDailyOk,
    mode: launchModeOk,
    smart: smartSettings === undefined || smartPick !== 'on' || smartWizAck,
    consent: true, policy: true, arm: true,
  }
  const launchApproved = (id: LaunchStepId) => id === 'conn' ? launchConnected : launchValid[id] && launchApprovals[id] === launchSig[id]
  const launchActive: LaunchStepId | 'final' = LAUNCH_STEPS.find(item => !launchApproved(item.id))?.id ?? 'final'
  const launchApprove = (id: LaunchStepId) => { if (launchValid[id]) setLaunchApprovals(current => ({...current, [id]: launchSig[id]})) }
  // Geri al: bu adımın ve ondan sonraki tüm adımların onayı kalkar; sunucuya yazılmış bir şey geri alınmaz (yazma yalnız en sonda olur).
  const launchUndo = (id: LaunchStepId) => setLaunchApprovals(current => {
    const from = LAUNCH_STEPS.findIndex(item => item.id === id)
    const next = {...current}
    LAUNCH_STEPS.slice(from).forEach(item => { delete next[item.id] })
    return next
  })
  const launchOpOf = (key: string): LaunchOp | undefined => {
    const op = launchOps[key] as LaunchOp | undefined
    if (!op) return undefined
    if (op.state === 'done') return op.sig === launchOpSig[key as keyof typeof launchOpSig] ? op : undefined
    return op
  }
  const launchVerified: Record<LaunchStepId, boolean> = {
    conn: launchConnected,
    risk: !launchNeedsRisk, daily: !launchNeedsRisk,
    mode: chosenMode === savedMode, smart: !smartChangeWanted,
    consent: guideInput.authorizationValid && consentLeftMs >= draft.hours * 3600 * 1000,
    policy: guideInput.policyAcknowledged,
    arm: guideInput.armed && guideInput.authorizationValid,
  }
  const launchCanRun = launchActive === 'final' && launchPhraseOk && !running && !busy
  const launchFailed = (Object.values(launchOps) as LaunchOp[]).some(item => item.state === 'error')
  const launchRunning = (Object.values(launchOps) as LaunchOp[]).some(item => item.state === 'working') || working === 'launch'
  const launchDoneCount = LAUNCH_STEPS.filter(item => launchApproved(item.id)).length + (launchOpOf('start')?.state === 'done' ? 1 : 0)
  const launchTotal = LAUNCH_STEPS.length + 1
  const launchNote = (id: LaunchStepId) => ({
    conn: launchConnected ? 'Bağlı, bakiye doğrulandı' : 'Bağlantı yok; önce Bağlantı sekmesinden anahtarı kaydet',
    risk: wizRiskNums ? `${usdt(wizRiskNums.perTrade)} / işlem (%${draft.risk})` : `%${draft.risk}`,
    daily: wizRiskNums ? `${usdt(wizRiskNums.perDay)} / gün (%${draft.daily})` : `%${draft.daily}`,
    mode: `${chosenRow?.label ?? '—'} · işlem başı %${chosenRow?.per_trade_percent ?? '—'}`,
    smart: smartSettings === undefined ? 'Bilgi alınamadı, atlanır' : smartPick === 'on' ? `AÇILACAK · eşik ${smartWizThreshold}, ${smartWizWait} dk` : smartPick === 'off' ? 'KAPATILACAK' : smartCurrentlyOn ? 'Açık, böyle kalır' : 'Kapalı, böyle kalır',
    consent: `${draft.hours} saatlik izin`, policy: 'Risk limitlerini kabul', arm: dt('Canlı emir izni (botu başlatmaz)'),
  })[id]
  // ---- Gerçek verilerle akıllı uyarılar (kural tabanlı; piyasa tahmini değildir). Uyarı bilgilendirir, engellemez.
  type Advice = {tone: 'ok' | 'warn' | 'bad' | 'info'; text: string}
  const todayUtc = new Date().toISOString().slice(0, 10)
  const dayData = original?.daily && original.daily.day === todayUtc ? original.daily : null
  const dayBase = dayData && finite(dayData.opening_wallet) && (dayData.opening_wallet ?? 0) > 0 ? (dayData.opening_wallet as number) : wallet
  const dayCapUsdt = caps?.daily_loss_limit_usdt
  const dayLimitNew = dayBase !== null && dayBase > 0 ? Math.min(dayBase * draft.daily / 100, typeof dayCapUsdt === 'number' && dayCapUsdt > 0 ? dayCapUsdt : Infinity) : null
  const dayLossTotal = dayData ? finite(dayData.loss) : null            // açık zarar dahil: günlük kilidi bunu sayar
  const dayLossRealized = dayData ? finite(dayData.realized_loss) : null // gerçekleşen: yeni işlem bütçesine bunu sayar
  const dayUsedPct = dayLossTotal !== null && dayLimitNew ? dayLossTotal / dayLimitNew * 100 : null
  const launchAdvice = (id: LaunchStepId | 'final'): Advice[] => {
    const out: Advice[] = []
    const openCount = positions.length
    if (id === 'risk') {
      if (openCount > 0) out.push({tone: 'info', text: `${openCount} açık pozisyon var. Onların Stop/TP'sine dokunulmaz.`})
    }
    if (id === 'daily') {
      if (wizTrades !== null && wizTrades < 2) out.push({tone: 'warn', text: `Günlük sınır yalnız ~${wizTrades} işlem zararına yetiyor; tek kötü işlemden sonra gün biter.`})
      if (draft.daily > 10) out.push({tone: 'warn', text: `%${draft.daily} günlük sınır yüksek: kötü bir günde bakiyenin bu kadarı gidebilir.`})
      if (wizRiskNums?.perDayCapped) out.push({tone: 'info', text: `Sistem tavanı nedeniyle günlük kayıp en çok ${usdt(wizRiskNums.perDay)} olur.`})
      if (dayData?.locked) out.push({tone: 'bad', text: 'Bugün günlük kayıp kilidi AÇIK: yeni giriş kapalı. Gün sayacı UTC 00:00\'da sıfırlanır.'})
      else if (dayUsedPct !== null && dayUsedPct >= 100) out.push({tone: 'bad', text: 'Bugünkü zarar bu limiti zaten aştı; yeni giriş açılmaz.'})
      else if (dayUsedPct !== null && dayUsedPct >= 50) out.push({tone: 'warn', text: 'Günlük limitin büyük kısmı kullanılmış. Yeni işlem başlatmadan önce yarına kadar beklemeyi değerlendirebilirsin.'})
      if (!dayData) out.push({tone: 'info', text: 'Bugüne ait zarar verisi yok; limit güncel bakiyeden hesaplanır.'})
    }
    if (id === 'mode' && chosenMode !== 'guvenli') out.push({tone: 'warn', text: `${chosenRow?.label ?? 'Bu'} modda her işleme daha çok teminat ayrılır; zarar büyüyebilir.`})
    if (id === 'smart' && smartPick === 'on') out.push({tone: 'warn', text: 'Açıkken Binance\'e gerçek koruma/kapatma emri gönderebilir. Kural tabanlıdır; piyasayı tahmin etmez.'})
    if (id === 'consent') {
      if (guideInput.authorizationValid && consentLeftMs < draft.hours * 3600 * 1000) out.push({tone: 'warn', text: `Mevcut izin ${duration(Math.max(0, consentLeftMs / 1000))} sonra bitiyor; seçtiğin ${draft.hours} saatten kısa olduğu için yenilenecek. Yenilenince ARM sıfırlanır ve ayrıca yeniden verilir.`})
      else if (guideInput.authorizationValid) out.push({tone: 'ok', text: 'Mevcut izin yeterli; yenilenmeyecek.'})
    }
    if (id === 'arm') out.push({tone: 'info', text: 'ARM yalnız canlı emir iznidir; botu BAŞLATMAZ. Başlatma ayrı ve son adımdır.'})
    if (id === 'final' && openCount > 0) out.push({tone: 'info', text: `${openCount} açık pozisyon var; botu başlatmak onları kapatmaz.`})
    return out
  }
  // Teminat önerisi seçilen risk paketinden gelir; yalnız ÖNERİDİR, mod kendi onayıyla ayrı seçilir.
  const recPreset = RISK_PRESETS.find(item => item.risk_percent === draft.risk)
  const recModeLabel: string = recPreset?.margin_mode ?? 'Güvenli'
  const recModeId = recModeLabel === 'Agresif' ? 'agresif' : recModeLabel === 'Orta' ? 'orta' : 'guvenli'
  const modeLeverage = Number(original?.risk_limits?.leverage) > 0 ? Number(original?.risk_limits?.leverage) : 3
  const modeBudget = wallet !== null ? riskSummary(wallet, draft.risk, draft.daily, caps)?.perTrade ?? null : null
  /** Bu teminat modunda pozisyon üst sınırı ve stop %1 uzaktayken gerçekleşecek risk (bilgi amaçlı tahmin). */
  const modeNumbers = (perTradePercent: number) => {
    if (wallet === null || !(wallet > 0)) return null
    const marginCeiling = Number(original?.risk_limits?.system_ceilings?.max_margin_per_trade_usdt) > 0 ? Number(original?.risk_limits?.system_ceilings?.max_margin_per_trade_usdt) : 300
    const position = Math.min(wallet * perTradePercent / 100, marginCeiling) * modeLeverage
    const loss = modeBudget !== null ? Math.min(modeBudget, position * 0.01) : position * 0.01
    return {position, riskPercent: loss / wallet * 100}
  }
  const modeWhy = recModeId === 'guvenli'
    ? 'Güvenli: her işleme en az teminat ayrılır. Önce bununla başla, alışınca değiştir.'
    : `${recPreset?.label ?? 'Bu'} paketinde (işlem başı %${draft.risk}) Güvenli modda pozisyon teminat tavanına takılır ve seçtiğin risk gerçekleşmez. Bu yüzden ${recModeLabel} önerilir; kazanç da kayıp da büyür.`
  const launchWhy: Record<LaunchStepId, string> = {
    conn: 'Gerçek bakiye olmadan risk rakamları hesaplanamaz.',
    risk: 'Dengeli: işlem başı %1 risk, bakiyeni hızlı eritmeden yeterli fırsat verir. İlk kez başlıyorsan en makul seçim.',
    daily: 'Önerilen sınır, seçtiğin risk paketinin günlük payıdır: kötü bir günde kayıp burada durur.',
    mode: modeWhy,
    smart: smartCurrentlyOn ? 'Şu an açık: puan düşünce kârdaki pozisyonu erken kapatabilir. Erken kapanmadan memnun değilsen kapat; orijinal Stop ve TP her durumda çalışır.' : 'Kapalı kalsın: bu özellik gerçek emir gönderebilir. İlk günler yalnız izleyip sonra açman daha güvenli.',
    consent: 'Botun işlem açabilmesi için belirli süreli açık iznin gerekir. Süre bitince bot yeni işlem açmaz.',
    policy: 'Sistemin sabit risk limitlerini (en çok pozisyon, günlük işlem sayısı vb.) kabul ettiğini kaydeder.',
    arm: 'ARM canlı emir kapısını açar. Başlatmadan ayrı tutulur ki iki karar ayrı verilsin.',
  }
  const launchRec = (id: LaunchStepId): string | null => {
    if (id === 'daily') {
      const presetDaily = RISK_PRESETS.find(item => item.risk_percent === draft.risk)?.daily_loss_percent
      return presetDaily !== undefined ? `%${presetDaily}` : null  // kendi riskini yazana uydurma bir öneri gösterilmez
    }
    return id === 'mode' ? recModeLabel : null  // risk adımının önerisi kendi kartında anlatılır
  }
  const chipOf = (label: string, state: 'ok' | 'idle' | 'working' | 'error', title?: string) =>
    <span className="lcWzChip" data-state={state} title={title}><i aria-hidden="true">{state === 'ok' ? <Check/> : state === 'working' ? <RefreshCw className="lcSpin"/> : state === 'error' ? '!' : null}</i>{label}</span>
  const stateChips = (id: LaunchStepId) => {
    const meta = LAUNCH_STEPS.find(item => item.id === id)!
    const op = meta.op ? launchOpOf(meta.op) : undefined
    const approved = launchApproved(id)
    return <span className="lcWzChips lcWzLine" aria-label="Onay, doğrulama ve işlem durumu">
      {id === 'conn' ? null : <>{chipOf('Sen onayla', approved ? 'ok' : 'idle')}<i className="lcWzLn" aria-hidden="true"/></>}
      {chipOf('Kontrol', launchVerified[id] || (op?.state === 'done' && op.verified) ? 'ok' : 'idle')}
      {id === 'conn' ? null : <><i className="lcWzLn" aria-hidden="true"/>{chipOf('Kaydedilir', op?.state === 'done' ? 'ok' : op?.state === 'working' ? 'working' : op?.state === 'error' ? 'error' : 'idle')}</>}
    </span>
  }
  const ringR = 26, ringC = 2 * Math.PI * ringR
  const activeMeta = launchActive === 'final' ? null : LAUNCH_STEPS.find(item => item.id === launchActive)!
  const activeIndex = launchActive === 'final' ? launchTotal : LAUNCH_STEPS.findIndex(item => item.id === launchActive) + 1
  // ---- Risk paketi adımı: hazır paketler + "Kendim ayarlarım". Yalnız taslağı (draft) değiştirir; kaydetme en sonda mevcut akışla olur.
  const rkPreset = RISK_PRESETS.find(item => item.risk_percent === draft.risk && item.symbol_count === draft.coins && item.session_hours === draft.hours)
  const rkCustom = wizCustom || !rkPreset
  const rkRec = RISK_PRESETS.find(item => item.id === 'dengeli')!
  const rkNums = (risk: number, daily: number) => wallet !== null ? riskSummary(wallet, risk, Math.max(daily, risk), caps) : null
  // Sunucu tavanı işlem başı kaybı kısabilir: kötü seri hesabı gerçekte uygulanabilen yüzdeyle yapılır.
  const rkEffective = (risk: number) => { const nums = rkNums(risk, risk); return nums && wallet ? nums.perTrade / wallet * 100 : risk }
  const rkAmount = (risk: number, daily: number) => {
    const nums = rkNums(risk, daily)
    return nums ? <span className="lcRkAmt"><b>{money(nums.perTrade)}</b><small>USDT · işlem başı</small>{nums.perTradeCapped && <em>sunucu tavanı</em>}</span>
      : <span className="lcRkAmt"><b>{pct(risk)}</b><small>işlem başı risk</small></span>
  }
  const setWizRisk = (value: number) => { setDraft(current => ({...current, risk: value})); setRiskAck(false); setPhrase('') }
  const rkIndex = RISK_STEPS.reduce((best, value, index) => Math.abs(value - draft.risk) < Math.abs(RISK_STEPS[best] - draft.risk) ? index : best, 0)
  const rkBetween = (() => {
    const same = RISK_PRESETS.find(item => item.risk_percent === draft.risk)
    if (same) return `Risk olarak ${same.label} ile aynı seviyede.`
    const lower = [...RISK_PRESETS].reverse().find(item => item.risk_percent < draft.risk)
    const upper = RISK_PRESETS.find(item => item.risk_percent > draft.risk)
    return lower && upper ? `Senin ayarın ${lower.label} ile ${upper.label} arasında.` : null
  })()
  const rkSimRows = (rkCustom || rkPreset?.id !== rkRec.id) ? [
    {label: rkRec.label, risk: rkRec.risk_percent},
    {label: rkCustom ? 'Senin ayarın' : rkPreset!.label, risk: draft.risk},
  ] : []
  const rkSimLoss = streakLoss(rkEffective(draft.risk))
  const riskStepView = <div className="lcRk" data-testid="launch-risk-step">
    {wallet !== null ? <p className="lcRkWallet"><span>{`Bakiyen ${money(wallet)} USDT`}</span><small>rakamlar buna göre</small></p>
      : <p className="lcHint warn">Bakiye doğrulanınca her seçeneğin USDT karşılığı burada görünür.</p>}
    <div className={rkCustom ? 'lcRkMini' : 'lcRkList'} role="radiogroup" aria-label="Risk paketi">
      {RISK_PRESETS.map(preset => {
        const selected = !rkCustom && rkPreset?.id === preset.id
        const nums = rkNums(preset.risk_percent, preset.daily_loss_percent)
        return <button type="button" role="radio" aria-checked={selected} key={preset.id} className="lcRkOpt" data-id={preset.id} data-tone={riskToneOf(preset.risk_percent)} data-selected={selected || undefined} disabled={busy} onClick={() => choosePreset(preset.id)}>
          <span className="lcRkName"><b>{preset.label}</b>{preset.id === rkRec.id && <em>ÖNERİLEN</em>}</span>
          {rkCustom ? <small className="lcRkMeta">{nums ? `${pct(preset.risk_percent)} · ${money(nums.perTrade)} USDT` : pct(preset.risk_percent)}</small> : <>
            {rkAmount(preset.risk_percent, preset.daily_loss_percent)}
            <small className="lcRkMeta">{nums ? `Günde en çok ${money(nums.perDay)} USDT · ${preset.symbol_count} coin · ${preset.session_hours} saat` : `Günlük ${pct(preset.daily_loss_percent)} · ${preset.symbol_count} coin · ${preset.session_hours} saat`}</small>
            <i className="lcRkMeter" aria-hidden="true"><s style={{width: `${Math.max(4, preset.risk_percent / 15 * 100)}%`}}/></i>
          </>}
        </button>
      })}
    </div>
    <button type="button" role="radio" aria-checked={rkCustom} className="lcRkOpt lcRkCustom" data-testid="launch-risk-custom" data-selected={rkCustom || undefined} disabled={busy} onClick={() => setWizCustom(true)}>
      <i className="lcRkIco" aria-hidden="true"><SlidersHorizontal/></i>
      <span className="lcRkName lcRkStack"><b>Kendim ayarlarım</b><small>{rkCustom ? 'Senin ayarın' : 'Risk, coin sayısı ve süreyi sen seç'}</small></span>
      {rkCustom ? rkAmount(draft.risk, draft.risk) : <ChevronRight className="lcRkGo" aria-hidden="true"/>}
    </button>
    {rkCustom && <div className="lcRkPanel" data-testid="launch-risk-panel">
      <div className="lcRkField">
        <div className="lcRkHead"><span>İşlem başı risk</span><b>{pct(draft.risk)}</b></div>
        <input type="range" className="lcRkRange" min={0} max={RISK_STEPS.length - 1} step={1} value={rkIndex} aria-label="İşlem başı risk" aria-valuetext={pct(draft.risk)} disabled={busy} data-testid="launch-risk-range" onChange={event => setWizRisk(RISK_STEPS[Number(event.target.value)])}/>
        <div className="lcRkTicks" aria-hidden="true">{RISK_TICKS.map(tick => <span key={tick} style={{left: `${RISK_STEPS.indexOf(tick) / (RISK_STEPS.length - 1) * 100}%`}}>{pct(tick)}</span>)}</div>
      </div>
      <div className="lcRkField">
        <div className="lcRkHead"><span>Taranacak coin sayısı</span><b>{Number.isFinite(draft.coins) ? draft.coins : '—'}</b></div>
        <div className="lcRkStep">
          <button type="button" aria-label="Coin sayısını azalt" disabled={busy || running || draft.coins <= 10} onClick={() => setStartParam('coins', Math.max(10, Math.ceil(draft.coins / 5) * 5 - 5))}>−</button>
          <input className="lcRkNum" type="number" inputMode="numeric" min={10} max={100} step={5} value={Number.isFinite(draft.coins) ? draft.coins : ''} aria-label="Taranacak coin sayısı" disabled={busy || running} data-testid="launch-risk-coins" onChange={event => setStartParam('coins', Math.round(Number(event.target.value)))}/>
          <button type="button" aria-label="Coin sayısını artır" disabled={busy || running || draft.coins >= 100} onClick={() => setStartParam('coins', Math.min(100, Math.floor(draft.coins / 5) * 5 + 5))}>+</button>
        </div>
        <div className="lcRkEnds" aria-hidden="true"><span>en az 10</span><span>en çok 100</span></div>
        {!validCoins(draft.coins) && <p className="lcHint stop" role="alert">Coin sayısı 10 ile 100 arasında olmalı.</p>}
      </div>
      <div className="lcRkField">
        <div className="lcRkHead"><span>Oturum süresi</span><b>{`${draft.hours} saat`}</b></div>
        <div className="lcRkSeg" role="group" aria-label="Oturum süresi">{[1, 4, 12, 24].map(value => <button type="button" key={value} aria-pressed={draft.hours === value} disabled={busy || running} onClick={() => setStartParam('hours', value)}>{`${value} saat`}</button>)}</div>
      </div>
      {rkBetween && <p className="lcRkNear">{rkBetween}</p>}
      {running && <p className="lcHint">Çalışırken coin sayısı ve süre bir sonraki başlatmada geçerli olur.</p>}
    </div>}
    {!rkCustom && rkPreset?.id === rkRec.id && <div className="lcRkWhy">
      <b>{`Neden ${rkRec.label}?`}</b>
      <ul>
        <li>{(() => { const nums = rkNums(rkRec.risk_percent, rkRec.daily_loss_percent); return nums ? `Bir kayıpta en çok ${money(nums.perTrade)} USDT gider; bakiyeni hızlı eritmez.` : `İşlem başı ${pct(rkRec.risk_percent)} risk bakiyeni hızlı eritmez.` })()}</li>
        <li>{`Günlük sınır ${pct(rkRec.daily_loss_percent)}: üst üste ${Math.round(rkRec.daily_loss_percent / rkRec.risk_percent)} kayıpta bot o gün durur.`}</li>
      </ul>
    </div>}
    {rkSimRows.length > 0 && <div className="lcRkSim" data-tone={riskToneOf(draft.risk)} data-testid="launch-risk-streak">
      <div className="lcRkSimHead"><b>Kötü seri örneği</b><small>üst üste 5 kayıp</small></div>
      {rkSimRows.map(row => { const loss = streakLoss(rkEffective(row.risk)); return <div className="lcRkSimRow" key={row.label} data-tone={riskToneOf(row.risk)}>
        <span>{row.label}</span><i aria-hidden="true"><s style={{width: `${Math.max(3, loss * 100)}%`}}/></i><b>{`−%${Math.round(loss * 100)}`}</b>
      </div> })}
      {wallet !== null && <p>{`5 kötü işlemde ${money(wallet)} USDT → yaklaşık ${money(wallet * (1 - rkSimLoss))} USDT.`}</p>}
    </div>}
    {draft.risk > 2 && draft.risk <= 5 && <label className="lcCheck lcRkAck"><input type="checkbox" checked={riskAck} onChange={event => setRiskAck(event.target.checked)}/><span>%{draft.risk} işlem riskinin yüksek olduğunu anladım.</span></label>}
    {draft.risk > 5 && <label className="lcField lcRkPhrase">Onay için <b className="selectablePhrase" translate="no">{HIGH_RISK_PHRASE}</b> yaz<input value={phrase} autoComplete="off" onChange={event => setPhrase(event.target.value)}/>
      <small className="lcRkCount" aria-live="polite">{phrase === HIGH_RISK_PHRASE ? '✓' : `${[...phrase].filter((char, index) => char === [...HIGH_RISK_PHRASE][index]).length}/${[...HIGH_RISK_PHRASE].length}`}</small></label>}
  </div>
  const editorOf = (id: LaunchStepId) => <>
    {id === 'risk' && riskStepView}
    {id === 'daily' && <><div className="lcWizChips" role="radiogroup" aria-label="Günlük kayıp sınırı">{DAILY_CHOICES.filter(value => value >= draft.risk).map(value => {
      const recommended = value === (RISK_PRESETS.find(item => item.risk_percent === draft.risk)?.daily_loss_percent ?? -1)
      return <button type="button" role="radio" aria-checked={draft.daily === value} key={value} className="lcWizChip" data-selected={draft.daily === value || undefined} data-rec={recommended || undefined} disabled={busy} onClick={() => { setDailyText(null); setDraft(current => ({...current, daily: value})) }}><b>%{value}{recommended && <em>ÖNERİLEN</em>}</b><small>{wallet !== null ? usdt(riskSummary(wallet, draft.risk, value, caps)?.perDay ?? 0) : ''}</small></button>
    })}</div>
      <label className="lcField lcDailyOwn"><span>Ya da kendin yaz <em>%1 – %25</em></span>
        <span className="lcDailyBox"><b aria-hidden="true">%</b><input type="text" inputMode="decimal" autoComplete="off" data-testid="launch-daily-own" placeholder={String(draft.daily)} value={dailyText ?? ''} disabled={busy}
          onChange={event => { const text = event.target.value; setDailyText(text); const value = Number(text.replace(',', '.')); if (text.trim() !== '' && Number.isFinite(value)) setDraft(current => ({...current, daily: value})) }}/></span>
      </label>
      {dailyText !== null && dailyText.trim() !== '' && !wizDailyOk && <p className="lcHint stop">Sınır %1 ile %25 arasında olmalı ve işlem riskinden (%{draft.risk}) küçük olmamalı.</p>}
    </>}
    {id === 'mode' && <>
      <div className="lcWizChips lcWizModes" role="radiogroup" aria-label="Teminat modu">{modeRows.map(item => {
        const numbers = modeNumbers(item.per_trade_percent)
        return <button type="button" role="radio" aria-checked={chosenMode === item.mode} key={item.mode} className="lcWizChip lcWizMode" data-mode={item.mode} data-selected={chosenMode === item.mode || undefined} data-rec={item.mode === recModeId || undefined} disabled={busy} onClick={() => { setCustomOn(false); pickMode(item.mode) }}>
          <b>{item.label}{item.mode === recModeId && <em>ÖNERİLEN</em>}</b>
          <small>%{item.per_trade_percent} teminat / işlem</small>
          <span className="lcMeter" aria-hidden="true"><i style={{width: `${Math.min(100, item.per_trade_percent)}%`}}/></span>
          {numbers && <span className="lcWizModeFacts"><span>Pozisyon en çok <b>{usdt(numbers.position)}</b></span><span>Stop %1'de risk ≈ <b>{pct(numbers.riskPercent)}</b></span></span>}
        </button>
      })}</div>
      {wallet !== null && <p className="lcHint">Rakamlar tahmindir: {modeLeverage}x kaldıraç ve stop fiyattan %1 uzakta varsayılır. Stop uzaklaştıkça risk seçtiğin %{draft.risk}'e yaklaşır, asla geçmez.</p>}
      {chosenMode !== savedMode && chosenMode !== 'guvenli' && <label className="lcCheck"><input type="checkbox" checked={modeAck} onChange={event => setModeAck(event.target.checked)}/><span>Bu modda her işleme daha çok teminat ayrılacağını ve zararın büyüyebileceğini anladım.</span></label>}
      {chosenMode !== savedMode && chosenMode === 'agresif' && <label className="lcField">Onay için şunu yaz: <code translate="no">{AGGRESSIVE_PHRASE}</code><input type="text" value={modePhrase} autoComplete="off" onChange={event => setModePhrase(event.target.value)}/></label>}
    </>}
    {id === 'smart' && smartSettings !== undefined && <>
      <div className="lcSmartState" data-on={smartCurrentlyOn || undefined}>
        <i aria-hidden="true"/>
        <span><b>Şu an {smartCurrentlyOn ? 'AÇIK' : 'KAPALI'}</b><small>{smartCurrentlyOn ? `Puan eşiği ${smartSettings?.threshold ?? '—'} · bekleme ${smartSettings?.wait_minutes ?? '—'} dk` : 'Hiçbir akıllı emir gönderilmiyor'}</small></span>
      </div>
      <ol className="lcFlow" aria-label="Akıllı koruma nasıl çalışır">
        <li><i>1</i><b>Puan düşer</b><small>Eşiğin altına iner</small></li>
        <li><i>2</i><b>Bekler</b><small>{smartCurrentlyOn && smartPick !== 'on' ? smartSettings?.wait_minutes ?? smartWizWait : smartWizWait} dk</small></li>
        <li><i>3</i><b>Yeni mum</b><small>Hâlâ düşük mü?</small></li>
        <li><i>4</i><b>Kârdaysa çıkar</b><small>Zarardaysa kapatmaz</small></li>
      </ol>
      <div className="lcWizList" role="radiogroup" aria-label="Akıllı koruma">
        {([['keep', smartCurrentlyOn ? 'Açık kalsın' : 'Kapalı kalsın', smartCurrentlyOn ? 'Mevcut eşik ve bekleme süresi korunur.' : 'Pozisyonları yalnız orijinal Stop ve TP yönetir.'],
           ...(smartCurrentlyOn ? [['off', 'Kapat', 'Geçici stoplar kaldırılır; orijinal Stop ve TP aynen durur.']] : [['on', 'Aç', 'Kârdaki pozisyonu erken kapatabilir; gerçek Binance emri gönderir.']])] as string[][]).map(([optionId, title, note]) =>
          <button type="button" role="radio" aria-checked={smartPick === optionId} key={optionId} className="lcWizOption lcSmartOption" data-kind={optionId} data-selected={smartPick === optionId || undefined} disabled={busy} onClick={() => setSmartPick(optionId as 'keep' | 'on' | 'off')}>
            <span className="lcWizHead"><b>{title}</b>{optionId === 'keep' && <em>Değişiklik yok</em>}</span><small>{note}</small>
          </button>)}
      </div>
      {smartAdvanced}
      {smartPick === 'on' && <div className="lcSmartPick">
        <label>Puan eşiği<select value={smartWizThreshold} disabled={busy} onChange={event => setSmartWizThreshold(Number(event.target.value))}>{[60, 65, 70, 75, 80].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Bekleme süresi<select value={smartWizWait} disabled={busy} onChange={event => setSmartWizWait(Number(event.target.value))}>{[5, 10, 15, 20, 30].map(value => <option key={value} value={value}>{value} dk</option>)}</select></label>
        <label className="lcCheck"><input type="checkbox" checked={smartWizAck} onChange={event => setSmartWizAck(event.target.checked)}/><span>Akıllı koruma açıkken gerçek Binance emri gönderebileceğini anlıyorum.</span></label>
      </div>}
    </>}
  </>
  const adviceView = (id: LaunchStepId | 'final') => {
    const items = launchAdvice(id)
    const dayCard = id === 'daily' && dayData && dayUsedPct !== null && dayLimitNew !== null
    return <>
      {dayCard && <div className="lcWzGauge" data-tone={dayData?.locked || dayUsedPct >= 100 ? 'bad' : dayUsedPct >= 50 ? 'warn' : 'ok'} data-testid="launch-day-gauge">
        <b>%{dayUsedPct.toLocaleString('tr-TR', {maximumFractionDigits: 1})}</b>
        <span>Günlük kayıp limitinin kullanılan kısmı</span>
        <i style={{'--p': `${Math.min(100, dayUsedPct)}%`} as CSSProperties}/>
        <dl>
          <div><dt>Gerçekleşen zarar</dt><dd>{dayLossRealized !== null ? usdt(-dayLossRealized) : '—'}</dd></div>
          <div><dt>Açık pozisyon dahil toplam</dt><dd>{usdt(dayLossTotal !== null ? -dayLossTotal : null)}</dd></div>
          <div><dt>Yeni günlük limit</dt><dd>{usdt(dayLimitNew)}</dd></div>
        </dl>
        <small>Yeni işlem bütçesi gerçekleşen zarara, günlük kilit ise açık pozisyonun anlık zararı dahil toplama bakar.</small>
      </div>}
      {items.length > 0 && <ul className="lcWzAdvice">{items.map((item, index) => <li key={index} data-tone={item.tone}><i aria-hidden="true">{item.tone === 'ok' ? <Check/> : item.tone === 'info' ? 'i' : '!'}</i><span>{item.text}</span></li>)}</ul>}
    </>
  }
  const finalRows: Array<[string, string]> = [
    ['İşlem başı risk', wizRiskNums ? `${usdt(wizRiskNums.perTrade)} (%${draft.risk})` : `%${draft.risk}`],
    ['Günlük en çok kayıp', wizRiskNums ? `${usdt(wizRiskNums.perDay)} (%${draft.daily})` : `%${draft.daily}`],
    ['Teminat modu', `${chosenRow?.label ?? '—'} (%${chosenRow?.per_trade_percent ?? '—'})`],
    ['Akıllı koruma', smartPick === 'on' ? 'AÇILACAK' : smartPick === 'off' ? 'KAPATILACAK' : smartCurrentlyOn ? 'Açık' : 'Kapalı'],
    ['Süre · coin', `${draft.hours} saat · ${draft.coins} coin`],
  ]
  const opRows: Array<{key: string; title: string; note: string}> = [
    {key: 'risk', title: 'Ayarları kaydet', note: 'Risk + günlük sınır, sunucudan geri okunur'},
    {key: 'mode', title: 'Teminat modunu kaydet', note: 'Sunucudan geri okunur'},
    {key: 'smart', title: 'Akıllı koruma ayarı', note: 'Sunucudan geri okunur'},
    {key: 'consent', title: 'Risk iznini ver', note: `${draft.hours} saat`},
    {key: 'policy', title: 'Risk limitleri onayı', note: 'Sunucudan geri okunur'},
    {key: 'arm', title: 'ARM: canlı emir izni', note: 'Botu başlatmaz'},
    {key: 'start', title: 'Botu başlat', note: 'ARM yoksa başlamaz'},
  ]
  const launchView = <div className="lcWz" data-testid="launch-wizard">
    <header className="lcWzHead">
      <div className="lcWzRing" role="img" aria-label={`İlerleme: ${launchDoneCount} / ${launchTotal}`}>
        <svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r={ringR} className="lcWzRingBg"/><circle cx="32" cy="32" r={ringR} className="lcWzRingFg" style={{strokeDasharray: ringC, strokeDashoffset: ringC * (1 - launchDoneCount / launchTotal)}}/></svg>
        <b>{launchDoneCount}/{launchTotal}</b>
      </div>
      <div>
        <h2 id="lc-sheet-title">{dt('Canlıya hazırlık')}</h2>
        <p className="lcSheetLead">{launchActive === 'final' ? 'Son adım: güvenlik onayı' : `Adım ${activeIndex} / ${launchTotal} · ${activeMeta?.title}`}</p>
      </div>
    </header>
    <div className="lcMobileOnly lcWzSeg" aria-hidden="true"><span><i style={{width: `${Math.round(launchDoneCount / launchTotal * 100)}%`}}/></span><small>{`${Math.min(activeIndex, launchTotal)} / ${launchTotal}`}</small></div>
    <div className="lcWzBody">
      <ol className="lcWzRail" aria-label="Adımlar">
        {LAUNCH_STEPS.map((item, index) => {
          const approved = launchApproved(item.id), active = launchActive === item.id
          const op = item.op ? launchOpOf(item.op) : undefined
          const state = op?.state === 'error' ? 'error' : approved ? 'done' : active ? 'active' : 'locked'
          return <li key={item.id} data-state={state}>
            <i className="lcWzDot" aria-hidden="true">{state === 'done' ? <Check/> : state === 'error' ? '!' : index + 1}</i>
            <span><b>{dt(item.title)}</b>{approved && <small>{launchNote(item.id)}</small>}</span>
            {approved && item.id !== 'conn' && !launchRunning && <button type="button" className="lcLink" onClick={() => launchUndo(item.id)} aria-label={`${item.title} onayını geri al`}>Geri al</button>}
          </li>
        })}
        <li data-state={launchActive === 'final' ? 'active' : 'locked'}><i className="lcWzDot" aria-hidden="true">{LAUNCH_STEPS.length + 1}</i><span><b>Güvenlik onayı</b></span></li>
      </ol>
      <div className="lcWzMain">
        {activeMeta ? <section className="lcWzCard" key={activeMeta.id} data-testid={`launch-step-${activeMeta.id}`}>
          <small className="lcWzKicker">ADIM {activeIndex}</small>
          <h3>{activeMeta.title}</h3>
          {activeMeta.id === 'conn'
            ? <p className="lcWzWhy">{launchWhy.conn} Bağlantı yok; önce Bağlantı sekmesinden Binance anahtarını kaydet.</p>
            : <>
              {activeMeta.id !== 'risk' && <details className="lcWzWhyD"><summary>Neden bu öneri?</summary><p className="lcWzWhy">{launchWhy[activeMeta.id]}</p></details>}
              {editorOf(activeMeta.id)}
              {launchRec(activeMeta.id) && <p className="lcWzRec">Önerilen: <b>{launchRec(activeMeta.id)}</b></p>}
              {adviceView(activeMeta.id)}
              {stateChips(activeMeta.id)}
            </>}
          {activeMeta.id === 'conn'
            ? <button type="button" className="lcBtn lcPrimary lcWzGo" onClick={() => { closeSheet(); goTab('baglanti') }}>Bağlantı'ya git</button>
            : <>
              {activeIndex > 2 && !launchRunning && <button type="button" className="lcMobileOnly lcWzBack" onClick={() => launchUndo(LAUNCH_STEPS[activeIndex - 2].id)}>← Geri</button>}
              {!launchValid[activeMeta.id] && activeMeta.id !== 'risk' && <p className="lcHint stop">{activeMeta.id === 'smart' ? 'Açmak için onay kutusunu işaretle.' : activeMeta.id === 'daily' ? 'Sınır %1–%25 arası ve işlem riskinden küçük olmamalı.' : 'Önce gerekli onay kutusunu / yazıyı tamamla.'}</p>}
              <button type="button" className="lcBtn lcPrimary lcWzGo" data-testid={`launch-approve-${activeMeta.id}`} disabled={!launchValid[activeMeta.id] || busy} onClick={() => launchApprove(activeMeta.id)}>{activeMeta.id !== 'risk' ? 'Onaylıyorum ✓' : rkCustom ? 'Ayarımı onayla ✓' : PRESET_APPROVE[rkPreset?.id ?? ''] ?? 'Onaylıyorum ✓'}</button>
              {activeMeta.id === 'risk' && !launchValid.risk && launchRiskValid && <p className="lcHint lcRkWait">{draft.risk > 5 ? 'Yazıyı tamamlayınca açılır.' : 'Onay kutusunu işaretleyince açılır.'}</p>}
              {activeMeta.id === 'risk' && rkCustom && <p className="lcHint lcRkWait">Günlük kayıp sınırını bir sonraki adımda seçeceksin.</p>}
            </>}
        </section> : <section className="lcWzCard lcWzFinal" data-testid="launch-final">
          <small className="lcWzKicker">SON ADIM · GÜVENLİK PANELİ</small>
          <h3>Her şey senin onayınla hazır</h3>
          <dl className="lcWzSum">{finalRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          {adviceView('final')}
          <ol className="lcWzOps" aria-label="İşlem sonuçları">
            {opRows.map(row => {
              const op = launchOpOf(row.key)
              const state = op?.state ?? 'idle'
              return <li key={row.key} data-state={state}>
                <i className="lcWzDot" aria-hidden="true">{state === 'done' ? <Check/> : state === 'working' ? <RefreshCw className="lcSpin"/> : state === 'error' ? '!' : null}</i>
                <span><b>{row.title}</b><small>{op?.state === 'error' ? op.error : state === 'done' ? 'Tamamlandı ve doğrulandı' : state === 'working' ? 'Çalışıyor…' : row.note}</small></span>
              </li>
            })}
          </ol>
          <label className="lcField lcLaunchConfirm">Onay için <b className="selectablePhrase" translate="no">ONAYLIYORUM</b> yaz
            <input value={launchPhrase} autoComplete="off" autoCapitalize="characters" spellCheck={false} disabled={busy} data-ok={launchPhraseOk || undefined} onChange={event => setLaunchPhrase(event.target.value)} placeholder="ONAYLIYORUM"/>
          </label>
          <p className="lcHint">Yazınca işlemler sırayla çalışır. Biri başarısız olursa orada durur, bot başlamaz; tamamlananlar tekrar edilmez. Açık pozisyonlar ve orijinal Stop/TP değişmez. Gerçek parayla işlem açılabilir.</p>
          <button type="button" className="lcBtn lcPrimary lcWzGo" data-testid="launch-run" disabled={!launchCanRun} onClick={() => void runLaunch()}>{launchRunning ? 'Çalışıyor…' : launchFailed ? 'Tekrar dene' : 'Onayla ve başlat'}</button>
          {!launchRunning && <button type="button" className="lcLink" onClick={() => launchUndo('arm')}>Son onayı geri al</button>}
        </section>}
      </div>
    </div>
  </div>
  const summaryRisk = wallet !== null ? riskSummary(wallet, savedRisk, savedDaily, caps) : null
  const summaryView = <>
    <h2 id="lc-sheet-title">Ayarlarım</h2>
    <p className="lcSheetLead">Şu an kayıtlı olan tüm ayarlar tek yerde. Değiştirmek için rehberi aç.</p>
    <dl className="lcWizSummary lcSumGrid">
      <div><dt>İşlem başı risk</dt><dd>{summaryRisk ? usdt(summaryRisk.perTrade) : '—'} <small>(%{savedRisk})</small></dd></div>
      <div><dt>Günlük kayıp sınırı</dt><dd>{summaryRisk ? usdt(summaryRisk.perDay) : '—'} <small>(%{savedDaily})</small></dd></div>
      <div><dt>Teminat modu</dt><dd>{savedRow?.label ?? '—'} <small>(%{savedRow?.per_trade_percent ?? '—'})</small></dd></div>
      <div><dt>Akıllı koruma</dt><dd>{smartSettings ? (smartSettings.emergency_off ? 'ACİL DURDURULDU' : smartSettings.enabled ? 'AÇIK' : 'KAPALI') : '—'}</dd></div>
      {smartSettings && <div><dt>Puan eşiği · bekleme</dt><dd>{smartSettings.threshold} · {smartSettings.wait_minutes} dk</dd></div>}
      {smartSettings && <div><dt>Kâr koruma stopu · akıllı çıkış</dt><dd>{smartSettings.protect_stop_enabled ? 'açık' : 'kapalı'} · {smartSettings.smart_exit_enabled ? 'açık' : 'kapalı'}</dd></div>}
      {smartSettings && <div><dt>Ters yön sinyali</dt><dd>{smartSettings.direction_mismatch_action === 'count_as_low' ? 'Düşük puan gibi say' : 'Sadece kaydet'}</dd></div>}
    </dl>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet}>Kapat</button><button type="button" className="lcBtn lcPrimary" onClick={openWizard}>Rehberle değiştir</button></div>
  </>
  const sheetView = sheet === 'launch' ? launchView : sheet === 'summary' ? summaryView : sheet === 'wizard' ? wizardView : sheet === 'approvals' ? <>
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
      <label className="lcCheck"><input type="checkbox" checked={approvalAccepted} onChange={event => setApprovalAccepted(event.target.checked)}/><span>{dt('Yukarıdakileri okudum; gerçek parayla işlem riskini kabul ediyorum.')}</span></label>
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
    {savedRisk > 5 && <><label className="lcField">Onay için <b className="selectablePhrase" translate="no">{HIGH_RISK_PHRASE}</b> yaz<input value={startPhrase} autoComplete="off" onChange={event => setStartPhrase(event.target.value)}/></label></>}
    <label className="lcCheck"><input type="checkbox" checked={startUnderstood} onChange={event => setStartUnderstood(event.target.checked)}/><span>{dt('Bu tutarları gördüm; gerçek parayla işlem açılacağını anladım.')}</span></label>
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
      <li data-tone="mute"><Power aria-hidden="true"/><span>{dt('Canlı emir izni (ARM) kapanır. Yeniden başlatmak istediğinde rehber seni tek adımla geri götürür.')}</span></li>
    </ul>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcDanger" disabled={Boolean(working)} onClick={() => void stop()}>{working === 'stop' ? 'Durduruluyor…' : 'Yeni işlemleri durdur'}</button></div>
    {positions.length > 0 && <button type="button" className="lcLink lcCloseAll" onClick={() => setSheet('close-all')} disabled={Boolean(working)}>Pozisyonları da hemen kapatmam gerekiyor →</button>}
  </> : sheet === 'close-all' ? <>
    <h2 id="lc-sheet-title">Acil durdurma · pozisyonları kapat</h2>
    <p className="lcSheetLead">Bota ait giriş ve koruma emirleri iptal edilir, doğrulanmış bot pozisyonları <b>piyasa fiyatından reduce-only</b> emirle kapatılır. Kayıp kesinleşebilir. Sonrasında yeniden başlatmak 2FA ister.</p>
    <label className="lcField">Onay için <b className="selectablePhrase" translate="no">{CLOSE_ALL_PHRASE}</b> yaz<input value={closePhrase} autoComplete="off" onChange={event => setClosePhrase(event.target.value)}/></label>
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
  </> : sheet === 'edit-protection' && manualTarget ? <>
    <h2 id="lc-sheet-title">{manualTarget.symbol.replace('USDT', '')} · stop ve hedefler</h2>
    <p className="lcSheetLead">{manualTarget.direction} · giriş {manualTarget.entry} · şu anki fiyat {manualTarget.mark}. Yalnız değiştirdiğin alanlar gönderilir.</p>
    <div className="lcPosEdit">
      <label className="lcField">Stop<input type="text" inputMode="decimal" autoComplete="off" value={editLevels.stop} onChange={event => setEditLevels(current => ({...current, stop: event.target.value.replace(/[^0-9.,]/g, '')}))}/></label>
      <label className="lcField">TP1<input type="text" inputMode="decimal" autoComplete="off" value={editLevels.tp1} onChange={event => setEditLevels(current => ({...current, tp1: event.target.value.replace(/[^0-9.,]/g, '')}))}/></label>
      <label className="lcField">TP2<input type="text" inputMode="decimal" autoComplete="off" value={editLevels.tp2} onChange={event => setEditLevels(current => ({...current, tp2: event.target.value.replace(/[^0-9.,]/g, '')}))}/></label>
      <label className="lcField">TP3<input type="text" inputMode="decimal" autoComplete="off" value={editLevels.tp3} onChange={event => setEditLevels(current => ({...current, tp3: event.target.value.replace(/[^0-9.,]/g, '')}))}/></label>
    </div>
    <ul className="lcStopList">
      <li data-tone="mute"><ShieldCheck aria-hidden="true"/><span>Yeni stop Binance'te doğrulanır. Kurulamazsa eski stop geri konur; o da olmazsa pozisyon güvenlik için kapatılır. Pozisyon stopsuz bırakılmaz.</span></li>
      <li data-tone="mute"><Check aria-hidden="true"/><span>Stopu uzaklaştırıp zararı işlem başı risk sınırının üstüne çıkaramazsın. Gerçekleşmiş hedef değiştirilemez.</span></li>
    </ul>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcPrimary" disabled={Boolean(working)} onClick={() => void saveProtection()}>{working === 'edit-protection' ? 'Gönderiliyor…' : 'Kaydet'}</button></div>
  </> : sheet === 'reduce-position' && manualTarget ? <>
    <h2 id="lc-sheet-title">{manualTarget.symbol.replace('USDT', '')} · kısmi kapat</h2>
    <p className="lcSheetLead">{manualTarget.direction} · giriş {manualTarget.entry}. Seçtiğin pay <b>piyasa fiyatından, reduce-only</b> emirle kapanır; kalan kısım stopla korunmaya devam eder.</p>
    <div className="lcSegWrap" role="group" aria-label="Kapatılacak pay"><span>Kapatılacak pay</span>
      <div className="lcSeg lcSeg3">{([25, 50, 75] as const).map(value => <button type="button" key={value} aria-pressed={reducePct === value} disabled={Boolean(working)} onClick={() => setReducePct(value)}>%{value}</button>)}</div></div>
    <ul className="lcStopList">
      <li data-tone="mute"><ShieldCheck aria-hidden="true"/><span>Açık TP1/TP2 emirleri kalan pozisyona göre küçültülür. Kalan kısım Binance alt sınırının altında kalacaksa işlem reddedilir; o durumda tamamını kapat.</span></li>
    </ul>
    <div className="lcSheetActions"><button type="button" className="lcBtn" onClick={closeSheet} disabled={Boolean(working)}>Vazgeç</button>
      <button type="button" className="lcBtn lcDanger" disabled={Boolean(working)} onClick={() => void reducePosition()}>{working === 'reduce-position' ? 'Gönderiliyor…' : `Evet, %${reducePct} kapat`}</button></div>
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
  // Mobil: kurulum bitti, başlatmaya hazır → büyük rehber kartı yerine durum kartında tek düğme.
  const readyToStart = setupPhase && guide.action === 'start'
  const modeLabelNow = original?.margin_modes?.find(row => row.mode === savedMode)
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
  // ---- Kais Koç sayacı: tamamı gösterim; veriler yukarıdaki sayaçlardan gelir, hiçbir şey göndermez.
  const pad2 = (n: number) => String(n).padStart(2, '0')
  const mmss = (t: number) => `${pad2(Math.floor(t / 60))}:${pad2(t % 60)}`
  const hms = (t: number) => `${pad2(Math.floor(t / 3600))}:${pad2(Math.floor((t % 3600) / 60))}:${pad2(t % 60)}`
  if (sessionLeft > sessionTotal.current) sessionTotal.current = sessionLeft
  const lastScanAgo = Number.isFinite(lastScanMs) ? (now - lastScanMs) / 1000 : Infinity
  const koScanning = scanner?.stage === 'SCANNING'
  const koJustDone = !koScanning && lastScanAgo >= 0 && lastScanAgo < 20
  const koSession = koView === 'session' && !koScanning && !koJustDone
  const koPhase = koScanning ? 'scan' : koJustDone ? 'done' : 'wait'
  const koWait = upcoming ? Math.max(0, Math.round(upcoming.inSeconds)) : 0
  const koCands = Number(scanner?.candidate_count) || 0
  const koFrac = koScanning || koJustDone ? 1 : koSession ? (sessionTotal.current > 0 ? sessionLeft / sessionTotal.current : 0) : Math.min(1, koWait / 900)
  const koDeg = Math.round(Math.max(0, Math.min(1, koFrac)) * 360)
  const koTicks = Math.round(Math.max(0, Math.min(1, koFrac)) * 60)
  const koLabel = koScanning ? 'TARANIYOR' : koJustDone ? 'TARAMA BİTTİ' : koSession ? 'OTURUM' : 'SONRAKİ TARAMA'
  const koBig = koScanning ? 'Taranıyor' : koJustDone ? (koCands > 0 ? `${koCands} aday` : '0 aday') : koSession ? hms(sessionLeft) : upcoming ? mmss(koWait) : '—'
  const koSize = koScanning ? 'word' : koJustDone ? 'word' : koSession ? 'hms' : 'time'
  const koSub = koScanning ? 'coinler kontrol ediliyor' : koJustDone ? (koCands > 0 ? 'kurallara uyan fırsat var' : 'şimdilik fırsat yok') : koSession ? 'sayaca dokun: tarama' : Number.isFinite(lastScanMs) ? `son tarama ${clock(scanner?.last_scan_at)}` : 'ilk tarama bekleniyor'
  const koSmart = koScanning ? 'Kurallara uyan fırsat aranıyor' : koJustDone ? (koCands > 0 ? 'Fırsat bulundu · stop ve hedefle emir açılır' : 'Fırsat yok · sıradaki mumu bekliyor') : koWait > 300 ? 'Sakin bekleme · mum kapanışı bekleniyor' : koWait > 60 ? 'Mum kapanışına az kaldı' : 'Tarama yaklaşıyor'
  const koUrgent = koPhase === 'wait' && !koSession && upcoming !== null && koWait <= 60
  const koSessionPct = sessionTotal.current > 0 ? Math.min(100, Math.round(sessionLeft / sessionTotal.current * 100)) : 0
  const minMargin = finite(caps?.min_margin_usdt)
  const coachChecks: {ok: boolean; text: string}[] = coach ? [
    {ok: Boolean(guideInput.connected), text: guideInput.connected ? 'Binance bağlı' : 'Binance bağlantısı yok'},
    {ok: availableBalance !== null && (minMargin === null || availableBalance >= minMargin), text: availableBalance === null ? 'Bakiye okunuyor' : minMargin !== null && availableBalance < minMargin ? `Bakiye düşük · ${usdt(availableBalance)}` : `Bakiye yeterli · ${usdt(availableBalance)}`},
    {ok: !guideInput.dailyLocked, text: guideInput.dailyLocked ? 'Günlük kayıp sınırı doldu' : dailyLoss !== null && dailyLimit !== null ? `Günlük kayıp ${usdt(dailyLoss)} / ${usdt(dailyLimit)}` : 'Günlük sınır açık'},
    {ok: sessionLeft <= 0 || sessionLeft > 3600, text: sessionLeft > 0 ? sessionLeft <= 3600 ? `Oturum ${duration(sessionLeft)} sonra bitiyor` : `Oturum ${duration(sessionLeft)} kaldı` : 'Oturum süresi okunuyor'},
  ] : []
  const coachNote = coach && running ? [why, breakdown?.headline ? `Son 24 saat: ${breakdown.headline}` : null].filter(Boolean).join(' ') : ''

  return <section className="lcRoot originalLiveWorkspace" aria-label={dt('Canlı İşlem')} data-demo={demo || undefined} data-phase={guide.phase} data-stale={stale || undefined} data-manual-modal={manualModal || undefined}>
    <SpotHint id="launch-start" active={readyToStart && !manualModal} targets={LAUNCH_TARGETS} title="Buradan başla." text="Bu buton risk, günlük sınır ve izinleri senin için adım adım hazırlar. Her adımı sen onaylarsın."/>
    {manualModal && <><div className="lcManualBackdrop" role="presentation" onClick={closeManual}/><button type="button" className="lcManualClose" aria-label="Manuel işlem panelini kapat" onClick={closeManual}><X aria-hidden="true"/></button></>}
    <header className="lcStatusBar" data-tone={barState.tone} data-ready={readyToStart || undefined}>
      <div className="lcStatusMain"><div className="lcMobileOnly lcMPills"><span className="lcMPill" data-live={running || undefined}><i aria-hidden="true"/>{dt('CANLI İŞLEM')}</span><span className="lcMPill">KAİS LONG</span></div>
        <span className="lcStatusDot" aria-hidden="true"/><div className="lcStatusText"><small className="lcDeskKicker">{dt('CANLI İŞLEM')} · KAİS LONG</small><b>{barState.label}</b><small className="lcMobileOnly lcMUpd"><Clock aria-hidden="true"/>Son güncelleme <em>{lastGood.current ? new Date(lastGood.current.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : '—'}</em></small></div>
        <div className="lcMobileOnly lcMore">
          <button type="button" className="lcMoreBtn" aria-label="Diğer işlemler" aria-haspopup="menu" aria-expanded={moreOpen} onClick={() => setMoreOpen(value => !value)}><span aria-hidden="true">⋯</span></button>
          {moreOpen && <><div className="lcMoreShade" role="presentation" onClick={() => setMoreOpen(false)}/>
            <div className="lcMoreMenu" role="menu">
              <button type="button" role="menuitem" onClick={() => { setMoreOpen(false); goTab('baglanti') }}><i className="lcMoreIco" data-k="conn" aria-hidden="true"><ShieldCheck/></i><span>Bağlantı ayrıntısı</span></button>
              <button type="button" role="menuitem" onClick={() => { setMoreOpen(false); askKais(kaisQuestion) }}><i className="lcMoreIco" data-k="ask" aria-hidden="true"><MessageCircle/></i><span>Kais’e sor</span></button>
              <button type="button" role="menuitem" onClick={() => { setMoreOpen(false); void Promise.resolve(refresh()).catch(() => undefined) }}><i className="lcMoreIco" data-k="refresh" aria-hidden="true"><RefreshCw/></i><span>Yenile</span></button>
              <button type="button" role="menuitem" onClick={() => { setMoreOpen(false); window.location.href = '/profile#notify-settings' }}><i className="lcMoreIco" data-k="bell" aria-hidden="true"><Bell/></i><span>{notify ? 'Bildirimler açık' : 'Bildirimleri aç'}</span></button>
              {live && <><div className="lcMoreSep" role="separator"/>
              <button type="button" role="menuitem" className="lcMoreStop" onClick={() => { setMoreOpen(false); setSheet('close-all') }}><i className="lcMoreIco" data-k="stop" aria-hidden="true"><Power/></i><span>Acil durdur</span></button></>}
            </div></>}
        </div></div>
      {readyToStart && <div className="lcMobileOnly lcMStart">
        <button type="button" className="lcBtn lcPrimary" data-testid="launch-open-mobile" disabled={busy} onClick={openLaunch}><span className="lcMIco"><Zap aria-hidden="true"/></span><span className="lcMLbl">Hepsini hazırla ve başlat</span><span className="lcMGo"><ChevronRight aria-hidden="true"/></span></button>
        <ul className="lcMReady" aria-label="Hazırlık durumu"><li><ShieldCheck aria-hidden="true"/>Risk<b><Check aria-hidden="true"/></b></li><li><FileCheck aria-hidden="true"/>Onaylar<b><Check aria-hidden="true"/></b></li></ul>
        <div className="lcMStartRow">
          {(!demo || props.onOpenManual) && <button type="button" className="lcMManual" data-testid="open-manual-mobile" onClick={demo ? props.onOpenManual : openManual}><Zap aria-hidden="true"/><span>Manuel işlem aç</span><ChevronRight className="lcMChev" aria-hidden="true"/></button>}
          <button type="button" className="lcMAsk" onClick={() => askKais(kaisQuestion)}><MessageCircle aria-hidden="true"/><span>Kais’e sor</span><ChevronRight className="lcMChev" aria-hidden="true"/></button>
        </div>
      </div>}
      <dl className="lcStatusStats">
        {running && <div><dt>Oturum</dt><dd data-testid="original-session-remaining">{sessionLeft > 0 ? `${duration(sessionLeft)} kaldı` : '—'}</dd></div>}
        {running && <div><dt>Son tarama</dt><dd>{Number.isFinite(lastScanMs) ? `${clock(scanner?.last_scan_at)} · ${scanner?.scanned_symbol_count ?? 0} coin` : 'bekleniyor'}</dd></div>}
        {upcoming && <div><dt>Sonraki tarama</dt><dd data-testid="live-next-scan">{new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})} · {duration(upcoming.inSeconds)}</dd></div>}
        <div><dt>Bakiye</dt><dd>{verified && wallet !== null ? usdt(wallet) : '—'}</dd></div>
        <div><dt>Bugün</dt><dd data-sign={pnlToday === null || pnlToday === 0 ? undefined : pnlToday < 0 ? 'down' : 'up'}>{pnlToday !== null ? usdt(pnlToday, true) : '—'}</dd></div>
      </dl>
      <div className="lcStatusLinks">
        <span className="lcUpdated"><i className="lcLiveDot" aria-hidden="true"/><span><small>Son güncelleme</small><b>{lastGood.current ? new Date(lastGood.current.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '—'}</b></span></span>
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
    <section className="lcMobileOnly lcMSum" aria-label="Hesap özeti (telefon)">
      <div className="lcMSumTop">
        <span><small>Bakiye</small><b>{verified && wallet !== null ? usdt(wallet) : '—'}</b></span>
        <span><small>Bugün</small><b data-sign={pnlToday === null || pnlToday === 0 ? undefined : pnlToday < 0 ? 'down' : 'up'}>{pnlToday !== null ? usdt(pnlToday, true) : '—'}</b></span>
      </div>
      {dailyLoss !== null && dailyLimit !== null && <div className="lcMLimit"><div><small>Günlük zarar limiti</small><small>kalan <b>{usdt(Math.max(0, dailyLimit - dailyLoss))}</b> / {usdt(dailyLimit)}</small></div>
        <div className="lcBar" role="progressbar" aria-label="Günlük kayıp sınırı kullanımı" aria-valuemin={0} aria-valuemax={dailyLimit} aria-valuenow={Math.min(dailyLimit, Math.max(0, dailyLoss))} data-high={dailyLoss / dailyLimit >= 0.8 || undefined}><i style={{width: `${Math.min(100, Math.max(0, dailyLoss / dailyLimit * 100))}%`}}/></div></div>}
      <div className="lcMChips">
        <span><small>Kullanılabilir</small><b>{verified && availableBalance !== null ? usdt(availableBalance) : '—'}</b></span>
        <span><small>Açık pozisyon</small><b>{live ? `${positions.length}${finite(policy.max_positions) ? ` / ${policy.max_positions}` : ''}` : '—'}</b></span>
        {live && modeLabelNow && <span><small>Teminat modu</small><b>{modeLabelNow.label} %{modeLabelNow.per_trade_percent}</b></span>}
      </div>
    </section>
    {stale && <p className="lcStaleNote" role="status">Bağlantı kesildi; aşağıdaki bilgiler {duration(staleAge)} önce doğrulandı. Yeniden bağlanınca kendiliğinden güncellenir.</p>}

    <div className="lcGrid" data-coach={coach || undefined} data-noscan={!(running || scanner?.last_scan_at) || undefined}>
      <div className="lcMain">
        <section className="lcGuide" data-tone={guide.tone} data-phase={guide.phase} data-testid="live-guide" data-ready={readyToStart || undefined} aria-live="polite" key={advanced}>
          <header className="lcGuideHead">
            <span className="lcEye" aria-hidden="true" data-tone={guide.tone}/>
            <div><b>{coach ? 'KAİS KOÇ' : 'KAİS REHBER'}</b><small>{stale ? 'Bağlantı bekleniyor · ilerlemen korunuyor' : coach ? 'Kurulum tamam · seni bilgilendiriyorum' : guide.phase === 'loading' ? 'Hazırlanıyor' : guide.phase === 'problem' ? 'Önce bunu çözelim · ilerlemen korunuyor' : `Adım ${guide.step} / ${GUIDE_STEPS.length} · ${GUIDE_STEPS[guide.step - 1]}`}</small></div>
            {!coach && guide.phase !== 'loading' && <span className="lcMobileOnly lcGuideRing" role="img" aria-label={`Adım ${guide.step} / ${GUIDE_STEPS.length}`} style={{'--p': `${Math.round(guide.step / GUIDE_STEPS.length * 100)}%`} as CSSProperties}><b aria-hidden="true">{guide.step}/{GUIDE_STEPS.length}</b></span>}
            {coach && !stale && <span className="lcMobileOnly lcKoPill">ÇALIŞIYOR</span>}
            <span className="lcHeadBtns">
              <button type="button" className="lcAsk" onClick={() => askKais(kaisQuestion)}><MessageCircle aria-hidden="true"/>Kais’e sor</button>
              {(!demo || props.onOpenManual) && <button type="button" className="lcManualBtn" data-testid="open-manual" onClick={demo ? props.onOpenManual : openManual}><Zap aria-hidden="true"/>Manuel işlem aç</button>}
            </span>
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
          {coach && !stale && <div className="lcMobileOnly lcKo" data-phase={koPhase} data-urgent={koUrgent || undefined} data-testid="coach-ring">
            <button type="button" className="lcKoRing" data-size={koSize} style={{'--deg': `${koDeg}deg`} as CSSProperties} aria-label={koSession ? 'Oturum kalan süresi. Dokununca sonraki taramayı gösterir' : 'Sonraki tarama sayacı. Dokununca oturum süresini gösterir'} onClick={() => setKoView(value => value === 'scan' ? 'session' : 'scan')}>
              <svg className="lcKoSvg" viewBox="0 0 224 224" aria-hidden="true">{Array.from({length: 60}, (_, index) => <line key={index} x1="112" y1="6" x2="112" y2="17" transform={`rotate(${index * 6} 112 112)`} data-on={index < koTicks || undefined}/>)}</svg><i className="lcKoSweep" aria-hidden="true"/><i className="lcKoHead" aria-hidden="true"/>
              <span className="lcKoTxt"><small>{koLabel}</small><b>{koBig}</b><em>{koSub}</em></span>
            </button>
            <p className="lcKoSmart" role="status"><Check aria-hidden="true"/>{koSmart}</p>
          </div>}
          {coach && running && !stale && <div className="lcMobileOnly lcKoSession" data-urgent={(sessionLeft > 0 && sessionLeft <= 3600) || undefined}>
            <div><small>OTURUM</small><b>{sessionLeft > 0 ? `${hms(sessionLeft)} kaldı` : '—'}</b></div>
            <i aria-hidden="true"><u style={{width: `${koSessionPct}%`}}/></i>
          </div>}
          {verified && original && guideInput.connected && !guideInput.emergency && (() => {
            const approvalsOk = guideInput.authorizationValid && guideInput.policyAcknowledged
            const todo = [!riskSaved, !approvalsOk].filter(Boolean).length
            const done = 2 - todo
            return <div className="lcSetup" data-complete={todo === 0 || undefined} aria-label="Kurulum durumu">
              <i className="lcSetupRing" style={{'--p': `${done * 50}%`} as CSSProperties} aria-hidden="true"><b>{done}/2</b></i>
              <div className="lcSetupBody">
                <b>{todo === 0 ? 'Kurulum tamam' : `${todo} adım kaldı`}</b>
                <ul>
                  <li data-ok={riskSaved || undefined}>{riskSaved ? '✓' : '○'} Risk ve günlük sınır{!riskSaved && <button type="button" className="lcLink" disabled={busy} onClick={openWizard}>ayarla</button>}</li>
                  <li data-ok={approvalsOk || undefined}>{approvalsOk ? '✓' : '○'} Güvenlik onayları{!approvalsOk && <button type="button" className="lcLink" disabled={busy} onClick={() => setSheet('approvals')}>onayla</button>}</li>
                  <li data-ok={smartSettings?.enabled || undefined} data-optional>{smartSettings?.enabled ? '✓' : '·'} Akıllı koruma <small>{smartSettings?.enabled ? 'açık' : 'isteğe bağlı, kapalı'}</small></li>
                </ul>
              </div>
            </div>
          })()}
          <div className="lcGuideActions">
            {setupPhase && <button type="button" className="lcBtn lcPrimary lcLaunchBtn" data-testid="launch-open" disabled={busy} onClick={openLaunch}><Zap aria-hidden="true"/>Hepsini hazırla ve başlat</button>}
            {!setupPhase && guide.action && guide.actionLabel && guide.action !== 'stop' && (guide.action === 'open-connection'
              ? <a className="lcBtn lcPrimary" href="/master-trade?tab=baglanti" data-testid="live-guide-action" onClick={event => { event.preventDefault(); goTab('baglanti') }}>{guide.actionLabel}</a>
              : <button type="button" className="lcBtn lcPrimary" data-testid="live-guide-action" disabled={busy && guide.action !== 'retry'} onClick={() => act()}>{actionIcon}{guide.action === 'retry' && working === 'retry' ? 'Okunuyor…' : guide.actionLabel}</button>)}
            {guide.why && <button type="button" className="lcBtn lcGhost" aria-expanded={whyOpen} onClick={() => setWhyOpen(value => !value)}>Neden gerekli?</button>}
            {coach && running && !stale && <button type="button" className="lcBtn lcPrimary lcExtend" data-urgent={(sessionLeft > 0 && sessionLeft <= 3600) || undefined} disabled={busy} onClick={() => { setExtendUnderstood(false); setSheetError(''); setSheet('extend') }}>Oturumu uzat</button>}
            {!setupPhase && verified && original && guideInput.connected && !guideInput.emergency && <button type="button" className="lcBtn lcGhost lcWizOpen" data-testid="koc-wizard-open" disabled={busy} onClick={openWizard}>Kais Koç ile ayarla</button>}
            {!setupPhase && verified && original && guideInput.connected && <button type="button" className="lcBtn lcGhost" disabled={busy} onClick={() => setSheet('summary')}>Ayarlarım</button>}
            {coach && <button type="button" className="lcBtn lcGhost" onClick={() => { setSettingsOpen(true); window.setTimeout(() => document.getElementById('lc-drawer')?.scrollIntoView({behavior: 'smooth', block: 'start'}), 30) }}>Risk ayarını gör</button>}
          </div>
          {whyOpen && guide.why && <p className="lcWhy">{guide.why}</p>}
          {retryIn !== null && <p className="lcRetry" role="status"><RefreshCw aria-hidden="true" className="lcSpin"/>{retryIn > 0 ? `${retryIn} sn sonra kendiliğinden tekrar denenecek` : 'Tekrar deneniyor…'}{retry.attempt > 0 ? ` · ${retry.attempt + 1}. deneme` : ''}</p>}
        </section>

        {alerts.length > 0 && <div className="lcAlerts">{alerts.map((item, index) => <p key={index} className="lcAlert" data-tone={item.tone}>{item.tone === 'ok' ? <ShieldCheck aria-hidden="true"/> : <TriangleAlert aria-hidden="true"/>}{item.text}</p>)}</div>}

        {(running || scanner?.last_scan_at) && <section className="lcCard lcScan" aria-label="Canlı tarama">
          <header className="lcCardHead"><div><small>{dt('CANLI TARAMA')}</small><h3>{scanStage[scanner?.stage || ''] || (running ? 'İlk tarama bekleniyor' : 'Strateji durdu')}</h3></div>
            <button type="button" className="lcAsk" onClick={() => askKais('Canlı stratejim son taramada neden işlem açmadı? Kısaca açıkla.')}><MessageCircle aria-hidden="true"/>Kais’e sor</button></header>
          <div className="lcScanStats">
            <span><small>Son tarama</small><b>{Number.isFinite(lastScanMs) ? clock(scanner?.last_scan_at) : '—'}</b><em>{Number.isFinite(lastScanMs) ? ago(lastScanMs) : 'henüz yok'}</em></span>
            <span data-accent><small>Sonraki tarama</small><b>{upcoming ? new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : '—'}</b><em>{upcoming ? banWaits ? 'Binance engeli bitince' : `${duration(upcoming.inSeconds)} sonra` : 'strateji çalışmıyor'}</em></span>
            <span><small>Taranan</small><b>{scanner?.scanned_symbol_count ?? 0} coin</b><em>{scanner?.candidate_count ? `${scanner.candidate_count} aday` : 'aday yok'}</em></span>
            <span><small>Son 24 saat</small><b>{scanner?.last_24h?.scans ?? 0} tarama</b><em>{scanner?.last_24h?.candidates ?? 0} aday · {scanner?.last_24h?.trades ?? 0} işlem</em></span>
          </div>
          <p className="lcMobileOnly lcMScanLine">{upcoming ? `Sonraki tarama ${new Date(upcoming.at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})}` : 'Strateji çalışmıyor'} · son 24 saatte {scanner?.last_24h?.scans ?? 0} tarama, {scanner?.last_24h?.candidates ?? 0} aday, {scanner?.last_24h?.trades ?? 0} işlem</p>
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
                  {plan?.source === 'MANUAL' && <span><small>TP2 (%{tp2Percent(plan)})</small><b>{String(plan?.targets?.[1] ?? '—')}</b>{!external && flag(protection?.tp2)}</span>}
                  <span><small>TP3</small><b>{String(plan?.targets?.[2] ?? '—')}</b>{!external && flag(protection?.tp3)}</span>
                </div>
                {note && <p className="lcNote" data-tone={note.tone}>{note.text}</p>}
                {!external && (() => { const text = shadowNote((plan as {exit_shadow?: unknown} | undefined)?.exit_shadow); return text && <p className="lcNote lcShadowNote" data-tone="wait">{text}</p> })()}
                {closable && plan?.source === 'MANUAL' && (() => {
                  const levels = Array.isArray(plan?.targets) ? plan.targets.map(value => String(value ?? '')) : []
                  const manual: ManualTarget = {planId, symbol: String(position.symbol || ''), direction: String(position.direction || ''), entry: String(position.entry_price ?? '—'), pnl,
                    stop: String(plan?.stop_loss ?? ''), targets: [levels[0] ?? '', levels[1] ?? '', levels[2] ?? ''], mark: String(position.mark_price ?? '—')}
                  return <div className="lcPosManual">
                    <button type="button" className="lcBtn" disabled={busy || protection?.stop !== true} onClick={() => askEditProtection(manual)}>Stop / hedef değiştir</button>
                    <button type="button" className="lcBtn" disabled={busy || protection?.stop !== true} onClick={() => askReduce(manual)}>Kısmi kapat</button>
                  </div>
                })()}
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
        <section className="lcCard lcEvents" data-mfold-open={eventsOpen || undefined} aria-label="Son olaylar">
          <header className="lcCardHead"><div><small>AKIŞ</small><h3>Son olaylar</h3></div><button type="button" className="lcMobileOnly lcLink" aria-expanded={eventsOpen} onClick={() => setEventsOpen(value => !value)}>{eventsOpen ? 'Gizle' : `Göster (${allEvents.length})`}</button></header>
          <div className="lcChips" role="tablist" aria-label="Olay filtresi">{([['all', 'Tümü'], ['trade', 'İşlemler'], ['warn', 'Uyarılar'], ['system', 'Sistem']] as const).map(([id, label]) =>
            <button type="button" role="tab" key={id} aria-selected={eventFilter === id} data-on={eventFilter === id || undefined} onClick={() => setEventFilter(id)}>{label}</button>)}</div>
          {!verified && allEvents.length > 0 && <p className="lcHint">Son görülen kayıtlar; güncel durum doğrulanamadı.</p>}
          {shownEvents.length === 0 ? <p className="lcEmpty">{allEvents.length ? 'Bu filtrede olay yok.' : 'Henüz olay yok.'}</p> : <ul>{shownEvents.map((event, index) => <li key={`${event.created_at}-${index}`} data-tone={eventTone(event.kind)}>
            <time>{clock(event.created_at)}</time><span>{event.message ?? String(event.kind || '').replaceAll('_', ' ')}</span></li>)}</ul>}
        </section>
      </aside>
    </div>

    {verified && original && guideInput.connected && !guideInput.emergency && <section className="lcMobileOnly lcMSet" aria-label="Ayarların">
      <span><small>Ayarların</small><b>{riskSaved ? `Risk ${pct(savedRisk)}` : 'Risk seçilmedi'}{modeLabelNow ? ` · ${modeLabelNow.label} %${modeLabelNow.per_trade_percent}` : ''}{smartSettings ? ` · Akıllı koruma ${smartSettings.emergency_off ? 'durduruldu' : smartSettings.enabled ? 'açık' : 'kapalı'}` : ''}</b></span>
      <button type="button" className="lcBtn" data-testid="mobile-settings-edit" disabled={busy} onClick={openWizard}>Düzenle</button>
    </section>}

    <details className="lcDrawer" id="lc-drawer" open={settingsDrawerOpen || undefined} onToggle={event => { const open = (event.currentTarget as HTMLDetailsElement).open; if (coach) setSettingsOpen(open) }}>
      <summary><span className="lcDrawerIcon"><SlidersHorizontal aria-hidden="true"/></span><span className="lcDrawerTitle"><b>Ayarlar ve gelişmiş</b><small>Risk ayarı, kurtarma ve acil durdurma tek yerde.</small></span><span className="lcDrawerChip" data-tone={guideInput.emergency ? 'stop' : 'ok'}><i aria-hidden="true"/>{guideInput.emergency ? 'Acil durdurma kilidi var' : 'Her şey yolunda'}</span><ChevronDown aria-hidden="true"/></summary>
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

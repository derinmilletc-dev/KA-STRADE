import {useCallback, useEffect, useMemo, useRef, useState, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {Check, ChevronDown, Clock, Loader2, Pause, Play, Power, RefreshCw, ShieldAlert, ShieldCheck, Square, Target, TrendingDown, TrendingUp, TriangleAlert, Wallet, WifiOff, X, Zap} from 'lucide-react'
import {currentLang} from '../../i18n'
import FarmerGuide from './FarmerGuide'
import FarmerGuideCard from './FarmerGuideCard'
import {
  ageText, armRemaining, badgeOf, emergencyOutcome, friendlyError, money, pickScreen, pnlView, priceText, protectionOf, slotView, statusMessageKey,
  tr_, whyKey, type CopyKey, type FarmerCandidate, type FarmerPosition, type FarmerPreview, type FarmerSettings, type FarmerStatus, type Lang,
} from './farmerView'
import {showToast} from '../../toast-center'
import './farmer.css'

/** KAIS FARMER ekranı. Sunucu tek doğruluk kaynağıdır: marjin, risk, PnL, ARM, günlük kayıp ve koruma durumu burada HESAPLANMAZ, yalnız gösterilir.
 *  Komutlar (etkinleştir, STOP BOT, acil kapat, ARM yenile) sunucuya gider; sonuç sunucudan yeniden okunur. */
export type FarmerPanelProps = {
  get: (path: string) => Promise<any>
  post: (path: string, body?: Record<string, unknown>) => Promise<any>
  accountConnected: boolean
  onConnect?: () => void
  onAsk?: (question: string) => void
  /** KAIS KOÇ görünümü açılıp kapanınca (kokpit ızgarası Kais Long ile aynı koç stillerini kullansın). */
  onCoachChange?: (on: boolean) => void
  /** Testler için sabit saat (saniye). */
  nowSeconds?: () => number
}

type Form = Record<'min_score' | 'margin_usdt' | 'leverage' | 'target_usdt' | 'max_loss_usdt' | 'max_positions' | 'total_margin_cap' | 'dead_score' | 'dead_confirmations', string> & {dead_on: boolean}
const FIELDS = ['min_score', 'margin_usdt', 'leverage', 'target_usdt', 'max_loss_usdt', 'max_positions', 'total_margin_cap', 'dead_score', 'dead_confirmations'] as const

function formOf(settings: Partial<FarmerSettings> | null | undefined): Form {
  const s = settings ?? {}
  return {
    min_score: String(s.min_score ?? ''), margin_usdt: String(s.margin_usdt ?? ''), leverage: String(s.leverage ?? ''), target_usdt: String(s.target_usdt ?? ''),
    max_loss_usdt: String(s.max_loss_usdt ?? ''), max_positions: String(s.max_positions ?? ''), total_margin_cap: String(s.total_margin_cap ?? ''),
    dead_score: String(s.dead_score ?? 75), dead_confirmations: String(s.dead_confirmations ?? 2), dead_on: s.dead_score !== null && s.dead_score !== undefined,
  }
}
function bodyOf(form: Form): Record<string, unknown> {
  const num = (value: string) => (value.trim() === '' ? NaN : Number(value.replace(',', '.')))
  const out: Record<string, unknown> = {}
  for (const key of FIELDS) out[key] = num(form[key])
  if (!form.dead_on) out.dead_score = null
  for (const key of Object.keys(out)) if (typeof out[key] === 'number' && Number.isNaN(out[key])) return {__invalid: true}
  return out
}
const sameAsRecommended = (form: Form, rec: Record<string, unknown> | undefined) => !!rec && FIELDS.every(key => {
  if (key === 'dead_score') return form.dead_on ? Number(form.dead_score) === Number(rec.dead_score) : rec.dead_score === null
  return Number(form[key]) === Number(rec[key])
})

export default function FarmerPanel({get, post, accountConnected, onConnect, onAsk, onCoachChange, nowSeconds = () => Date.now() / 1000}: FarmerPanelProps) {
  const lang: Lang = useMemo(() => (currentLang() === 'tr' ? 'tr' : 'en'), [])
  const t = (key: CopyKey) => tr_(lang, key)
  const L = (tr: string, en: string) => (lang === 'tr' ? tr : en)
  const getRef = useRef(get), postRef = useRef(post)
  getRef.current = get; postRef.current = post

  const [status, setStatus] = useState<FarmerStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState('')
  const [flash, setFlash] = useState<{tone: 'ok' | 'warn' | 'bad'; text: string} | null>(null)
  const [guide, setGuide] = useState<{open: boolean; topic?: string}>({open: false})
  const [emergency, setEmergency] = useState<'closed' | 'confirm' | 'working'>('closed')
  const [emergencyResult, setEmergencyResult] = useState<'success' | 'partial' | 'failure' | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [preview, setPreview] = useState<FarmerPreview | null>(null)
  const [formError, setFormError] = useState('')
  const dirty = useRef(false)
  const [wzEdit, setWzEdit] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await getRef.current('/farmer/status')
      setStatus(data as FarmerStatus)
      setError(false)
    } catch { setError(true) } finally { setLoading(false) }
  }, [])

  // Kontrollü yoklama: ekran görünürken; KAPALI'da seyrek, aktifken daha sık. Sekme gizliyken durur.
  const active = status ? status.state !== 'OFF' || status.positions.length > 0 : false
  useEffect(() => {
    let timer: number | undefined
    let stopped = false
    const tick = async () => {
      if (stopped) return
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') await load()
      if (!stopped) timer = window.setTimeout(tick, active ? 10000 : 30000)
    }
    void tick()
    const onVisible = () => { if (document.visibilityState === 'visible') void load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { stopped = true; if (timer) window.clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [load, active])

  useEffect(() => {
    if (status && form === null) {
      const base = formOf(status.config ?? status.recommended)
      // Kâr hedefi, en fazla kayıp ve marjin tavanı kullanıcıdan gelir; henüz kaydedilmediyse boş başlar (varsayılan yok).
      setForm(status.configured ? base : {...base, target_usdt: '', max_loss_usdt: '', total_margin_cap: ''})
      setPreview(status.configured ? status.preview : null)
    }
  }, [status, form])

  // Risk önizleme: SUNUCU hesaplar; burada yalnız çağrılır (350 ms sakinleşince).
  useEffect(() => {
    if (!form || !dirty.current) return
    const body = bodyOf(form)
    if (body.__invalid) { setFormError(L('Lütfen tüm alanları sayıyla doldur.', 'Please fill every field with a number.')); return }
    const handle = window.setTimeout(async () => {
      try {
        const data = await postRef.current('/farmer/preview', body)
        setPreview(data.preview as FarmerPreview); setFormError('')
      } catch (e) { setFormError(friendlyError(e, lang)) }
    }, 350)
    return () => window.clearTimeout(handle)
  }, [form, lang])

  // Sonuç bildirimi ekranın neresinde olursan ol görünür (üstte, sabit): düğmeye basıp aşağıda kalan kullanıcı sonucu kaçırmaz.
  useEffect(() => { if (flash) showToast({body: flash.text, tone: flash.tone}) }, [flash])
  const run = async (name: string, action: () => Promise<void>) => {
    if (busy) return
    setBusy(name); setFlash(null)
    try { await action() } catch (e) { setFlash({tone: 'bad', text: friendlyError(e, lang)}) } finally { setBusy(''); void load() }
  }

  const accountOk = accountConnected
  const screen = pickScreen({status, loading, error, accountConnected: accountOk})
  const now = nowSeconds()
  const state = status?.state ?? 'OFF'
  const hasPositions = (status?.positions.length ?? 0) > 0
  const canEdit = !!status && status.available && state === 'OFF' && !hasPositions
  const armActive = !!status?.arm.active

  const activate = () => run('activate', async () => {
    if (!status) return
    if (form && (!status.configured || dirty.current)) {
      const body = bodyOf(form)
      if (body.__invalid) { setFormError(L('Lütfen tüm alanları sayıyla doldur.', 'Please fill every field with a number.')); return }
      await postRef.current('/farmer/settings', body)
      dirty.current = false
    }
    const started = await postRef.current('/farmer/start') as {state?: string} | null | undefined
    if (!started || started.state !== 'RUNNING') throw new Error('FARMER_NOT_STARTED')   // HTTP 200 tek başına başarı sayılmaz
    setFlash({tone: 'ok', text: L('KAIS FARMER etkinleştirildi.', 'KAIS FARMER is active.')})
  })
  const saveSettings = () => run('save', async () => {
    if (!form) return
    const body = bodyOf(form)
    if (body.__invalid) { setFormError(L('Lütfen tüm alanları sayıyla doldur.', 'Please fill every field with a number.')); return }
    await postRef.current('/farmer/settings', body)
    dirty.current = false; setWzEdit(false)
  })
  const stopBot = () => run('stop', async () => { await postRef.current('/farmer/stop-bot'); setFlash({tone: 'ok', text: L('Yeni girişler durduruldu. Açık pozisyonlar korunuyor.', 'New entries stopped. Existing positions stay protected.')}) })
  const renewArm = () => run('arm', async () => { await postRef.current('/arm', {understood: true}); setFlash({tone: 'ok', text: L('İşlem oturumu yenilendi.', 'Trading session renewed.')}) })
  const acknowledge = () => run('ack', async () => { await postRef.current('/farmer/acknowledge') })
  const doEmergency = async () => {
    setEmergency('working'); setEmergencyResult(null)
    try {
      const result = await postRef.current('/farmer/emergency-close')
      setEmergencyResult(emergencyOutcome(result))
    } catch { setEmergencyResult('failure') }
    setEmergency('closed'); void load()
  }
  const edit = (patch: Partial<Form>) => { dirty.current = true; setForm(current => ({...(current as Form), ...patch})) }
  const openGuide = (topic?: string) => setGuide({open: true, topic})
  const ask = (q: string) => onAsk?.(q)


  const wizardActive = !!status && !loading && !error && accountOk && status.available && (screen === 'onboarding' || (screen === 'dashboard' && state === 'OFF' && !hasPositions)) && state !== 'INTERVENTION_REQUIRED'
  const body = ((): ReactNode => {
    if (screen === 'loading') return <section className="fmCard fmCenter" data-testid="farmer-loading" role="status" aria-live="polite"><Loader2 className="fmSpin" aria-hidden="true"/><p>{L('KAIS FARMER durumu okunuyor…', 'Reading KAIS FARMER status…')}</p></section>
    if (screen === 'backend_unavailable') return <section className="fmCard fmCenter" data-testid="farmer-backend-unavailable" role="alert">
      <WifiOff aria-hidden="true"/><h3>{L('Şu an bağlanılamıyor', 'Cannot connect right now')}</h3>
      <p>{L('KAIS FARMER durumuna ulaşılamadı. Açık pozisyonlar varsa borsadaki koruma emirleri çalışmaya devam eder.', 'Could not reach KAIS FARMER. If positions are open, their protection orders keep working on the exchange.')}</p>
      <button type="button" className="fmBtn" onClick={() => { setLoading(true); void load() }}><RefreshCw aria-hidden="true"/>{L('Tekrar dene', 'Try again')}</button>
    </section>
    if (screen === 'account_unavailable') return <section className="fmCard fmCenter" data-testid="farmer-account-unavailable">
      <WifiOff aria-hidden="true"/><h3>{L('Önce Binance hesabını bağla', 'Connect your Binance account first')}</h3>
      <p>{L('KAIS FARMER canlı hesap bilgine ihtiyaç duyar. Bağlantıdan sonra buradan devam edebilirsin.', 'KAIS FARMER needs your live account. After connecting you can continue here.')}</p>
      {onConnect && <button type="button" className="fmBtn fmPrimary" onClick={onConnect}>{L('Bağlantıyı aç', 'Open connection')}</button>}
    </section>
    if (!status || !form) return null
    const notices = <>
      {flash && <p className="fmFlash" data-tone={flash.tone} role="status">{flash.text}</p>}
      {emergencyResult && <EmergencyResult kind={emergencyResult} L={L} onClose={() => setEmergencyResult(null)}/>}
    </>
    if (screen === 'disabled') return <>
      <section className="fmCard fmDisabled" data-testid="farmer-disabled" role="status">
        <TriangleAlert aria-hidden="true"/><div><h3>{t('disabled.title')}</h3><p>{t('disabled.body')}</p></div>
      </section>
      <Setup {...{status, form, edit, canEdit: false, preview, formError, lang, L, openGuide, mode: 'preview', onActivate: activate, busy}}/>
    </>
    const wizardOn = (screen === 'onboarding' || (screen === 'dashboard' && state === 'OFF' && !hasPositions)) && state !== 'INTERVENTION_REQUIRED'
    if (wizardOn) return <>
      {notices}
      <Wizard {...{status, form, edit, preview, formError, lang, L, openGuide, busy, armActive, step: wzEdit || !status.configured ? 1 : !armActive ? 2 : 3, editing: wzEdit && status.configured,
        onSave: saveSettings, onRenew: renewArm, onActivate: activate, onEdit: () => setWzEdit(true), onCancelEdit: () => { setWzEdit(false); dirty.current = false; setForm(formOf(status.config ?? status.recommended)) }}}/>
    </>
    if (screen === 'onboarding') return <>
      {notices}
      <section className="fmCard fmHero" data-testid="farmer-onboarding">
        <h3>{L('Başlamadan önce', 'Before you start')}</h3>
        <p>{t('intro')}</p>
        <p className="fmPrinciple">{L('Boş yer bir sorun değildir: KAIS FARMER yalnızca nitelikli bir kurulum çıktığında pozisyon açar.', 'An empty slot is not a problem: KAIS FARMER opens a position only when a qualified setup appears.')}</p>
      </section>
      <Setup {...{status, form, edit, canEdit: true, preview, formError, lang, L, openGuide, mode: 'onboarding', onActivate: activate, busy}}/>
      <ArmNote status={status} L={L} now={now} busy={busy} onRenew={renewArm}/>
    </>
    return <Dashboard {...{status, form, edit, canEdit, preview, formError, lang, L, t, now, busy, armActive, emergencyBusy: emergency !== 'closed', flash,
      onActivate: activate, onStop: stopBot, onRenew: renewArm, onAck: acknowledge, onEmergency: () => setEmergency('confirm'), openGuide, ask,
      emergencyResult, clearEmergency: () => setEmergencyResult(null)}}/>
  })()

  return <section className="fmRoot" data-testid="farmer-panel" data-screen={screen} data-state={status?.state}>
    {status && screen !== 'loading' && screen !== 'backend_unavailable' ? <FarmerGuideCard status={status} screen={screen} L={L} now={now} onConnect={onConnect} onRenew={renewArm} preview={preview}
      onOpenGuide={() => openGuide()} onAsk={ask} busy={!!busy} onCoachChange={onCoachChange} editing={wzEdit} wizard={wizardActive}>{body}</FarmerGuideCard> : body}
    <FarmerGuide lang={lang} open={guide.open} topic={guide.topic} onClose={() => setGuide({open: false})} onAsk={onAsk}/>
    {emergency !== 'closed' && <EmergencyModal lang={lang} working={emergency === 'working'} count={status?.positions.length ?? 0} onCancel={() => setEmergency('closed')} onConfirm={() => void doEmergency()}/>}
  </section>
}

// ---------------------------------------------------------------- Panel (canlı ekran)
type DashProps = {
  status: FarmerStatus; form: Form; edit: (patch: Partial<Form>) => void; canEdit: boolean; preview: FarmerPreview | null; formError: string; lang: Lang
  L: (tr: string, en: string) => string; t: (key: CopyKey) => string; now: number; busy: string; armActive: boolean; emergencyBusy: boolean
  flash: {tone: 'ok' | 'warn' | 'bad'; text: string} | null; onActivate: () => void; onStop: () => void; onRenew: () => void; onAck: () => void
  onEmergency: () => void; openGuide: (topic?: string) => void; ask: (q: string) => void; emergencyResult: 'success' | 'partial' | 'failure' | null; clearEmergency: () => void
}

function Dashboard(p: DashProps) {
  const {status, L, t} = p
  const state = status.state
  const badge = badgeOf(state)
  const hasPositions = status.positions.length > 0
  const slots = slotView(status)
  const intervention = state === 'INTERVENTION_REQUIRED'
  const message = t(statusMessageKey(state, hasPositions))
  const canActivate = state === 'OFF' && !intervention
  const canStop = state === 'RUNNING' || state === 'ENTRY_PAUSED_ARM' || state === 'ENTRY_PAUSED_SAFETY'
  const BadgeIcon = { power: Power, play: Play, pause: Pause, square: Square, alert: TriangleAlert }[badge.icon]
  return <>
    <section className="fmCard fmStatus" data-tone={badge.tone} data-testid="farmer-status" aria-live="polite">
      <div className="fmStatusTop">
        <span className="fmBadge" data-tone={badge.tone} data-testid="farmer-badge"><BadgeIcon aria-hidden="true"/>{t(badge.key)}</span>
        {status.today && <span className="fmToday"><small>{L('Bugünün net PnL’i', 'Today’s net PnL')}</small><b data-sign={pnlView('VERIFIED', status.today.verified_net_pnl).sign ?? undefined}>{status.today.verified_net_pnl === null ? '—' : money(status.today.verified_net_pnl, true)}</b>
          {(status.today.pending_count + status.today.unverified_count) > 0 && <em>{L('doğrulanmayı bekleyen sonuçlar var', 'some results are not verified yet')}</em>}</span>}
      </div>
      <p className="fmStatusMsg" data-testid="farmer-message">{message}</p>
      {state === 'ENTRY_PAUSED_SAFETY' && status.why.code === 'DAILY_LOSS' && <p className="fmSub">{t('why.DAILY_LOSS')}</p>}
      <div className="fmActions">
        {canActivate && <button type="button" className="fmBtn fmPrimary" data-testid="farmer-activate" disabled={!!p.busy} onClick={p.onActivate}>{p.busy === 'activate' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <Zap aria-hidden="true"/>}{L('KAIS FARMER’I ETKİNLEŞTİR', 'ACTIVATE KAIS FARMER')}</button>}
        {canStop && <button type="button" className="fmBtn fmWarn" data-testid="farmer-stop" disabled={!!p.busy} onClick={p.onStop}><Square aria-hidden="true"/>STOP BOT</button>}
        {state === 'ENTRY_PAUSED_ARM' && <button type="button" className="fmBtn fmPrimary" data-testid="farmer-renew-arm" disabled={!!p.busy} onClick={p.onRenew}><Clock aria-hidden="true"/>{L('ARM’I YENİLE', 'RENEW ARM')}</button>}
      </div>
      {canStop && <p className="fmHint">{L('STOP BOT yalnızca yeni girişleri durdurur. Mevcut pozisyonlar korunmaya devam eder.', 'STOP BOT stops new entries only. Existing positions remain protected.')}</p>}
      {state === 'STOPPING' && <p className="fmHint">{L('Pozisyonlar kapanınca KAIS FARMER kapanır.', 'KAIS FARMER switches off once positions have closed.')}</p>}
      {canActivate && !p.armActive && <p className="fmHint" data-testid="farmer-arm-needed">{L('Yeni pozisyon açabilmesi için geçerli bir işlem oturumu (ARM) gerekir.', 'A valid trading session (ARM) is required before new positions can be opened.')}</p>}
    </section>

    {intervention && <Intervention status={status} L={L} busy={p.busy} onAck={p.onAck}/>}
    {p.flash && <p className="fmFlash" data-tone={p.flash.tone} role="status">{p.flash.text}</p>}
    {p.emergencyResult && <EmergencyResult kind={p.emergencyResult} L={L} onClose={p.clearEmergency}/>}

    <Insight status={status} L={L} t={t}/>
    <Overview status={status} L={L} slots={slots} showAvail={state === 'RUNNING'}/>
    <ArmCard status={status} L={L} now={p.now} busy={p.busy} onRenew={p.onRenew} hideRenew={state === 'ENTRY_PAUSED_ARM'}/>

    <section className="fmSection" aria-labelledby="fm-positions">
      <h3 id="fm-positions">{L('Pozisyonlar', 'Positions')}</h3>
      {hasPositions
        ? <div className="fmPositions">{status.positions.map(pos => <PositionCard key={pos.grid_id} pos={pos} L={L} t={t}/>)}</div>
        : <p className="fmEmpty" data-testid="farmer-no-positions">{L('Şu an açık pozisyon yok. KAIS FARMER yalnızca nitelikli bir kurulum çıkınca pozisyon açar.', 'No open positions right now. KAIS FARMER opens one only when a qualified setup appears.')}</p>}
    </section>

    {(!hasPositions || slots.free > 0) && <WhyCard status={status} L={L} t={t} openGuide={p.openGuide}/>}
    <Scanner items={status.scanner} L={L} t={t} openGuide={p.openGuide}/>

    <details className="fmSettings" data-testid="farmer-settings">
      <summary><span>{L('Ayarlar', 'Settings')}</span><ChevronDown aria-hidden="true"/></summary>
      <Setup {...{status, form: p.form, edit: p.edit, canEdit: p.canEdit, preview: p.preview, formError: p.formError, lang: p.lang, L, openGuide: p.openGuide, mode: 'edit', onActivate: p.onActivate, busy: p.busy}}/>
    </details>

    <section className="fmDanger" aria-labelledby="fm-emergency">
      <div><h3 id="fm-emergency">{L('Acil durum', 'Emergency')}</h3>
        <p>{L('STOP BOT’tan farklıdır: Farmer’a ait tüm pozisyonları kapatmayı dener.', 'Different from STOP BOT: it attempts to close all KAIS FARMER positions.')}</p></div>
      <button type="button" className="fmBtn fmDangerBtn" data-testid="farmer-emergency" disabled={!hasPositions || p.emergencyBusy} onClick={p.onEmergency}><TriangleAlert aria-hidden="true"/>EMERGENCY CLOSE</button>
    </section>
  </>
}

function Insight({status, L, t}: {status: FarmerStatus; L: (tr: string, en: string) => string; t: (key: CopyKey) => string}) {
  if (status.state === 'OFF' || status.state === 'INTERVENTION_REQUIRED') return null
  const rows = status.scanner.filter(c => typeof c.score === 'number')
  const best = rows.reduce<FarmerCandidate | null>((acc, c) => (!acc || (c.score as number) > (acc.score as number) ? c : acc), null)
  const min = status.config?.min_score
  const open = status.positions.length
  let text: string
  if (open > 0) text = L(`${open} pozisyon açık. Stop ve kâr emirleri borsada duruyor; ben izliyorum.`, `${open} open. Stop and target orders sit on the exchange; I'm watching.`)
  else if (best && typeof min === 'number') text = best.status === 'QUALIFIED' || best.status === 'WAITING_CONFIRMATION'
    ? L(`${best.symbol} ${best.direction} adayı ${Math.round(best.score as number)} puanla öne çıktı; onay bekleniyor.`, `${best.symbol} ${best.direction} leads at ${Math.round(best.score as number)}; waiting for confirmation.`)
    : L(`En yakın aday ${best.symbol}: ${Math.round(best.score as number)} puan, eşik ${min}. Eşiğe ulaşırsa girerim.`, `Closest is ${best.symbol}: ${Math.round(best.score as number)}, threshold ${min}. I enter only if it gets there.`)
  else text = t(whyKey(status.why.code))
  return <aside className="fmInsight" data-testid="farmer-insight"><span aria-hidden="true"><Zap/></span><p><b>{L('Kais diyor ki', 'Kais says')}</b>{text}</p></aside>
}

function Overview({status, L, slots, showAvail}: {status: FarmerStatus; L: (tr: string, en: string) => string; slots: ReturnType<typeof slotView>; showAvail: boolean}) {
  const cfg = status.config
  const usdt = (value: number | undefined) => (typeof value === 'number' && Number.isFinite(value) ? `${value} USDT` : '—')
  return <section className="fmCard fmOverview" data-testid="farmer-overview" aria-label={L('Farmer özeti', 'Farmer overview')}>
    <div className="fmSlots" data-testid="farmer-slots">
      <div className="fmSlotsRow" role="img" aria-label={L(`${slots.used} / ${slots.capacity} pozisyon`, `${slots.used} of ${slots.capacity} positions`)}>
        {slots.dots.map((d, i) => <i key={i} data-state={d} aria-hidden="true"/>)}
        {slots.zero && <span className="fmSlotsZero">0</span>}
      </div>
      <b>{L(`${slots.used} / ${slots.capacity} etkin pozisyon`, `${slots.used} / ${slots.capacity} effective positions`)}</b>
      {slots.limitedByMargin && <small data-testid="farmer-cap-note">{L(`Marjin tavanın aynı anda en fazla ${slots.capacity} pozisyona izin veriyor (ayar: ${slots.configuredMax}).`, `Your margin cap allows only ${slots.capacity} simultaneous positions (setting: ${slots.configuredMax}).`)}</small>}
      {showAvail && !slots.zero && slots.free > 0 && <small>{L(`${slots.free} pozisyon müsait. KAIS FARMER bunları yalnızca nitelikli bir kurulum çıkınca kullanır.`, `${slots.free} position${slots.free === 1 ? '' : 's'} available. KAIS FARMER will use them only when a qualified setup appears.`)}</small>}
      {slots.zero && <small>{L('Mevcut ayarlarla hiç pozisyon açılamıyor.', 'With the current settings no position can be opened.')}</small>}
    </div>
    <div className="fmGauge" data-testid="farmer-gauge">
      <div className="fmGaugeTop"><span>{L('Marjin', 'Margin')}</span><b>{usdt(status.margin_used_usdt)} <em>/ {typeof status.margin_used_usdt === 'number' && typeof status.available_margin_usdt === 'number' ? `${Math.round((status.margin_used_usdt + status.available_margin_usdt) * 100) / 100} USDT` : '—'}</em></b></div>
      <i className="fmGaugeBar" aria-hidden="true"><u style={{width: `${typeof status.margin_used_usdt === 'number' && typeof status.available_margin_usdt === 'number' && status.margin_used_usdt + status.available_margin_usdt > 0 ? Math.min(100, Math.round(status.margin_used_usdt / (status.margin_used_usdt + status.available_margin_usdt) * 100)) : 0}%`}}/></i>
    </div>
    <ul className="fmChips" aria-label={L('Hedef ve risk', 'Target and risk')}>
      <li data-kind="goal"><small>{L('Kâr hedefi', 'Profit target')}</small><b>{cfg ? money(cfg.target_usdt, true) : '—'}</b></li>
      <li data-kind="risk"><small>{L('En fazla kayıp', 'Max loss')}</small><b>{cfg ? `-${money(cfg.max_loss_usdt).replace('-', '')}` : '—'}</b></li>
      <li><small>{L('Kaldıraç', 'Leverage')}</small><b>{cfg ? `${cfg.leverage}×` : '—'}</b></li>
    </ul>
  </section>
}

function ArmCard({status, L, now, busy, onRenew, hideRenew = false}: {status: FarmerStatus; L: (tr: string, en: string) => string; now: number; busy: string; onRenew: () => void; hideRenew?: boolean}) {
  const active = status.arm.active
  const left = armRemaining(status.arm.expires_at, now)
  return <section className="fmCard fmArm" data-active={active || undefined} data-testid="farmer-arm">
    <div><small>{L('İşlem oturumu', 'Trading session')}</small>
      <b>{active ? L('AKTİF', 'ACTIVE') : L('SÜRESİ DOLDU', 'EXPIRED')}</b>
      {active && left && <span>{L('Bitiş', 'Expires')}: {left}</span>}</div>
    {active
      ? <p>{L('Yeni girişlere izin var. Süre dolarsa mevcut pozisyonlar yine korunur.', 'New entries are allowed. If it expires, existing positions remain protected.')}</p>
      : <><p>{L('Mevcut pozisyonlar korunuyor. Yeni girişlere izin vermek için oturumu yenile.', 'Existing positions remain protected. Renew the session to allow new entries.')}</p>
        {!hideRenew && <button type="button" className="fmBtn fmPrimary" data-testid="farmer-arm-renew" disabled={!!busy} onClick={onRenew}>{L('ARM’I YENİLE', 'RENEW ARM')}</button>}</>}
  </section>
}
function ArmNote({status, L, now, busy, onRenew}: {status: FarmerStatus; L: (tr: string, en: string) => string; now: number; busy: string; onRenew: () => void}) {
  return status.arm.active ? null : <ArmCard {...{status, L, now, busy, onRenew}}/>
}

function PositionCard({pos, L, t}: {pos: FarmerPosition; L: (tr: string, en: string) => string; t: (key: CopyKey) => string}) {
  const [open, setOpen] = useState(false)
  const pnl = pnlView(pos.pnl_state, pos.verified_net_pnl)
  const prot = protectionOf(pos)
  const protText = { protected: L('KORUMALI', 'PROTECTED'), closing: L('KAPANIYOR', 'CLOSING'), attention: L('DİKKAT', 'ATTENTION'), pending: L('HAZIRLANIYOR', 'PREPARING') }[prot]
  const live = typeof pos.unrealized_net_usdt === 'number' ? money(pos.unrealized_net_usdt, true) : '—'
  return <article className="fmPos" data-testid="farmer-position" data-symbol={pos.symbol} data-protection={prot}>
    <header><b>{pos.symbol}</b><span className="fmSide" data-side={pos.side}>{pos.side}</span>
      <span className="fmProt" data-protection={prot}>{prot === 'protected' ? <ShieldCheck aria-hidden="true"/> : <TriangleAlert aria-hidden="true"/>}{protText}</span></header>
    <dl className="fmPosGrid">
      <div><dt>{L('Giriş', 'Entry')}</dt><dd>{priceText(pos.entry_price)}</dd></div>
      <div><dt>{L('Güncel', 'Current')}</dt><dd>{priceText(pos.mark_price)}</dd></div>
      <div><dt>{L('Hedef', 'Target')}</dt><dd>{money(pos.target_usdt, true)} NET</dd></div>
      <div><dt>{L('Risk', 'Risk')}</dt><dd>{pos.max_loss_usdt === null ? '—' : `-${money(pos.max_loss_usdt).replace('-', '')}`}</dd></div>
      <div><dt>{L('Anlık (tahmini)', 'Now (estimated)')}</dt><dd>{live}</dd></div>
      <div><dt>{L('Süre', 'Age')}</dt><dd>{ageText(pos.age_s)}</dd></div>
    </dl>
    <p className="fmPnl" data-kind={pnl.kind} data-testid="farmer-pnl"><span>{pnl.kind === 'verified' ? <b data-sign={pnl.sign ?? undefined}>{pnl.text}</b> : <b>{pnl.text}</b>}</span><em>{t(`pnl.${pnl.kind === 'verified' ? 'VERIFIED' : pnl.kind === 'unverified' ? 'UNVERIFIED' : 'PENDING'}` as CopyKey)}</em></p>
    <button type="button" className="fmMore" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? L('Ayrıntıyı gizle', 'Hide details') : L('Ayrıntılar', 'Details')}<ChevronDown aria-hidden="true"/></button>
    {open && <dl className="fmPosGrid fmPosMore">
      <div><dt>{L('Hedef fiyat', 'Target price')}</dt><dd>{priceText(pos.tp_price)}</dd></div>
      <div><dt>{L('Stop fiyatı', 'Stop price')}</dt><dd>{priceText(pos.stop_price)}</dd></div>
      <div><dt>{L('Skor', 'Score')}</dt><dd>{pos.score ?? '—'}</dd></div>
      <div><dt>{L('Marjin', 'Margin')}</dt><dd>{pos.margin_usdt === null ? '—' : `${pos.margin_usdt} USDT`}</dd></div>
      <div><dt>{L('Tahmini maliyet', 'Estimated cost')}</dt><dd>{money(pos.est_cost_usdt)}</dd></div>
      <div><dt>{L('Ölü pozisyon sayacı', 'Dead check count')}</dt><dd>{pos.dead_score === null ? L('Kapalı', 'Off') : pos.dead_consecutive}</dd></div>
    </dl>}
  </article>
}

function WhyCard({status, L, t, openGuide}: {status: FarmerStatus; L: (tr: string, en: string) => string; t: (key: CopyKey) => string; openGuide: (topic?: string) => void}) {
  const [open, setOpen] = useState(false)
  const slots = slotView(status)
  const primary = status.why.code
  const extra = status.why.details.filter(d => d.code !== primary)
  return <section className="fmCard fmWhy" data-testid="farmer-why">
    <h3>{slots.used > 0 ? L('Neden daha fazla pozisyon yok?', 'Why no more positions?') : L('Neden pozisyon yok?', 'Why no position?')}</h3>
    <p data-testid="farmer-why-primary">{t(whyKey(primary))}</p>
    {extra.length > 0 && <>
      <button type="button" className="fmMore" aria-expanded={open} onClick={() => setOpen(!open)}>{L('Ayrıntıları gör', 'View details')}<ChevronDown aria-hidden="true"/></button>
      {open && <ul className="fmWhyList">{extra.map(d => <li key={d.code}>{t(whyKey(d.code))}{d.count > 1 ? ` ×${d.count}` : ''}</li>)}</ul>}
    </>}
    <button type="button" className="fmLink" onClick={() => openGuide('noposition')}>{L('Daha fazla bilgi → KAIS REHBER', 'Learn more → KAIS REHBER')}</button>
  </section>
}

function Scanner({items, L, t, openGuide}: {items: FarmerCandidate[]; L: (tr: string, en: string) => string; t: (key: CopyKey) => string; openGuide: (topic?: string) => void}) {
  return <section className="fmSection" aria-labelledby="fm-scanner" data-testid="farmer-scanner">
    <h3 id="fm-scanner">{L('Piyasa tarayıcısı', 'Market Scanner')}</h3>
    {items.length === 0
      ? <p className="fmEmpty" data-testid="farmer-no-candidates">{L('Şu an kalite şartlarını karşılayan aday yok. İşlem zorlanmıyor.', 'No candidate currently meets your quality requirements. No trade is being forced.')}</p>
      : <ul className="fmCands">{items.slice(0, 8).map(c => <li key={c.symbol} data-status={c.status}>
        <b>{c.symbol}</b><span className="fmScore">{c.score === null ? '—' : Math.round(c.score)}</span>
        {c.score !== null && <i className="fmScoreBar" aria-hidden="true"><u style={{width: `${Math.max(0, Math.min(100, c.score))}%`}}/></i>}
        <span className="fmCandSide">{c.direction}</span>
        <em data-status={c.status}>{t(`cand.${c.status}` as CopyKey)}</em></li>)}</ul>}
    <p className="fmFoot">{L('Yüksek skor = KAIS FARMER modeline göre daha güçlü kurulum.', 'Higher score = a stronger setup according to the KAIS FARMER model.')} <button type="button" className="fmLink" onClick={() => openGuide('score')}>{L('Daha fazla', 'Learn more')}</button></p>
  </section>
}

function Intervention({status, L, busy, onAck}: {status: FarmerStatus; L: (tr: string, en: string) => string; busy: string; onAck: () => void}) {
  const open = status.positions.length
  return <section className="fmCard fmAttention" data-testid="farmer-intervention" role="alert">
    <h3><TriangleAlert aria-hidden="true"/>{L('Dikkat gerekiyor', 'Attention Required')}</h3>
    <p>{L('KAIS FARMER bir ya da daha fazla bileşenin durumunu güvenle doğrulayamadığı için yeni işlemi duraklattı.', 'KAIS FARMER has paused new activity because it could not safely verify the state of one or more Farmer components.')}</p>
    <h4>{L('Neler korunuyor?', 'What is protected?')}</h4>
    <p>{open > 0
      ? L(`${open} KAIS FARMER pozisyonu var. Borsadaki koruma emirleri yerinde kalmaya devam eder; ek pozisyon açılmaz.`, `There ${open === 1 ? 'is 1 KAIS FARMER position' : `are ${open} KAIS FARMER positions`}. Protection orders on the exchange stay in place; no further positions will be opened.`)
      : L('Açık KAIS FARMER pozisyonu görünmüyor. Yeni pozisyon açılmayacak.', 'No open KAIS FARMER position is shown. No new position will be opened.')}</p>
    <h4>{L('Ne yapmalısın?', 'What should you do?')}</h4>
    <p>{L('Devam etmeden önce Binance hesabında Farmer pozisyonlarını ve emirlerini gözden geçir. Belirsiz bir borsa durumundan sonra KAIS FARMER’ı körlemesine yeniden başlatma.', 'Please review the issue before restarting KAIS FARMER. Check your Farmer positions and orders on Binance first. Never restart blindly after an uncertain exchange state.')}</p>
    <button type="button" className="fmBtn" data-testid="farmer-ack" disabled={!!busy} onClick={onAck}>{busy === 'ack' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <RefreshCw aria-hidden="true"/>}{L('Durumu yeniden kontrol et', 'Re-check state')}</button>
    <small>{L('Kontrol temiz çıkarsa KAIS FARMER KAPALI konuma döner; yeniden etkinleştirmek sana kalır.', 'If the check is clean KAIS FARMER returns to OFF; activating it again is up to you.')}</small>
  </section>
}

function EmergencyResult({kind, L, onClose}: {kind: 'success' | 'partial' | 'failure'; L: (tr: string, en: string) => string; onClose: () => void}) {
  const text = {
    success: L('Farmer pozisyonları kapatıldı ve borsada düz olduğu doğrulandı.', 'KAIS FARMER positions were closed and verified flat on the exchange.'),
    partial: L('Bazı pozisyonlar kapandı, ancak hepsi doğrulanamadı. Binance’te kalan pozisyonları kontrol et.', 'Some positions closed, but not all could be verified. Check the remaining positions on Binance.'),
    failure: L('Kapatma tamamlanamadı. Pozisyonların korunması sürüyor; Binance’te durumu kontrol et.', 'The close could not be completed. Protection stays in place; please check the state on Binance.'),
  }[kind]
  return <p className="fmFlash fmResult" data-tone={kind === 'success' ? 'ok' : 'bad'} data-testid={`farmer-emergency-${kind}`} role="status">{text}<button type="button" className="fmX" aria-label={L('Kapat', 'Dismiss')} onClick={onClose}><X size={14}/></button></p>
}

function EmergencyModal({lang, working, count, onCancel, onConfirm}: {lang: Lang; working: boolean; count: number; onCancel: () => void; onConfirm: () => void}) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const L = (tr: string, en: string) => (lang === 'tr' ? tr : en)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    cancelRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !working) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); previous?.focus?.() }
  }, [working, onCancel])
  return createPortal(<div className="fmBackdrop" role="presentation">
    <section className="fmModal fmEmergencyModal" role="alertdialog" aria-modal="true" aria-labelledby="fm-em-title" aria-describedby="fm-em-body" data-testid="farmer-emergency-modal">
      <h3 id="fm-em-title">{L('Tüm KAIS FARMER pozisyonları kapatılsın mı?', 'Close all KAIS FARMER positions?')}</h3>
      <p id="fm-em-body">{L(`Bu işlem, Farmer’a ait ${count} pozisyonu reduce-only emirlerle kapatmayı dener. Pozisyonlar ekranda görünenden farklı bir fiyattan kapanabilir. Diğer botlara ve manuel pozisyonlara dokunmaz.`, `This will attempt to close all Farmer-owned positions (${count}) using reduce-only orders. Positions may close at a different price than shown. Other bots and manual positions are not touched.`)}</p>
      <div className="fmModalBtns">
        <button ref={cancelRef} type="button" className="fmBtn" disabled={working} onClick={onCancel}>{L('Vazgeç', 'Cancel')}</button>
        <button type="button" className="fmBtn fmDangerBtn" data-testid="farmer-emergency-confirm" disabled={working} onClick={onConfirm}>{working ? <><Loader2 className="fmSpin" aria-hidden="true"/>{L('Kapatılıyor…', 'Closing…')}</> : L('Acil kapat', 'Emergency Close')}</button>
      </div>
    </section>
  </div>, document.body)
}

// ---------------------------------------------------------------- Kurulum / Ayarlar
type SetupProps = {
  status: FarmerStatus; form: Form; edit: (patch: Partial<Form>) => void; canEdit: boolean; preview: FarmerPreview | null; formError: string; lang: Lang
  L: (tr: string, en: string) => string; openGuide: (topic?: string) => void; mode: 'onboarding' | 'edit' | 'preview' | 'wizard'; onActivate: () => void; busy: string
}

function Setup({status, form, edit, canEdit, preview, formError, L, openGuide, mode, onActivate, busy}: SetupProps) {
  const rec = status.recommended as Record<string, unknown>
  const isRec = sameAsRecommended(form, rec)
  const limits = status.limits
  const disabled = !canEdit
  const field = (key: keyof Omit<Form, 'dead_on'>, label: string, help: string, topic: string, opts?: {step?: string; suffix?: string}) => <div className="fmField">
    <span className="fmFieldTop"><label htmlFor={`fm-f-${key}`}>{label}</label><button type="button" className="fmLink" aria-label={`${label}: ${L('Daha fazla', 'Learn more')}`} onClick={() => openGuide(topic)}>{L('Daha fazla', 'Learn more')}</button></span>
    <span className="fmInputWrap"><input id={`fm-f-${key}`} inputMode="decimal" type="text" value={form[key]} disabled={disabled} aria-describedby={`fm-h-${key}`} onChange={e => edit({[key]: e.target.value} as Partial<Form>)}/>{opts?.suffix && <i aria-hidden="true">{opts.suffix}</i>}</span>
    <small id={`fm-h-${key}`}>{help}</small>
  </div>
  return <div className="fmSetup" data-testid="farmer-setup">
    {mode === 'onboarding' && <>
      <h3>{L('Kurulumun', 'Your setup')}</h3>
      <div className="fmThree">
        <div><i>1</i><b>{L('Pozisyon büyüklüğü', 'Position Size')}</b><span>{L('Her pozisyonda ne kadar marjin kullanılsın?', 'How much margin should be used per position?')}</span></div>
        <div><i>2</i><b>{L('Risk', 'Risk')}</b><span>{L('Tek pozisyonda en fazla ne kadar kaybetmeyi göze alıyorsun?', 'How much are you willing to lose on one position?')}</span></div>
        <div><i>3</i><b>{L('Hedef', 'Target')}</b><span>{L('Ne kadar net kâr hedefliyorsun?', 'How much net profit are you targeting?')}</span></div>
      </div>
    </>}
    {mode !== 'wizard' && <div className="fmRecBar">
      <span className="fmChip" data-kind={isRec ? 'rec' : 'custom'} data-testid="farmer-config-kind">{isRec ? L('Önerilen', 'Recommended') : L('Özel', 'Custom')}</span>
      {status.provisional && <small>{L('Önerilen değerler başlangıç içindir; sunucudan gelir.', 'Recommended values are a starting point; they come from the server.')}</small>}
      {!isRec && canEdit && <button type="button" className="fmLink" onClick={() => edit(formOfRec(rec))}>{L('Önerilene dön', 'Reset to recommended')}</button>}
    </div>}
    {!canEdit && mode === 'edit' && <p className="fmHint">{L('Ayarlar KAIS FARMER kapalıyken ve açık pozisyon yokken değiştirilebilir.', 'Settings can be changed only while KAIS FARMER is off and no position is open.')}</p>}

    <fieldset disabled={disabled} className="fmGroup"><legend>{L('TEMEL', 'BASIC')}</legend>
      <div className="fmField">
        <span className="fmFieldTop"><label htmlFor="fm-f-margin_usdt">{L('Pozisyon marjini', 'Position Margin')}</label><button type="button" className="fmLink" aria-label={`${L('Pozisyon marjini', 'Position Margin')}: ${L('Daha fazla', 'Learn more')}`} onClick={() => openGuide('slots')}>{L('Daha fazla', 'Learn more')}</button></span>
        <select id="fm-f-margin_usdt" value={form.margin_usdt} disabled={disabled} aria-describedby="fm-h-margin_usdt" onChange={e => edit({margin_usdt: e.target.value})}>
          {(limits.margin_options as number[]).map(o => <option key={o} value={String(o)}>{o} USDT</option>)}
        </select>
        <small id="fm-h-margin_usdt">{L('Her pozisyonda kullanılacak teminat.', 'Collateral used for each position.')}</small>
      </div>
      {field('leverage', L('Kaldıraç', 'Leverage'), L('Yüksek kaldıraç maruziyeti artırır.', 'Higher leverage increases exposure.'), 'target', {suffix: '×'})}
      {mode !== 'wizard' && field('target_usdt', L('Kâr hedefi', 'Profit Target'), L('KAIS FARMER, tahmini net sonuç hedefine ulaşınca kapatmayı amaçlar. Gerçek sonuç ücretler, fonlama ve piyasaya göre farklı olabilir.', 'KAIS FARMER aims to close a position when the estimated net result reaches your target. Actual execution can differ because of fees, funding and market conditions.'), 'target', {suffix: 'USDT'})}
      {mode !== 'wizard' && field('max_loss_usdt', L('En fazla kayıp', 'Max Loss'), L('Tek pozisyonda planlanan en yüksek net kayıp.', 'The highest planned net loss on one position.'), 'maxloss', {suffix: 'USDT'})}
    </fieldset>

    <fieldset disabled={disabled} className="fmGroup"><legend>{L('KALİTE', 'QUALITY')}</legend>
      {field('min_score', L('Minimum skor', 'Minimum Score'), L('Bu skorun altındaki kurulumlar atlanır.', 'Setups below this score are skipped.'), 'score')}
      <div className="fmToggle"><input id="fm-f-dead_on" type="checkbox" checked={form.dead_on} disabled={disabled} aria-describedby="fm-h-dead_on" onChange={e => edit({dead_on: e.target.checked})}/><span><label htmlFor="fm-f-dead_on"><b>{L('Ölü pozisyon koruması', 'Dead Position Protection')}</b></label><small id="fm-h-dead_on">{L('Kurulum belirgin biçimde bozulursa pozisyon erken kapatılabilir.', 'A position can be closed early if its setup clearly deteriorates.')}</small></span>
        <button type="button" className="fmLink" aria-label={`${L('Ölü pozisyon koruması', 'Dead Position Protection')}: ${L('Daha fazla', 'Learn more')}`} onClick={() => openGuide('dead')}>{L('Daha fazla', 'Learn more')}</button></div>
    </fieldset>

    <fieldset disabled={disabled} className="fmGroup"><legend>{L('PORTFÖY', 'PORTFOLIO')}</legend>
      {field('max_positions', L('En fazla pozisyon', 'Max Positions'), L('Üst sınırdır; bu kadar işlem açılacağı anlamına gelmez.', 'A ceiling, not an instruction to open that many.'), 'slots')}
      {mode !== 'wizard' && field('total_margin_cap', L('Marjin tavanı', 'Margin Cap'), L(`Farmer’ın toplam marjin tavanı (en çok ${status.hard_cap_usdt} USDT).`, `Total margin cap for KAIS FARMER (max ${status.hard_cap_usdt} USDT).`), 'slots', {suffix: 'USDT'})}
      <div className="fmField fmReadOnly"><span className="fmFieldTop"><b>{L('Bekleme (Cooldown)', 'Cooldown')}</b></span><span>{L(`Aynı sembol için her ${Math.round((status.signal_interval_s ?? 900) / 60)} dakikalık mumda en fazla bir deneme.`, `At most one attempt per symbol per ${Math.round((status.signal_interval_s ?? 900) / 60)}-minute candle.`)}</span></div>
    </fieldset>

    <details className="fmAdvanced"><summary>{L('Gelişmiş ayarlar', 'Advanced settings')}<ChevronDown aria-hidden="true"/></summary>
      <fieldset disabled={disabled || !form.dead_on} className="fmGroup">
        {field('dead_score', L('Ölü skor', 'Dead Score'), L('Skor bunun altına düşerse ve piyasa ters yönü onaylarsa kontrol sayılır.', 'A check counts when the score falls below this and the market confirms the opposite direction.'), 'dead')}
        {field('dead_confirmations', L('Ölü onay sayısı', 'Dead Confirmation'), L('Kapanıştan önce kaç ardışık mum onayı gerekir.', 'How many consecutive candle confirmations are needed before closing.'), 'dead')}
      </fieldset>
    </details>

    {mode !== 'wizard' && <RiskPreview preview={preview} L={L} openGuide={openGuide}/>}
    {mode !== 'wizard' && formError && <p className="fmFlash" data-tone="bad" role="alert" data-testid="farmer-form-error">{formError}</p>}
    {mode === 'onboarding' && <>
      <button type="button" className="fmBtn fmPrimary fmWide" data-testid="farmer-activate" disabled={!!busy || !!formError} onClick={onActivate}>{busy === 'activate' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <Zap aria-hidden="true"/>}{L('KAIS FARMER’I ETKİNLEŞTİR', 'ACTIVATE KAIS FARMER')}</button>
      <p className="fmHint">{L('Etkinleştirmek gerçek pozisyon açmaya izin verir. Hedefler garanti değildir.', 'Activating allows real positions to be opened. Targets are not guarantees.')}</p>
    </>}
    {mode === 'edit' && canEdit && <button type="button" className="fmBtn fmPrimary" data-testid="farmer-save" disabled={!!busy || !!formError} onClick={onActivate}>{L('Kaydet ve etkinleştir', 'Save and activate')}</button>}
  </div>
}

function formOfRec(rec: Record<string, unknown>): Partial<Form> {
  return {min_score: String(rec.min_score), margin_usdt: String(rec.margin_usdt), leverage: String(rec.leverage), max_positions: String(rec.max_positions),
    dead_score: String(rec.dead_score ?? 75), dead_confirmations: String(rec.dead_confirmations), dead_on: rec.dead_score !== null && rec.dead_score !== undefined}
}

function RiskPreview({preview, L, openGuide}: {preview: FarmerPreview | null; L: (tr: string, en: string) => string; openGuide: (topic?: string) => void}) {
  if (!preview) return null
  const verdictText = {GOOD: L('UYGUN', 'GOOD'), TIGHT: L('SIKI', 'TIGHT'), NOT_RECOMMENDED: L('ÖNERİLMEZ', 'NOT RECOMMENDED')}[preview.verdict]
  const warn: Record<string, string> = {
    TARGET_TOO_SMALL: L('Hedefin tahmini işlem maliyetine çok yakın olabilir.', 'Your target may be too close to estimated trading costs.'),
    STOP_TOO_TIGHT: L('Seçilen risk sınırı normal piyasa hareketinde korunmakta zorlanabilir.', 'The selected risk limit may be difficult to maintain under normal market movement.'),
    MARGIN_CAP_LIMITS: L(`Mevcut marjin tavanın aynı anda en fazla ${preview.effective_capacity} pozisyona izin veriyor.`, `Your current margin cap allows only ${preview.effective_capacity} simultaneous position${preview.effective_capacity === 1 ? '' : 's'}.`),
    HIGH_LEVERAGE: L('Yüksek kaldıraç maruziyeti ve tasfiye hassasiyetini artırır.', 'Higher leverage increases exposure and liquidation sensitivity.'),
  }
  return <section className="fmPreview" data-testid="farmer-preview" data-verdict={preview.verdict} aria-label={L('Risk önizlemen', 'Your Risk Preview')}>
    <div className="fmPvHead"><h4>{L('Risk önizlemen', 'Your Risk Preview')}</h4><b className="fmPvVerdict" data-verdict={preview.verdict}>{verdictText}</b></div>
    <dl className="fmPvMain">
      <div data-kind="goal"><dt>{L('Hedef', 'Target')}</dt><dd>{money(preview.target_usdt, true)}</dd></div>
      <div data-kind="risk"><dt>{L('En yüksek kayıp', 'Max loss')}</dt><dd>-{money(preview.max_loss_usdt).replace('-', '')}</dd></div>
      <div><dt>{L('Marjin', 'Margin')}</dt><dd>{preview.margin_usdt}<em> USDT</em></dd></div>
    </dl>
    <p className="fmPvSub">{L(`${preview.leverage}× kaldıraç · yaklaşık ${preview.notional_usdt} USDT büyüklük · tahmini maliyet ${money(preview.est_cost_usdt)}`, `${preview.leverage}× leverage · about ${preview.notional_usdt} USDT size · est. cost ${money(preview.est_cost_usdt)}`)}</p>
    {preview.warnings.length > 0 && <ul className="fmWarnList">{preview.warnings.map(w => warn[w] ? <li key={w}><TriangleAlert aria-hidden="true"/>{warn[w]}</li> : null)}</ul>}
    <small>{L('Uyarılardır, yatırım tavsiyesi değildir.', 'These are warnings, not trading advice.')} <button type="button" className="fmLink" onClick={() => openGuide('guarantee')}>{L('Daha fazla', 'Learn more')}</button></small>
  </section>
}


// ---------------------------------------------------------------- Sihirbaz: Rehber adım adım yaptırır
type WizardProps = {
  status: FarmerStatus; form: Form; edit: (patch: Partial<Form>) => void; preview: FarmerPreview | null; formError: string; lang: Lang
  L: (tr: string, en: string) => string; openGuide: (topic?: string) => void; busy: string; armActive: boolean; step: 1 | 2 | 3; editing: boolean
  onSave: () => void; onRenew: () => void; onActivate: () => void; onEdit: () => void; onCancelEdit: () => void
}

const PROFILES = [
  {id: 'safe', tone: 'cool', icon: ShieldCheck, tr: 'Temkinli', en: 'Careful', dtr: 'Küçük hedef, dar zarar', den: 'Small target, tight loss', target: 0.3, loss: 1, cap: 25},
  {id: 'balanced', tone: 'mint', icon: Target, tr: 'Dengeli', en: 'Balanced', dtr: 'Çoğu kişi için orta yol', den: 'The middle road', target: 0.4, loss: 2, cap: 25},
  {id: 'bold', tone: 'warm', icon: Zap, tr: 'Atak', en: 'Bold', dtr: 'Daha büyük hedef ve kayıp', den: 'Bigger target and loss', target: 1, loss: 3, cap: 50},
] as const

function Wizard(p: WizardProps) {
  const {status, form, edit, preview, formError, L, lang, openGuide, busy, step} = p
  const cap = Number(status.hard_cap_usdt) || 500
  const caps = [...new Set([25, 50, 100, 200, cap].filter(v => v <= cap))]
  const field = (key: 'target_usdt' | 'max_loss_usdt' | 'total_margin_cap', label: string, chips: number[], topic: string, accent: 'gain' | 'loss' | 'cap', Icon: typeof Wallet) => {
    const value = form[key]
    return <div className="fmWzField" data-accent={accent} data-filled={value.trim() !== '' || undefined}>
      <div className="fmWzRow">
        <span className="fmWzIco" aria-hidden="true"><Icon/></span>
        <label htmlFor={`fm-f-${key}`}>{label}</label>
        <button type="button" className="fmWzHelp" aria-label={`${label}: ${L('Daha fazla', 'Learn more')}`} onClick={() => openGuide(topic)}>?</button>
        <span className="fmWzInput"><input id={`fm-f-${key}`} inputMode="decimal" type="text" placeholder="0" value={value} onChange={e => edit({[key]: e.target.value} as Partial<Form>)}/><i aria-hidden="true">USDT</i></span>
      </div>
      <span className="fmWzChips" role="group" aria-label={`${label}: ${L('hızlı seç', 'quick pick')}`}>{chips.map(c => <button type="button" key={c} data-on={Number(value) === c || undefined} onClick={() => edit({[key]: String(c)} as Partial<Form>)}>{c}</button>)}</span>
    </div>
  }
  const filled = form.target_usdt.trim() !== '' && form.max_loss_usdt.trim() !== '' && form.total_margin_cap.trim() !== ''
  const picked = PROFILES.find(pr => Number(form.target_usdt) === pr.target && Number(form.max_loss_usdt) === pr.loss && Number(form.total_margin_cap) === pr.cap && form.target_usdt !== '')
  const cfg = status.config
  const capacity = Number(status.effective_capacity ?? preview?.effective_capacity ?? 0)
  const plan = (t: number, l: number, n: number) => L(`Bir işlemde en fazla -$${l} kaybedersin, hedef +$${t}. ${n > 0 ? `Aynı anda en fazla ${n} işlem açılır.` : ''}`, `You risk at most -$${l} per trade, target +$${t}. ${n > 0 ? `Up to ${n} trades at once.` : ''}`)
  const tip = !preview ? null
    : preview.verdict === 'NOT_RECOMMENDED' ? {tone: 'bad', text: L('Bu rakamlarla işlem kurulamıyor. Kaybı biraz artır ya da hedefi küçült.', 'These numbers cannot form a trade. Raise the loss a bit or lower the target.')}
    : preview.verdict === 'TIGHT' ? {tone: 'warn', text: L('Dar bir ayar. Normal dalgalanmada stop çalışabilir; daha geniş bir kayıp sınırı dene.', 'A tight setup. Normal moves may hit the stop; try a wider loss limit.')}
    : {tone: 'ok', text: plan(preview.target_usdt, preview.max_loss_usdt, capacity)}
  const wzRef = useRef<HTMLDivElement>(null)
  const firstStep = useRef(true)
  // Adım değişince (kaydet → oturum → başlat) panelin başına kaydır; yoksa ekran eski yerinde kalıyor
  useEffect(() => {
    if (firstStep.current) { firstStep.current = false; return }
    const id = requestAnimationFrame(() => { try { (wzRef.current?.closest('.fmRoot') ?? wzRef.current)?.scrollIntoView({behavior: 'smooth', block: 'start'}) } catch { /* eski tarayıcı */ } })
    return () => cancelAnimationFrame(id)
  }, [step])
  return <div className="fmWz" ref={wzRef} data-step={step} data-testid="farmer-wizard">
    {step === 1 && <div className="fmWzPane" key="s1" data-testid="farmer-setup">
      <div className="fmProfiles" role="radiogroup" aria-label={L('Hazır profil', 'Ready profile')}>
        {PROFILES.map(pr => { const Icon = pr.icon; return <button type="button" role="radio" aria-checked={picked?.id === pr.id} key={pr.id} data-tone={pr.tone} data-on={picked?.id === pr.id || undefined} onClick={() => edit({target_usdt: String(pr.target), max_loss_usdt: String(pr.loss), total_margin_cap: String(Math.min(pr.cap, cap))})}>
          <Icon aria-hidden="true"/><b>{L(pr.tr, pr.en)}</b><span>{L(pr.dtr, pr.den)}</span><em>+{pr.target} / -{pr.loss}</em>
        </button> })}
      </div>
      <p className="fmWzOr"><span>{picked ? L('Bu profil seçili. İstersen rakamları değiştir:', 'Profile selected. Adjust the numbers if you like:') : L('Ya da rakamları kendin yaz:', 'Or type your own numbers:')}</span></p>
      <div className="fmWzFields">
        {field('target_usdt', L('Kâr hedefi', 'Profit Target'), [0.3, 0.5, 1, 2], 'target', 'gain', TrendingUp)}
        {field('max_loss_usdt', L('En fazla kayıp', 'Max Loss'), [1, 2, 3, 5], 'maxloss', 'loss', TrendingDown)}
        {field('total_margin_cap', L('Marjin tavanı', 'Margin Cap'), caps, 'slots', 'cap', Wallet)}
      </div>
      {Number(form.target_usdt) > 0 && Number(form.max_loss_usdt) > 0 && <div className="fmWzBalance" data-testid="farmer-balance" aria-label={L('Kazanç ve kayıp dengesi', 'Gain versus loss balance')}>
        <div className="fmWzBar"><i data-k="gain" style={{flexGrow: Number(form.target_usdt)}}/><i data-k="loss" style={{flexGrow: Number(form.max_loss_usdt)}}/></div>
        <div className="fmWzBarTx"><span data-k="gain"><TrendingUp aria-hidden="true"/>{L('Kazanırsan', 'If it wins')} <b>+${Number(form.target_usdt)}</b></span><span data-k="loss"><TrendingDown aria-hidden="true"/>{L('Kaybedersen', 'If it loses')} <b>-${Number(form.max_loss_usdt)}</b></span></div>
      </div>}
      {tip && <p className="fmWzTip" data-tone={tip.tone} data-testid="farmer-step-tip"><b>{tip.tone === 'ok' ? L('Plan', 'Plan') : L('İpucu', 'Tip')}</b>{tip.text}</p>}
      <RiskPreview preview={preview} L={L} openGuide={openGuide}/>
      <details className="fmAdvanced"><summary>{L('Gelişmiş ayarlar', 'Advanced settings')}<ChevronDown aria-hidden="true"/></summary>
        <Setup {...{status, form, edit, canEdit: true, preview, formError, lang, L, openGuide, mode: 'wizard' as const, onActivate: p.onActivate, busy}}/>
      </details>
      {formError && <p className="fmFlash" data-tone="bad" role="alert" data-testid="farmer-form-error">{formError}</p>}
      <div className="fmWzNav fmWzSticky">
        <button type="button" className="fmBtn fmPrimary fmWide" data-testid="farmer-save" disabled={!!busy || !!formError || !filled} onClick={p.onSave}>{busy === 'save' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <Check aria-hidden="true"/>}{filled ? L('Kaydet ve devam et', 'Save and continue') : L('Önce bir profil seç', 'Pick a profile first')}</button>
        {p.editing && <button type="button" className="fmWzLink" onClick={p.onCancelEdit}>{L('Vazgeç', 'Cancel')}</button>}
      </div>
    </div>}

    {step === 2 && <div className="fmWzPane" key="s2" data-testid="farmer-step-session">
      <ul className="fmWzPoints">
        <li><i><Clock aria-hidden="true"/></i><span><b>{L('Süreli izin', 'Time-limited')}</b>{L('Süre dolunca yeni işlem açılmaz.', 'No new trades once it expires.')}</span></li>
        <li><i><ShieldCheck aria-hidden="true"/></i><span><b>{L('Pozisyonların korunur', 'Positions stay protected')}</b>{L('Süre bitse de stop ve hedef borsada durur.', 'Stop and target stay on the exchange.')}</span></li>
        <li><i><Zap aria-hidden="true"/></i><span><b>{L('İstediğin an durdur', 'Stop anytime')}</b>{L('STOP BOT yeni girişleri hemen keser.', 'STOP BOT halts new entries at once.')}</span></li>
      </ul>
      <div className="fmWzNav fmWzSticky">
        <button type="button" className="fmBtn fmPrimary fmWide" data-testid="farmer-arm-renew" disabled={!!busy} onClick={p.onRenew}>{busy === 'arm' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <Clock aria-hidden="true"/>}{L('Oturumu aç', 'Open session')}</button>
        <button type="button" className="fmWzLink" onClick={p.onEdit}>{L('Ayarları değiştir', 'Change settings')}</button>
      </div>
    </div>}

    {step === 3 && <div className="fmWzPane" key="s3" data-testid="farmer-step-start">
      {cfg && <div className="fmWzPlan"><small>{L('SENİN PLANIN', 'YOUR PLAN')}</small><p>{plan(cfg.target_usdt, cfg.max_loss_usdt, capacity)}</p>
        <dl><div><dt>{L('Hedef', 'Target')}</dt><dd className="up">{money(cfg.target_usdt, true)}</dd></div><div><dt>{L('En fazla kayıp', 'Max loss')}</dt><dd className="down">-{money(cfg.max_loss_usdt).replace('-', '')}</dd></div><div><dt>{L('Toplam tavan', 'Total cap')}</dt><dd>{cfg.total_margin_cap}<em> USDT</em></dd></div><div><dt>{L('Kaldıraç', 'Leverage')}</dt><dd>{cfg.leverage}×</dd></div></dl></div>}
      <ul className="fmWzChecks" aria-label={L('Hazırlık durumu', 'Readiness')}><li data-ok><i>✓</i>{L('Binance bağlı', 'Binance connected')}</li><li data-ok><i>✓</i>{L('Ayarlar kayıtlı', 'Settings saved')}</li><li data-ok><i>✓</i>{L('İşlem oturumu açık', 'Session open')}</li></ul>
      {cfg && capacity > 0 && <p className="fmWzCap" data-testid="farmer-step-capacity"><b>{L(`Aynı anda en fazla ${capacity} pozisyon`, `Up to ${capacity} positions at once`)}</b><span>{L(`Her pozisyonda ${cfg.margin_usdt} USDT teminat · toplam tavan ${cfg.total_margin_cap} USDT`, `${cfg.margin_usdt} USDT collateral each · total cap ${cfg.total_margin_cap} USDT`)}</span></p>}
      <div className="fmWzNav fmWzSticky">
        <button type="button" className="fmBtn fmPrimary fmWide fmWzGo" data-testid="farmer-activate" disabled={!!busy} onClick={p.onActivate}>{busy === 'activate' ? <Loader2 className="fmSpin" aria-hidden="true"/> : <Zap aria-hidden="true"/>}{L('KAIS FARMER’ı etkinleştir', 'Activate KAIS FARMER')}</button>
        <button type="button" className="fmWzLink" onClick={p.onEdit}>{L('Ayarları değiştir', 'Change settings')}</button>
      </div>
      <p className="fmHint">{L('Etkinleştirmek gerçek pozisyon açmaya izin verir. Hedefler garanti değildir.', 'Activating allows real positions to be opened. Targets are not guarantees.')}</p>
    </div>}
  </div>
}

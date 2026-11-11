import {useEffect, useMemo, useRef, useState} from 'react'
import {BarChart3} from 'lucide-react'
import DcaWatch from './DcaWatch'
import {computeDca, DCA_LIMITS, DCA_PRESETS, fitToCap, LOSS_HEADROOM, planFit, type DcaPreset, type DcaPresetForm, type DcaResult, type DcaSide} from './dcaMath'
import {goPositions} from '../../trade-intent'
import CoinAnalysisSheet, {type AnalysisRow} from './CoinAnalysisSheet'
import type {SignalMap} from './coin-ranking'
import '../../manual-analysis-sheet.css'
import './dca.css'

type Call = <T = any>(base: string, path: string, options?: RequestInit & {timeoutMs?: number}) => Promise<T>
type ConfirmConfig = {title: string; message: string; expected?: string; simple?: boolean; action: () => Promise<void>}

export type DcaPanelProps = {
  symbol: string
  price: number
  side: DcaSide
  liveArmed: boolean
  call: Call
  v25Base: string
  demoBase: string
  /** Demo hesabı: aynı ekran, adresler demo motoruna gider; ARM/izin gerekmez. */
  demo?: boolean
  askConfirm: (config: ConfirmConfig) => void
  /** Taranabilecek coinler (fiyat ve hacimle); "Uygun coin bul" için. */
  coins?: Array<{symbol: string; display?: string; price: number; volume?: number}>
  onPickSymbol?: (symbol: string) => void
  onPickSide?: (side: DcaSide) => void
  /** Kais AI sinyalleri (coin → yön eğilimi ve güven); varsa öneriler buna göre sıralanır. */
  signals?: SignalMap | null
  /** Canlı izin durumu: 24 saatlik risk izni geçerli mi, acil durdurma açık mı. */
  authorizationValid?: boolean
  emergency?: boolean
  permBusy?: boolean
  permError?: string
  /** 24 saatlik risk izni (gerekirse) + ARM'ı tek seferde verir. */
  onGrant?: () => Promise<boolean> | Promise<void> | void
  onClearEmergency?: () => void
}

type Account = 'DEMO' | 'LIVE'
type DcaInputFilters = {tickSize: number; stepSize: number; minNotional: number} | undefined
type Form = DcaPresetForm
type Preset = DcaPreset

const STATUS: Record<string, {text: string; tone: 'run' | 'wait' | 'stop' | 'bad'}> = {
  RUNNING: {text: 'Çalışıyor', tone: 'run'},
  PAUSED: {text: 'Durduruldu', tone: 'wait'},
  CYCLE_DONE: {text: 'Tur bitti', tone: 'run'},
  STOPPED: {text: 'Bitti', tone: 'stop'},
  COMPLETED: {text: 'Hedefe ulaştı', tone: 'stop'},
  EMERGENCY_CLOSED: {text: 'Acil kapatıldı', tone: 'stop'},
  BLOCKED: {text: 'Müdahale gerek', tone: 'bad'},
  INTERVENTION_REQUIRED: {text: 'Müdahale gerek', tone: 'bad'},
  EMERGENCY: {text: 'Acil durum', tone: 'bad'},
}
const FINAL = ['STOPPED', 'COMPLETED', 'EMERGENCY_CLOSED']
const MAX_BOTS = 3   // backend dca_live.MAX_BOTS_PER_USER ile aynı

const num = (value: unknown) => { const n = Number(String(value).replace(',', '.')); return Number.isFinite(n) ? n : NaN }
export const fmtPrice = (value: number) => (Number.isFinite(value) && value !== 0 ? String(Number(value.toPrecision(6))) : '—')
export const fmtUsd = (value: number, sign = false) => {
  if (!Number.isFinite(value)) return '—'
  const text = Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return `${value < 0 ? '−' : sign && value > 0 ? '+' : ''}${text} USDT`
}

/** Merdiven grafiği: fiyat ekseni üstte yüksek; emirler, ortalama, hedef, stop ve tahmini likidasyon. `scrub` 0..1: fiyat başlangıçtan stopa iner. */
export function DcaLadder({result, price, side, scrub}: {result: DcaResult; price: number; side: DcaSide; scrub: number}) {
  const W = 384, H = 340, padT = 24, padB = 30
  const sign = side === 'LONG' ? 1 : -1
  const stages = result.stages
  const full = stages[stages.length - 1]
  const core = [price, result.stopPrice, stages[0].tpPrice, full.tpPrice, ...stages.map(s => s.entry)]
  const coreHi = Math.max(...core), coreLo = Math.min(...core)
  const room = (coreHi - coreLo) * 0.16
  // Likidasyon çoğu zaman çok uzaktır; ekseni ezmesin diye ancak yakınsa gerçek yerinde, değilse kenarda "uzakta" oku olarak çizilir.
  const liq = result.liquidation
  const liqPast = (side === 'LONG' ? result.stopPrice - liq : liq - result.stopPrice)
  const liqNear = Number.isFinite(liq) && liq > 0 && liqPast >= 0 && liqPast <= (coreHi - coreLo) * 0.6
  const hi = side === 'LONG' ? coreHi + room * 0.4 : coreHi + (liqNear ? liqPast : room)
  const lo = side === 'LONG' ? coreLo - (liqNear ? liqPast : room) : coreLo - room * 0.4
  const span = hi - lo || 1
  const plotH = H - padT - padB
  const y = (value: number) => padT + ((hi - value) / span) * plotH
  const yLiq = liqNear ? y(liq) : (side === 'LONG' ? H - padB + 6 : padT - 6)
  const maxNotional = Math.max(...stages.map(s => s.notional))
  const scrubPrice = price + (result.stopPrice - price) * scrub
  const filled = (entry: number) => sign * (scrubPrice - entry) <= 1e-9
  const barX = 74, barMax = 130, gutter = 262
  const baseIsPrice = Math.abs(stages[0].entry - price) < 1e-9
  // Sağ sütun etiketleri üst üste binmesin: y'ye göre sıralanır, yakın olanlar aşağı itilir.
  const tags = [
    {key: 'tp', y: y(full.tpPrice), text: `Hedef ${fmtPrice(full.tpPrice)}`, cls: 'dcaTagTp'},
    ...(stages[0].tpPrice !== full.tpPrice ? [{key: 'tp0', y: y(stages[0].tpPrice), text: `Taban hedefi ${fmtPrice(stages[0].tpPrice)}`, cls: 'dcaTagTp faint'}] : []),
    {key: 'avg', y: y(full.avgEntry), text: `Ort. ${fmtPrice(full.avgEntry)}`, cls: 'dcaTagAvg'},
    {key: 'stop', y: y(result.stopPrice), text: `STOP ${fmtPrice(result.stopPrice)}`, cls: 'dcaTagStop'},
    ...(baseIsPrice ? [] : [{key: 'px', y: y(price), text: `Fiyat ${fmtPrice(price)}`, cls: 'dcaTagPx'}]),
    {key: 'liq', y: yLiq, text: `Likid. ~${fmtPrice(liq)}${liqNear ? '' : side === 'LONG' ? ' ↓' : ' ↑'}`, cls: 'dcaTagLiq'},
  ].sort((a, b) => a.y - b.y)
  for (let i = 1; i < tags.length; i++) if (tags[i].y - tags[i - 1].y < 13) tags[i] = {...tags[i], y: tags[i - 1].y + 13}
  return (
    <svg className="dcaLadder" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="DCA merdiven grafiği">
      <defs>
        <linearGradient id="dcaZone" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3ddc97" stopOpacity="0.14" />
          <stop offset="1" stopColor="#ff6b6b" stopOpacity="0.20" />
        </linearGradient>
        <linearGradient id="dcaBar" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#3ddc97" />
          <stop offset="1" stopColor="#16c9a8" />
        </linearGradient>
        <pattern id="dcaHatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="7" height="7" fill="rgba(255,107,107,0.05)" />
          <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(255,107,107,0.35)" strokeWidth="2" />
        </pattern>
        <filter id="dcaGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect x={barX - 8} y={Math.min(y(price), y(result.stopPrice))} width={gutter - barX + 4} height={Math.abs(y(result.stopPrice) - y(price))} rx="10" fill="url(#dcaZone)" />
      <rect x={barX - 8} y={Math.min(y(result.stopPrice), yLiq)} width={gutter - barX + 4} height={Math.abs(yLiq - y(result.stopPrice))} fill="url(#dcaHatch)" />
      {stages.map(s => {
        const on = filled(s.entry)
        const w = 26 + (s.notional / maxNotional) * (barMax - 26)
        return (
          <g key={s.index} className="dcaStageG" data-on={on ? '1' : '0'}>
            <line x1={barX - 8} x2={gutter - 4} y1={y(s.entry)} y2={y(s.entry)} className="dcaStageLine" />
            <rect x={barX} y={y(s.entry) - 7} width={w} height={14} rx="7" fill="url(#dcaBar)" className="dcaStageBar" />
            <circle cx={barX} cy={y(s.entry)} r="6" className="dcaStageNode" />
            <text x="8" y={y(s.entry) + 4} className="dcaLblL">{s.index === 0 ? (baseIsPrice ? 'Taban' : 'Taban') : `Ek ${s.index}`}</text>
            <text x={barX + w + 8} y={y(s.entry) + 4} className="dcaLblR">{fmtPrice(s.entry)}</text>
          </g>
        )
      })}
      <line x1={barX - 8} x2={gutter - 4} y1={y(full.avgEntry)} y2={y(full.avgEntry)} className="dcaAvgLine" />
      <line x1={barX - 8} x2={gutter - 4} y1={y(full.tpPrice)} y2={y(full.tpPrice)} className="dcaTpLine" />
      {stages[0].tpPrice !== full.tpPrice && <line x1={barX - 8} x2={gutter - 4} y1={y(stages[0].tpPrice)} y2={y(stages[0].tpPrice)} className="dcaTpLine faint" />}
      <line x1={barX - 8} x2={gutter - 4} y1={y(result.stopPrice)} y2={y(result.stopPrice)} className="dcaStopLine" />
      {liqNear && <line x1={barX - 8} x2={gutter - 4} y1={yLiq} y2={yLiq} className="dcaLiqLine" />}
      <line x1={barX - 8} x2={gutter - 4} y1={y(price)} y2={y(price)} className="dcaPriceLine" />
      {tags.map(tag => <text key={tag.key} x={gutter + 2} y={tag.y + 4} className={tag.cls}>{tag.text}</text>)}
      <g className="dcaScrub" style={{transform: `translateY(${y(scrubPrice)}px)`}}>
        <line x1={barX - 8} x2={gutter - 4} y1="0" y2="0" className="dcaScrubLine" />
        <circle cx={gutter - 14} cy="0" r="7" filter="url(#dcaGlow)" className="dcaScrubDot" />
      </g>
    </svg>
  )
}

function scenarioAt(result: DcaResult, price: number, side: DcaSide, scrub: number) {
  const sign = side === 'LONG' ? 1 : -1
  const at = price + (result.stopPrice - price) * scrub
  const filled = result.stages.filter(s => sign * (at - s.entry) <= 1e-9)
  const top = filled[filled.length - 1] ?? result.stages[0]
  const pnl = sign * (at - top.avgEntry) * top.cumQty
  return {at, count: filled.length, avg: top.avgEntry, tp: top.tpPrice, qty: top.cumQty, pnl, atStop: scrub >= 0.999}
}

type ScanItem = {symbol: string; display: string; price: number; change: number; total: number; fixed: boolean; idx: number}
const FILTER_CACHE = new Map<string, {tickSize: number; stepSize: number; minNotional: number}>()
const fitLabel = (state: 'ok' | 'fix' | 'no') => state === 'ok' ? 'Sana uygun' : state === 'fix' ? 'Küçültülerek uygun' : 'Bu coinde uygun değil'

export default function DcaPanel(props: DcaPanelProps) {
  const {symbol, price, side, liveArmed: liveArmedProp, call, v25Base, askConfirm, coins = [], signals, onPickSymbol, onPickSide, authorizationValid: authorizationProp = true, emergency: emergencyProp = false, permBusy = false, permError = '', onGrant, onClearEmergency} = props
  const account: Account = props.demo ? 'DEMO' : 'LIVE'
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [started, setStarted] = useState<{symbol: string; side: DcaSide; at: number; symbols?: string[]} | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [anaSym, setAnaSym] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [form, setForm] = useState<Form>(DCA_PRESETS[1].form)
  const [presetId, setPresetId] = useState('')
  const [filters, setFilters] = useState<{tickSize: number; stepSize: number; minNotional: number} | null>(null)
  const [filtersError, setFiltersError] = useState('')
  const [scrub, setScrub] = useState(0.55)
  const [status, setStatus] = useState<any>(null)
  const [statusError, setStatusError] = useState('')
  const [notStarted, setNotStarted] = useState<Array<{sym: string; why: string}>>([])
  const [server, setServer] = useState<{state: 'idle' | 'busy' | 'ok' | 'error'; text: string; plan?: any; gate?: {ok: boolean; reason: string}}>({state: 'idle', text: ''})
  const [busy, setBusy] = useState('')
  const [smartEntry, setSmartEntry] = useState(true)
  const [trailOn, setTrailOn] = useState(false)
  const [extras, setExtras] = useState<string[]>([])
  const [extraNote, setExtraNote] = useState('')
  const [startTap, setStartTap] = useState(0)
  const autoFixes = useRef(0)
  const panelRef = useRef<HTMLElement | null>(null)
  // Başlatma bildirimleri / sonuç panelin üstünde çıkar; ekran oraya kayar ki kullanıcı kaçırmasın.
  const toPanelTop = () => { try { panelRef.current?.scrollIntoView({behavior: 'smooth', block: 'start'}) } catch { /* eski tarayıcı */ } }
  const [startError, setStartError] = useState('')
  const [launching, setLaunching] = useState<{coins: string[]; done: string[]; now: string; phase: string} | null>(null)

  const isDemo = account === 'DEMO'
  const liveArmed = isDemo ? true : liveArmedProp
  const authorizationValid = isDemo ? true : authorizationProp
  const emergency = isDemo ? false : emergencyProp
  const base = isDemo ? `${props.demoBase}/dca` : `${v25Base}/dca/live`
  const filtersPath = isDemo ? `${props.demoBase}/grid/filters` : `${v25Base}/grid/filters`
  const synced = picked !== null && symbol === picked

  const readFilters = async (sym: string) => {
    const cached = FILTER_CACHE.get(sym)
    if (cached) return cached
    const data = await call<any>(filtersPath, `?symbol=${encodeURIComponent(sym)}`)
    const tick = Number(data?.tickSize), stepSize = Number(data?.stepSize), min = Number(data?.minNotional)
    if (!(tick > 0 && stepSize > 0 && min >= 0)) throw new Error('Borsa kuralları geçersiz geldi.')
    const value = {tickSize: tick, stepSize, minNotional: min}
    FILTER_CACHE.set(sym, value)
    return value
  }

  useEffect(() => {
    if (!symbol || !picked) return
    let cancelled = false
    setFilters(null); setFiltersError('')
    readFilters(symbol).then(value => { if (!cancelled) setFilters(value) }).catch(error => { if (!cancelled) setFiltersError(error?.message || 'Borsa kuralları okunamadı.') })
    return () => { cancelled = true }
  }, [symbol, picked, filtersPath])

  const [lossEdit, setLossEdit] = useState<{open: boolean; value: string; ok: boolean; busy: boolean; error: string}>({open: false, value: '', ok: false, busy: false, error: ''})
  const saveLoss = () => {
    const value = Number(lossEdit.value.replace(',', '.'))
    if (!(value >= 0.5 && value <= 25)) { setLossEdit(c => ({...c, error: 'Sınır 0,5 ile 25 USDT arasında olmalı.'})); return }
    setLossEdit(c => ({...c, busy: true, error: ''}))
    void call(v25Base, '/policy', {method: 'PUT', timeoutMs: 30000, body: JSON.stringify({max_loss_per_trade: value, understood: true})})
      .then(() => loadStatus()).then(() => setLossEdit({open: false, value: '', ok: false, busy: false, error: ''}))
      .catch(error => setLossEdit(c => ({...c, busy: false, error: error?.message || 'Kaydedilemedi.'})))
  }
  const loadStatus = () => call<any>(base, '/status').then(data => { setStatus(data); setStatusError('') }).catch(error => { setStatus(null); setStatusError(error?.message || 'Durum okunamadı.') })
  useEffect(() => {
    let cancelled = false
    const tick = () => { if (!cancelled) void loadStatus() }
    tick()
    const timer = window.setInterval(tick, started ? 2000 : 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [base, started !== null])
  useEffect(() => { setServer({state: 'idle', text: ''}) }, [symbol, side, form, smartEntry])
  useEffect(() => { if (step !== 2) autoFixes.current = 0 }, [step])
  useEffect(() => { setNote('') }, [symbol, side])
  useEffect(() => { setExtras([]); setExtraNote('') }, [symbol, side, form])

  const input = useMemo(() => ({
    side, price, baseMargin: num(form.margin), safetyOrders: form.safety, stepPct: num(form.step), volumeMultiplier: num(form.volume),
    takeProfitPct: num(form.tp), stopGapPct: num(form.gap), leverage: form.leverage, filters: filters ?? undefined,
  }), [side, price, form, filters])
  const result = useMemo(() => computeDca(input), [input])
  const ready = synced && price > 0 && filters !== null
  const scenario = result.valid ? scenarioAt(result, price, side, scrub) : null
  const set = <K extends keyof Form>(key: K, value: Form[K]) => { setPresetId('custom'); setForm(current => ({...current, [key]: value})) }
  const cap = Number(status?.live_max_margin_usdt) > 0 ? Number(status.live_max_margin_usdt) : 10
  const rl = status?.risk_limits
  const maxLoss = Math.min(...[Number(rl?.max_loss_per_trade), Number(rl?.daily_loss_left)].filter(v => Number.isFinite(v) && v > 0), Infinity)
  const lossCap = Number.isFinite(maxLoss) ? maxLoss : 0
  // Birlikte açılan botlar aynı günlük zarar hakkını paylaşır (çalışan bot payını ayırır): coin sayısı arttıkça her botun payı küçülür.
  const nBots = 1 + extras.length
  const dailyLeftNow = Number(rl?.daily_loss_left)
  const lossCapN = nBots > 1 && dailyLeftNow > 0 ? Math.min(lossCap || Infinity, dailyLeftNow / nBots) : lossCap
  const inputOf = (f: Form, over?: {price: number; filters: NonNullable<DcaInputFilters>}) => ({...input, ...over, baseMargin: num(f.margin), safetyOrders: f.safety, stepPct: num(f.step), volumeMultiplier: num(f.volume), takeProfitPct: num(f.tp), stopGapPct: num(f.gap), leverage: f.leverage})
  const fmtMargin = (value: number) => String(Number(value.toFixed(2)))
  const falls = side === 'LONG' ? 'düşerse' : 'yükselirse'

  // Üç hazır plan, bu coin ve canlı toplam marj limiti için gerçek hesapla: gerekirse otomatik küçültülür.
  const presetPlans = useMemo(() => ready ? DCA_PRESETS.map(preset => {
    const fit = planFit(inputOf(preset.form), cap, lossCap)
    const fix = fit.state === 'over-cap' ? fitToCap(inputOf(preset.form), cap, lossCap) : null
    const state: 'ok' | 'fix' | 'no' = fit.state === 'ok' ? 'ok' : fix ? 'fix' : 'no'
    const adjusted: Form = fit.state === 'ok' && fit.margin ? {...preset.form, margin: fmtMargin(fit.margin)} : fix ? {...preset.form, margin: fmtMargin(fix.margin), safety: fix.safety, volume: fix.volume.toFixed(1)} : preset.form
    return {preset, state, form: adjusted, result: computeDca(inputOf(adjusted)), shrunk: state === 'fix'}
  }) : [], [input, ready, cap, lossCap])
  const fitNow = useMemo(() => ready ? planFit(input, cap, lossCap) : null, [input, ready, cap, lossCap])
  const fixNow = useMemo(() => ready && fitNow?.state === 'over-cap' ? fitToCap(input, cap, lossCap) : null, [input, ready, cap, lossCap, fitNow])

  const marginChips = useMemo(() => [5, 10, 15, 20, 30, 50], [])
  // Özel (elle değiştirilmiş) plan: canlı limitine ve tek işlem zarar sınırına sığmıyorsa nedenini söyler; "Otomatik sığdır" düzeltir.
  const customBlock = !ready || !result.valid ? ''
    : result.totalMargin > cap + 1e-9 ? `Bu ayar ${fmtUsd(result.totalMargin)} marj ister; canlı limitin ${cap} USDT.`
    : lossCapN > 0 && result.worstLoss > lossCapN * LOSS_HEADROOM + 1e-9 ? (nBots > 1 ? `${nBots} coin birlikte: her botun en kötü kaybı en çok ${fmtUsd(lossCapN)} olabilir (günlük zarar hakkı paylaşılır); şimdi ${fmtUsd(result.worstLoss)}.` : `En kötü kayıp ${fmtUsd(result.worstLoss)}; tek işlem zarar sınırın ${lossCap} USDT.`)
    : ''
  const fixNowCustom = ready && customBlock ? fitToCap(input, cap, lossCapN) : null
  const multiFixed = useRef('')
  useEffect(() => {
    // Coin eklenince plan kendiliğinden küçülür; yoksa 2. / 3. bot sunucuda "zarar hakkı yok" diye reddedilir.
    if (step !== 2 || nBots < 2 || !fixNowCustom || multiFixed.current === `${nBots}|${symbol}`) return
    multiFixed.current = `${nBots}|${symbol}`
    setPresetId('custom'); setForm(c => ({...c, margin: fmtMargin(fixNowCustom.margin), safety: fixNowCustom.safety, volume: fixNowCustom.volume.toFixed(1)}))
    setNote(`${nBots} coin birlikte açılacağı için plan küçültüldü: günlük zarar hakkı botlar arasında paylaşılıyor.`)
  }, [step, nBots, symbol, fixNowCustom])
  useEffect(() => { if (nBots < 2) multiFixed.current = '' }, [nBots])
  const applyCustomFix = () => { if (fixNowCustom) { setPresetId('custom'); setForm(c => ({...c, margin: fmtMargin(fixNowCustom.margin), safety: fixNowCustom.safety, volume: fixNowCustom.volume.toFixed(1)})) } }
  const lastPlanRef = useRef('')
  const choosePlan = (plan: NonNullable<typeof presetPlans>[number]) => {
    lastPlanRef.current = plan.preset.id
    setPresetId(plan.preset.id)
    setForm({...plan.form, recycle: form.recycle})   // "Otomatik tekrar" kullanıcının seçimidir; plan değişince sıfırlanmaz
    setNote(lossCap > 0 && plan.state !== 'no' ? `Plan tek işlem zarar sınırın olan ${lossCap} USDT'ye göre ayarlandı (en kötü kayıp bunun altında).` : plan.shrunk ? `Plan ${cap} USDT limitine sığsın diye otomatik küçültüldü (daha az ek alım, daha yavaş büyüme).` : plan.state === 'ok' ? `Yatırım tutarı ${cap} USDT limitine göre ayarlandı.` : '')
  }
  const autoRef = useRef('')
  useEffect(() => {
    if (step !== 2 || !ready || presetId) return
    const key = `${symbol}|${side}`
    if (autoRef.current === key) return
    const choice = presetPlans.find(plan => plan.preset.id === 'balanced' && plan.state !== 'no') ?? presetPlans.find(plan => plan.state !== 'no')
    if (choice) { autoRef.current = key; choosePlan(choice) }
  }, [step, ready, presetId, presetPlans])

  // Uygun coin tarayıcı: açılınca kendiliğinden çalışır; en hacimli coinlerin borsa kurallarını okur, Dengeli planı gerçek hesapla dener.
  const [scan, setScan] = useState<{state: 'idle' | 'busy' | 'done'; checked: number; total: number; items: ScanItem[]}>({state: 'idle', checked: 0, total: 0, items: []})
  const scanToken = useRef(0)
  const scanKey = `${coins.length}|${cap}|${lossCap}|${status === null ? 0 : 1}`
  useEffect(() => {
    if (!coins.length || (status === null && !statusError)) return
    const token = ++scanToken.current
    const pool = coins.filter(coin => coin.price > 0).sort((a, b) => Number(b.volume ?? 0) - Number(a.volume ?? 0)).slice(0, 40)
    const found: ScanItem[] = []
    let checked = 0
    setScan({state: 'busy', checked: 0, total: pool.length, items: []})
    const queue = pool.map((coin, idx) => ({coin, idx}))
    const worker = async () => {
      for (let job = queue.shift(); job; job = queue.shift()) {
        if (token !== scanToken.current) return
        const {coin, idx} = job
        try {
          const f = await readFilters(coin.symbol)
          const trial = inputOf(DCA_PRESETS[1].form, {price: coin.price, filters: f})
          const fit = planFit(trial, cap, lossCap)
          if (fit.state === 'ok' && fit.total) found.push({symbol: coin.symbol, display: coin.display || coin.symbol, price: coin.price, change: Number((coin as any).change ?? NaN), total: fit.total, fixed: false, idx})
          else if (fit.state === 'over-cap') { const fix = fitToCap(trial, cap, lossCap); if (fix) found.push({symbol: coin.symbol, display: coin.display || coin.symbol, price: coin.price, change: Number((coin as any).change ?? NaN), total: fix.total, fixed: true, idx}) }
        } catch { /* bu coin atlanır */ }
        checked += 1
        if (token === scanToken.current) setScan({state: 'busy', checked, total: pool.length, items: [...found]})
      }
    }
    void Promise.all(Array.from({length: 6}, worker)).then(() => { if (token === scanToken.current) setScan({state: 'done', checked, total: pool.length, items: [...found]}) })
    return () => { scanToken.current += 1 }
  }, [scanKey])

  const rankScan = (limit: number) => {
    const rank = (item: ScanItem) => {
      const sig = signals?.[item.symbol]
      if (sig && sig.lean !== 'NEUTRAL') return sig.lean === side ? 2000 + sig.confidence : -1
      // Kais AI sinyali yoksa yön için 24 saatlik değişime bakılır: LONG için yükselen, SHORT için düşen coinler öne geçer; ters gidenler sona.
      const move = item.change
      if (!Number.isFinite(move) || Math.abs(move) < 0.3) return 1000
      return (side === 'LONG') === (move > 0) ? 1500 + Math.min(Math.abs(move), 10) : -1
    }
    const ranked = scan.items.map(item => ({item, r: rank(item)}))
    const good = ranked.filter(entry => entry.r >= 0).sort((a, b) => b.r - a.r || Number(a.item.fixed) - Number(b.item.fixed) || a.item.total - b.item.total || a.item.idx - b.item.idx)
    const against = ranked.filter(entry => entry.r < 0).sort((a, b) => a.item.idx - b.item.idx)
    return [...good, ...(good.length < 4 ? against.slice(0, 4 - good.length) : [])].slice(0, limit).map(entry => ({...entry.item, against: entry.r < 0, strong: entry.r >= 1500}))
  }
  const suggestions = useMemo(() => rankScan(6), [scan.items, signals, side])
  const extraPool = useMemo(() => rankScan(12), [scan.items, signals, side])
  const known = new Map(scan.items.map(item => [item.symbol, item]))
  const q = query.trim().toUpperCase().replace('/', '')
  const searchRows = q ? coins.filter(coin => coin.symbol.includes(q) || String(coin.display || '').toUpperCase().replace('/', '').includes(q)).slice(0, 6) : []

  const anaRows: AnalysisRow[] = query
    ? searchRows.map(coin => ({symbol: coin.symbol, display: coin.display, price: coin.price, change: Number((coin as any).change ?? NaN)}))
    : suggestions.map(item => ({symbol: item.symbol, display: item.display, price: item.price, change: item.change}))
  const pickCoin = (sym: string) => { setAnaSym(null); onPickSymbol?.(sym); setPicked(sym); setPresetId(''); setNote(''); setQuery(''); setStep(2) }
  const top3 = suggestions.filter(item => !item.against).slice(0, 3)
  const pickTop3 = () => {
    const [first, ...rest] = top3
    if (!first) return
    pickCoin(first.symbol)
    setExtraNote('')
    setExtras(rest.map(item => item.symbol).slice(0, Math.max(0, MAX_BOTS - activeCount - 1)))
  }
  const pickSide = (next: DcaSide) => { onPickSide?.(next); setPresetId(''); autoRef.current = '' }

  const smartAvail = status?.smart_entry_enabled === true
  const trailAvail = status?.trailing_enabled === true && num(form.tp) >= 0.4
  const useSmart = smartAvail && smartEntry
  const trailPct = trailAvail && trailOn ? Number((num(form.tp) / 2).toFixed(2)) : 0
  const bodyFor = (sym: string, inp: ReturnType<typeof inputOf>, preview: boolean, confirmation = '') => JSON.stringify({
    symbol: sym, direction: side, base_margin_usdt: inp.baseMargin, safety_orders: inp.safetyOrders, step_pct: inp.stepPct, volume_multiplier: inp.volumeMultiplier,
    take_profit_pct: inp.takeProfitPct, stop_gap_pct: inp.stopGapPct, leverage: inp.leverage, recycle: form.recycle, smart_entry: useSmart, trailing_pct: trailPct, preview, confirmation,
  })
  const body = (preview: boolean, confirmation = '') => bodyFor(symbol, input, preview, confirmation)
  const activeCount = (status?.grids ?? []).filter((g: any) => (g?.kind === 'DCA' || g?.spec?.meta?.kind === 'DCA') && !FINAL.includes(g.status)).length
  const slots = Math.max(0, MAX_BOTS - activeCount - 1)
  const extraInput = async (sym: string) => {
    const coin = coins.find(c => c.symbol === sym)
    if (!coin || !(coin.price > 0)) throw new Error('Fiyat okunamadı.')
    const f = await readFilters(sym)
    const trial = inputOf(form, {price: coin.price, filters: f})
    const fit = planFit(trial, cap, lossCapN)
    if (fit.state !== 'ok') throw new Error('Bu plan bu coine sığmıyor.')
    return trial
  }
  // Ek coin adayları: her biri için mevcut plan ayarıyla gerçek hesap (yatırım, en kötü kayıp, sığıyor mu).
  const [extraFits, setExtraFits] = useState<Record<string, {state: 'busy' | 'ok' | 'no'; total?: number; worst?: number; reason?: string}>>({})
  useEffect(() => {
    if (step !== 2 || !ready || slots <= 0) return
    let cancelled = false
    const list = extraPool.filter(item => item.symbol !== symbol)
    setExtraFits(Object.fromEntries(list.map(item => [item.symbol, {state: 'busy' as const}])))
    void (async () => {
      for (const item of list) {
        if (cancelled) return
        let next: {state: 'ok' | 'no'; total?: number; worst?: number; reason?: string}
        try {
          const coin = coins.find(c => c.symbol === item.symbol)
          if (!coin || !(coin.price > 0)) throw new Error('Fiyat okunamadı')
          const f = await readFilters(item.symbol)
          const trial = inputOf(form, {price: coin.price, filters: f})
          const r = computeDca(trial)
          if (!r.valid) next = {state: 'no', reason: 'Bu plan bu coine uymuyor'}
          else if (planFit(trial, cap, lossCap).state !== 'ok') next = {state: 'no', reason: 'Limitine sığmıyor'}
          else next = {state: 'ok', total: r.totalMargin, worst: r.worstLoss}
        } catch (error: any) { next = {state: 'no', reason: error?.message || 'Okunamadı'} }
        if (!cancelled) setExtraFits(current => ({...current, [item.symbol]: next}))
      }
    })()
    return () => { cancelled = true }
  }, [step, ready, slots, symbol, side, form, extraPool.map(item => item.symbol).join(',')])
  const toggleExtra = (sym: string) => {
    setExtraNote('')
    if (extras.includes(sym)) { setExtras(c => c.filter(x => x !== sym)); return }
    if (extras.length >= slots) { setExtraNote(`En fazla ${slots} ek coin seçebilirsin.`); return }
    const fit = extraFits[sym]
    if (fit?.state === 'no') { setExtraNote(`${sym.replace('USDT', '')}: ${fit.reason || 'Uygun değil.'}`); return }
    setExtras(c => [...c, sym])
  }
  const okExtras = extraPool.filter(item => item.symbol !== symbol && extraFits[item.symbol]?.state === 'ok').map(item => item.symbol)
  const pickAllExtras = () => { setExtraNote(''); setExtras(okExtras.slice(0, slots)) }
  const extrasTotal = extras.reduce((sum, sym) => sum + (extraFits[sym]?.total ?? 0), 0)
  const extrasWorst = extras.reduce((sum, sym) => sum + (extraFits[sym]?.worst ?? 0), 0)
  const checkOnServer = async () => {
    setServer({state: 'busy', text: 'Sunucu planı kendi kurallarıyla yeniden hesaplıyor…'})
    try {
      const data = await call<any>(base, '/start', {method: 'POST', body: body(true), timeoutMs: 60000})
      setServer({state: 'ok', plan: data.plan, text: 'Sunucu planı onayladı.', gate: data.entry_gate && typeof data.entry_gate.ok === 'boolean' ? {ok: data.entry_gate.ok, reason: String(data.entry_gate.reason || '')} : undefined})
    } catch (error: any) {
      const text = String(error?.message || 'Sunucu planı reddetti.')
      // Sunucu "en kötü zarar sınırı aştı" derse (çoğu kez kuruş farkı) yatırımı bir tık küçültüp kendisi yeniden dener; en fazla 2 kez.
      if (/En kötü zarar/.test(text) && autoFixes.current < 2 && lossCap > 0) {
        const fit = planFit(input, cap, lossCap * (autoFixes.current === 0 ? 0.93 : 0.8))
        if (fit.state === 'ok' && fit.margin && fit.margin < num(form.margin)) {
          autoFixes.current += 1
          set('margin', String(fit.margin))
          setNote(`Sunucu zarar sınırını kuruş farkıyla aştı; yatırım ${fit.margin} USDT'ye küçültüldü, yeniden kontrol ediliyor.`)
          return
        }
      }
      setServer({state: 'error', text})
    }
  }
  useEffect(() => {
    if (step !== 2 || server.state !== 'idle' || !result.valid || !ready || !presetId || customBlock !== '') return
    const timer = window.setTimeout(() => { void checkOnServer() }, 700)   // ayar değişirken her dokunuşta sunucuya gitmesin
    return () => window.clearTimeout(timer)
  }, [step, server.state, result.valid, ready, presetId, customBlock])

  const liveEnabled = status?.enabled !== false && status?.enabled !== undefined
  const whyBlocked = !ready ? (filtersError || 'Borsa kuralları yükleniyor…')
    : !result.valid ? 'Önce planı düzelt.'
    : !liveEnabled ? 'Canlı DCA sunucuda kapalı.'
    : !liveArmed || !authorizationValid ? 'Önce yukarıdaki "İzni ver" düğmesine bas.'
    : result.totalMargin > cap + 1e-9 ? `Canlı DCA şu an en çok ${cap} USDT toplam marjinle çalışır.`
    : server.state === 'busy' ? 'Sunucu kontrol ediyor…'
    : customBlock ? customBlock
    : server.state === 'error' ? 'Sunucu planı reddetti (sebebi yukarıda). Riski azaltıp tekrar dene.'
    : server.state !== 'ok' ? 'Sunucu onayı bekleniyor.'
    : useSmart && server.gate && !server.gate.ok ? 'Akıllı başlama: Kais şu an bu coini uygun görmüyor (sebebi yukarıda). Başka coin seç ya da akıllı başlamayı kapat.'
    : ''

  const start = () => {
    const n = 1 + extras.length
    askConfirm({
      title: isDemo ? 'DEMO DCA BAŞLAT' : 'CANLI DCA BAŞLAT',
      message: `GERÇEK PARA. ${[symbol, ...extras].join(', ')} ${side}: her coinde taban + ${form.safety} ek emir, coin başına marjin ${fmtUsd(result.totalMargin)}, kaldıraç ${form.leverage}x. `
        + (n > 1 ? `${n} coin birlikte başlar; toplam marjin ${fmtUsd(result.totalMargin * n)}, hepsinde stop çalışırsa en kötü zarar en çok ${fmtUsd(result.worstLoss * n)} (yaklaşık). ` : `Tüm emirler dolup stop çalışırsa tahmini en kötü zarar ${fmtUsd(result.worstLoss)}. `)
        + (useSmart ? 'Akıllı başlama açık: Kais uygun görene kadar bot bekler. ' : 'Taban emir hemen piyasadan girer. ')
        + 'Ek emirler limit olarak bekler, stop dolan her miktar için kurulur.',
      expected: isDemo ? 'DEMO DCA BAŞLAT' : 'CANLI DCA BAŞLAT',
      action: async () => { toPanelTop(); try { await runStart(); toPanelTop() } catch (error: any) { setStartError(error?.message || 'Başlatılamadı.'); toPanelTop(); throw error } },
    })
  }
  const runStart = async () => {
    try { await runStartInner() } finally { setLaunching(null) }
  }
  const runStartInner = async () => {
        setStartError('')
        setLaunching({coins: [symbol, ...extras], done: [], now: symbol, phase: 'Hazırlanıyor…'})
        const word = isDemo ? 'DEMO DCA BAŞLAT' : 'CANLI DCA BAŞLAT'
        const jobs: Array<{sym: string; inp: ReturnType<typeof inputOf>; skip?: boolean}> = [{sym: symbol, inp: input}]
        for (const sym of extras) jobs.push({sym, inp: await extraInput(sym)})
        const failed: string[] = []
        const failedList: Array<{sym: string; why: string}> = []
        setNotStarted([])
        // Önce hepsini sunucuya onaylat: ana coin reddedilirse hiçbiri başlamaz; ek coin reddedilirse o atlanır, diğerleri başlar.
        if (jobs.length > 1) setLaunching(c => c && {...c, phase: 'Ek coinler sunucuda kontrol ediliyor…'})
        for (const job of jobs.slice(1)) {
          try { await call(base, '/start', {method: 'POST', body: bodyFor(job.sym, job.inp, true), timeoutMs: 60000}) }
          catch (error: any) { failed.push(`${job.sym}: ${error?.message || 'reddedildi'}`); failedList.push({sym: job.sym, why: error?.message || 'Sunucu reddetti'}); job.skip = true }
        }
        for (const [index, job] of jobs.entries()) {
          if (job.skip) continue
          setLaunching(c => c && {...c, now: job.sym, phase: 'Binance\'e emir gönderiliyor…'})
          try { await call(base, '/start', {method: 'POST', timeoutMs: 60000, body: bodyFor(job.sym, job.inp, false, word)}) }
          catch (error: any) { if (index === 0) throw error; failed.push(`${job.sym}: ${error?.message || 'başlamadı'}`); failedList.push({sym: job.sym, why: error?.message || 'Başlamadı'}) }
          setLaunching(c => c && {...c, done: [...c.done, job.sym]})
        }
        setStarted({symbol, side, at: Date.now(), symbols: jobs.filter(j => !j.skip && !failedList.some(f => f.sym === j.sym)).map(j => j.sym)})
        setStep(4)
        setNotStarted(failedList)   // loadStatus hata satırını siler; bu liste silinmez
        await loadStatus()
  }
  const act = (kind: 'stop' | 'emergency' | 'resume', gridId: string) => {
    const query = `?grid_id=${encodeURIComponent(gridId)}`
    if (kind === 'resume') { setBusy(gridId); void Promise.resolve(!liveArmed && onGrant ? onGrant() : true).then(() => call(base, `/resume${query}`, {method: 'POST', timeoutMs: 60000})).then(loadStatus).catch(error => setStatusError(error?.message || 'Devam ettirilemedi.')).finally(() => setBusy('')); return }
    askConfirm({
      title: kind === 'stop' ? 'DCA botunu durdur' : 'DCA ACİL DURDUR',
      message: kind === 'stop' ? 'Bekleyen ek emirler iptal edilir. Açık pozisyon varsa stop ve kâr hedefiyle yerinde kalır.'
        : 'Bekleyen emirler iptal edilir, pozisyon piyasadan kapatılır ve kapandığı borsadan doğrulanır. Doğrulanamazsa koruyucu stoplara dokunulmaz.',
      expected: kind === 'emergency' ? 'ACİL DURDUR' : undefined, simple: kind === 'stop',
      action: async () => { await call(base, `/${kind}${query}`, {method: 'POST', timeoutMs: 90000}); await loadStatus() },
    })
  }

  const bots: any[] = (status?.grids ?? []).filter((g: any) => g?.kind === 'DCA' || g?.spec?.meta?.kind === 'DCA')
  const active = bots.filter(g => !FINAL.includes(g.status))
  const finished = bots.filter(g => FINAL.includes(g.status)).slice(0, 3)
  const dir = side === 'LONG'
  const display = coins.find(coin => coin.symbol === symbol)?.display || symbol
  const stepsMeta = [{n: 1, label: 'Coin'}, {n: 2, label: 'Plan'}, {n: 4, label: 'Takip'}] as const
  const canStep = (n: number) => step !== 4 && started === null ? (n === 1 || (n === 2 && picked !== null) || false) : n === 4 || n === 1
  const lastStage = result.stages[result.stages.length - 1]
  const markOf = (sym: string) => { const p = Number(coins.find(coin => coin.symbol === sym)?.price); return p > 0 ? p : null }
  const usable = [...presetPlans].filter(plan => plan.state !== 'no').sort((a, b) => a.preset.risk - b.preset.risk)
  const current = usable.find(plan => plan.preset.id === (presetId === 'custom' ? lastPlanRef.current : presetId))
  const totalCycles = bots.reduce((sum, bot) => sum + (Number(bot.stats?.completed_cycles) || 0), 0)
  const totalRealized = bots.reduce((sum, bot) => sum + (Number(bot.stats?.realized_usdt) || 0), 0)

  return (
    <section ref={panelRef} className="dca dw" aria-label="DCA botu" data-step={step}>
      {bots.length > 0 && step !== 4 && <div className="dcaSum" data-testid="dca-summary" aria-label="DCA özeti">
        <span><small>Biten tur</small><b>{totalCycles}</b></span>
        <span><small>Gerçekleşen net</small><b className={totalRealized >= 0 ? 'up' : 'down'}>{fmtUsd(totalRealized, true)}</b></span>
        <span><small>Çalışan bot</small><b>{active.length}/{MAX_BOTS}</b></span>
      </div>}
      {active.length > 0 && step !== 4 && <div className="dcaBots" aria-label="Çalışan botlar">{active.map(bot => <BotCard key={bot.grid_id} bot={bot} busy={busy === bot.grid_id} onAct={act} mark={markOf(bot.symbol)} />)}</div>}

      <ol className="dwSteps" aria-label="Adımlar">
        {stepsMeta.map(item => <li key={item.n} data-state={step === item.n ? 'current' : step > item.n ? 'done' : 'todo'}>
          <button type="button" disabled={!canStep(item.n)} aria-current={step === item.n ? 'step' : undefined} onClick={() => { if (item.n === 1 && started) { setStarted(null); setPicked(null); setPresetId('') } setStep(item.n as 1 | 2 | 3 | 4) }}><i>{step > item.n ? '✓' : stepsMeta.findIndex(m => m.n === item.n) + 1}</i><span>{item.label}</span></button>
        </li>)}
      </ol>

      {step === 1 && <div className="dwPane" data-testid="dca-step-1">
        <h3 className="dwH">Fiyatın hangi yöne gideceğini düşünüyorsun?</h3>
        <p className="dwLead">DCA botu fiyat sana ters giderse planlı ek alımlar yapıp ortalama maliyetini düşürür, fiyat az toparlayınca kârla çıkar. Zarar için her zaman stop vardır.</p>
        <div className="dwSide" role="radiogroup" aria-label="Yön">
          <button type="button" role="radio" aria-checked={dir} data-on={dir || undefined} data-side="long" onClick={() => pickSide('LONG')}><b>LONG</b><small>Yükselecek diye düşünüyorum</small></button>
          <button type="button" role="radio" aria-checked={!dir} data-on={!dir || undefined} data-side="short" onClick={() => pickSide('SHORT')}><b>SHORT</b><small>Düşecek diye düşünüyorum</small></button>
        </div>

        {!query && top3.length >= 2 && <div className="dwKais" data-testid="dca-kais-top">
          <div><small>KAİS ÖNERİSİ</small><b>{dir ? 'LONG' : 'SHORT'} için şu an en uygun {top3.length} coin</b></div>
          <ul>{top3.map(item => <li key={item.symbol}><b>{(item.display || item.symbol).replace('/USDT', '')}</b>{signals?.[item.symbol] && signals[item.symbol].lean === side ? <em>AI %{Math.round(signals[item.symbol].confidence)}</em> : Number.isFinite(item.change) ? <em className={item.change >= 0 ? 'up' : 'down'}>{item.change >= 0 ? '+' : ''}{item.change.toFixed(1)}%</em> : null}</li>)}</ul>
          <button type="button" onClick={pickTop3}>{Math.min(top3.length, 1 + Math.max(0, MAX_BOTS - activeCount - 1))} tanesini birlikte kur</button>
          <small>Aynı plan her coinde ayrı bot olarak çalışır. Bir sonraki ekranda kontrol edip başlatırsın.</small>
        </div>}
        <div className="dwSecHead"><b>{dir ? 'LONG' : 'SHORT'} için sana uygun coinler</b>
          <small>{scan.state === 'busy' ? `Taranıyor ${scan.checked}/${scan.total}` : scan.state === 'done' ? `${scan.items.length} uygun coin bulundu` : ''}</small></div>
        <p className="dwHint">“Uygun”: bu coin, canlı toplam {cap} USDT limitine sığan bir DCA planı kuruyor. {signals ? 'Kais AI aynı yönü gösteriyorsa en üstte.' : 'Kais AI sinyali şu an alınamadı; seçtiğin yöne uyan 24 saatlik harekete göre sıralı (LONG: yükselen, SHORT: düşen).'}</p>
        <label className="dwFind"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Coin ara: BTC, SOL, XRP…" inputMode="search" autoCapitalize="characters" aria-label="Coin ara"/>
          {query && <button type="button" className="dwFindX" aria-label="Aramayı temizle" onClick={() => setQuery('')}>×</button>}</label>
        {scan.state === 'busy' && suggestions.length === 0 && !query && <ul className="dwCoins" aria-hidden="true">{[0, 1, 2].map(i => <li key={i} className="dwSkel"/>)}</ul>}
        {!query && suggestions.length > 0 && <ul className="dwCoins dwScroll" data-testid="dca-suggestions">{suggestions.map(item => {
          const sig = signals?.[item.symbol]
          return <li key={item.symbol} className="dwAnaLi"><button type="button" onClick={() => pickCoin(item.symbol)}>
            <span className="dwCoinMain"><b>{item.display}</b><small>{fmtPrice(item.price)}{Number.isFinite(item.change) ? <em className={item.change >= 0 ? 'up' : 'down'}> {item.change >= 0 ? '+' : ''}{item.change.toFixed(2)}%</em> : null}</small></span>
            <span className="dwCoinSide">
              {sig && sig.lean !== 'NEUTRAL' && !item.against && <span className="dwBadge ok">AI {side} %{Math.round(sig.confidence)}</span>}
              {item.against && <span className="dwBadge warn">AI ters yön</span>}
              <span className="dwFit">≈{fmtUsd(item.total)}</span>
            </span>
          </button>{sig && <button type="button" className="dwAnaBtn" aria-label={`${item.display} analizini aç`} onClick={() => setAnaSym(item.symbol)}><BarChart3 aria-hidden="true"/></button>}</li>
        })}</ul>}
        {!query && scan.state === 'done' && suggestions.length === 0 && <p className="dwWarn">Şu an {cap} USDT limite sığan coin bulunamadı. Yukarıdan başka bir coin arayabilirsin.</p>}
        {query && <ul className="dwCoins dwScroll dwSearchRes">{searchRows.map(coin => {
          const item = known.get(coin.symbol)
          return <li key={coin.symbol} className="dwAnaLi"><button type="button" onClick={() => pickCoin(coin.symbol)}>
            <span className="dwCoinMain"><b>{coin.display || coin.symbol}</b><small>{fmtPrice(coin.price)}</small></span>
            <span className="dwCoinSide"><span className="dwFit" data-state={item ? 'ok' : 'unk'}>{item ? `≈${fmtUsd(item.total)}` : 'Seçince kontrol edilir'}</span></span>
          </button>{signals?.[coin.symbol] && <button type="button" className="dwAnaBtn" aria-label={`${coin.display || coin.symbol} analizini aç`} onClick={() => setAnaSym(coin.symbol)}><BarChart3 aria-hidden="true"/></button>}</li>
        })}{searchRows.length === 0 && <li className="dwEmpty">Sonuç yok.</li>}</ul>}
        <CoinAnalysisSheet rows={anaRows} symbol={anaSym} signals={signals} onClose={() => setAnaSym(null)} onOpen={setAnaSym} onPick={pickCoin}/>
      </div>}

      {step === 2 && <div className="dwPane" data-testid="dca-step-2">
        <button type="button" className="dwSummary" onClick={() => setStep(1)}><b>{display}</b><span className={dir ? 'up' : 'down'}>{side}</span><span>{fmtPrice(price)} USDT</span><em>Değiştir</em></button>
        <h3 className="dwH">Ne kadar risk?</h3>
        <p className="dwLead">Sürgüyü kaydır, gerisini bot ayarlar. Her planda stop var.</p>
        {!ready && <p className="dwHint">{filtersError || 'Borsa kuralları okunuyor…'}</p>}
        {ready && presetPlans.every(plan => plan.state === 'no') && <div className="dwWarn" data-testid="dca-advice">
          <b>{display} bu limitle çalışmaz</b>
          <span>Borsanın en düşük işlem tutarı yüzünden bu coin {fitNow?.total ? `en az ${fmtUsd(fitNow.total)}` : 'daha yüksek'} toplam marj ister; canlı limitin {cap} USDT.</span>
          <button type="button" onClick={() => setStep(1)}>Başka coin seç</button>
        </div>}
        {ready && usable.length > 0 && <div className="dwRiskS" data-testid="dca-risk-slider">
          <div className="dwRiskSTop"><b>{presetId === 'custom' ? 'Özel ayar' : current?.preset.name ?? '—'}</b>{current && presetId !== 'custom' && <em className="dwRiskTag" data-level={current.preset.risk}>Risk {current.preset.risk}/5</em>}</div>
          <input type="range" min={0} max={usable.length - 1} step={1} value={Math.max(0, usable.findIndex(plan => plan.preset.id === (presetId === 'custom' ? lastPlanRef.current : presetId)))}
            onChange={event => { const plan = usable[Number(event.target.value)]; if (plan) choosePlan(plan) }} aria-label="Risk seviyesi" style={{['--pos' as string]: `${usable.length > 1 ? Math.max(0, usable.findIndex(plan => plan.preset.id === (presetId === 'custom' ? lastPlanRef.current : presetId))) / (usable.length - 1) * 100 : 50}%`} as React.CSSProperties}/>
          <div className="dwRiskSEnds"><span>Temkinli</span><span>Atak</span></div>
          <small>{presetId === 'custom' ? 'Ayarları elle değiştirdin. Sürgüyü kaydırırsan hazır plana döner.' : current?.preset.hint}</small>
          {result.valid && <div className="dwBig">
            <span><small>Yatırım</small><b>{fmtUsd(result.totalMargin)}</b></span>
            <span><small>Hedef kâr</small><b className="up">{fmtUsd(lastStage?.tpNetProfitAfterCosts ?? 0, true)}</b></span>
            <span><small>En kötü</small><b className="down">{fmtUsd(-result.worstLoss)}</b></span>
            <span><small>Kaldıraç</small><b>{form.leverage}x</b></span>
          </div>}
        </div>}
        {ready && customBlock && <p className="dwWarn" role="alert">{customBlock}{fixNowCustom && <> <button type="button" className="dwFix" onClick={applyCustomFix}>Otomatik sığdır</button></>}</p>}
        {ready && result.valid && <DcaPriceLine result={result} price={price} side={side} />}
        <details className="dwSettings" data-testid="dca-settings">
          <summary>Ayarları aç<small>yatırım, kaldıraç, ek alım, kâr hedefi</small></summary>
        {ready && customBlock && <p className="dwWarn" role="alert" data-testid="dca-custom-block">{customBlock}{fixNowCustom && <> <button type="button" className="dwFix" onClick={applyCustomFix}>Otomatik sığdır</button></>}</p>}
        {ready && <label className="dcaToggle dwRecycle" data-testid="dca-recycle"><input type="checkbox" checked={form.recycle} onChange={e => setForm(c => ({...c, recycle: e.target.checked}))} /><span><b>Otomatik tekrar</b><small>Kâr hedefi dolunca yeni tur başlar. Her yeni tur risk kapılarından baştan geçer; biri geçmezse bot durur.</small></span></label>}
        {ready && smartAvail && <label className="dcaToggle dwRecycle" data-testid="dca-smart-entry"><input type="checkbox" checked={smartEntry} onChange={e => setSmartEntry(e.target.checked)} /><span><b>Akıllı başlama</b><small>Bot, Kais bu coin için yönü uygun görene kadar bekler (en fazla birkaç saat). Uygun görmezse hiç işlem açmaz.</small></span></label>}
        {ready && trailAvail && <label className="dcaToggle dwRecycle" data-testid="dca-trailing"><input type="checkbox" checked={trailOn} onChange={e => setTrailOn(e.target.checked)} /><span><b>Takip eden kâr</b><small>Fiyat hedefe gelince bot hemen kapatmaz; kârı yukarı doğru takip eden bir stop ile kovalar. Stop geri gitmez. Hedef yerine daha fazla kâr da, biraz daha az kâr da olabilir.</small></span></label>}
        {ready && <p className="dwHint" data-testid="dca-no-guarantee">Kâr garantisi yoktur. Fiyat ek emirlerin ötesine giderse stop çalışır ve yukarıda gösterilen en kötü kayıp yaşanabilir.</p>}
        {rl?.max_loss_per_trade > 0 && <div className="dwLoss" data-testid="dca-loss-limit">
          <div><small>Tek işlem zarar sınırın (canlı risk ayarı)</small><b>{rl.max_loss_per_trade} USDT</b></div>
          <button type="button" onClick={() => setLossEdit(c => ({...c, open: !c.open, value: String(rl.max_loss_per_trade)}))}>{lossEdit.open ? 'Kapat' : 'Değiştir'}</button>
          {lossEdit.open && <div className="dwLossEdit">
            <p>Planlar bu sınıra göre otomatik küçültülür. Yükseltirsen daha büyük plan kurulur ama bir işlemde kaybedebileceğin tutar da artar.</p>
            <input type="text" inputMode="decimal" value={lossEdit.value} onChange={event => setLossEdit(c => ({...c, value: event.target.value}))} aria-label="Tek işlem zarar sınırı (USDT)"/>
            <label><input type="checkbox" checked={lossEdit.ok} onChange={event => setLossEdit(c => ({...c, ok: event.target.checked}))}/> Gerçek para kaybı riskini anladım.</label>
            <p className="dwHint">Kaydedince otomasyon durur ve risk onayını yeniden vermen gerekebilir.</p>
            {lossEdit.error && <p className="dwWarn" role="alert">{lossEdit.error}</p>}
            <button type="button" disabled={!lossEdit.ok || lossEdit.busy} onClick={saveLoss}>{lossEdit.busy ? 'Kaydediliyor…' : 'Kaydet'}</button>
          </div>}
        </div>}
        {note && <p className="dcaNote2" role="status">{note}</p>}
        {ready && result.errors.length > 0 && presetId === '' && <ul className="dcaErrors" role="alert">{result.errors.map(text => <li key={text}><i>!</i><span>{text}</span></li>)}</ul>}

        <div className="dwAdv dwAdvFlat" data-testid="dca-advanced">
          <div className="dwAdvBody">
            {ready && <div className="dwSumBar" data-bad={customBlock ? '1' : undefined} role="status" aria-label="Canlı özet">
              <span><small>Yatırım</small><b>{result.valid ? fmtUsd(result.totalMargin) : '—'}</b></span>
              <span><small>En kötü kayıp</small><b className="down">{result.valid ? fmtUsd(-result.worstLoss) : '—'}</b></span>
              <span><small>Hedef kâr</small><b className="up">{result.valid && result.stages.length ? fmtUsd(result.stages[result.stages.length - 1].tpNetProfitAfterCosts, true) : '—'}</b></span>
              <span><small>Likidasyona</small><b>{result.valid && Number.isFinite(result.liquidationGapPct) ? `%${result.liquidationGapPct.toFixed(0)}` : '—'}</b></span>
            </div>}
            {ready && result.valid && <p className="dwStory2">Bot <b>{fmtUsd(num(form.margin))}</b> marjla girer. Fiyat her <b>%{form.step}</b> {falls} <b>{form.safety}</b> kez ek alım yapar (her biri <b>{form.volume}x</b> büyük). Ortalamadan <b>%{form.tp}</b> kârda kapatır; son ek alımın <b>%{form.gap}</b> ötesinde stop çalışır.</p>}
            {ready && result.errors.length > 0 && <ul className="dcaErrors" role="alert">{result.errors.map(text => <li key={text}><i>!</i><span>{text}</span></li>)}</ul>}
            <div className="dwAdvGroup">
              <h4>Giriş</h4>
              <Field label="Taban marjin" suffix="USDT"><input inputMode="decimal" value={form.margin} onChange={e => set('margin', e.target.value)} /></Field>
              <Chips options={marginChips} value={num(form.margin)} onPick={v => set('margin', fmtMargin(v))} fmt={v => `${v}`} />
              <div className="dcaField"><label>Kaldıraç</label><Chips options={[3, 4, 5]} value={form.leverage} onPick={v => set('leverage', v)} fmt={v => `${v}x`} /><small>DCA'da en fazla {DCA_LIMITS.maxLeverage}x. Kaldıraç arttıkça likidasyon yaklaşır.</small></div>
            </div>
            <div className="dwAdvGroup">
              <h4>Ek alımlar</h4>
              <div className="dcaField"><label>Ek emir sayısı</label><Chips options={[1, 2, 3, 4, 5]} value={form.safety} onPick={v => set('safety', v)} fmt={v => `${v}`} /></div>
              <Slider label="Ek emirler arası aralık" unit="%" min={DCA_LIMITS.stepPct[0]} max={DCA_LIMITS.stepPct[1]} step={0.1} value={form.step} onChange={v => set('step', v)} />
              <Chips options={[0.5, 1, 1.5, 2, 3, 5]} value={num(form.step)} onPick={v => set('step', String(v))} fmt={v => `%${v}`} />
              <Slider label="Hacim çarpanı" unit="x" min={1} max={DCA_LIMITS.maxVolumeMultiplier} step={0.1} value={form.volume} onChange={v => set('volume', v)} hint="Her ek emir bir öncekinden bu kadar büyük" />
              <Chips options={[1, 1.2, 1.5, 1.8, 2]} value={num(form.volume)} onPick={v => set('volume', v.toFixed(1))} fmt={v => `${v}x`} />
            </div>
            <div className="dwAdvGroup">
              <h4>Çıkış</h4>
              <Slider label="Kâr hedefi (ortalamadan)" unit="%" min={DCA_LIMITS.takeProfitPct[0]} max={5} step={0.1} value={form.tp} onChange={v => set('tp', v)} />
              <Chips options={[0.5, 0.8, 1, 1.5, 2, 3]} value={num(form.tp)} onPick={v => set('tp', String(v))} fmt={v => `%${v}`} />
              <Slider label="Stop mesafesi (son ek emrin ötesi)" unit="%" min={DCA_LIMITS.stopGapPct[0]} max={5} step={0.1} value={form.gap} onChange={v => set('gap', v)} />
              <Chips options={[0.8, 1, 1.5, 2, 3]} value={num(form.gap)} onPick={v => set('gap', String(v))} fmt={v => `%${v}`} />
            </div>
            <div className="dwAdvActions">
              <button type="button" onClick={() => { const plan = presetPlans.find(p => p.preset.id === (lastPlanRef.current || 'balanced')) ?? presetPlans.find(p => p.state !== 'no'); if (plan) choosePlan(plan) }}>Seçili plana dön</button>
              <button type="button" disabled={!fixNowCustom && !(fitNow?.state === 'over-cap' && fixNow)} onClick={() => { if (fixNowCustom) applyCustomFix(); else if (fixNow) { setPresetId('custom'); setForm(c => ({...c, margin: fmtMargin(fixNow.margin), safety: fixNow.safety, volume: fixNow.volume.toFixed(1)})) } }}>Limitime sığdır</button>
            </div>
            <div className="dcaCard dcaVisual">
              {result.valid && ready ? <>
                <DcaLadder result={result} price={price} side={side} scrub={scrub} />
                <div className="dcaScrubBox">
                  <div className="dcaScrubHead"><b>Fiyat gidersen ne olur?</b><span>{scenario ? fmtPrice(scenario.at) : '—'}</span></div>
                  <input className="dcaRange" type="range" min={0} max={1000} value={Math.round(scrub * 1000)} onChange={e => setScrub(Number(e.target.value) / 1000)} aria-label="Fiyat senaryosu" />
                  {scenario && <div className="dcaScenario" data-neg={scenario.pnl < 0 ? '1' : '0'}>
                    <span><small>Dolan emir</small><b>{scenario.count}/{result.stages.length}</b></span><span><small>Ortalama</small><b>{fmtPrice(scenario.avg)}</b></span>
                    <span><small>Hedef</small><b>{fmtPrice(scenario.tp)}</b></span><span><small>Anlık K/Z</small><b className={scenario.pnl >= 0 ? 'up' : 'down'}>{fmtUsd(scenario.pnl, true)}</b></span>
                  </div>}
                </div>
              </> : <p className="dwHint">Plan geçerli olunca merdiven burada görünür.</p>}
            </div>
          </div>
        </div>
        </details>
        <div className="dwExtras" data-testid="dca-extras">
          <div className="dwExHead"><b>Aynı ayarla başka coin de ekle</b><span>{extras.length}/{slots}</span></div>
          <small>{slots > 0
            ? `Aynı anda en fazla ${MAX_BOTS} bot çalışır (güvenlik sınırı). Şu an ${activeCount} çalışıyor, bu yüzden ${slots} coin daha ekleyebilirsin. Her coin ayrı bot olarak, aynı plan yüzdeleriyle çalışır.`
            : `Aynı anda en fazla ${MAX_BOTS} bot çalışır ve şu an hepsi dolu. Yer açmak için çalışan bir botu durdur.`}</small>
          {slots > 0 && <>
            <div className="dwExList">{extraPool.filter(item => item.symbol !== symbol).map(item => {
              const fit = extraFits[item.symbol]
              const on = extras.includes(item.symbol)
              const move = Number.isFinite(item.change) ? `${item.change > 0 ? '+' : ''}${item.change.toFixed(1)}%` : ''
              return <button type="button" key={item.symbol} data-on={on || undefined} data-state={fit?.state ?? 'busy'} data-against={item.against || undefined} aria-pressed={on} onClick={() => toggleExtra(item.symbol)}>
                <span className="dwExTop"><b>{(item.display || item.symbol).replace('/USDT', '')}</b>{move && <i className={item.change > 0 ? 'up' : 'down'}>{move}</i>}{item.strong && <em>{dir ? 'Yükselişte' : 'Düşüşte'}</em>}{item.against && <em className="warn">Ters yönde</em>}</span>
                <small>{!fit || fit.state === 'busy' ? 'Hesaplanıyor…' : fit.state === 'no' ? fit.reason : `${fmtUsd(fit.total ?? 0)} yatırım · en kötü ${fmtUsd(-(fit.worst ?? 0))}`}</small>
              </button>
            })}</div>
            <div className="dwExActions">
              <button type="button" disabled={okExtras.length === 0} onClick={pickAllExtras}>Uygun olanlardan {Math.min(slots, okExtras.length)} tanesini seç</button>
              {extras.length > 0 && <button type="button" onClick={() => { setExtras([]); setExtraNote('') }}>Temizle</button>}
            </div>
            {extras.length > 0 && <p className="dwExTotal" role="status">Toplam {1 + extras.length} coin · yatırım <b>{fmtUsd(result.totalMargin + extrasTotal)}</b> · hepsinde stop çalışırsa en kötü zarar <b className="down">{fmtUsd(-(result.worstLoss + extrasWorst))}</b></p>}
          </>}
          {extraNote && <em className="dwExNote">{extraNote}</em>}
        </div>
        {(!liveArmed || !authorizationValid || emergency) && <div className="dwPerm" data-testid="dca-perm" data-tone={emergency ? 'bad' : 'warn'}>
          {emergency ? <>
            <b>Acil durdurma açık</b><span>Yeni işlem açılamaz. Önce acil durdurmayı kaldırman gerekir (2FA kodu ister).</span>
            <button type="button" onClick={() => onClearEmergency?.()}>Acil durdurmayı kaldır</button>
          </> : <>
            <b>Canlı işlem izni gerekli</b><span>24 saatlik risk izni, risk limitlerinin onayı ve ARM tek dokunuşla verilir. Gerçek para kullanılacağını ve mevcut limitleri onaylamış olursun.</span>
            <button type="button" disabled={permBusy} onClick={() => void onGrant?.()}>{permBusy ? 'Veriliyor…' : 'İzni ver'}</button>
            {permError && <em>{permError}</em>}
          </>}
        </div>}
        <div className="dwVerify" data-state={server.state} role="status">
          {server.state === 'busy' && <><i className="dwSpin" aria-hidden="true"/><span>Sunucu planı kontrol ediyor…</span></>}
          {server.state === 'ok' && <><i aria-hidden="true">✓</i><span><b>Sunucu onayladı.</b> En kötü zarar {fmtUsd(-Number(server.plan?.worst_loss_usdt))}, toplam marj {fmtUsd(Number(server.plan?.total_margin_usdt))}.</span></>}
          {server.state === 'error' && <><i aria-hidden="true">!</i><span><b>Sunucu reddetti:</b> {server.text}</span></>}
          {server.state === 'idle' && <span>Sunucu kontrolü bekleniyor…</span>}
          {server.state === 'ok' && useSmart && server.gate && !server.gate.ok && <span data-testid="dca-gate-warn"><b>Kais şu an uygun görmüyor:</b> {server.gate.reason}. <button type="button" onClick={() => setSmartEntry(false)}>Akıllı başlamayı kapat</button> <button type="button" onClick={() => setServer({state: 'idle', text: ''})}>Yeniden kontrol et</button></span>}
        </div>
        <p className="dwFine">Gerçek para kullanılır. Başlatınca bir kez daha yazı onayı istenir. Hesaplar tahmindir: funding, kayma ve borsanın gerçek teminat tablosu dahil değil.</p>
      </div>}

      {step === 4 && started && (() => {
        const mine = bots.filter(bot => bot.symbol === started.symbol).sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0))[0]
        const waited = Date.now() - started.at
        return <div className="dwPane" data-testid="dca-step-4">
          {notStarted.length > 0 && <div className="dcaWhy bad" role="alert" data-testid="dca-not-started"><b>Başlamayan coinler ({notStarted.length}):</b>{notStarted.map(item => <p key={item.sym}><b>{item.sym.replace('USDT', '')}</b>: {item.why}</p>)}<button type="button" onClick={() => setNotStarted([])}>Tamam</button></div>}
          <div className="dwDone"><i aria-hidden="true">✓</i><div><b>DCA botu başlatıldı</b><span>{started.symbol} · {started.side}. Taban emir piyasadan girer, ek emirler limit olarak beklemeye alınır, stop ve kâr hedefi kurulur.</span></div></div>
          {mine ? <BotCard key={mine.grid_id} bot={mine} busy={busy === mine.grid_id} onAct={act} mark={markOf(mine.symbol)} /> : <div className="dwVerify" data-state="busy" role="status"><i className="dwSpin" aria-hidden="true"/><span>{waited > 20000 ? 'Bot durumu henüz görünmedi. Açık pozisyonlarından kontrol et.' : 'Bot kuruluyor, durum birazdan burada görünür…'}</span></div>}
          {(() => { const others = (started.symbols ?? []).filter(sym => sym !== started.symbol).map(sym => bots.filter(bot => bot.symbol === sym).sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0))[0]).filter(Boolean); return others.map((bot: any) => <BotCard key={bot.grid_id} bot={bot} busy={busy === bot.grid_id} onAct={act} mark={markOf(bot.symbol)} />) })()}
          <p className="dwFine">Bu kart kendiliğinden güncellenir. İşlemin Binance&apos;te açıldığını “Açık pozisyonlar” bölümünde de görürsün.</p>
          <div className="dwBar dwBar2">
            <button type="button" className="dwBack" onClick={() => { setStarted(null); setPicked(null); setPresetId(''); setStep(1) }}>Yeni DCA</button>
            <button type="button" className="dwGo" data-testid="dca-goto-positions" onClick={() => goPositions(started.symbol)}>Açık pozisyonlarıma git</button>
          </div>
        </div>
      })()}

      {step === 2 && whyBlocked && <p className="dwWhy" role="alert" key={startTap} data-tap={startTap > 0 || undefined}><i aria-hidden="true">!</i><span><b>Şu an başlatılamıyor</b>{whyBlocked}</span></p>}
      {step === 2 && startError && <p className="dwWhy" data-bad role="alert"><i aria-hidden="true">!</i><span><b>Başlatılamadı</b>{startError}</span></p>}
      {launching && <div className="dwLaunch" role="status" aria-live="polite" data-testid="dca-launching">
        <div className="dwLaunchBox">
          <i className="dwSpin dwSpinBig" aria-hidden="true"/>
          <b>DCA başlatılıyor</b>
          <span>{launching.phase}</span>
          <ul>{launching.coins.map(c => <li key={c} data-state={launching.done.includes(c) ? 'ok' : launching.now === c ? 'now' : 'wait'}><em>{launching.done.includes(c) ? '✓' : launching.now === c ? '…' : '•'}</em>{c.replace('USDT', '')}</li>)}</ul>
          <small>Kaldıraç, marjin modu ve emirler borsada kuruluyor; 10–30 sn sürebilir. Sayfayı kapatma.</small>
        </div>
      </div>}
      <div className="dwBar">
        {step === 2 && <><button type="button" className="dwBack" onClick={() => setStep(1)}>Geri</button><button type="button" className="dwGo live" data-testid="dca-start" aria-disabled={Boolean(whyBlocked) || undefined} data-blocked={Boolean(whyBlocked) || undefined} onClick={() => { if (whyBlocked) { setStartTap(n => n + 1); return } start() }}>Canlı başlat</button></>}
      </div>

      {statusError && <p className="dcaWhy bad">{statusError}</p>}

      {finished.length > 0 && <div className="dcaPast"><b>Son botlar</b>{finished.map(bot => <div key={bot.grid_id}><span>{bot.symbol} {bot.side}</span><span>{STATUS[bot.status]?.text || bot.status}</span><span className={Number(bot.stats?.realized_usdt) >= 0 ? 'up' : 'down'}>{fmtUsd(Number(bot.stats?.realized_usdt || 0), true)}</span></div>)}</div>}
    </section>
  )
}

function Field({label, suffix, children}: {label: string; suffix?: string; children: React.ReactNode}) {
  return <div className="dcaField"><label>{label}</label><div className="dcaInput">{children}{suffix && <em>{suffix}</em>}</div></div>
}

function Slider({label, unit, min, max, step, value, onChange, hint}: {label: string; unit: string; min: number; max: number; step: number; value: string; onChange: (v: string) => void; hint?: string}) {
  const n = num(value)
  const pct = Number.isFinite(n) ? Math.max(0, Math.min(100, ((n - min) / (max - min)) * 100)) : 0
  return (
    <div className="dcaField dcaSlider">
      <label><span>{label}</span><b>{value}{unit}</b></label>
      <input type="range" min={min} max={max} step={step} value={Number.isFinite(n) ? Math.min(Math.max(n, min), max) : min} style={{['--p' as any]: `${pct}%`}} onChange={e => onChange(String(Number(e.target.value)))} />
      {hint && <small>{hint}</small>}
    </div>
  )
}

function Chips({options, value, onPick, fmt}: {options: number[]; value: number; onPick: (v: number) => void; fmt: (v: number) => string}) {
  return <div className="dwChips" role="group">{options.map(option => <button key={option} type="button" className={Math.abs(option - value) < 1e-9 ? 'on' : ''} aria-pressed={Math.abs(option - value) < 1e-9} onClick={() => onPick(option)}>{fmt(option)}</button>)}</div>
}

function Stat({label, value, tone, hint}: {label: string; value: string; tone?: 'up' | 'down'; hint?: string}) {
  return <div className="dcaStat"><small>{label}</small><b className={tone}>{value}</b>{hint && <em>{hint}</em>}</div>
}

function BotCard({bot, busy, onAct, mark = null}: {bot: any; busy: boolean; onAct: (kind: 'stop' | 'emergency' | 'resume', id: string) => void; mark?: number | null}) {
  const stats = bot.stats || {}
  const info = STATUS[bot.status] || {text: bot.status, tone: 'wait' as const}
  const total = Number(stats.stages_total || 0), done = Number(stats.stages_filled || 0)
  const long = bot.side === 'LONG'
  const alerts: Array<{level: string; text: string}> = (bot.alerts || []).slice(-2)
  const hasPos = Math.abs(Number(bot.position_qty || 0)) > 1e-9
  const watching = !FINAL.includes(bot.status)
  return (
    <article className="dcaBot" data-tone={info.tone}>
      <div className="dcaBotTop">
        <div><b>{bot.symbol}</b><span className={long ? 'up' : 'down'}>{long ? 'LONG ▲' : 'SHORT ▼'}</span></div>
        <span className="dcaStatus" data-tone={info.tone}><i />{info.text}</span>
      </div>
      {watching && <DcaWatch bot={bot} mark={mark}/>}
      {!watching && <div className="dcaRail" aria-label={`${done}/${total} emir doldu`}>
        {Array.from({length: total}).map((_, index) => <span key={index} className={index < done ? 'on' : ''}><i>{index === 0 ? 'T' : index}</i></span>)}
      </div>}
      {watching ? <div className="dcaBotGrid dcaBotGrid2"><span><small>Pozisyon</small><b>{hasPos ? Number(bot.position_qty).toString() : '—'}</b></span><span><small>Ortalama</small><b>{stats.avg_entry ? fmtPrice(Number(stats.avg_entry)) : '—'}</b></span></div> : <div className="dcaBotGrid">
        <span><small>Pozisyon</small><b>{hasPos ? Number(bot.position_qty).toString() : '—'}</b></span>
        <span><small>Ortalama</small><b>{stats.avg_entry ? fmtPrice(Number(stats.avg_entry)) : '—'}</b></span>
        <span><small>Kâr hedefi</small><b className="up">{stats.tp_price ? fmtPrice(Number(stats.tp_price)) : '—'}</b></span>
        <span><small>Stop</small><b className="down">{fmtPrice(Number(stats.stop_price))}</b></span>
        <span><small>Tur</small><b>{stats.cycle ?? 1}{Number(stats.completed_cycles) > 0 ? ` · ${stats.completed_cycles} bitti` : ''}</b></span>
        <span><small>Gerçekleşen</small><b className={Number(stats.realized_usdt) >= 0 ? 'up' : 'down'}>{fmtUsd(Number(stats.realized_usdt || 0), true)}</b></span>
      </div>}
      {stats.trail_active && !watching && <p className="dcaBotReason" data-testid="dca-trail-on">Takip eden kâr açık: stop kârı kovalıyor, geri gitmez.</p>}
      {bot.status === 'CYCLE_DONE' && Number(stats.trailing_pct) >= 0 && bot.reason && <p className="dcaBotReason">{bot.reason}</p>}
      {bot.liq?.distance_pct != null && bot.liq.level !== 'ok' && <p className="dcaBotWarn">Likidasyona %{bot.liq.distance_pct} kaldı.</p>}
      {bot.reason && !watching && <p className="dcaBotReason">{bot.reason}</p>}
      {alerts.map((a, i) => <p key={i} className="dcaBotAlert" data-level={a.level}>{a.text}</p>)}
      <div className="dcaBotActions">
        {bot.status === 'PAUSED' && <button type="button" disabled={busy} onClick={() => onAct('resume', bot.grid_id)}>Devam ettir</button>}
        {bot.status !== 'PAUSED' && !bot.needs_user && <button type="button" onClick={() => onAct('stop', bot.grid_id)}>Durdur</button>}
        <button type="button" className="danger" onClick={() => onAct('emergency', bot.grid_id)}>{bot.status === 'PAUSED' && !hasPos ? 'Botu sonlandır' : bot.needs_user ? 'Kapat ve borsayla eşitle' : 'Acil durdur'}</button>
      </div>
    </article>
  )
}

// Planın resmi: yukarıdan aşağı fiyat sırasıyla hedef, şimdi, ek alımlar ve stop; her satırda şimdiki fiyata uzaklık.
function DcaPriceLine({result, price, side}: {result: DcaResult; price: number; side: DcaSide}) {
  const long = side === 'LONG'
  const first = result.stages[0]
  const now = first?.entry ?? price
  const tp = first?.tpPrice ?? 0    // yalnız ilk alımla hedef; ek alım olursa hedef ortalamayla birlikte yaklaşır
  const rows: Array<{key: string; price: number; kind: 'now' | 'buy' | 'tp' | 'stop'; label: string; note?: string}> = [
    {key: 'tp', price: tp, kind: 'tp', label: 'Hedef', note: `Buraya ${long ? 'çıkınca' : 'inince'} kârla kapatır`},
    {key: 'now', price: now, kind: 'now', label: 'Şimdi', note: 'İlk alım'},
    ...result.stages.slice(1).map(stage => ({key: `s${stage.index}`, price: stage.entry, kind: 'buy' as const, label: `${stage.index}. ek alım`,
      note: stage.index === 1 ? `Fiyat buraya ${long ? 'düşerse' : 'çıkarsa'} tekrar alır` : undefined})),
    {key: 'stop', price: result.stopPrice, kind: 'stop', label: 'Stop', note: 'Buraya gelirse zararla çıkar'},
  ]
  const sorted = rows.filter(row => row.price > 0).sort((a, b) => b.price - a.price)
  const dist = (value: number) => { const pct = (value - now) / now * 100; return Math.abs(pct) < 0.005 ? '' : `${pct > 0 ? '+' : '−'}%${Math.abs(pct).toFixed(2)}` }
  return <div className="dwLine" data-testid="dca-price-line" aria-label="Plan fiyat çizgisi">
    <div className="dwLineHead"><b>Plan resmi</b><small>{long ? 'Düşüşte ek alır, toparlanınca satar' : 'Yükselişte ek satar, geri gelince kapatır'}</small></div>
    <ol>{sorted.map(row => <li key={row.key} data-kind={row.kind}>
      <i aria-hidden="true"/>
      <span><b>{row.label}</b>{row.note && <small>{row.note}</small>}</span>
      <span className="dwLineP"><b>{fmtPrice(row.price)}</b>{dist(row.price) && <small>{dist(row.price)}</small>}</span>
    </li>)}</ol>
    <p className="dwLineFoot">Ek alım olursa ortalama düşer, hedef de {long ? 'aşağı' : 'yukarı'} yaklaşır.</p>
  </div>
}

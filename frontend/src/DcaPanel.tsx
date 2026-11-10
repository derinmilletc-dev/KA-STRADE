import {useEffect, useMemo, useRef, useState} from 'react'
import {computeDca, DCA_LIMITS, DCA_PRESETS, fitToCap, planFit, type DcaPreset, type DcaPresetForm, type DcaResult, type DcaSide} from './dcaMath'
import {goPositions} from '../../trade-intent'
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
  askConfirm: (config: ConfirmConfig) => void
  /** Taranabilecek coinler (fiyat ve hacimle); "Uygun coin bul" için. */
  coins?: Array<{symbol: string; display?: string; price: number; volume?: number}>
  onPickSymbol?: (symbol: string) => void
  onPickSide?: (side: DcaSide) => void
  /** Kais AI sinyalleri (coin → yön eğilimi ve güven); varsa öneriler buna göre sıralanır. */
  signals?: Record<string, {lean: string; confidence: number}> | null
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
  const {symbol, price, side, liveArmed, call, v25Base, askConfirm, coins = [], signals, onPickSymbol, onPickSide, authorizationValid = true, emergency = false, permBusy = false, permError = '', onGrant, onClearEmergency} = props
  const account: Account = 'LIVE'
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [started, setStarted] = useState<{symbol: string; side: DcaSide; at: number} | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [note, setNote] = useState('')
  const [form, setForm] = useState<Form>(DCA_PRESETS[1].form)
  const [presetId, setPresetId] = useState('')
  const [filters, setFilters] = useState<{tickSize: number; stepSize: number; minNotional: number} | null>(null)
  const [filtersError, setFiltersError] = useState('')
  const [scrub, setScrub] = useState(0.55)
  const [status, setStatus] = useState<any>(null)
  const [statusError, setStatusError] = useState('')
  const [server, setServer] = useState<{state: 'idle' | 'busy' | 'ok' | 'error'; text: string; plan?: any}>({state: 'idle', text: ''})
  const [busy, setBusy] = useState('')

  const base = `${v25Base}/dca/live`
  const filtersPath = `${v25Base}/grid/filters`
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
  useEffect(() => { setServer({state: 'idle', text: ''}) }, [symbol, side, form])
  useEffect(() => { setNote('') }, [symbol, side])

  const input = useMemo(() => ({
    side, price, baseMargin: num(form.margin), safetyOrders: form.safety, stepPct: num(form.step), volumeMultiplier: num(form.volume),
    takeProfitPct: num(form.tp), stopGapPct: num(form.gap), leverage: form.leverage, filters: filters ?? undefined,
  }), [side, price, form, filters])
  const result = useMemo(() => computeDca(input), [input])
  const ready = synced && price > 0 && filters !== null
  const scenario = result.valid ? scenarioAt(result, price, side, scrub) : null
  const set = <K extends keyof Form>(key: K, value: Form[K]) => { setPresetId(''); setForm(current => ({...current, [key]: value})) }
  const cap = Number(status?.live_max_margin_usdt) > 0 ? Number(status.live_max_margin_usdt) : 10
  const rl = status?.risk_limits
  const maxLoss = Math.min(...[Number(rl?.max_loss_per_trade), Number(rl?.daily_loss_left)].filter(v => Number.isFinite(v) && v > 0), Infinity)
  const lossCap = Number.isFinite(maxLoss) ? maxLoss : 0
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

  const choosePlan = (plan: NonNullable<typeof presetPlans>[number]) => {
    setPresetId(plan.preset.id)
    setForm(plan.form)
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

  const suggestions = useMemo(() => {
    const rank = (item: ScanItem) => {
      const sig = signals?.[item.symbol]
      if (!sig || sig.lean === 'NEUTRAL') return 1000
      return sig.lean === side ? 2000 + sig.confidence : -1
    }
    const ranked = scan.items.map(item => ({item, r: rank(item)}))
    const good = ranked.filter(entry => entry.r >= 0).sort((a, b) => b.r - a.r || Number(a.item.fixed) - Number(b.item.fixed) || a.item.total - b.item.total || a.item.idx - b.item.idx)
    const against = ranked.filter(entry => entry.r < 0).sort((a, b) => a.item.idx - b.item.idx)
    return [...good, ...(good.length < 4 ? against.slice(0, 4 - good.length) : [])].slice(0, 6).map(entry => ({...entry.item, against: entry.r < 0}))
  }, [scan.items, signals, side])
  const known = new Map(scan.items.map(item => [item.symbol, item]))
  const q = query.trim().toUpperCase().replace('/', '')
  const searchRows = q ? coins.filter(coin => coin.symbol.includes(q) || String(coin.display || '').toUpperCase().replace('/', '').includes(q)).slice(0, 6) : []

  const pickCoin = (sym: string) => { onPickSymbol?.(sym); setPicked(sym); setPresetId(''); setNote(''); setQuery(''); setStep(2) }
  const pickSide = (next: DcaSide) => { onPickSide?.(next); setPresetId(''); autoRef.current = '' }

  const body = (preview: boolean, confirmation = '') => JSON.stringify({
    symbol, direction: side, base_margin_usdt: input.baseMargin, safety_orders: input.safetyOrders, step_pct: input.stepPct, volume_multiplier: input.volumeMultiplier,
    take_profit_pct: input.takeProfitPct, stop_gap_pct: input.stopGapPct, leverage: input.leverage, recycle: form.recycle, preview, confirmation,
  })
  const checkOnServer = async () => {
    setServer({state: 'busy', text: 'Sunucu planı kendi kurallarıyla yeniden hesaplıyor…'})
    try {
      const data = await call<any>(base, '/start', {method: 'POST', body: body(true), timeoutMs: 60000})
      setServer({state: 'ok', plan: data.plan, text: 'Sunucu planı onayladı.'})
    } catch (error: any) { setServer({state: 'error', text: error?.message || 'Sunucu planı reddetti.'}) }
  }
  useEffect(() => { if (step === 3 && server.state === 'idle' && result.valid && ready) void checkOnServer() }, [step, server.state, result.valid, ready])

  const liveEnabled = status?.enabled !== false && status?.enabled !== undefined
  const whyBlocked = !ready ? (filtersError || 'Borsa kuralları yükleniyor…')
    : !result.valid ? 'Önce planı düzelt.'
    : !liveEnabled ? 'Canlı DCA sunucuda kapalı.'
    : !liveArmed || !authorizationValid ? 'Önce yukarıdaki "İzni ver" düğmesine bas.'
    : result.totalMargin > cap + 1e-9 ? `Canlı DCA şu an en çok ${cap} USDT toplam marjinle çalışır.`
    : server.state === 'busy' ? 'Sunucu kontrol ediyor…'
    : server.state !== 'ok' ? 'Sunucu onayı bekleniyor.'
    : ''

  const start = () => {
    askConfirm({
      title: 'CANLI DCA BAŞLAT',
      message: `GERÇEK PARA. ${symbol} ${side}: taban + ${form.safety} ek emir, toplam marjin ${fmtUsd(result.totalMargin)}, kaldıraç ${form.leverage}x. `
        + `Tüm emirler dolup stop çalışırsa tahmini en kötü zarar ${fmtUsd(result.worstLoss)}. Taban emir hemen piyasadan girer; ek emirler limit olarak bekler, stop dolan her miktar için kurulur.`,
      expected: 'CANLI DCA BAŞLAT',
      action: async () => {
        await call(base, '/start', {method: 'POST', timeoutMs: 60000, body: body(false, 'CANLI DCA BAŞLAT')})
        setStarted({symbol, side, at: Date.now()})
        setStep(4)
        await loadStatus()
      },
    })
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
  const stepsMeta = [{n: 1, label: 'Coin'}, {n: 2, label: 'Plan'}, {n: 3, label: 'Başlat'}, {n: 4, label: 'Takip'}] as const
  const canStep = (n: number) => step !== 4 && started === null ? (n === 1 || (n === 2 && picked !== null) || (n === 3 && picked !== null && result.valid && ready)) : n === 4 || n === 1
  const lastStage = result.stages[result.stages.length - 1]

  return (
    <section className="dca dw" aria-label="DCA botu" data-step={step}>
      {active.length > 0 && step !== 4 && <div className="dcaBots" aria-label="Çalışan botlar">{active.map(bot => <BotCard key={bot.grid_id} bot={bot} busy={busy === bot.grid_id} onAct={act} />)}</div>}

      <ol className="dwSteps" aria-label="Adımlar">
        {stepsMeta.map(item => <li key={item.n} data-state={step === item.n ? 'current' : step > item.n ? 'done' : 'todo'}>
          <button type="button" disabled={!canStep(item.n)} aria-current={step === item.n ? 'step' : undefined} onClick={() => { if (item.n === 1 && started) { setStarted(null); setPicked(null); setPresetId('') } setStep(item.n as 1 | 2 | 3 | 4) }}><i>{step > item.n ? '✓' : item.n}</i><span>{item.label}</span></button>
        </li>)}
      </ol>

      {step === 1 && <div className="dwPane" data-testid="dca-step-1">
        <h3 className="dwH">Fiyatın hangi yöne gideceğini düşünüyorsun?</h3>
        <p className="dwLead">DCA botu fiyat sana ters giderse planlı ek alımlar yapıp ortalama maliyetini düşürür, fiyat az toparlayınca kârla çıkar. Zarar için her zaman stop vardır.</p>
        <div className="dwSide" role="radiogroup" aria-label="Yön">
          <button type="button" role="radio" aria-checked={dir} data-on={dir || undefined} data-side="long" onClick={() => pickSide('LONG')}><b>LONG</b><small>Yükselecek diye düşünüyorum</small></button>
          <button type="button" role="radio" aria-checked={!dir} data-on={!dir || undefined} data-side="short" onClick={() => pickSide('SHORT')}><b>SHORT</b><small>Düşecek diye düşünüyorum</small></button>
        </div>

        <div className="dwSecHead"><b>{dir ? 'LONG' : 'SHORT'} için sana uygun coinler</b>
          <small>{scan.state === 'busy' ? `Taranıyor ${scan.checked}/${scan.total}` : scan.state === 'done' ? `${scan.items.length} uygun coin bulundu` : ''}</small></div>
        <p className="dwHint">“Uygun”: bu coin, canlı toplam {cap} USDT limitine sığan bir DCA planı kuruyor. {signals ? 'Kais AI aynı yönü gösteriyorsa en üstte.' : 'Kais AI sinyali şu an alınamadı; hacme göre sıralı.'}</p>
        <label className="dwFind"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Coin ara: BTC, SOL, XRP…" inputMode="search" autoCapitalize="characters" aria-label="Coin ara"/>
          {query && <button type="button" className="dwFindX" aria-label="Aramayı temizle" onClick={() => setQuery('')}>×</button>}</label>
        {scan.state === 'busy' && suggestions.length === 0 && !query && <ul className="dwCoins" aria-hidden="true">{[0, 1, 2].map(i => <li key={i} className="dwSkel"/>)}</ul>}
        {!query && suggestions.length > 0 && <ul className="dwCoins dwScroll" data-testid="dca-suggestions">{suggestions.map(item => {
          const sig = signals?.[item.symbol]
          return <li key={item.symbol}><button type="button" onClick={() => pickCoin(item.symbol)}>
            <span className="dwCoinMain"><b>{item.display}</b><small>{fmtPrice(item.price)}{Number.isFinite(item.change) ? <em className={item.change >= 0 ? 'up' : 'down'}> {item.change >= 0 ? '+' : ''}{item.change.toFixed(2)}%</em> : null}</small></span>
            <span className="dwCoinSide">
              {sig && sig.lean !== 'NEUTRAL' && !item.against && <span className="dwBadge ok">AI {side} %{Math.round(sig.confidence)}</span>}
              {item.against && <span className="dwBadge warn">AI ters yön</span>}
              <span className="dwFit">≈{fmtUsd(item.total)}</span>
            </span>
          </button></li>
        })}</ul>}
        {!query && scan.state === 'done' && suggestions.length === 0 && <p className="dwWarn">Şu an {cap} USDT limite sığan coin bulunamadı. Yukarıdan başka bir coin arayabilirsin.</p>}
        {query && <ul className="dwCoins dwScroll dwSearchRes">{searchRows.map(coin => {
          const item = known.get(coin.symbol)
          return <li key={coin.symbol}><button type="button" onClick={() => pickCoin(coin.symbol)}>
            <span className="dwCoinMain"><b>{coin.display || coin.symbol}</b><small>{fmtPrice(coin.price)}</small></span>
            <span className="dwCoinSide"><span className="dwFit" data-state={item ? 'ok' : 'unk'}>{item ? `≈${fmtUsd(item.total)}` : 'Seçince kontrol edilir'}</span></span>
          </button></li>
        })}{searchRows.length === 0 && <li className="dwEmpty">Sonuç yok.</li>}</ul>}
      </div>}

      {step === 2 && <div className="dwPane" data-testid="dca-step-2">
        <button type="button" className="dwSummary" onClick={() => setStep(1)}><b>{display}</b><span className={dir ? 'up' : 'down'}>{side}</span><span>{fmtPrice(price)} USDT</span><em>Değiştir</em></button>
        <h3 className="dwH">Plan seç</h3>
        <p className="dwLead">Hepsi stoplu. Fark: ne kadar sık ek alım yapıldığı ve ne kadar risk alındığı. Emin değilsen Dengeli iyi başlangıçtır.</p>
        {!ready && <p className="dwHint">{filtersError || 'Borsa kuralları okunuyor…'}</p>}
        {ready && presetPlans.every(plan => plan.state === 'no') && <div className="dwWarn" data-testid="dca-advice">
          <b>{display} bu limitle çalışmaz</b>
          <span>Borsanın en düşük işlem tutarı yüzünden bu coin {fitNow?.total ? `en az ${fmtUsd(fitNow.total)}` : 'daha yüksek'} toplam marj ister; canlı limitin {cap} USDT.</span>
          <button type="button" onClick={() => setStep(1)}>Başka coin seç</button>
        </div>}
        {ready && <div className="dwPlans" role="radiogroup" aria-label="Plan">{presetPlans.map(plan => {
          const r = plan.result
          const disabled = plan.state === 'no'
          const tp = r.stages.length ? r.stages[r.stages.length - 1].tpNetProfitAfterCosts : 0
          return <button key={plan.preset.id} type="button" role="radio" aria-checked={presetId === plan.preset.id} data-on={presetId === plan.preset.id || undefined} data-state={plan.state} disabled={disabled} onClick={() => choosePlan(plan)}>
            <span className="dwPlanHead"><b>{plan.preset.name}</b>{plan.preset.id === 'balanced' && <em>Önerilen</em>}</span>
            <small>{plan.preset.hint}</small>
            {disabled ? <span className="dwPlanNo">Bu coinde uygun değil</span> : <>
              <span className="dwPlanLine">Fiyat her %{plan.form.step} {falls} bir ek alım, en fazla {plan.form.safety} kez</span>
              <span className="dwPlanNums"><span><small>Yatırım</small><b>{fmtUsd(r.totalMargin)}</b></span><span><small>En kötü kayıp</small><b className="down">{fmtUsd(-r.worstLoss)}</b></span><span><small>Hedef kâr</small><b className="up">{fmtUsd(tp, true)}</b></span></span>
            </>}
          </button>
        })}</div>}
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

        <details className="dwAdv">
          <summary>Gelişmiş ayarlar</summary>
          <div className="dcaGrid">
            <div className="dcaCard dcaForm">
              <Field label="Taban marjin" suffix="USDT"><input inputMode="decimal" value={form.margin} onChange={e => set('margin', e.target.value)} /></Field>
              <div className="dcaField"><label>Ek emir sayısı</label><div className="dcaSeg">{[1, 2, 3, 4, 5].map(n => <button key={n} type="button" className={form.safety === n ? 'on' : ''} onClick={() => set('safety', n)}>{n}</button>)}</div></div>
              <div className="dcaField"><label>Kaldıraç</label><div className="dcaSeg">{[1, 2, 3, 4, 5].map(n => <button key={n} type="button" className={form.leverage === n ? 'on' : ''} onClick={() => set('leverage', n)}>{n}x</button>)}</div><small>DCA'da en fazla {DCA_LIMITS.maxLeverage}x.</small></div>
              <Slider label="Ek emirler arası aralık" unit="%" min={DCA_LIMITS.stepPct[0]} max={DCA_LIMITS.stepPct[1]} step={0.1} value={form.step} onChange={v => set('step', v)} />
              <Slider label="Hacim çarpanı" unit="x" min={1} max={DCA_LIMITS.maxVolumeMultiplier} step={0.1} value={form.volume} onChange={v => set('volume', v)} hint="Her ek emir bir öncekinden bu kadar büyük" />
              <Slider label="Kâr hedefi (ortalamadan)" unit="%" min={DCA_LIMITS.takeProfitPct[0]} max={5} step={0.1} value={form.tp} onChange={v => set('tp', v)} />
              <Slider label="Stop mesafesi (son ek emrin ötesi)" unit="%" min={DCA_LIMITS.stopGapPct[0]} max={5} step={0.1} value={form.gap} onChange={v => set('gap', v)} />
              <label className="dcaToggle"><input type="checkbox" checked={form.recycle} onChange={e => { setPresetId(''); setForm(c => ({...c, recycle: e.target.checked})) }} /><span><b>Tur bitince yeniden başlat</b><small>Her yeni tur risk kapılarından baştan geçer; biri geçmezse bot durur.</small></span></label>
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
        </details>
      </div>}

      {step === 3 && <div className="dwPane" data-testid="dca-step-3">
        <button type="button" className="dwSummary" onClick={() => setStep(1)}><b>{display}</b><span className={dir ? 'up' : 'down'}>{side}</span><span>{fmtPrice(price)} USDT</span><em>Değiştir</em></button>
        <h3 className="dwH">Bot şunu yapacak</h3>
        {result.valid && ready ? <>
          <ol className="dwStory">
            {result.stages.map(stage => <li key={stage.index}>
              <i>{stage.index === 0 ? '▶' : stage.index}</i>
              <span>{stage.index === 0
                ? <><b>Şimdi:</b> {fmtPrice(stage.entry)} fiyattan {fmtUsd(stage.notional)} değerinde ilk işlemi açar.</>
                : <><b>Fiyat {fmtPrice(stage.entry)}’e {falls}:</b> {fmtUsd(stage.notional)} ek işlem açar, ortalama giriş {fmtPrice(stage.avgEntry)} olur.</>}</span>
            </li>)}
            <li className="win"><i>✓</i><span><b>Kâr:</b> fiyat ortalama girişin %{form.tp} ötesine gelince hepsini kapatır. Tüm kademeler dolmuşsa net kâr ≈ <b className="up">{fmtUsd(lastStage?.tpNetProfitAfterCosts ?? 0, true)}</b> (ücret, kayma ve funding payı düşülmüş tahmin).</span></li>
            <li className="loss"><i>!</i><span><b>Koruma:</b> fiyat {fmtPrice(result.stopPrice)}’e ulaşırsa stop çalışır ve çıkar. En kötü kayıp ≈ <b className="down">{fmtUsd(-result.worstLoss)}</b> (ücret, kayma ve funding payı dahil).</span></li>
          </ol>
          <p className="dwHint" data-testid="dca-approx-note">Bu rakamlar yaklaşık önizlemedir; geçerli plan kararını ve gerçek hesabı sunucu verir. Sunucu planı reddederse başlatılamaz.</p>
          <div className="dwRisk"><span><small>Toplam yatırım (marj)</small><b>{fmtUsd(result.totalMargin)}</b></span><span><small>Kaldıraç</small><b>{form.leverage}x</b></span><span><small>Canlı limit</small><b>{cap} USDT</b></span></div>
        </> : <p className="dwWarn">Plan geçerli değil. Geri dönüp başka bir plan ya da coin seç.</p>}

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
          {server.state === 'error' && <><i aria-hidden="true">!</i><span><b>Sunucu reddetti:</b> {server.text} <button type="button" onClick={() => setStep(2)}>Planı değiştir</button></span></>}
          {server.state === 'idle' && <span>Sunucu kontrolü bekleniyor…</span>}
        </div>
        <p className="dwFine">Gerçek para kullanılır. Başlatınca bir kez daha yazı onayı istenir. Hesaplar tahmindir: funding, kayma ve borsanın gerçek teminat tablosu dahil değil.</p>
      </div>}

      {step === 4 && started && (() => {
        const mine = bots.filter(bot => bot.symbol === started.symbol).sort((a, b) => Number(b.created_at ?? 0) - Number(a.created_at ?? 0))[0]
        const waited = Date.now() - started.at
        return <div className="dwPane" data-testid="dca-step-4">
          <div className="dwDone"><i aria-hidden="true">✓</i><div><b>DCA botu başlatıldı</b><span>{started.symbol} · {started.side}. Taban emir piyasadan girer, ek emirler limit olarak beklemeye alınır, stop ve kâr hedefi kurulur.</span></div></div>
          {mine ? <BotCard key={mine.grid_id} bot={mine} busy={busy === mine.grid_id} onAct={act} /> : <div className="dwVerify" data-state="busy" role="status"><i className="dwSpin" aria-hidden="true"/><span>{waited > 20000 ? 'Bot durumu henüz görünmedi. Açık pozisyonlarından kontrol et.' : 'Bot kuruluyor, durum birazdan burada görünür…'}</span></div>}
          <p className="dwFine">Bu kart kendiliğinden güncellenir. İşlemin Binance&apos;te açıldığını “Açık pozisyonlar” bölümünde de görürsün.</p>
          <div className="dwBar dwBar2">
            <button type="button" className="dwBack" onClick={() => { setStarted(null); setPicked(null); setPresetId(''); setStep(1) }}>Yeni DCA</button>
            <button type="button" className="dwGo" data-testid="dca-goto-positions" onClick={() => goPositions(started.symbol)}>Açık pozisyonlarıma git</button>
          </div>
        </div>
      })()}

      <div className="dwBar">
        {step === 2 && <><button type="button" className="dwBack" onClick={() => setStep(1)}>Geri</button><button type="button" className="dwGo" data-testid="dca-next" disabled={!ready || !result.valid || !presetId} onClick={() => setStep(3)}>Devam · kontrol et</button></>}
        {step === 3 && <><button type="button" className="dwBack" onClick={() => setStep(2)}>Geri</button><button type="button" className="dwGo live" data-testid="dca-start" disabled={Boolean(whyBlocked)} onClick={start}>Canlı başlat</button></>}
      </div>
      {step === 3 && whyBlocked && <p className="dcaWhy">{whyBlocked}</p>}
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

function Stat({label, value, tone, hint}: {label: string; value: string; tone?: 'up' | 'down'; hint?: string}) {
  return <div className="dcaStat"><small>{label}</small><b className={tone}>{value}</b>{hint && <em>{hint}</em>}</div>
}

function BotCard({bot, busy, onAct}: {bot: any; busy: boolean; onAct: (kind: 'stop' | 'emergency' | 'resume', id: string) => void}) {
  const stats = bot.stats || {}
  const info = STATUS[bot.status] || {text: bot.status, tone: 'wait' as const}
  const total = Number(stats.stages_total || 0), done = Number(stats.stages_filled || 0)
  const long = bot.side === 'LONG'
  const alerts: Array<{level: string; text: string}> = (bot.alerts || []).slice(-2)
  const hasPos = Math.abs(Number(bot.position_qty || 0)) > 1e-9
  return (
    <article className="dcaBot" data-tone={info.tone}>
      <div className="dcaBotTop">
        <div><b>{bot.symbol}</b><span className={long ? 'up' : 'down'}>{long ? 'LONG ▲' : 'SHORT ▼'}</span></div>
        <span className="dcaStatus" data-tone={info.tone}><i />{info.text}</span>
      </div>
      <div className="dcaRail" aria-label={`${done}/${total} emir doldu`}>
        {Array.from({length: total}).map((_, index) => <span key={index} className={index < done ? 'on' : ''}><i>{index === 0 ? 'T' : index}</i></span>)}
      </div>
      <div className="dcaBotGrid">
        <span><small>Pozisyon</small><b>{hasPos ? Number(bot.position_qty).toString() : '—'}</b></span>
        <span><small>Ortalama</small><b>{stats.avg_entry ? fmtPrice(Number(stats.avg_entry)) : '—'}</b></span>
        <span><small>Kâr hedefi</small><b className="up">{stats.tp_price ? fmtPrice(Number(stats.tp_price)) : '—'}</b></span>
        <span><small>Stop</small><b className="down">{fmtPrice(Number(stats.stop_price))}</b></span>
        <span><small>Tur</small><b>{stats.cycle ?? 1}{Number(stats.completed_cycles) > 0 ? ` · ${stats.completed_cycles} bitti` : ''}</b></span>
        <span><small>Gerçekleşen</small><b className={Number(stats.realized_usdt) >= 0 ? 'up' : 'down'}>{fmtUsd(Number(stats.realized_usdt || 0), true)}</b></span>
      </div>
      {bot.liq?.distance_pct != null && bot.liq.level !== 'ok' && <p className="dcaBotWarn">Likidasyona %{bot.liq.distance_pct} kaldı.</p>}
      {bot.reason && <p className="dcaBotReason">{bot.reason}</p>}
      {alerts.map((a, i) => <p key={i} className="dcaBotAlert" data-level={a.level}>{a.text}</p>)}
      <div className="dcaBotActions">
        {bot.status === 'PAUSED' && <button type="button" disabled={busy} onClick={() => onAct('resume', bot.grid_id)}>Devam ettir</button>}
        {bot.status !== 'PAUSED' && !bot.needs_user && <button type="button" onClick={() => onAct('stop', bot.grid_id)}>Durdur</button>}
        <button type="button" className="danger" onClick={() => onAct('emergency', bot.grid_id)}>{bot.status === 'PAUSED' && !hasPos ? 'Botu sonlandır' : bot.needs_user ? 'Kapat ve borsayla eşitle' : 'Acil durdur'}</button>
      </div>
    </article>
  )
}

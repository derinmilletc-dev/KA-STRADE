import {useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode} from 'react'
import {Check, Grid3X3, Info, Minus, ShieldAlert, Sparkles, TriangleAlert, X} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang} from './i18n'
import {requestPositionsRefresh} from './trade-intent'
import {HoldButton} from './studio-hold'
import {ConsentRenew} from './StudioConsent'
import {tx, type AnalysisLevels} from './studio-quick-trade'
import {
  GRID_MAX_COUNT, GRID_MIN_COUNT, atrPct, backtestGrid, barSeconds, chartLinesFor, chartLinesForRows, gridActive, gridPnl, gridRangeStatus, gridStatusText, gridSummary, previewGrid, priceText, requestGrid,
  HIGH_LEVERAGE_ABOVE, previewBody, setGridLanguageReader, snapTo, startBody, suggestGrid,
  type Candle, type GridChartLine, type GridFilters, type GridMode, type GridParams, type GridPreset, type GridDay, type GridRow, type GridStatus,
} from './studio-grid'
import './studio-grid.css'

setGridLanguageReader(() => currentLang() === 'en')

const V25 = `${API_BASE}/v25`
const DEMO = `${API_BASE}/binance-demo`
const PREFS_KEY = 'kaistrade-studio-grid-prefs'
const HOLD_MS = {live: 1500, demo: 800}
const locale = () => currentLang() === 'en' ? 'en-US' : 'tr-TR'
const fmt = (value: number, digits = 2) => value.toLocaleString(locale(), {minimumFractionDigits: digits, maximumFractionDigits: digits})
const signed = (value: number, digits = 2) => `${value >= 0 ? '+' : '−'}${fmt(Math.abs(value), digits)}`
const short = (symbol: string) => symbol.replace(/USDT$/, '')
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0
const base = (demo: boolean) => demo ? `${DEMO}/grid` : `${V25}/grid/live`
const filtersUrl = (demo: boolean, symbol: string) => `${demo ? DEMO : V25}/grid/filters?symbol=${encodeURIComponent(symbol)}`

type Prefs = {margin: number; leverage: number; recycle: boolean; mode: GridMode; preset: Exclude<GridPreset, 'custom'>}
const DEFAULT_PREFS: Prefs = {margin: 50, leverage: 2, recycle: true, mode: 'NEUTRAL', preset: 'analysis'}
const readPrefs = (): Prefs => {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')
    const v = typeof raw === 'object' && raw !== null ? raw as Record<string, unknown> : {}
    return {
      margin: Number(v.margin) > 0 ? Number(v.margin) : DEFAULT_PREFS.margin, leverage: Number(v.leverage) >= 1 ? Number(v.leverage) : DEFAULT_PREFS.leverage,
      recycle: v.recycle !== false, mode: v.mode === 'LONG' || v.mode === 'SHORT' || v.mode === 'NEUTRAL' ? v.mode : DEFAULT_PREFS.mode,
      preset: v.preset === 'tight' || v.preset === 'wide' || v.preset === 'analysis' || v.preset === 'atr' ? v.preset : DEFAULT_PREFS.preset,
    }
  } catch { return DEFAULT_PREFS }
}
const savePrefs = (prefs: Prefs) => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)) } catch { /* tarayıcı kaydı kapalı olabilir */ } }

export type GridToast = {kind: 'ok' | 'warn' | 'error'; title: string; detail?: string}

/** Seçili hesabın (Demo ya da Canlı) grid durumu: sayfa açıkken düzenli yoklanır; sekme gizliyken durur. */
function useGridStatus(demo: boolean, fast: boolean, enabled: boolean) {
  const [status, setStatus] = useState<GridStatus | null>(null)
  const [error, setError] = useState('')
  const alive = useRef(true)
  const reload = useCallback(async () => {
    const result = await requestGrid<GridStatus>(`${base(demo)}/status`, 'GET', undefined, 15000)
    if (!alive.current) return
    if (result.ok) { setStatus(result.data); setError('') } else { setError(result.message); if (result.status === 403 || result.status === 404) setStatus(null) }
  }, [demo])
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { setStatus(null); setError('') }, [demo])
  useEffect(() => {
    if (!enabled) return
    void reload()
    const tick = () => { if (document.visibilityState === 'visible') void reload() }
    const id = window.setInterval(tick, fast ? 4000 : 20000)
    return () => window.clearInterval(id)
  }, [enabled, fast, reload])
  return {status, setStatus, error, reload}
}

type NumFieldProps = {label: string; value: number; onCommit: (value: number) => void; tick?: number; disabled?: boolean; tone?: 'stop' | 'range'; id: string}
/** Sayı alanı: yazarken serbest, bitince (blur/Enter) doğrulanıp tikte yuvarlanır. Geçersizse eski değere döner. */
function NumField({label, value, onCommit, tick, disabled, tone = 'range', id}: NumFieldProps) {
  const [text, setText] = useState(() => String(value))
  const focused = useRef(false)
  useEffect(() => { if (!focused.current) setText(String(value)) }, [value])
  const commit = () => {
    focused.current = false
    const parsed = Number(text.replace(',', '.'))
    if (Number.isFinite(parsed) && parsed > 0) { const snapped = snapTo(parsed, tick); onCommit(snapped); setText(String(snapped)) } else setText(String(value))
  }
  return <label className="sqgField" data-tone={tone} htmlFor={id}><small>{label}</small>
    <input id={id} inputMode="decimal" autoComplete="off" spellCheck={false} value={text} disabled={disabled} onFocus={() => { focused.current = true }}
      onChange={event => setText(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') { event.currentTarget.blur() } }}/>
  </label>
}

/** İsteğe bağlı USDT sınırı: boş/0 = kapalı. Yalnız pozitif sayı kabul edilir. */
function ProtField({label, hint, value, onCommit, disabled, id}: {label: string; hint: string; value: number; onCommit: (value: number) => void; disabled?: boolean; id: string}) {
  const [text, setText] = useState(() => value > 0 ? String(value) : '')
  const focused = useRef(false)
  useEffect(() => { if (!focused.current) setText(value > 0 ? String(value) : '') }, [value])
  const commit = () => {
    focused.current = false
    const raw = text.trim().replace(',', '.')
    if (!raw) { onCommit(0); setText(''); return }
    const parsed = Number(raw)
    if (Number.isFinite(parsed) && parsed > 0 && parsed <= 1_000_000) { const rounded = Math.round(parsed * 100) / 100; onCommit(rounded); setText(String(rounded)) } else setText(value > 0 ? String(value) : '')
  }
  return <label className="sqgField sqgProtField" htmlFor={id}><small>{label}</small>
    <input id={id} inputMode="decimal" autoComplete="off" spellCheck={false} placeholder={tx('Kapalı', 'Off')} value={text} disabled={disabled} onFocus={() => { focused.current = true }}
      onChange={event => setText(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur() }}/>
    <em>{hint}</em>
  </label>
}

type HandleKey = 'upper' | 'lower' | 'stop' | 'stopUpper'
type MapProps = {mode: GridMode; price: number; params: GridParams; lines: {price: number; kind: 'buy' | 'sell' | 'skip'}[]; tick?: number; disabled: boolean; onChange: (key: HandleKey, value: number) => void}

/** Dikey aralık haritası: fiyat, kademe çizgileri ve sürüklenebilir sınır/stop tutamakları. Klavye: ↑/↓ (Shift = daha büyük adım). */
function RangeMap({mode, price, params, lines, tick, disabled, onChange}: MapProps) {
  const H = 168
  const ref = useRef<HTMLDivElement>(null)
  const drag = useRef<{key: HandleKey; min: number; max: number} | null>(null)
  const [active, setActive] = useState<HandleKey | null>(null)
  const frozen = useRef<{min: number; max: number} | null>(null)
  const values = [price, params.lower, params.upper, params.stop, ...(mode === 'NEUTRAL' ? [params.stopUpper] : [])].filter(finite)
  const lo = Math.min(...values), hi = Math.max(...values)
  const pad = (hi - lo) * .12 || price * .01
  const axis = frozen.current ?? {min: lo - pad, max: hi + pad}
  const clamp01 = (n: number) => Math.min(1, Math.max(0, n))
  const y = (p: number) => clamp01((axis.max - p) / (axis.max - axis.min)) * H
  const handles: {key: HandleKey; label: string; value: number; tone: 'range' | 'stop'}[] = [
    {key: 'upper', label: tx('Üst', 'Upper'), value: params.upper, tone: 'range'},
    {key: 'lower', label: tx('Alt', 'Lower'), value: params.lower, tone: 'range'},
    mode === 'NEUTRAL' ? {key: 'stopUpper', label: tx('Üst stop', 'Upper stop'), value: params.stopUpper, tone: 'stop'} : null,
    {key: 'stop', label: mode === 'NEUTRAL' ? tx('Alt stop', 'Lower stop') : 'Stop', value: params.stop, tone: 'stop'},
  ].filter((item): item is {key: HandleKey; label: string; value: number; tone: 'range' | 'stop'} => item !== null)

  const valueAt = (clientY: number, min: number, max: number) => {
    const box = ref.current?.getBoundingClientRect()
    if (!box || box.height <= 0) return null
    const ratio = clamp01((clientY - box.top) / box.height)
    return snapTo(max - ratio * (max - min), tick)
  }
  const down = (key: HandleKey) => (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (disabled || (event.pointerType === 'mouse' && event.button !== 0)) return
    try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* desteklenmiyorsa sorun değil */ }
    drag.current = {key, min: axis.min, max: axis.max}; frozen.current = {min: axis.min, max: axis.max}; setActive(key)
  }
  const move = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const d = drag.current
    if (!d) return
    const next = valueAt(event.clientY, d.min, d.max)
    if (next !== null && next > 0) onChange(d.key, next)
  }
  const up = () => { drag.current = null; frozen.current = null; setActive(null) }
  const key = (handle: HandleKey, current: number) => (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
    event.preventDefault()
    const step = Math.max(tick ?? 0, price * (event.shiftKey ? .01 : .001))
    const next = snapTo(current + (event.key === 'ArrowUp' ? step : -step), tick)
    if (next > 0) onChange(handle, next)
  }
  const rangeTop = y(params.upper), rangeBottom = y(params.lower)
  return <div className="sqgMap" ref={ref} style={{height: H}} data-active={active ?? undefined} aria-label={tx('Grid aralığı haritası', 'Grid range map')}>
    <div className="sqgZone" data-kind="range" style={{top: rangeTop, height: Math.max(2, rangeBottom - rangeTop)}} aria-hidden="true"/>
    {lines.map((line, index) => <i key={`${line.price}-${index}`} className="sqgLine" data-kind={line.kind} style={{top: y(line.price)}} aria-hidden="true"/>)}
    <div className="sqgPrice" style={{top: y(price)}} aria-hidden="true"><span>{priceText(price, locale())}</span></div>
    {handles.map(handle => <button key={handle.key} type="button" className="sqgHandle" data-tone={handle.tone} data-on={active === handle.key || undefined} disabled={disabled}
      style={{top: y(handle.value)}} role="slider" aria-orientation="vertical" aria-label={handle.label} aria-valuemin={Number(axis.min.toPrecision(6))} aria-valuemax={Number(axis.max.toPrecision(6))} aria-valuenow={handle.value}
      aria-valuetext={`${handle.label} ${priceText(handle.value, locale())}`} onPointerDown={down(handle.key)} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onLostPointerCapture={up} onKeyDown={key(handle.key, handle.value)}>
      <b>{handle.label}</b><span>{priceText(handle.value, locale())}</span>
    </button>)}
  </div>
}

const MODES: {id: GridMode; tr: string; en: string; hintTr: string; hintEn: string}[] = [
  {id: 'NEUTRAL', tr: 'Nötr', en: 'Neutral', hintTr: 'Fiyat iki yöne de giderken kazanır: altta alış, üstte satış.', hintEn: 'Earns while price swings both ways: buys below, sells above.'},
  {id: 'LONG', tr: 'Yükseliş', en: 'Uptrend', hintTr: 'Yalnız alış kademeleri: düşüşlerde alır, bir kademe yukarıda satar.', hintEn: 'Buy levels only: buys dips, sells one level higher.'},
  {id: 'SHORT', tr: 'Düşüş', en: 'Downtrend', hintTr: 'Yalnız satış kademeleri: yükselişlerde satar, bir kademe aşağıda geri alır.', hintEn: 'Sell levels only: sells rallies, buys back one level lower.'},
]
const PRESETS: {id: Exclude<GridPreset, 'custom'>; tr: string; en: string}[] = [{id: 'analysis', tr: 'Analize göre', en: 'By analysis'}, {id: 'atr', tr: 'Oynaklık', en: 'Volatility'}, {id: 'tight', tr: 'Dar', en: 'Tight'}, {id: 'wide', tr: 'Geniş', en: 'Wide'}]
const MARGIN_STEPS = [20, 50, 100, 200, 300], LEVERAGE_STEPS = [1, 2, 3, 5, 10, 15, 20, 25, 30, 40, 50]

export type GridProps = {
  symbol: string; price: number | null; demo: boolean; premium: boolean; onUpgrade: () => void; analysis: AnalysisLevels; decision: string
  liveBlocker: string; policy: Record<string, unknown>; dataReady: boolean; hasPosition: boolean; hidden: boolean
  onSelect: (symbol: string) => void; onToast: (toast: GridToast) => void; onOpenChange?: (open: boolean) => void
  candles?: readonly Candle[]; onLines?: (lines: GridChartLine[]) => void; extra?: ReactNode
}

export default function StudioGrid({symbol, price, demo, premium, onUpgrade, analysis, decision, liveBlocker, policy, dataReady, hasPosition, hidden, onSelect, onToast, onOpenChange, candles = [], onLines, extra}: GridProps) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'new' | 'running' | 'history'>('new')
  const [prefs, setPrefs] = useState<Prefs>(readPrefs)
  const [params, setParams] = useState<GridParams | null>(null)
  const [preset, setPreset] = useState<GridPreset>(prefs.preset)
  const [note, setNote] = useState('')
  const [filters, setFilters] = useState<GridFilters | null>(null)
  const [filtersState, setFiltersState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [ask, setAsk] = useState<string | null>(null)
  const [server, setServer] = useState<{state: 'idle' | 'checking' | 'ok' | 'rejected' | 'unknown'; message: string; worst?: number; perCycle?: number}>({state: 'idle', message: ''})
  const busyRef = useRef(false)
  useEffect(() => { onOpenChange?.(open && !hidden); return () => onOpenChange?.(false) }, [open, hidden, onOpenChange])
  const symbolRef = useRef(symbol)
  symbolRef.current = symbol

  const {status, setStatus, error: statusError, reload} = useGridStatus(demo, open, !hidden)
  const rows = status?.grids ?? (status?.grid ? [status.grid] : [])
  const running = rows.filter(gridActive)
  const finished = rows.filter(row => !gridActive(row))
  const atr = useMemo(() => { const pct = atrPct(candles), bar = barSeconds(candles); return pct !== null && bar !== null ? {pct, barSeconds: bar} : null }, [candles])
  const here = running.find(row => row.symbol === symbol) ?? null
  const maxCount = Math.min(GRID_MAX_COUNT, Number(status?.max_levels) || GRID_MAX_COUNT)
  const maxMargin = Number(status?.max_margin_usdt) > 0 ? Number(status?.max_margin_usdt) : 300
  const policyLev = Number(policy.max_leverage)
  const maxLeverage = Math.max(1, Math.min(Number(status?.max_leverage) || 50, !demo && Number.isFinite(policyLev) && policyLev >= 1 ? Math.floor(policyLev) : 50))
  const policyMargin = Number(policy.max_margin_per_trade)
  const marginCap = Math.min(maxMargin, !demo && Number.isFinite(policyMargin) && policyMargin > 0 ? policyMargin : maxMargin)
  const marginChoices = useMemo(() => { const list = MARGIN_STEPS.filter(v => v <= marginCap); return list.length ? list : [Math.max(10, Math.floor(marginCap))] }, [marginCap])
  const leverageChoices = useMemo(() => { const list = LEVERAGE_STEPS.filter(v => v <= maxLeverage); return list.length ? list : [1] }, [maxLeverage])
  const nearest = (value: number, list: number[]) => list.includes(value) ? value : list.reduce((best, item) => Math.abs(item - value) < Math.abs(best - value) ? item : best, list[0])
  const margin = nearest(prefs.margin, marginChoices), leverage = nearest(prefs.leverage, leverageChoices)
  const maxLoss = !demo && Number(policy.max_loss_per_trade) > 0 ? Number(policy.max_loss_per_trade) : null
  const flagOff = status !== null && status.enabled === false
  const disabledReason = flagOff ? (demo ? tx('Demo grid şu an kapalı.', 'Demo grid is turned off right now.') : tx('Canlı grid henüz açılmadı. Önce Demo hesapta dene.', 'Live grid is not enabled yet. Try it on the Demo account first.')) : ''

  const updatePrefs = (change: Partial<Prefs>) => setPrefs(current => { const next = {...current, ...change}; savePrefs(next); return next })

  // Panel açılınca borsa kurallarını (fiyat/miktar adımı, en düşük emir) oku; okunamazsa önizleme uyarır ama başlatma sunucuda yine kontrol edilir.
  useEffect(() => {
    if (!open || tab !== 'new') return
    let cancelled = false
    setFiltersState('loading'); setFilters(null)
    void requestGrid<Record<string, unknown>>(filtersUrl(demo, symbol), 'GET', undefined, 15000).then(result => {
      if (cancelled || symbolRef.current !== symbol) return
      const raw = result.ok ? result.data : null
      const tickSize = Number(raw?.tickSize), stepSize = Number(raw?.stepSize), minNotional = Number(raw?.minNotional), minQty = Number(raw?.minQty)
      if (raw && tickSize > 0 && stepSize > 0 && Number.isFinite(minNotional)) { setFilters({tickSize, stepSize, minNotional, minQty: Number.isFinite(minQty) ? minQty : 0}); setFiltersState('ready') }
      else setFiltersState('error')
    })
    return () => { cancelled = true }
  }, [open, tab, demo, symbol])

  const suggest = useCallback((mode: GridMode, which: Exclude<GridPreset, 'custom'>, keep?: Partial<GridParams>) => {
    const s = suggestGrid(mode, price ?? 0, analysis, which, {tick: filters?.tickSize, step: filters?.stepSize, atr, maxCount, margin, leverage, minNotional: filters?.minNotional})
    if (!s) { setParams(null); setNote(tx('Fiyat ya da analiz verisi hazır değil.', 'Price or analysis data is not ready.')); return }
    setNote(s.note)
    setParams({mode, lower: s.lower, upper: s.upper, count: s.count, stop: s.stop, stopUpper: s.stopUpper, margin, leverage, recycle: prefs.recycle, ...keep})
  }, [price, analysis, filters, atr, maxCount, margin, leverage, prefs.recycle])

  // Panel ilk açıldığında, coin değişince ve kurallar gelince öneri yenilenir; kullanıcı elle değiştirdiyse (Özel) üzerine yazılmaz.
  const seeded = useRef('')
  useEffect(() => {
    if (!open || tab !== 'new') { seeded.current = ''; return }
    if (filtersState === 'loading' || !finite(price)) return
    const key = `${symbol}|${demo}|${filtersState}`
    if (seeded.current === key) return
    seeded.current = key
    suggest(prefs.mode, prefs.preset)
    setPreset(prefs.preset)
  }, [open, tab, symbol, demo, filtersState, price, prefs.mode, prefs.preset, suggest])
  useEffect(() => { setAsk(null); setError('') }, [symbol, demo, open])
  // Teminat/kaldıraç değişince hesaplar yeni değerlerle yeniden yapılsın.
  useEffect(() => { setParams(current => current && (current.margin !== margin || current.leverage !== leverage) ? {...current, margin, leverage} : current) }, [margin, leverage])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busyRef.current) { event.stopPropagation(); setOpen(false) } }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [open])

  const limits = useMemo(() => ({maxMargin: marginCap, maxLeverage, maxLoss}), [marginCap, maxLeverage, maxLoss])
  const preview = useMemo(() => params ? previewGrid(params, price, filters, limits) : null, [params, price, filters, limits])
  const backtest = useMemo(() => params && preview && finite(price) ? backtestGrid(params, preview, price, candles) : null, [params, preview, price, candles])

  const edit = (change: Partial<GridParams>) => { setParams(current => current ? {...current, ...change} : current); setPreset('custom'); setError('') }
  const pickMode = (mode: GridMode) => { updatePrefs({mode}); suggest(mode, preset === 'custom' ? prefs.preset : preset); if (preset === 'custom') setPreset(prefs.preset); setError('') }
  const pickPreset = (which: Exclude<GridPreset, 'custom'>) => { updatePrefs({preset: which}); setPreset(which); if (params) suggest(params.mode, which); setError('') }

  const [levAck, setLevAck] = useState(false)
  const needAck = !demo && !!params && params.leverage > HIGH_LEVERAGE_ABOVE
  useEffect(() => { setLevAck(false) }, [params?.leverage, symbol])
  const dayLimitHit = !demo && status?.day?.reached === true
  const blockReason = !dataReady || !finite(price) ? tx('Fiyat verisi hazır değil.', 'Price data is not ready.')
    : !demo && !premium ? ''
    : hasPosition ? tx(`${short(symbol)} üzerinde açık pozisyon var; aynı coinde grid açılmaz.`, `There is an open ${short(symbol)} position; a grid cannot start on the same coin.`)
    : here ? tx(`${short(symbol)} için zaten çalışan bir grid var.`, `A grid is already running on ${short(symbol)}.`)
    : dayLimitHit ? tx(`Günlük zarar limitine ulaşıldı (−${fmt(status?.day?.limit_usdt ?? 0, 2)} USDT). Yeni grid yarın (UTC) açılır.`, `Daily loss limit reached (−${fmt(status?.day?.limit_usdt ?? 0, 2)} USDT). New grids unlock tomorrow (UTC).`)
    : !demo && liveBlocker ? liveBlocker
    : disabledReason || (filtersState === 'error' ? tx('Borsa kuralları okunamadı; güvenli tarafta kalmak için başlatılmaz.', 'Exchange rules could not be read; for safety the grid will not start.') : '')
  const canStart = !!params && !!preview?.ok && !blockReason && filtersState === 'ready' && !busy && running.length < 5 && server.state !== 'rejected' && (!needAck || levAck)

  // Sunucuya plan sorusu (emir yok): sunucunun kuralları son sözü söyler; kesin ret varsa başlatma kapanır, ulaşılamazsa yerel kontrol geçerli kalır.
  const serverKey = params ? JSON.stringify(startBody(symbol, params, demo)) : ''
  const localOk = !!params && !!preview?.ok && !blockReason && filtersState === 'ready' && (demo || premium)
  useEffect(() => {
    if (!open || tab !== 'new' || !localOk || !params) { setServer({state: 'idle', message: ''}); return }
    let cancelled = false
    setServer({state: 'checking', message: ''})
    const timer = window.setTimeout(() => {
      void requestGrid<{plan?: {worst_loss_usdt?: number; profit_per_cycle_usdt?: number}}>(`${base(demo)}/start`, 'POST', previewBody(symbol, params, demo), 20000).then(result => {
        if (cancelled) return
        if (result.ok) setServer({state: 'ok', message: '', worst: Number(result.data?.plan?.worst_loss_usdt), perCycle: Number(result.data?.plan?.profit_per_cycle_usdt)})
        else if (!result.uncertain && result.status >= 400 && result.status < 500 && result.status !== 401 && result.status !== 403 && result.status !== 404) setServer({state: 'rejected', message: result.message})
        else setServer({state: 'unknown', message: ''})
      })
    }, 650)
    return () => { cancelled = true; window.clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, localOk, serverKey])

  const start = async () => {
    if (!params || busyRef.current) return
    if (!demo && !premium) { onUpgrade(); return }
    busyRef.current = true; setBusy(tx('Grid kuruluyor…', 'Starting grid…')); setError('')
    try {
      const result = await requestGrid<GridStatus & {plan?: Record<string, unknown>}>(`${base(demo)}/start`, 'POST', startBody(symbol, params, demo), 90000)
      if (result.ok) {
        setStatus(result.data); setTab('running'); requestPositionsRefresh()
        onToast({kind: 'ok', title: tx(`${short(symbol)} grid başlatıldı`, `${short(symbol)} grid started`), detail: demo ? tx('Demo hesapta çalışıyor · gerçek para yok.', 'Running on the Demo account · no real money.') : tx('Limit emirler borsaya konuyor; durdurma ve acil kapatma Çalışan sekmesinde.', 'Limit orders are being placed; stop and emergency close are on the Running tab.')})
      } else if (result.uncertain) {
        setTab('running'); void reload()
        onToast({kind: 'warn', title: tx('Grid sonucu doğrulanamadı', 'Grid result could not be confirmed'), detail: `${result.message} ${tx('Çalışan sekmesini kontrol et; aynı gridi tekrar başlatma.', 'Check the Running tab; do not start the same grid again.')}`})
      } else setError(result.message)
    } finally { busyRef.current = false; setBusy('') }
  }

  const act = async (row: GridRow, kind: 'stop' | 'emergency' | 'resume'): Promise<boolean> => {
    if (busyRef.current) return false
    busyRef.current = true; setBusy(kind === 'emergency' ? tx('Acil kapatılıyor…', 'Emergency closing…') : kind === 'stop' ? tx('Durduruluyor…', 'Stopping…') : tx('Devam ettiriliyor…', 'Resuming…')); setError('')
    let succeeded = false
    try {
      const result = await requestGrid<GridStatus>(`${base(demo)}/${kind}?grid_id=${encodeURIComponent(row.grid_id)}`, 'POST', {}, kind === 'emergency' ? 120000 : 60000)
      if (result.ok) {
        succeeded = true
        setStatus(result.data); setAsk(null); requestPositionsRefresh()
        onToast({kind: 'ok', title: kind === 'emergency' ? tx(`${short(row.symbol)} grid acil kapatıldı`, `${short(row.symbol)} grid emergency-closed`) : kind === 'stop' ? tx(`${short(row.symbol)} grid durduruldu`, `${short(row.symbol)} grid stopped`) : tx(`${short(row.symbol)} grid devam ediyor`, `${short(row.symbol)} grid resumed`),
          detail: kind === 'stop' ? tx('Bekleyen girişler iptal edildi; açık pozisyon varsa stop ve hedefleriyle yerinde.', 'Pending entries were cancelled; any open position stays with its stop and targets.') : undefined})
      } else if (result.uncertain) { void reload(); setError(`${result.message} ${tx('Durum yenileniyor; sonucu kontrol et.', 'Refreshing the status; check the result.')}`) } else setError(result.message)
    } finally { busyRef.current = false; setBusy('') }
    return succeeded
  }
  // Aralığı yeniden kur: grid'i durdurur; pozisyon kalmadıysa fiyata ortalanmış yeni öneriyle "Yeni" sekmesini açar.
  const recenter = async (row: GridRow) => {
    const ok = await act(row, 'stop')
    if (!ok) return
    if (row.side === 'LONG' || row.side === 'SHORT' || row.side === 'NEUTRAL') updatePrefs({mode: row.side})
    seeded.current = ''; setPreset(prefs.preset); setTab('new')
  }

  const totalNow = running.reduce((sum, row) => { const p = gridPnl(row, row.symbol === symbol ? price : null); return sum + (p.total ?? p.realized) }, 0)
  const hasBlocking = rows.some(row => row.needs_user)
  const holdMs = demo ? HOLD_MS.demo : HOLD_MS.live
  const startWord = params?.mode === 'NEUTRAL' ? tx('Nötr', 'Neutral') : params?.mode === 'LONG' ? tx('Yükseliş', 'Uptrend') : tx('Düşüş', 'Downtrend')

  // Grafikte çizgiler: panel açıkken önizleme, kapalıyken bu coindeki çalışan gridlerin kademeleri.
  const lines = useMemo<GridChartLine[]>(() => {
    if (hidden) return []
    if (open && tab === 'new') return chartLinesFor(preview, params)
    return chartLinesForRows(running.filter(row => row.symbol === symbol))
  }, [hidden, open, tab, preview, params, running, symbol])
  useEffect(() => { onLines?.(lines) }, [lines, onLines])
  useEffect(() => () => onLines?.([]), [onLines])

  if (hidden) return null
  return <>
    {!open && <div className="sqgChipRow"><button type="button" className="sqgChip" data-live={running.length > 0 || undefined} data-warn={hasBlocking || undefined} aria-label={tx('Grid paneli', 'Grid panel')} aria-haspopup="dialog" onClick={() => { setTab(running.length && !here ? 'running' : 'new'); setOpen(true) }}>
      <Grid3X3 aria-hidden="true"/><b>GRID</b>
      {running.length > 0 && <><span className="sqgDot" aria-hidden="true"/><span>{running.length}</span><strong data-tone={totalNow >= 0 ? 'up' : 'down'}>{signed(totalNow)}</strong></>}
    </button>{extra}</div>}

    {open && <aside className="sqgPanel" role="dialog" aria-label={tx('Grid paneli', 'Grid panel')} data-demo={demo || undefined}>
      <header className="sqgHead">
        <Grid3X3 aria-hidden="true"/><b>GRID</b><span className="sqtMode" data-demo={demo || undefined}>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
        <div className="sqgTabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'new'} data-on={tab === 'new'} onClick={() => setTab('new')}>{tx('Yeni', 'New')}</button>
          <button type="button" role="tab" aria-selected={tab === 'running'} data-on={tab === 'running'} onClick={() => setTab('running')}>{tx('Çalışan', 'Running')}{running.length > 0 && <em>{running.length}</em>}</button>
          <button type="button" role="tab" aria-selected={tab === 'history'} data-on={tab === 'history'} onClick={() => setTab('history')}>{tx('Geçmiş', 'History')}</button>
        </div>
        <button type="button" className="sqgX" aria-label={tx('Kapat', 'Close')} onClick={() => setOpen(false)} disabled={!!busy}><X aria-hidden="true"/></button>
      </header>

      <div className="sqgBody">
        {tab === 'new' && <div className="sqgCols">
          <div className="sqgCol">
          <div className="sqgSeg" role="radiogroup" aria-label={tx('Grid türü', 'Grid type')}>
            {MODES.map(item => <button key={item.id} type="button" role="radio" aria-checked={params?.mode === item.id} data-on={params?.mode === item.id} data-mode={item.id} onClick={() => pickMode(item.id)}>{tx(item.tr, item.en)}</button>)}
          </div>
          <p className="sqgHint"><Info aria-hidden="true"/>{params ? tx(MODES.find(m => m.id === params.mode)?.hintTr ?? '', MODES.find(m => m.id === params.mode)?.hintEn ?? '') : ''}</p>

          <div className="sqgPresets" role="radiogroup" aria-label={tx('Aralık genişliği', 'Range width')}>
            {PRESETS.map(item => <button key={item.id} type="button" role="radio" aria-checked={preset === item.id} data-on={preset === item.id} onClick={() => pickPreset(item.id)}>{item.id === 'analysis' && <Sparkles aria-hidden="true"/>}{tx(item.tr, item.en)}</button>)}
            {preset === 'custom' && <span className="sqgCustom" data-on="true">{tx('Özel', 'Custom')}</span>}
          </div>
          {note && <p className="sqgNote">{note}{decision === 'LONG' || decision === 'SHORT' ? ` ${tx('Analiz', 'Analysis')}: ${decision}.` : ''}</p>}

          {params && finite(price)
            ? <div className="sqgGrid2">
                <RangeMap mode={params.mode} price={price} params={params} lines={preview?.lines ?? []} tick={filters?.tickSize} disabled={!!busy} onChange={(key, value) => edit({[key]: value} as Partial<GridParams>)}/>
                <div className="sqgFields">
                  <NumField id="sqg-upper" label={tx('Üst sınır', 'Upper bound')} value={params.upper} tick={filters?.tickSize} disabled={!!busy} onCommit={value => edit({upper: value})}/>
                  <NumField id="sqg-lower" label={tx('Alt sınır', 'Lower bound')} value={params.lower} tick={filters?.tickSize} disabled={!!busy} onCommit={value => edit({lower: value})}/>
                  {params.mode === 'NEUTRAL' && <NumField id="sqg-stop-up" label={tx('Üst stop', 'Upper stop')} tone="stop" value={params.stopUpper} tick={filters?.tickSize} disabled={!!busy} onCommit={value => edit({stopUpper: value})}/>}
                  <NumField id="sqg-stop" label={params.mode === 'NEUTRAL' ? tx('Alt stop', 'Lower stop') : 'Stop'} tone="stop" value={params.stop} tick={filters?.tickSize} disabled={!!busy} onCommit={value => edit({stop: value})}/>
                  <div className="sqgCount" role="group" aria-label={tx('Kademe sayısı', 'Level count')}><small>{tx('Kademe', 'Levels')}</small>
                    <div><button type="button" aria-label={tx('Kademeyi azalt', 'Fewer levels')} disabled={!!busy || params.count <= GRID_MIN_COUNT} onClick={() => edit({count: params.count - 1})}><Minus aria-hidden="true"/></button>
                      <output aria-live="polite">{params.count}</output>
                      <button type="button" aria-label={tx('Kademeyi artır', 'More levels')} disabled={!!busy || params.count >= maxCount} onClick={() => edit({count: params.count + 1})}>+</button></div>
                  </div>
                </div>
              </div>
            : <p className="sqgEmpty">{tx('Fiyat verisi bekleniyor…', 'Waiting for price data…')}</p>}
          </div>

          <div className="sqgCol">
          <div className="sqgParams">
            <div className="sqtParam"><small>{tx('Toplam teminat (USDT)', 'Total margin (USDT)')}</small><div role="radiogroup" aria-label={tx('Toplam teminat', 'Total margin')}>{marginChoices.map(v => <button key={v} type="button" role="radio" aria-checked={v === margin} data-on={v === margin} disabled={!!busy} onClick={() => updatePrefs({margin: v})}>{v}</button>)}</div></div>
            <div className="sqtParam"><small>{tx('Kaldıraç', 'Leverage')}</small><div role="radiogroup" className="sqgLevScroll" aria-label={tx('Kaldıraç', 'Leverage')}>{leverageChoices.map(v => <button key={v} type="button" role="radio" aria-checked={v === leverage} data-on={v === leverage} disabled={!!busy} onClick={() => updatePrefs({leverage: v})}>{v}x</button>)}</div></div>
          </div>

          {params && params.mode !== 'NEUTRAL'
            ? <label className="sqgSwitch"><input type="checkbox" checked={params.recycle} disabled={!!busy} onChange={event => { updatePrefs({recycle: event.target.checked}); edit({recycle: event.target.checked}) }}/><span aria-hidden="true"/>
                <div><b>{tx('Sürekli çalış', 'Keep cycling')}</b><small>{tx('Hedef dolunca aynı kademede yeniden giriş kurulur. Kapalıysa her kademe bir kez çalışır.', 'When a target fills the same level is re-armed. If off, each level runs once.')}</small></div></label>
            : params && <p className="sqgHint"><Info aria-hidden="true"/>{tx('Nötr grid her zaman sürekli çalışır: hedef dolunca aynı çizgide yeniden kurulur.', 'A neutral grid always cycles: when a target fills the line is re-armed.')}</p>}

          {params && <div className="sqgProt" role="group" aria-label={tx('Kâr ve zarar koruması', 'Profit and loss protection')}>
            <ProtField id="sqg-tp" label={tx('Hedef kâr (USDT)', 'Take profit (USDT)')} hint={params.closeOnTarget ? tx('Ulaşınca yeni giriş durur ve pozisyon kapanır', 'New entries stop and the position closes') : tx('Ulaşınca yeni giriş durur', 'New entries stop when reached')} value={params.takeProfit ?? 0} disabled={!!busy} onCommit={value => setParams(current => current ? {...current, takeProfit: value} : current)}/>
            <ProtField id="sqg-sl" label={tx('Zarar sınırı (USDT)', 'Loss limit (USDT)')} hint={tx('Ulaşınca grid kapatılır', 'The grid is closed when reached')} value={params.lossLimit ?? 0} disabled={!!busy} onCommit={value => setParams(current => current ? {...current, lossLimit: value} : current)}/>
            <ProtField id="sqg-lock" label={tx('Kâr kilidi (USDT)', 'Profit lock (USDT)')} hint={tx('Bu kâra ulaşınca kilit başlar; kâr zirvenin %70’ine düşerse grid kapanır', 'Starts at this profit; the grid closes if profit falls below 70% of its peak')} value={params.profitLock ?? 0} disabled={!!busy} onCommit={value => setParams(current => current ? {...current, profitLock: value} : current)}/>
            <label className="sqgSwitch sqgProtSwitch"><input type="checkbox" checked={!!params.closeOnTarget} disabled={!!busy || !(params.takeProfit && params.takeProfit > 0)} onChange={event => setParams(current => current ? {...current, closeOnTarget: event.target.checked} : current)}/><span aria-hidden="true"/>
              <div><b>{tx('Hedefte pozisyonu da kapat', 'Also close the position at target')}</b><small>{params.takeProfit && params.takeProfit > 0 ? tx('Kapalıysa hedefte yalnız yeni giriş durur.', 'If off, only new entries stop at the target.') : tx('Önce bir hedef kâr gir.', 'Enter a take-profit first.')}</small></div></label>
          </div>}

          {preview && preview.activeLevels > 0 && <dl className="sqgStats">
            <div><dt>{tx('Kademe aralığı', 'Level spacing')}</dt><dd>%{fmt(preview.spacingPct, 2)}</dd></div>
            <div data-tone="gain"><dt>{tx('Tur başı net kâr', 'Net per cycle')}</dt><dd>{preview.profitPerCycle !== null ? `+${fmt(preview.profitPerCycle, Math.abs(preview.profitPerCycle) < 0.1 ? 4 : 2)}` : '—'} <u>USDT</u></dd></div>
            <div data-tone="loss"><dt>{tx('En kötü durum', 'Worst case')}</dt><dd>{preview.worstLoss !== null ? `−${fmt(preview.worstLoss)}` : '—'} <u>USDT</u></dd></div>
            <div><dt>{tx('Gereken teminat', 'Margin needed')}</dt><dd>{fmt(preview.marginNeeded)} <u>USDT</u></dd></div>
          </dl>}

          {backtest && <section className="sqgBack" aria-label={tx('Geçmişe göre deneme', 'Backtest')}>
            <h4>{tx('Geçmişte nasıl çalışırdı?', 'How would it have run?')}</h4>
            <p className="sqgBackSub">{tx(`Son ${backtest.candles} mum (≈ ${fmt(backtest.hours, backtest.hours < 10 ? 1 : 0)} saat) · aynı genişlik ve kademe`, `Last ${backtest.candles} candles (≈ ${fmt(backtest.hours, backtest.hours < 10 ? 1 : 0)} h) · same width and levels`)}</p>
            <dl className="sqgStats">
              <div><dt>{tx('Tamamlanan tur', 'Cycles')}</dt><dd>{backtest.cycles} <u>{tx(`· günde ~${fmt(backtest.cyclesPerDay, 1)}`, `· ~${fmt(backtest.cyclesPerDay, 1)}/day`)}</u></dd></div>
              <div data-tone={backtest.net >= 0 ? 'gain' : 'loss'}><dt>{tx('Net sonuç', 'Net result')}</dt><dd>{signed(backtest.net, Math.abs(backtest.net) < 1 ? 3 : 2)} <u>USDT</u></dd></div>
              <div data-tone="loss"><dt>{tx('En büyük düşüş', 'Max drawdown')}</dt><dd>−{fmt(backtest.maxDrawdown)} <u>USDT</u></dd></div>
              <div><dt>{tx('Aralıkta kalma', 'Time in range')}</dt><dd>%{fmt(backtest.inRangePct, 0)}</dd></div>
            </dl>
            {backtest.stopped && <p className="sqgBackWarn"><TriangleAlert aria-hidden="true"/>{tx('Bu dönemde stop çalışırdı; grid erken biterdi.', 'The stop would have triggered in this window; the grid would have ended early.')}</p>}
            <p className="sqgBackFine">{tx('Geçmiş sonuç gelecek için garanti değildir. Ücret dahil; funding ve kayma hariç.', 'Past results are no guarantee. Fees included; funding and slippage excluded.')}</p>
          </section>}

          <ul className="sqgChecks" aria-live="polite">
            {(preview?.issues ?? []).map(text => <li key={text} data-ok="false"><TriangleAlert aria-hidden="true"/>{text}</li>)}
            {(preview?.warnings ?? []).map(text => <li key={text} data-ok="warn"><Info aria-hidden="true"/>{text}</li>)}
            {blockReason && <li data-ok="false"><ShieldAlert aria-hidden="true"/>{blockReason}{!demo && liveBlocker && blockReason === liveBlocker && <ConsentRenew blocker={liveBlocker} onDone={hours => onToast({kind: 'ok', title: tx('Risk izni yenilendi', 'Risk permission renewed'), detail: tx(`${hours} saat geçerli. Canlı işlem için ARM gerekebilir.`, `Valid for ${hours} h. Live trading may need ARM.`)})}/>}</li>}
            {server.state === 'rejected' && <li data-ok="false"><ShieldAlert aria-hidden="true"/>{server.message}</li>}
            {preview?.ok && !blockReason && server.state !== 'rejected' && (server.state === 'ok'
              ? <li data-ok="true"><Check aria-hidden="true"/>{tx('Sunucu planı doğruladı', 'The server confirmed the plan')}{Number.isFinite(server.worst) && server.worst ? tx(` · en kötü −${fmt(server.worst!)} USDT`, ` · worst case −${fmt(server.worst!)} USDT`) : ''}</li>
              : <li data-ok="true"><Check aria-hidden="true"/>{server.state === 'checking' ? tx('Kademeler borsa kurallarına uyuyor · sunucu doğruluyor…', 'Levels fit the exchange rules · the server is checking…') : tx('Kademeler borsa kurallarına uyuyor; son kontrolü sunucu yapar.', 'Levels fit the exchange rules; the server runs the final check.')}</li>)}
            {filtersState === 'loading' && <li data-ok="warn"><Info aria-hidden="true"/>{tx('Borsa kuralları okunuyor…', 'Reading exchange rules…')}</li>}
          </ul>
          {needAck && params && <label className="sqgAck" data-on={levAck || undefined}>
            <input type="checkbox" checked={levAck} onChange={event => setLevAck(event.target.checked)}/>
            <span aria-hidden="true"><Check/></span>
            <div><b>{tx(`${params.leverage}x yüksek kaldıraç`, `${params.leverage}x high leverage`)}</b><small>{tx(`Fiyat girişe göre yaklaşık %${fmt(100 / params.leverage, 1)} ters giderse pozisyon likide olabilir. Stop mesafesi bunun içinde kalmalı. Riski anlıyorum.`, `If price moves about ${fmt(100 / params.leverage, 1)}% against the entry the position can be liquidated. Keep your stop inside that distance. I understand the risk.`)}</small></div>
          </label>}
          {error && <p className="sqgError" role="alert"><TriangleAlert aria-hidden="true"/>{error}</p>}
          </div>
        </div>}

        {tab === 'running' && <>
          {statusError && !status && <p className="sqgError" role="alert"><TriangleAlert aria-hidden="true"/>{statusError}</p>}
          {status?.day && <DayCard day={status.day} demo={demo}/>}
          {!running.length && <p className="sqgEmpty">{tx('Çalışan grid yok. “Yeni” sekmesinden başlat.', 'No running grids. Start one from the “New” tab.')}</p>}
          {running.map(row => <GridCard key={row.grid_id} row={row} price={row.symbol === symbol ? price : null} demo={demo} busy={!!busy} ask={ask} setAsk={setAsk} holdMs={holdMs} onAct={act} onSelect={onSelect} onRecenter={recenter}/>)}
          {error && <p className="sqgError" role="alert"><TriangleAlert aria-hidden="true"/>{error}</p>}
        </>}

        {tab === 'history' && <History rows={finished}/>}
      </div>

      {tab === 'new' && <footer className="sqgFoot">
        {!demo && !premium
          ? <button type="button" className="sqgUpgrade" onClick={onUpgrade}>{tx('Canlı grid için Smart Trade / Master Mode gerekir', 'Live grid needs Smart Trade / Master Mode')}</button>
          : <HoldButton ms={holdMs} tone={params?.mode === 'SHORT' ? 'sell' : 'buy'} disabled={!canStart}
              label={busy || tx(`Basılı tut · ${startWord} grid başlat`, `Hold · start ${startWord} grid`)} onDone={() => void start()}
              hint={tx(`${symbol} ${startWord} gridini başlatmak için basılı tut`, `Press and hold to start the ${symbol} ${startWord} grid`)}/>}
        <p className="sqgFine">{demo ? tx('Yalnız Demo hesap · gerçek para yok.', 'Demo account only · no real money.') : tx('Gerçek para · tahmin ücret dahil, funding/kayma hariç.', 'Real money · estimate includes fees, not funding/slippage.')}</p>
      </footer>}
    </aside>}
  </>
}

const trim = (value: number, digits: number) => value.toLocaleString(locale(), {minimumFractionDigits: 0, maximumFractionDigits: digits})
const duration = (hours: number | null) => hours === null ? '—' : hours < 1 ? tx(`${Math.max(1, Math.round(hours * 60))} dk`, `${Math.max(1, Math.round(hours * 60))} min`) : hours < 48 ? tx(`${trim(hours, hours < 10 ? 1 : 0)} saat`, `${trim(hours, hours < 10 ? 1 : 0)} h`) : tx(`${trim(hours / 24, 1)} gün`, `${trim(hours / 24, 1)} d`)

/** Biten gridlerin kısa raporu: süre, tur, ücret ve net sonuç; üstte toplam. */
function DayCard({day, demo}: {day: GridDay; demo: boolean}) {
  const net = day.realized_usdt
  const limit = !demo && day.limit_usdt ? day.limit_usdt : 0
  const ratio = limit > 0 ? Math.min(1, day.used_usdt / limit) : 0
  const tone = day.reached ? 'bad' : ratio >= 0.6 ? 'warn' : 'ok'
  return <section className="sqgDay" data-tone={net >= 0 ? 'gain' : 'loss'} aria-label={tx('Bugünün özeti', "Today's summary")}>
    <header><h4>{tx('Bugün', 'Today')}</h4><span>{tx('UTC gün başından beri', 'Since UTC midnight')}</span></header>
    <div className="sqgDayNet"><b data-tone={net >= 0 ? 'up' : 'down'}>{signed(net, 2)}</b><u>USDT</u><small>{tx('gerçekleşen net', 'realized net')}</small></div>
    <dl className="sqgDayMeta">
      <div><dt>{tx('Grid', 'Grids')}</dt><dd>{day.grids}</dd></div>
      <div><dt>{tx('Tur', 'Cycles')}</dt><dd>{day.cycles}</dd></div>
      <div><dt>{tx('Ücret', 'Fees')}</dt><dd>{fmt(day.fees_usdt, 2)}</dd></div>
      {day.best_usdt !== null && <div><dt>{tx('En iyi', 'Best')}</dt><dd data-tone={day.best_usdt >= 0 ? 'up' : 'down'}>{signed(day.best_usdt, 2)}</dd></div>}
    </dl>
    {limit > 0 && <div className="sqgDayLimit" data-tone={tone}>
      <div className="sqgDayBar" role="progressbar" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={Math.min(limit, day.used_usdt)} aria-label={tx('Günlük zarar limiti', 'Daily loss limit')}><i style={{width: `${Math.round(ratio * 100)}%`}}/></div>
      <p>{day.reached ? tx(`Limit doldu · yeni grid açılmaz, açık gridlerde yeni giriş durur`, 'Limit reached · no new grids, open grids stop new entries')
        : tx(`Günlük zarar limiti: ${fmt(day.used_usdt, 2)} / ${fmt(limit, 2)} USDT · ${fmt(day.left_usdt ?? 0, 2)} hakkın var`, `Daily loss limit: ${fmt(day.used_usdt, 2)} / ${fmt(limit, 2)} USDT · ${fmt(day.left_usdt ?? 0, 2)} left`)}</p>
    </div>}
  </section>
}

function History({rows}: {rows: GridRow[]}) {
  const items = rows.map(row => ({row, ...gridSummary(row)}))
  const net = items.reduce((s, i) => s + i.net, 0), cycles = items.reduce((s, i) => s + i.cycles, 0), fees = items.reduce((s, i) => s + i.fees, 0)
  if (!items.length) return <p className="sqgEmpty">{tx('Henüz biten grid yok.', 'No finished grids yet.')}</p>
  return <>
    <dl className="sqgStats sqgTotals">
      <div data-tone={net >= 0 ? 'gain' : 'loss'}><dt>{tx('Toplam net', 'Total net')}</dt><dd>{signed(net, 3)} <u>USDT</u></dd></div>
      <div><dt>{tx('Toplam tur', 'Total cycles')}</dt><dd>{cycles}</dd></div>
      <div><dt>{tx('Ödenen ücret', 'Fees paid')}</dt><dd>{fmt(fees, 3)} <u>USDT</u></dd></div>
      <div><dt>{tx('Grid sayısı', 'Grids')}</dt><dd>{items.length}</dd></div>
    </dl>
    {items.map(({row, hours, cycles: c, net: n, fees: f}) => {
      const state = gridStatusText(row.status)
      const sideText = row.side === 'NEUTRAL' ? tx('Nötr', 'Neutral') : row.side === 'LONG' ? tx('Yükseliş', 'Uptrend') : tx('Düşüş', 'Downtrend')
      return <article key={row.grid_id} className="sqgHist" data-tone={state.tone}>
        <div className="sqgCardTop"><b>{row.symbol}</b><span className="sqgSide" data-side={row.side}>{sideText}</span><span className="sqgState" data-tone={state.tone}>{state.text}</span></div>
        <dl className="sqgMeta">
          <div><dt>{tx('Net', 'Net')}</dt><dd data-tone={n >= 0 ? 'up' : 'down'}>{signed(n, 3)}</dd></div>
          <div><dt>{tx('Tur', 'Cycles')}</dt><dd>{c}</dd></div>
          <div><dt>{tx('Ücret', 'Fees')}</dt><dd>{fmt(f, 3)}</dd></div>
          <div><dt>{tx('Süre', 'Duration')}</dt><dd>{duration(hours)}</dd></div>
        </dl>
        {row.reason && <p className="sqgReason">{row.reason}</p>}
      </article>
    })}
    <p className="sqgBackFine">{tx('Net: tamamlanan turların kârı (ücret düşülmüş). Stop ya da acil kapanıştaki zarar borsadaki gerçekleşen fiyata göre değişir; pozisyonlar sekmesinden kontrol et.', 'Net: profit of completed cycles (fees deducted). Loss on a stop or emergency close depends on the exchange fill price; check it on the Positions tab.')}</p>
  </>
}

function GridCard({row, price, demo, busy, ask, setAsk, holdMs, onAct, onSelect, onRecenter}: {
  row: GridRow; price: number | null; demo: boolean; busy: boolean; ask: string | null; setAsk: (id: string | null) => void; holdMs: number
  onAct: (row: GridRow, kind: 'stop' | 'emergency' | 'resume') => unknown; onSelect: (symbol: string) => void; onRecenter?: (row: GridRow) => unknown
}) {
  const done = !gridActive(row)
  const range = gridRangeStatus(row, price)
  const pnl = gridPnl(row, price)
  const state = gridStatusText(row.status)
  const levels = row.spec?.levels ?? []
  const entries = levels.map(l => l.entry), lo = entries.length ? Math.min(...entries) : null, hi = entries.length ? Math.max(...entries) : null
  const sideText = row.side === 'NEUTRAL' ? tx('Nötr', 'Neutral') : row.side === 'LONG' ? tx('Yükseliş', 'Uptrend') : tx('Düşüş', 'Downtrend')
  const openCount = row.stats?.open_levels.length ?? 0
  const alert = [...(row.alerts ?? [])].reverse().find(a => a.level === 'kritik' || a.level === 'uyarı')
  const staleLoop = !done && row.loop_running === false && !row.needs_user
  return <article className="sqgCard" data-tone={state.tone} data-done={done || undefined}>
    <div className="sqgCardTop">
      <button type="button" className="sqgSym" onClick={() => onSelect(row.symbol)} aria-label={tx(`${row.symbol} grafiğine git`, `Go to ${row.symbol} chart`)}><b>{row.symbol}</b></button>
      <span className="sqgSide" data-side={row.side}>{sideText}</span>
      <span className="sqgState" data-tone={state.tone}>{state.text}</span>
    </div>
    <div className="sqgPnl" data-tone={(pnl.total ?? pnl.realized) >= 0 ? 'up' : 'down'}>
      <strong>{signed(pnl.total ?? pnl.realized, Math.abs(pnl.total ?? pnl.realized) < 1 ? 4 : 2)} <u>USDT</u></strong>
      <small>{pnl.unrealized === null ? tx('gerçekleşen (açık kademe değeri için coini aç)', 'realised (open the coin to value open levels)') : tx('toplam · tur kârı + açık kademe', 'total · cycle profit + open levels')}</small>
    </div>
    <dl className="sqgMeta">
      <div><dt>{tx('Tur', 'Cycles')}</dt><dd>{pnl.cycles}</dd></div>
      <div><dt>{tx('Gerçekleşen', 'Realised')}</dt><dd data-tone={pnl.realized >= 0 ? 'up' : 'down'}>{signed(pnl.realized, 3)}</dd></div>
      <div><dt>{tx('Açık kademe', 'Open levels')}</dt><dd>{openCount}</dd></div>
      <div><dt>{tx('Aralık', 'Range')}</dt><dd>{lo !== null && hi !== null ? `${priceText(lo, locale())} – ${priceText(hi, locale())}` : '—'}</dd></div>
    </dl>
    {!done && row.liq && row.liq.distance_pct !== null && <div className="sqgLiq" data-level={row.liq.level} role="status"><span>{tx('Likidasyona', 'To liquidation')}</span><b>%{fmt(row.liq.distance_pct, 1)}</b><i aria-hidden="true"><u style={{width: `${Math.min(100, row.liq.distance_pct / 30 * 100)}%`}}/></i></div>}
    {!done && (row.spec?.meta?.profit_lock_usdt ?? 0) > 0 && <p className="sqgLock"><Info aria-hidden="true"/>{tx(`Kâr kilidi: ${fmt(row.spec?.meta?.profit_lock_usdt ?? 0, 2)} USDT'den sonra aktif · zirve ${fmt(row.peak_pnl ?? 0, 2)}`, `Profit lock: active above ${fmt(row.spec?.meta?.profit_lock_usdt ?? 0, 2)} USDT · peak ${fmt(row.peak_pnl ?? 0, 2)}`)}</p>}
    {row.reason && <p className="sqgReason">{row.reason}</p>}
    {alert && <p className="sqgAlert" data-level={alert.level}><TriangleAlert aria-hidden="true"/>{alert.text}</p>}
    {staleLoop && <p className="sqgAlert" data-level="uyarı"><TriangleAlert aria-hidden="true"/>{tx('Grid döngüsü durmuş (sunucu yeniden başlamış olabilir). Borsadaki stoplar yerinde; devam ettir.', 'The grid loop has stopped (the server may have restarted). Exchange stops stay in place; resume it.')}</p>}
    {!done && (range.state === 'above' || range.state === 'below') && <div className="sqgRange" role="status">
      <p><TriangleAlert aria-hidden="true"/>{range.state === 'above'
        ? tx(`Fiyat grid aralığının %${fmt(range.pct, 2)} üstünde. Alış kademeleri dolmaz; mevcut kademeler hedefte bekler.`, `Price is ${fmt(range.pct, 2)}% above the grid range. Buy levels will not fill; open levels wait at their targets.`)
        : tx(`Fiyat grid aralığının %${fmt(range.pct, 2)} altında. Satış kademeleri dolmaz; açık kademeler stopa yaklaşıyor olabilir.`, `Price is ${fmt(range.pct, 2)}% below the grid range. Sell levels will not fill; open levels may be nearing the stop.`)}</p>
      {onRecenter && (openCount === 0
        ? (ask === `recenter:${row.grid_id}`
            ? <div className="sqgAsk" role="alertdialog" aria-label={tx('Yeniden kur onayı', 'Re-centre confirmation')}>
                <span>{tx('Grid durdurulsun ve fiyata ortalanmış yeni bir öneri açılsın mı? Bekleyen girişler iptal edilir.', 'Stop this grid and open a new suggestion centred on the price? Pending entries are cancelled.')}</span>
                <div><button type="button" className="sqtBtn" data-tone="primary" disabled={busy} onClick={() => void onRecenter(row)}>{tx('Evet, yeniden kur', 'Yes, re-centre')}</button><button type="button" className="sqtBtn" disabled={busy} onClick={() => setAsk(null)}>{tx('Vazgeç', 'Cancel')}</button></div>
              </div>
            : <button type="button" className="sqtBtn" data-tone="primary" disabled={busy} onClick={() => setAsk(`recenter:${row.grid_id}`)}>{tx('Durdur ve yeniden kur', 'Stop and re-centre')}</button>)
        : <small>{tx('Açık kademe var: önce hedefine ulaşmasını bekle ya da acil kapat; sonra yeniden kurulabilir.', 'There are open levels: wait for them to reach their targets or emergency-close, then re-centre.')}</small>)}
    </div>}
    {!done && <div className="sqgActs">
      {ask === `stop:${row.grid_id}`
        ? <div className="sqgAsk" role="alertdialog" aria-label={tx('Grid durdurma onayı', 'Stop grid confirmation')}>
            <span>{tx('Bekleyen girişler iptal edilsin mi? Açık pozisyon varsa stop ve hedefleriyle kalır.', 'Cancel pending entries? An open position stays with its stop and targets.')}</span>
            <div><button type="button" className="sqtBtn" data-tone="primary" disabled={busy} onClick={() => onAct(row, 'stop')}>{tx('Evet, durdur', 'Yes, stop')}</button><button type="button" className="sqtBtn" disabled={busy} onClick={() => setAsk(null)}>{tx('Vazgeç', 'Cancel')}</button></div>
          </div>
        : <>
            {staleLoop && <button type="button" className="sqtBtn" data-tone="primary" disabled={busy} onClick={() => onAct(row, 'resume')}>{tx('Devam ettir', 'Resume')}</button>}
            {row.status !== 'PAUSED' && !row.needs_user && <button type="button" className="sqtBtn" disabled={busy} onClick={() => setAsk(`stop:${row.grid_id}`)}>{tx('Durdur', 'Stop')}</button>}
            <HoldButton ms={holdMs} tone="danger" disabled={busy} label={tx('Acil kapat · basılı tut', 'Emergency close · hold')} onDone={() => onAct(row, 'emergency')}
              hint={tx(`${row.symbol} gridini acil kapatmak için basılı tut`, `Press and hold to emergency-close the ${row.symbol} grid`)}/>
          </>}
    </div>}
    {demo && <span className="sqgDemoTag" aria-hidden="true">DEMO</span>}
  </article>
}

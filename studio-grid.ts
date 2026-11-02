// Analysis Studio · grid: öneri, önizleme hesabı ve sunucu çağrıları (ekran yok).
// Önizleme sunucudaki planlayıcıyla (grid_live.py) AYNI kademe yerleşimini ve zarar formülünü kullanır; yine de sunucu
// her başlatmada kendi kontrolünü yapar ve bu dosyadaki sonuç yalnız bir TAHMİNdir (funding ve kayma dahil değildir).
import type {AnalysisLevels, CallResult} from './studio-quick-trade'

// studio-quick-trade.ts'in dil okuyucusuyla aynı davranış; node testleri kardeş dosya içe aktarmadan çalışsın diye burada küçük bir kopyası var.
let readEnglish: () => boolean = () => false
export const setGridLanguageReader = (reader: () => boolean) => { readEnglish = reader }
const tx = (tr: string, en: string): string => readEnglish() ? en : tr

function errorText(payload: unknown, fallback: string): string {
  const detail = payload && typeof payload === 'object' ? (payload as {detail?: unknown; message?: unknown}).detail ?? (payload as {message?: unknown}).message : payload
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const parts = detail.map(item => item && typeof item === 'object' && typeof (item as {msg?: unknown}).msg === 'string' ? String((item as {msg: string}).msg) : typeof item === 'string' ? item : '').filter(Boolean)
    if (parts.length) return parts.join(' · ')
  }
  if (detail && typeof detail === 'object' && typeof (detail as {message?: unknown}).message === 'string') return String((detail as {message: string}).message)
  return fallback
}

export type GridMode = 'LONG' | 'SHORT' | 'NEUTRAL'
export type GridPreset = 'analysis' | 'atr' | 'tight' | 'wide' | 'custom'
export type GridFilters = {tickSize: number; stepSize: number; minNotional: number; minQty: number}
export type GridParams = {mode: GridMode; lower: number; upper: number; count: number; stop: number; stopUpper: number; margin: number; leverage: number; recycle: boolean; takeProfit?: number; lossLimit?: number; profitLock?: number; closeOnTarget?: boolean}

export const GRID_MAKER_FEE = 0.0002
export const GRID_TAKER_FEE = 0.0005
export const GRID_MIN_NOTIONAL = 5
export const GRID_MIN_COUNT = 2
export const GRID_MAX_COUNT = 20
export const GRID_MIN_SPACING_PCT = .3
export const GRID_LIVE_PHRASE = 'CANLI GRID BAŞLAT'
export const GRID_DEMO_PHRASE = 'DEMO GRID BAŞLAT'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0
const clamp = (value: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, value))

export function snapTo(value: number, step: number | undefined, mode: 'round' | 'floor' | 'ceil' = 'round'): number {
  if (!finite(step) || !finite(value)) return value
  const n = value / step
  const k = mode === 'floor' ? Math.floor(n + 1e-9) : mode === 'ceil' ? Math.ceil(n - 1e-9) : Math.round(n)
  const out = k * step
  const decimals = Math.max(0, Math.min(12, Math.ceil(-Math.log10(step)) + 1))
  return Number(out.toFixed(decimals))
}

/** Okunur fiyat: büyüklüğe göre ondalık sayısı. */
export function priceText(value: number | null | undefined, locale = 'tr-TR'): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  const digits = abs >= 1000 ? 2 : abs >= 100 ? 3 : abs >= 1 ? 4 : abs >= .01 ? 5 : 7
  return value.toLocaleString(locale, {minimumFractionDigits: 0, maximumFractionDigits: digits})
}

export type Suggestion = {lower: number; upper: number; count: number; stop: number; stopUpper: number; source: 'analysis' | 'default'; note: string}

type SuggestOptions = {tick?: number; step?: number; maxCount?: number; margin: number; leverage: number; minNotional?: number; atr?: {pct: number; barSeconds: number} | null}

/**
 * Fiyata, analize ve seçilen genişliğe göre akıllı aralık / kademe / stop önerir.
 * - Dar: ±%2 · Geniş: ±%6 · Analize göre: analizin hedef mesafesinden türetilir (analiz yoksa Dar'a düşer ve bunu söyler).
 * - Tek yönlü gridde stop, en uzak girişten en fazla ~%3 ötede kalır (sunucu varsayılan sınırı %5).
 * - Kademe sayısı: kademe aralığı ücretleri rahat karşılasın (>= %0,45 hedef, en az %0,3) ve her kademe borsanın alt emir sınırının üstünde kalsın.
 */
export function suggestGrid(mode: GridMode, price: number, analysis: AnalysisLevels, preset: Exclude<GridPreset, 'custom'>, options: SuggestOptions): Suggestion | null {
  if (!finite(price)) return null
  const tick = options.tick
  const maxCount = clamp(Math.floor(options.maxCount ?? GRID_MAX_COUNT), GRID_MIN_COUNT, GRID_MAX_COUNT)
  // Bir kademe en az bir miktar adımı (stepSize) kadar olmalı: BTC gibi pahalı coinlerde adım tek başına yüzlerce USDT olabilir.
  const minNotional = Math.max(Math.max(GRID_MIN_NOTIONAL, options.minNotional ?? 0) * 1.2, (options.step ?? 0) * price * 1.25)

  const a = analysis
  const hasAnalysis = !!a && finite(a.tp1) && finite(a.stop_loss)
  const base = a && finite(a.entry) ? a.entry : price
  let w: number
  let source: Suggestion['source'] = 'default'
  let atrUsed = false
  if (preset === 'tight') w = .02
  else if (preset === 'wide') w = .06
  else if (preset === 'atr' && options.atr && finite(options.atr.pct) && finite(options.atr.barSeconds)) {
    // Yarım günlük tipik hareket: ATR% × √(yarım gündeki mum sayısı). Sakin coinde dar, hareketli coinde geniş aralık çıkar.
    const barsHalfDay = Math.max(1, 43200 / options.atr.barSeconds)
    w = clamp(options.atr.pct / 100 * Math.sqrt(barsHalfDay), .012, .07); atrUsed = true
  }
  else if (preset === 'analysis' && hasAnalysis && a) {
    const d = Math.max(Math.abs((a.tp1 as number) - base), Math.abs(base - (a.stop_loss as number))) / base
    w = clamp(d * 1.4, .015, .07); source = 'analysis'
  } else w = .02

  let lower: number, upper: number, stop: number, stopUpper = 0
  if (mode === 'NEUTRAL') {
    lower = price * (1 - w); upper = price * (1 + w)
    const s = clamp(w * .35, .008, .03)
    stop = lower * (1 - s); stopUpper = upper * (1 + s)
  } else {
    let depth = clamp(w * 1.2, .012, .04)
    const analysisSide = a?.direction === 'LONG' || a?.direction === 'SHORT' ? a.direction : null
    const sign = mode === 'LONG' ? 1 : -1
    let analysisStop: number | null = null
    if (source === 'analysis' && analysisSide === mode && a && finite(a.stop_loss) && (mode === 'LONG' ? a.stop_loss < price : a.stop_loss > price)) {
      analysisStop = a.stop_loss
      depth = clamp(Math.abs(price - analysisStop) / price * .75, .012, .04)
    }
    const near = .0035
    if (mode === 'LONG') { upper = price * (1 - near); lower = price * (1 - depth) } else { lower = price * (1 + near); upper = price * (1 + depth) }
    const far = mode === 'LONG' ? lower : upper
    const stopPct = analysisStop ? clamp(Math.abs(far - analysisStop) / far, .006, .03) : clamp(depth * .45, .008, .03)
    stop = far * (1 - sign * stopPct)
  }
  lower = snapTo(lower, tick); upper = snapTo(upper, tick); stop = snapTo(stop, tick, mode === 'SHORT' ? 'ceil' : 'floor')
  if (mode === 'NEUTRAL') stopUpper = snapTo(stopUpper, tick, 'ceil')

  const rangePct = (upper - lower) / price
  const slots = mode === 'NEUTRAL' ? 0 : 1                 // tek yönlü: count-1 aralık · nötr: count aralık
  let count = clamp(Math.round(rangePct / .0045) + slots, 3, maxCount)
  const spacingPct = (n: number) => rangePct / Math.max(1, n - slots) * 100
  const perLevel = (n: number) => options.margin / n * options.leverage
  while (count > GRID_MIN_COUNT && (perLevel(count) < minNotional || spacingPct(count) < GRID_MIN_SPACING_PCT + .02)) count--
  const note = atrUsed
    ? tx('Aralık, coinin son mumlardaki oynaklığından (ATR) hesaplandı.', 'The range is derived from the coin’s recent volatility (ATR).')
    : preset === 'atr' ? tx('Oynaklık için yeterli mum yok; Dar aralık önerildi.', 'Not enough candles for volatility; the Tight range was suggested.')
    : source === 'analysis'
    ? tx('Aralık, analizin hedef/stop mesafesinden türetildi.', 'The range is derived from the analysis target/stop distance.')
    : preset === 'analysis'
      ? tx('Analizde kullanılabilir seviye yok; Dar aralık önerildi.', 'No usable analysis levels; the Tight range was suggested.')
      : preset === 'tight' ? tx('Fiyatın yaklaşık ±%2 çevresi.', 'About ±2% around the price.') : tx('Fiyatın yaklaşık ±%6 çevresi.', 'About ±6% around the price.')
  return {lower, upper, count, stop, stopUpper, source, note}
}

export type GridLine = {price: number; kind: 'buy' | 'sell' | 'skip'; target: number | null}
export type GridLevel = {entry: number; target: number; side: 'LONG' | 'SHORT'; qty: number}
export type GridPreview = {
  ok: boolean; issues: string[]; warnings: string[]; lines: GridLine[]; levels: GridLevel[]
  spacing: number; spacingPct: number; activeLevels: number; levelNotional: number; totalNotional: number; marginNeeded: number
  profitPerCycle: number | null; worstLoss: number | null; stopStretchPct: number | null
}

type PreviewLimits = {maxMargin?: number; maxLeverage?: number; maxLoss?: number | null}

const empty = (issues: string[]): GridPreview => ({ok: false, issues, warnings: [], lines: [], levels: [], spacing: 0, spacingPct: 0, activeLevels: 0, levelNotional: 0, totalNotional: 0, marginNeeded: 0, profitPerCycle: null, worstLoss: null, stopStretchPct: null})

/** Sunucuyla aynı kademe yerleşimi ve zarar formülü (grid_live.py · prepare_live_grid / prepare_live_neutral_grid). */
export function previewGrid(p: GridParams, price: number | null | undefined, filters: GridFilters | null, limits: PreviewLimits = {}): GridPreview {
  const issues: string[] = [], warnings: string[] = []
  if (!finite(price)) return empty([tx('Güncel fiyat bilinmiyor.', 'The current price is unknown.')])
  const count = Math.floor(Number(p.count))
  if (![p.lower, p.upper, p.margin, p.leverage, p.stop].every(finite)) return empty([tx('Alt, üst, stop, teminat ve kaldıraç sıfırdan büyük sayı olmalı.', 'Lower, upper, stop, margin and leverage must be numbers above zero.')])
  if (p.lower >= p.upper) return empty([tx('Alt sınır, üst sınırdan küçük olmalı.', 'The lower bound must be below the upper bound.')])
  if (!(count >= GRID_MIN_COUNT && count <= GRID_MAX_COUNT)) return empty([tx(`Kademe sayısı ${GRID_MIN_COUNT}-${GRID_MAX_COUNT} arasında olmalı.`, `The level count must be ${GRID_MIN_COUNT}-${GRID_MAX_COUNT}.`)])
  const neutral = p.mode === 'NEUTRAL'
  if (neutral) {
    if (!finite(p.stopUpper)) return empty([tx('Nötr grid için üst stop gerekli.', 'Neutral grid needs an upper stop.')])
    if (!(p.lower < price && price < p.upper)) issues.push(tx('Nötr grid için güncel fiyat alt ve üst sınırın arasında olmalı.', 'For a neutral grid the price must sit between the bounds.'))
    if (!(p.stop < p.lower)) issues.push(tx('Alt stop, alt sınırın altında olmalı.', 'The lower stop must be below the lower bound.'))
    if (!(p.stopUpper > p.upper)) issues.push(tx('Üst stop, üst sınırın üstünde olmalı.', 'The upper stop must be above the upper bound.'))
  } else if (p.mode === 'LONG' && !(p.stop < p.lower)) issues.push(tx('LONG grid için stop, alt sınırın ALTINDA olmalı.', 'For a LONG grid the stop must be BELOW the lower bound.'))
  else if (p.mode === 'SHORT' && !(p.stop > p.upper)) issues.push(tx('SHORT grid için stop, üst sınırın ÜSTÜNDE olmalı.', 'For a SHORT grid the stop must be ABOVE the upper bound.'))

  const tick = filters?.tickSize, step = filters?.stepSize
  const rows: {entry: number; target: number; side: 'LONG' | 'SHORT'; qty: number}[] = []
  const lines: GridLine[] = []
  const perMargin = p.margin / count
  const qtyOf = (entry: number) => { const raw = perMargin * p.leverage / entry; return finite(step) ? snapTo(raw, step, 'floor') : raw }
  let spacing: number
  if (neutral) {
    spacing = (p.upper - p.lower) / count
    const all = Array.from({length: count + 1}, (_, k) => snapTo(p.lower + spacing * k, tick))
    if (new Set(all).size !== all.length) issues.push(tx('Yuvarlamadan sonra iki çizgi aynı fiyata düşüyor; aralığı genişlet ya da kademeyi azalt.', 'After rounding two lines share a price; widen the range or reduce the levels.'))
    const skip = all.reduce((best, line, k) => Math.abs(line - price) < Math.abs(all[best] - price) ? k : best, 0)
    all.forEach((line, k) => {
      if (k === skip) { lines.push({price: line, kind: 'skip', target: null}); return }
      const side = k < skip ? 'LONG' : 'SHORT'
      const target = side === 'LONG' ? all[k + 1] : all[k - 1]
      rows.push({entry: line, target, side, qty: qtyOf(line)})
      lines.push({price: line, kind: side === 'LONG' ? 'buy' : 'sell', target})
    })
    if (!rows.some(r => r.side === 'LONG') || !rows.some(r => r.side === 'SHORT')) issues.push(tx('Fiyat aralığın bir ucuna çok yakın; iki tarafta da kademe olmalı.', 'The price is too close to one end; both sides need levels.'))
  } else {
    spacing = (p.upper - p.lower) / (count - 1)
    for (let i = 0; i < count; i++) {
      const entry = snapTo(p.mode === 'LONG' ? p.upper - spacing * i : p.lower + spacing * i, tick)
      const target = snapTo(p.mode === 'LONG' ? entry + spacing : entry - spacing, tick)
      rows.push({entry, target, side: p.mode as 'LONG' | 'SHORT', qty: qtyOf(entry)})
      lines.push({price: entry, kind: p.mode === 'LONG' ? 'buy' : 'sell', target})
    }
    if (new Set(rows.map(r => r.entry)).size !== rows.length) issues.push(tx('Yuvarlamadan sonra iki kademe aynı fiyata düşüyor; aralığı genişlet ya da kademeyi azalt.', 'After rounding two levels share a price; widen the range or reduce the levels.'))
    const wrong = rows.filter(r => (p.mode === 'LONG' ? r.entry >= price : r.entry <= price)).length
    if (wrong) issues.push(p.mode === 'LONG'
      ? tx(`${wrong} kademe güncel fiyatın üstünde; limit alış hemen dolar. Üst sınırı fiyatın altına çek.`, `${wrong} levels are above the price; the limit buy would fill at once. Move the upper bound below the price.`)
      : tx(`${wrong} kademe güncel fiyatın altında; limit satış hemen dolar. Alt sınırı fiyatın üstüne çek.`, `${wrong} levels are below the price; the limit sell would fill at once. Move the lower bound above the price.`))
  }

  const minNotional = Math.max(GRID_MIN_NOTIONAL, filters?.minNotional ?? 0)
  const tooSmall = rows.filter(r => r.qty <= 0 || (filters && r.qty < filters.minQty) || r.qty * r.entry + 1e-9 < minNotional).length
  if (tooSmall) {
    const avgEntry = rows.length ? rows.reduce((s, r) => s + r.entry, 0) / rows.length : price
    const need = Math.max(minNotional, (filters?.stepSize ?? 0) * avgEntry) * 1.05
    const needMargin = Math.ceil(need * rows.length / p.leverage)
    issues.push(tx(`${tooSmall} kademe borsanın en küçük emir büyüklüğünün altında kalıyor. Bu coinde bir kademe yaklaşık ${Math.ceil(need)} USDT ister: kademeyi azalt ya da teminatı en az ~${needMargin} USDT (bu kaldıraçla) yap.`,
      `${tooSmall} levels fall below the smallest order size. On this coin one level needs about ${Math.ceil(need)} USDT: reduce the levels or raise the margin to at least ~${needMargin} USDT (at this leverage).`))
  }
  const feeLoss = rows.filter(r => Math.abs(r.target - r.entry) * r.qty <= (r.entry + r.target) * r.qty * GRID_MAKER_FEE + 1e-12).length
  if (feeLoss) issues.push(tx(`${feeLoss} kademenin kârı işlem ücretini karşılamıyor; aralığı genişlet ya da kademeyi azalt.`, `${feeLoss} levels earn less than the trading fee; widen the range or reduce the levels.`))

  const sideLoss = (items: typeof rows, stop: number) =>
    items.reduce((s, r) => s + r.qty * Math.abs(r.entry - stop), 0) + items.reduce((s, r) => s + r.qty * r.entry, 0) * GRID_MAKER_FEE + items.reduce((s, r) => s + r.qty * stop, 0) * GRID_TAKER_FEE
  const longs = rows.filter(r => r.side === 'LONG'), shorts = rows.filter(r => r.side === 'SHORT')
  const stopLo = neutral ? snapTo(p.stop, tick, 'floor') : p.stop, stopHi = neutral ? snapTo(p.stopUpper, tick, 'ceil') : p.stop
  const worstLoss = neutral ? Math.max(sideLoss(longs, stopLo), sideLoss(shorts, stopHi)) : sideLoss(rows, p.stop)
  const longNotional = longs.reduce((s, r) => s + r.qty * r.entry, 0), shortNotional = shorts.reduce((s, r) => s + r.qty * r.entry, 0)
  const totalNotional = longNotional + shortNotional
  const marginNeeded = totalNotional / p.leverage
  const stretch = neutral
    ? Math.max(...longs.map(r => Math.abs(r.entry - stopLo) / r.entry * 100), ...shorts.map(r => Math.abs(stopHi - r.entry) / r.entry * 100), 0)
    : Math.max(...rows.map(r => Math.abs(r.entry - p.stop) / r.entry * 100), 0)
  if (marginNeeded > p.margin + 1e-6) issues.push(tx(`Gereken teminat ${marginNeeded.toFixed(2)} USDT, seçilen teminatı aşıyor.`, `The required margin is ${marginNeeded.toFixed(2)} USDT, above the selected margin.`))
  if (limits.maxMargin && p.margin > limits.maxMargin + 1e-9) issues.push(tx(`Toplam teminat en fazla ${limits.maxMargin} USDT olabilir.`, `Total margin can be at most ${limits.maxMargin} USDT.`))
  if (limits.maxLeverage && p.leverage > limits.maxLeverage) issues.push(tx(`Kaldıraç en fazla ${limits.maxLeverage}x olabilir.`, `Leverage can be at most ${limits.maxLeverage}x.`))
  if (typeof limits.maxLoss === 'number' && limits.maxLoss > 0 && worstLoss > limits.maxLoss + 1e-9) issues.push(tx(`En kötü zarar ${worstLoss.toFixed(2)} USDT, tek işlem sınırı ${limits.maxLoss} USDT. Teminatı ya da kademeyi azalt.`, `The worst-case loss is ${worstLoss.toFixed(2)} USDT, above the ${limits.maxLoss} USDT per-trade limit. Reduce margin or levels.`))
  if (stretch * p.leverage > 70) issues.push(tx('Stop, tahmini likidasyona çok yakın ya da ötesinde; kaldıracı düşür ya da stopu yakınlaştır.', 'The stop is too close to (or beyond) the estimated liquidation; lower the leverage or tighten the stop.'))

  const spacingPct = spacing / price * 100
  if (spacingPct < GRID_MIN_SPACING_PCT) warnings.push(tx(`Kademe aralığı %${spacingPct.toFixed(2)}; çok sık. Her tur kârı küçük kalır.`, `Level spacing is ${spacingPct.toFixed(2)}%; very tight. Each cycle earns little.`))
  if (!neutral && (p.upper - p.lower) / price > .12) warnings.push(tx('Aralık geniş; fiyat uzaklaşırsa çok sayıda kademe dolabilir.', 'The range is wide; many levels can fill if the price runs away.'))
  const avgProfit = rows.length ? rows.reduce((s, r) => s + Math.abs(r.target - r.entry) * r.qty - (r.entry + r.target) * r.qty * GRID_MAKER_FEE, 0) / rows.length : null
  return {
    ok: issues.length === 0, issues, warnings, lines, levels: rows, spacing, spacingPct, activeLevels: rows.length,
    levelNotional: rows.length ? totalNotional / rows.length : 0, totalNotional, marginNeeded, profitPerCycle: avgProfit, worstLoss, stopStretchPct: stretch,
  }
}

// ---------------------------------------------------------------- sunucu çağrıları
export type GridOrder = {client_id: string; role: string; level: number; cycle?: number; side: string; type: string; price: number; stop_price: number; qty: number; filled: number; status: string}
export type GridStats = {cycles: number; realized_usdt: number; gross_usdt: number; fees_usdt: number; open_levels: {level: number; cycle: number; entry: number; qty: number}[]; filled_levels?: number}
export type GridRow = {
  grid_id: string; symbol: string; side: string; status: string; reason: string; position_qty: number; needs_user: boolean; loop_running?: boolean; recycle?: boolean
  created_at?: number; updated_at?: number; stats?: GridStats | null; orders?: GridOrder[]; alerts?: {level: string; text: string}[]
  spec?: {stop_price?: number; stop_price_upper?: number; levels?: {entry: number; target: number; side?: string}[]
    meta?: {take_profit_usdt?: number; loss_limit_usdt?: number; profit_lock_usdt?: number; lock_keep_pct?: number; close_on_target?: boolean}}
  liq?: {distance_pct: number | null; level: string; liq_price: number; mark: number} | null; peak_pnl?: number
}
export type GridStatus = {enabled?: boolean; max_levels?: number; max_margin_usdt?: number; max_leverage?: number; active_count?: number; grids?: GridRow[]; grid?: GridRow | null}
export const GRID_FINAL = ['STOPPED', 'COMPLETED', 'EMERGENCY_CLOSED']
export const gridActive = (row: GridRow) => !GRID_FINAL.includes(row.status)

export function gridStatusText(status: string): {text: string; tone: 'ok' | 'warn' | 'bad' | 'idle'} {
  switch (status) {
    case 'RUNNING': return {text: tx('Çalışıyor', 'Running'), tone: 'ok'}
    case 'CREATED': return {text: tx('Hazırlanıyor', 'Starting'), tone: 'idle'}
    case 'PAUSED': return {text: tx('Duraklatıldı', 'Paused'), tone: 'warn'}
    case 'STOPPED': return {text: tx('Bitti', 'Finished'), tone: 'idle'}
    case 'COMPLETED': return {text: tx('Tamamlandı', 'Completed'), tone: 'idle'}
    case 'EMERGENCY': return {text: tx('Acil durduruluyor', 'Emergency stop running'), tone: 'warn'}
    case 'EMERGENCY_CLOSED': return {text: tx('Acil kapatıldı', 'Emergency closed'), tone: 'idle'}
    case 'BLOCKED': return {text: tx('Durdu · müdahale gerek', 'Halted · action needed'), tone: 'bad'}
    case 'INTERVENTION_REQUIRED': return {text: tx('Müdahale gerekli', 'Action required'), tone: 'bad'}
    default: return {text: status, tone: 'idle'}
  }
}

/** Tek bir kullanıcı-arayüzü kalıbı: tüm grid uçları (canlı ve demo) aynı gövdeyi döndürür. */
export async function requestGrid<T>(url: string, method: 'GET' | 'POST', body?: unknown, timeoutMs = 30000, fetcher: typeof fetch = fetch): Promise<CallResult<T>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetcher(url, {method, headers: body === undefined ? undefined : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal})
    let payload: unknown = null
    try { payload = await response.json() } catch { payload = null }
    if (response.ok) return {ok: true, data: payload as T}
    return {ok: false, status: response.status, uncertain: response.status >= 500 && response.status !== 503, message: errorText(payload, tx(`İstek reddedildi (HTTP ${response.status}).`, `Request rejected (HTTP ${response.status}).`))}
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError'
    return {ok: false, status: 0, uncertain: method === 'POST', message: aborted ? tx('Sunucu zamanında yanıt vermedi.', 'The server did not respond in time.') : tx('Sunucuya ulaşılamadı.', 'Could not reach the server.')}
  } finally { clearTimeout(timer) }
}

export function startBody(symbol: string, p: GridParams, demo: boolean) {
  const base = {symbol, direction: p.mode, lower: p.lower, upper: p.upper, count: p.count, margin_usdt: p.margin, leverage: p.leverage, stop: p.stop, recycle: p.mode === 'NEUTRAL' ? true : p.recycle,
    confirmation: demo ? GRID_DEMO_PHRASE : GRID_LIVE_PHRASE}
  const withStop = p.mode === 'NEUTRAL' ? {...base, stop_upper: p.stopUpper} : base
  return {...withStop, ...(p.profitLock && p.profitLock > 0 ? {profit_lock_usdt: p.profitLock} : {}), ...(p.closeOnTarget && p.takeProfit && p.takeProfit > 0 ? {close_on_target: true} : {}), ...(p.takeProfit && p.takeProfit > 0 ? {take_profit_usdt: p.takeProfit} : {}), ...(p.lossLimit && p.lossLimit > 0 ? {loss_limit_usdt: p.lossLimit} : {})}
}

/** Sunucuda yalnız plan hesaplatır (emir yok): panelde "sunucu onayladı" göstermek için. */
export function previewBody(symbol: string, p: GridParams, demo: boolean) {
  return {...startBody(symbol, p, demo), preview: true}
}

/** Canlı kâr/zarar tahmini: gerçekleşen tur kârı + açık kademelerin anlık değeri. */
export function gridPnl(row: GridRow, price: number | null | undefined): {realized: number; unrealized: number | null; total: number | null; cycles: number} {
  const realized = row.stats?.realized_usdt ?? 0
  const cycles = row.stats?.cycles ?? 0
  const open = row.stats?.open_levels ?? []
  if (!open.length) return {realized, unrealized: 0, total: realized, cycles}
  if (!finite(price)) return {realized, unrealized: null, total: null, cycles}
  const unrealized = open.reduce((s, l) => {
    const sideOf = (l as {side?: string}).side ?? row.side
    return s + (sideOf === 'SHORT' ? (l.entry - price) : (price - l.entry)) * l.qty
  }, 0)
  return {realized, unrealized, total: realized + unrealized, cycles}
}

// ---------------------------------------------------------------- mumlardan türetilen hesaplar
export type Candle = {time: number; open: number; high: number; low: number; close: number; volume?: number}

/** Mum süresi (saniye): son mumların zaman farklarının ortancası. Milisaniye zamanı da tanır. */
export function barSeconds(candles: readonly Candle[]): number | null {
  const tail = candles.slice(-21)
  const diffs: number[] = []
  for (let i = 1; i < tail.length; i++) { const d = tail[i].time - tail[i - 1].time; if (d > 0 && Number.isFinite(d)) diffs.push(d) }
  if (diffs.length < 2) return null
  diffs.sort((a, b) => a - b)
  const median = diffs[Math.floor(diffs.length / 2)]
  return median > 100000 ? median / 1000 : median
}

/** Ortalama gerçek aralık (ATR) fiyatın yüzdesi olarak. Yeterli mum yoksa null. */
export function atrPct(candles: readonly Candle[], period = 14): number | null {
  if (candles.length < period + 1) return null
  const tail = candles.slice(-(period + 1))
  let sum = 0
  for (let i = 1; i < tail.length; i++) {
    const c = tail[i], prev = tail[i - 1].close
    sum += Math.max(c.high - c.low, Math.abs(c.high - prev), Math.abs(c.low - prev))
  }
  const last = tail[tail.length - 1].close
  return finite(last) && sum > 0 ? sum / period / last * 100 : null
}

export type Backtest = {
  candles: number; hours: number; cycles: number; net: number; fees: number; maxDrawdown: number; stopped: boolean; stoppedAt: number | null
  openLevels: number; inRangePct: number; cyclesPerDay: number
}

/**
 * Aynı şekildeki gridin (aynı genişlik, kademe sayısı ve stoplar) geçmiş mumlarda nasıl çalışacağını tahmin eder.
 * Seviyeler pencerenin ilk fiyatına göre ölçeklenir; böylece "şimdiki gridi o günlerde kursaydım" sorusu cevaplanır.
 * Mum içi sıra bilinmediği için kötümser varsayım: yükselen mumda önce düşük, düşen mumda önce yüksek görülür.
 * Ücret (maker/taker) dahildir; funding, kayma ve kısmi dolum dahil DEĞİLDİR.
 */
export function backtestGrid(p: GridParams, preview: GridPreview, nowPrice: number, candles: readonly Candle[]): Backtest | null {
  if (!preview.ok || !preview.levels.length || !finite(nowPrice) || candles.length < 8) return null
  const first = candles[0].open
  if (!finite(first)) return null
  const k = first / nowPrice
  const neutral = p.mode === 'NEUTRAL'
  const stopLo = (neutral || p.mode === 'LONG') ? p.stop * k : 0
  const stopHi = neutral ? p.stopUpper * k : p.mode === 'SHORT' ? p.stop * k : Infinity
  type L = {entry: number; target: number; sign: 1 | -1; qty: number; open: boolean}
  const levels: L[] = preview.levels.map(r => ({entry: r.entry * k, target: r.target * k, sign: r.side === 'LONG' ? 1 : -1, qty: r.qty, open: false}))
  let realized = 0, fees = 0, cycles = 0, peak = 0, maxDd = 0, stopped = false, stoppedAt: number | null = null, inRange = 0
  const lower = p.lower * k, upper = p.upper * k
  const equityAt = (price: number) => realized + levels.reduce((s, l) => l.open ? s + l.sign * (price - l.entry) * l.qty : s, 0)
  const mark = (price: number) => { const e = equityAt(price); if (e > peak) peak = e; if (peak - e > maxDd) maxDd = peak - e }
  type Ev = {price: number; kind: 'entry' | 'target' | 'stop'; level?: L}
  const segment = (a: number, b: number) => {
    if (a === b || stopped) return
    const down = b < a, lo = Math.min(a, b), hi = Math.max(a, b)
    const hit = (price: number) => price >= lo && price <= hi
    const events: Ev[] = []
    for (const l of levels) {
      if (!l.open && hit(l.entry) && (l.sign === 1 ? down : !down)) events.push({price: l.entry, kind: 'entry', level: l})
      if (l.open && hit(l.target) && (l.sign === 1 ? !down : down)) events.push({price: l.target, kind: 'target', level: l})
    }
    if (down && hit(stopLo) && levels.some(l => l.open && l.sign === 1)) events.push({price: stopLo, kind: 'stop'})
    if (!down && hit(stopHi) && levels.some(l => l.open && l.sign === -1)) events.push({price: stopHi, kind: 'stop'})
    events.sort((x, y) => down ? y.price - x.price : x.price - y.price)
    for (const ev of events) {
      if (ev.kind === 'entry' && ev.level) { ev.level.open = true; mark(ev.price) }
      else if (ev.kind === 'target' && ev.level && ev.level.open) {
        const l = ev.level
        realized += l.sign * (l.target - l.entry) * l.qty - (l.entry + l.target) * l.qty * GRID_MAKER_FEE
        fees += (l.entry + l.target) * l.qty * GRID_MAKER_FEE
        l.open = false; cycles++; mark(ev.price)
      } else if (ev.kind === 'stop') {
        const side = down ? 1 : -1
        for (const l of levels) {
          if (!l.open || l.sign !== side) continue
          const fee = l.entry * l.qty * GRID_MAKER_FEE + ev.price * l.qty * GRID_TAKER_FEE
          realized += l.sign * (ev.price - l.entry) * l.qty - fee; fees += fee; l.open = false
        }
        mark(ev.price)
        if (!levels.some(l => l.open)) { stopped = true; return }
      }
    }
    mark(b)
  }
  for (let i = 0; i < candles.length && !stopped; i++) {
    const c = candles[i]
    const path = c.close >= c.open ? [c.open, c.low, c.high, c.close] : [c.open, c.high, c.low, c.close]
    for (let j = 1; j < path.length && !stopped; j++) segment(path[j - 1], path[j])
    if (stopped) { stoppedAt = i; break }
    if (c.close >= lower && c.close <= upper) inRange++
  }
  const seen = stopped && stoppedAt !== null ? stoppedAt + 1 : candles.length
  const bar = barSeconds(candles) ?? 0
  const hours = bar > 0 ? seen * bar / 3600 : 0
  const last = candles[Math.min(seen, candles.length) - 1]?.close ?? first
  const net = equityAt(last)
  return {
    candles: seen, hours, cycles, net, fees, maxDrawdown: maxDd, stopped, stoppedAt, openLevels: levels.filter(l => l.open).length,
    inRangePct: seen > 0 ? inRange / seen * 100 : 0, cyclesPerDay: hours > 0 ? cycles / (hours / 24) : 0,
  }
}

/** Fiyat grid aralığının dışında mı? Çalışan gridin kademe ve hedeflerinden bulunur; kısa bir tampon (%0,1) tanınır. */
export function gridRangeStatus(row: GridRow, price: number | null | undefined): {state: 'inside' | 'above' | 'below' | 'unknown'; pct: number} {
  const levels = row.spec?.levels ?? []
  if (!finite(price) || !levels.length) return {state: 'unknown', pct: 0}
  const all = levels.flatMap(l => [l.entry, l.target]).filter(finite)
  if (!all.length) return {state: 'unknown', pct: 0}
  const lo = Math.min(...all), hi = Math.max(...all)
  if (price > hi * 1.001) return {state: 'above', pct: (price - hi) / hi * 100}
  if (price < lo * .999) return {state: 'below', pct: (lo - price) / lo * 100}
  return {state: 'inside', pct: 0}
}

/** Grid geçmişi için tek satırlık özet. */
export function gridSummary(row: GridRow): {hours: number | null; cycles: number; net: number; fees: number} {
  const created = Number(row.created_at), updated = Number(row.updated_at)
  const hours = created > 0 && updated >= created ? (updated - created) / 3600 : null
  return {hours, cycles: row.stats?.cycles ?? 0, net: row.stats?.realized_usdt ?? 0, fees: row.stats?.fees_usdt ?? 0}
}

/** held: dolmuş (pozisyon tutan) kademe · liq: likidasyon fiyatı (eksen aralığını genişletmez; yalnız görünürse çizilir). */
export type GridChartLine = {price: number; kind: 'buy' | 'sell' | 'skip' | 'bound' | 'stop' | 'entry' | 'target' | 'held' | 'liq'; label?: string; id?: string; draggable?: boolean; noRange?: boolean}
/** Grafiğe bağlanan etkileşimler: fiyat seçme (limit emir) ve sürüklenebilir çizgiyi bırakma (stop taşıma). */
export type ChartTools = {pick?: {onPick: (price: number) => void}; onLineDrop?: (id: string, price: number) => void}

/** Grafik üzerinde çizilecek çizgiler: önizleme (panel açıkken) ya da çalışan gridlerin kademeleri ve stopları. */
export function chartLinesFor(preview: GridPreview | null, p: GridParams | null): GridChartLine[] {
  if (!preview || !p || preview.lines.length === 0) return []
  const out: GridChartLine[] = preview.lines.map(l => ({price: l.price, kind: l.kind}))
  out.push({price: p.upper, kind: 'bound', label: `${tx('Üst', 'Top')} ${priceText(p.upper)}`}, {price: p.lower, kind: 'bound', label: `${tx('Alt', 'Bottom')} ${priceText(p.lower)}`},
    {price: p.stop, kind: 'stop', label: `${p.mode === 'NEUTRAL' ? tx('Alt stop', 'Lower stop') : 'Stop'} ${priceText(p.stop)}`})
  if (p.mode === 'NEUTRAL' && finite(p.stopUpper)) out.push({price: p.stopUpper, kind: 'stop', label: `${tx('Üst stop', 'Upper stop')} ${priceText(p.stopUpper)}`})
  return out
}

export function chartLinesForRows(rows: readonly GridRow[]): GridChartLine[] {
  const out: GridChartLine[] = []
  for (const row of rows) {
    if (!gridActive(row)) continue
    // Dolmuş (pozisyon tutan) kademeler düz ve parlak, bekleyenler ince kesikli; dolmuş kademenin hedefi de ayrıca çizilir.
    const held = new Set((row.stats?.open_levels ?? []).map(l => l.level))
    const levels = row.spec?.levels ?? []
    levels.forEach((l, index) => {
      if (!finite(l.entry)) return
      const sell = l.side === 'SHORT' || (!l.side && row.side === 'SHORT')
      if (held.has(index)) {
        out.push({price: l.entry, kind: 'held'})
        if (finite(l.target)) out.push({price: l.target, kind: 'target'})
      } else out.push({price: l.entry, kind: sell ? 'sell' : 'buy'})
    })
    const lo = row.spec?.stop_price, hi = row.spec?.stop_price_upper
    if (finite(lo)) out.push({price: lo, kind: 'stop', label: `Stop ${priceText(lo)}`})
    if (finite(hi)) out.push({price: hi, kind: 'stop', label: `Stop ${priceText(hi)}`})
    const entries = levels.map(l => l.entry).filter(finite)
    if (entries.length) {
      out.push({price: Math.max(...entries), kind: 'bound', label: `${tx('Üst', 'Top')} ${priceText(Math.max(...entries))}`}, {price: Math.min(...entries), kind: 'bound', label: `${tx('Alt', 'Bottom')} ${priceText(Math.min(...entries))}`})
    }
    const liq = row.liq
    if (liq && finite(liq.liq_price) && liq.liq_price > 0) {
      out.push({price: liq.liq_price, kind: 'liq', noRange: true, label: `${tx('Likidasyon', 'Liquidation')} ${priceText(liq.liq_price)}${liq.distance_pct !== null && finite(liq.distance_pct) ? ` · %${liq.distance_pct.toFixed(1)}` : ''}`})
    }
  }
  return out
}

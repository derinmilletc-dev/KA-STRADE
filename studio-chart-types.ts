// Grafik türleri: mum, Heikin-Ashi, çizgi, alan, Renko. Hepsi aynı mum dizisinden saf hesapla üretilir (React yok).
// Heikin-Ashi ve Renko, mum dizisini başka bir mum dizisine çevirir; çizgi/alan yalnız çizim biçimini değiştirir.
export type ChartCandle = {time: number; open: number; high: number; low: number; close: number; volume: number}
export const CHART_TYPES = [
  {id: 'candles', label: 'Mum', hint: 'Klasik mum grafiği'},
  {id: 'heikin', label: 'Heikin-Ashi', hint: 'Gürültüyü azaltan yumuşatılmış mumlar'},
  {id: 'line', label: 'Çizgi', hint: 'Kapanış fiyatlarını birleştiren çizgi'},
  {id: 'area', label: 'Alan', hint: 'Kapanış çizgisi ve altı dolgulu'},
  {id: 'renko', label: 'Renko', hint: 'Zamandan bağımsız, sabit boyutlu tuğlalar (ATR tabanlı)'},
] as const
export type ChartType = typeof CHART_TYPES[number]['id']
const IDS = new Set<string>(CHART_TYPES.map(item => item.id))
export const sanitizeChartType = (value: unknown): ChartType => typeof value === 'string' && IDS.has(value) ? value as ChartType : 'candles'

const finite = (candle: ChartCandle) => [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)

export function heikinAshi(candles: readonly ChartCandle[]): ChartCandle[] {
  const out: ChartCandle[] = []
  for (const candle of candles) {
    if (!finite(candle)) continue
    const previous = out[out.length - 1]
    const close = (candle.open + candle.high + candle.low + candle.close) / 4
    const open = previous ? (previous.open + previous.close) / 2 : (candle.open + candle.close) / 2
    out.push({time: candle.time, open, close, high: Math.max(candle.high, open, close), low: Math.min(candle.low, open, close), volume: candle.volume})
  }
  return out
}

/** Wilder ATR'nin son değeri; yeterli veri yoksa null. */
export function lastAtr(candles: readonly ChartCandle[], period = 14): number | null {
  if (candles.length < period + 1) return null
  let atr = 0
  for (let i = 1; i <= period; i++) atr += trueRange(candles[i], candles[i - 1])
  atr /= period
  for (let i = period + 1; i < candles.length; i++) atr = (atr * (period - 1) + trueRange(candles[i], candles[i - 1])) / period
  return Number.isFinite(atr) && atr > 0 ? atr : null
}
const trueRange = (c: ChartCandle, p: ChartCandle) => Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close))

/** Tuğla boyutunu okunur bir sayıya yuvarlar (1, 2, 2.5, 5 × 10^n). */
export function niceBrick(value: number): number {
  if (!(value > 0) || !Number.isFinite(value)) return 0
  const power = Math.pow(10, Math.floor(Math.log10(value)))
  const unit = value / power
  const step = unit < 1.5 ? 1 : unit < 2.25 ? 2 : unit < 3.75 ? 2.5 : unit < 7.5 ? 5 : 10
  return step * power
}

const MAX_BRICKS = 400
/** Renko: kapanış, son tuğlanın ucundan bir tuğla boyu uzaklaşınca yeni tuğla; ters yönde iki tuğla gerekir. Zaman = tuğlayı tamamlayan mumun zamanı. */
export function renko(candles: readonly ChartCandle[], brick?: number): ChartCandle[] {
  const clean = candles.filter(finite)
  if (clean.length < 2) return []
  const size = brick && brick > 0 ? brick : niceBrick(lastAtr(clean) ?? 0)
  if (!(size > 0)) return []
  let base = Math.round(clean[0].close / size) * size
  let dir = 0
  const out: ChartCandle[] = []
  let volume = 0
  for (const candle of clean) {
    volume += candle.volume
    const move = (from: number, to: number) => {
      out.push({time: candle.time, open: from, close: to, high: Math.max(from, to), low: Math.min(from, to), volume})
      volume = 0
    }
    let guard = 0
    while (guard++ < MAX_BRICKS) {
      const up = candle.close - base
      if ((dir >= 0 && up >= size) || (dir < 0 && up >= size * 2)) {
        const from = dir < 0 ? base + size : base
        move(from, from + size); base = from + size; dir = 1
      } else if ((dir <= 0 && -up >= size) || (dir > 0 && -up >= size * 2)) {
        const from = dir > 0 ? base - size : base
        move(from, from - size); base = from - size; dir = -1
      } else break
    }
  }
  return out.slice(-MAX_BRICKS)
}

export function convertCandles<T extends ChartCandle>(candles: readonly T[], type: ChartType): ChartCandle[] | readonly T[] {
  if (type === 'heikin') return heikinAshi(candles)
  if (type === 'renko') return renko(candles)
  return candles
}

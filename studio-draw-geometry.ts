// Çizim araçlarının saf geometrisi/hesapları (React yok): fibonacci seviyeleri, kanal, ölçüm, dikdörtgen.
import type {ChartPoint} from './chart-drawings'

export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618] as const
export type FibLevel = {ratio: number; price: number}

/** A→B hareketinin geri çekilme seviyeleri: %0 = B (hareketin ucu), %100 = A (başlangıç). 1,272 ve 1,618 uzantıdır. */
export function fibLevels(a: ChartPoint, b: ChartPoint): FibLevel[] {
  return FIB_RATIOS.map(ratio => ({ratio, price: b.price - (b.price - a.price) * ratio}))
}
export const fibLabel = (ratio: number) => `${(ratio * 100).toLocaleString('en-US', {maximumFractionDigits: 1})}%`

export type Rect = {t1: number; t2: number; high: number; low: number}
export function rectBounds(a: ChartPoint, b: ChartPoint): Rect {
  return {t1: Math.min(a.time, b.time), t2: Math.max(a.time, b.time), high: Math.max(a.price, b.price), low: Math.min(a.price, b.price)}
}

/** Doğrunun verilen zamandaki fiyatı. Dikey doğruda (a.time=b.time) a'nın fiyatı. */
export function priceAt(a: ChartPoint, b: ChartPoint, time: number): number {
  if (b.time === a.time) return a.price
  return a.price + (b.price - a.price) * (time - a.time) / (b.time - a.time)
}

/** Paralel kanal: A–B ana doğru, C üçüncü nokta ikinci (paralel) doğrunun geçtiği yeri verir. */
export function channelOffset(a: ChartPoint, b: ChartPoint, c: ChartPoint): number {
  return c.price - priceAt(a, b, c.time)
}
export function channelLines(a: ChartPoint, b: ChartPoint, c: ChartPoint) {
  const offset = channelOffset(a, b, c)
  return {
    main: {a, b},
    parallel: {a: {time: a.time, price: a.price + offset}, b: {time: b.time, price: b.price + offset}},
    middle: {a: {time: a.time, price: a.price + offset / 2}, b: {time: b.time, price: b.price + offset / 2}},
    offset,
  }
}

export type Measure = {delta: number; pct: number | null; bars: number; seconds: number; up: boolean}
/** Ölçüm aracı: fiyat farkı, yüzde, mum sayısı ve süre (A→B). */
export function measure(a: ChartPoint, b: ChartPoint, stepSeconds: number): Measure {
  const delta = b.price - a.price
  const unit = (time: number) => time < 1e12 ? time : time / 1000
  const seconds = unit(b.time) - unit(a.time)
  const step = stepSeconds > 0 ? stepSeconds : 60
  return {delta, pct: a.price !== 0 ? delta / a.price * 100 : null, bars: Math.round(seconds / step), seconds, up: delta >= 0}
}
export function durationText(totalSeconds: number): string {
  const s = Math.abs(Math.round(totalSeconds))
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60)
  if (d > 0) return `${d}g ${h}sa`
  if (h > 0) return `${h}sa ${m}dk`
  return `${Math.max(m, s > 0 ? 1 : 0)}dk`
}

// ---- Çizim akışı (tıklama durum makinesi): React'siz, test edilebilir. ----
import type {DrawMode, Drawing} from './chart-drawings'

export type DraftStep = {anchors: ChartPoint[]; drawing: Drawing | null; askText: ChartPoint | null}
const same = (a: ChartPoint, b: ChartPoint) => a.time === b.time && a.price === b.price
const NEEDED: Partial<Record<DrawMode, number>> = {trend: 2, fib: 2, rect: 2, measure: 2, channel: 3}

/** Bir tıklamayı işler: yeterli nokta toplanınca çizimi döndürür; metin aracında yazı kutusu istenir. */
export function advanceDraft(mode: DrawMode, anchors: readonly ChartPoint[], point: ChartPoint, newId: () => string): DraftStep {
  if (mode === 'text') return {anchors: [], drawing: null, askText: point}
  const needed = NEEDED[mode]
  if (!needed) return {anchors: [], drawing: null, askText: null}
  if (anchors.length > 0 && same(anchors[anchors.length - 1], point)) return {anchors: [...anchors], drawing: null, askText: null}
  const next = [...anchors, point]
  if (next.length < needed) return {anchors: next, drawing: null, askText: null}
  const [a, b, c] = next
  const id = newId()
  switch (mode) {
    case 'trend': return {anchors: [], drawing: {id, type: 'trend', a, b}, askText: null}
    case 'fib': return {anchors: [], drawing: {id, type: 'fib', a, b}, askText: null}
    case 'rect': return {anchors: [], drawing: {id, type: 'rect', a, b}, askText: null}
    case 'measure': return {anchors: [], drawing: {id, type: 'measure', a, b}, askText: null}
    case 'channel': return {anchors: [], drawing: {id, type: 'channel', a, b, c}, askText: null}
    default: return {anchors: [], drawing: null, askText: null}
  }
}

/** Fare imleci altındaki geçici önizleme (taslak). Kanalda 2. noktadan sonra imleç, paralel doğrunun yerini belirler. */
export function draftPreview(mode: DrawMode, anchors: readonly ChartPoint[], hover: ChartPoint | null): Drawing | null {
  if (!hover || anchors.length === 0) return null
  const [a, b] = anchors
  const id = 'draft'
  switch (mode) {
    case 'trend': return {id, type: 'trend', a, b: hover}
    case 'fib': return {id, type: 'fib', a, b: hover}
    case 'rect': return {id, type: 'rect', a, b: hover}
    case 'measure': return {id, type: 'measure', a, b: hover}
    case 'channel': return anchors.length === 1 ? {id, type: 'trend', a, b: hover} : {id, type: 'channel', a, b, c: hover}
    default: return null
  }
}

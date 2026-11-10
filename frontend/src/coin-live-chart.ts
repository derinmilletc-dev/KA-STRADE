// Manuel işlem ekranındaki canlı mini grafik için saf hesap (ekrandan bağımsız, test edilebilir). Emir/hesap verisi yoktur.
export type ChartCandle = {time: number; close: number}
export type ChartPoint = {x: number; y: number; value: number; time: number}
export type ChartShape = {
  points: ChartPoint[]; line: string; area: string; first: number; last: number; min: number; max: number
  lastX: number; lastY: number; changePct: number; tone: 'up' | 'down'
}

/** Noktalardan yumuşak (monoton kübik) eğri: iki nokta arasında fiyatın gitmediği yere taşmaz, tepe/dip olduğu gibi kalır. */
export function smoothPath(points: ReadonlyArray<{x: number; y: number}>): string {
  const n = points.length
  if (n === 0) return ''
  const f = (value: number) => Number(value.toFixed(2))
  if (n === 1) return `M${f(points[0].x)} ${f(points[0].y)}`
  const dx: number[] = [], slope: number[] = []
  for (let i = 0; i < n - 1; i += 1) {
    dx.push(points[i + 1].x - points[i].x)
    slope.push(dx[i] === 0 ? 0 : (points[i + 1].y - points[i].y) / dx[i])
  }
  const tangent: number[] = new Array(n).fill(0)
  tangent[0] = slope[0]
  tangent[n - 1] = slope[n - 2]
  for (let i = 1; i < n - 1; i += 1) tangent[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2
  for (let i = 0; i < n - 1; i += 1) {
    if (slope[i] === 0) { tangent[i] = 0; tangent[i + 1] = 0; continue }
    const a = tangent[i] / slope[i], b = tangent[i + 1] / slope[i]
    const s = a * a + b * b
    if (s > 9) { const tau = 3 / Math.sqrt(s); tangent[i] = tau * a * slope[i]; tangent[i + 1] = tau * b * slope[i] }
  }
  let d = `M${f(points[0].x)} ${f(points[0].y)}`
  for (let i = 0; i < n - 1; i += 1) {
    const step = dx[i] / 3
    d += ` C${f(points[i].x + step)} ${f(points[i].y + tangent[i] * step)} ${f(points[i + 1].x - step)} ${f(points[i + 1].y - tangent[i + 1] * step)} ${f(points[i + 1].x)} ${f(points[i + 1].y)}`
  }
  return d
}

/** Kapanışlardan SVG çizgi ve alan yolu üretir; en az 2 geçerli mum yoksa null. Y ekseni ters çevrilir (yüksek fiyat yukarıda). */
export function buildChart(candles: ReadonlyArray<ChartCandle> | null | undefined, width: number, height: number, pad = 4): ChartShape | null {
  const rows = (candles ?? []).map(candle => ({value: Number(candle.close), time: Number(candle.time)})).filter(row => Number.isFinite(row.value) && row.value > 0)
  if (rows.length < 2 || !(width > 0) || !(height > 0)) return null
  const values = rows.map(row => row.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const innerW = width - pad * 2
  const innerH = height - pad * 2
  const points: ChartPoint[] = rows.map((row, index) => ({
    x: Number((pad + innerW * (index / (rows.length - 1))).toFixed(2)),
    y: Number((span === 0 ? height / 2 : pad + innerH * (1 - (row.value - min) / span)).toFixed(2)),
    value: row.value, time: row.time,
  }))
  const line = smoothPath(points)
  const lastPoint = points[points.length - 1]
  const area = `${line} L${lastPoint.x} ${height} L${points[0].x} ${height} Z`
  const first = values[0]
  const last = values[values.length - 1]
  return {points, line, area, first, last, min, max, lastX: lastPoint.x, lastY: lastPoint.y, changePct: (last - first) / first * 100, tone: last >= first ? 'up' : 'down'}
}

/** Parmak/imleç yatay konumundan (0–1) en yakın nokta; sınır dışı değerler kenara sabitlenir. */
export function nearestIndex(ratio: number, count: number): number {
  if (!(count > 0) || !Number.isFinite(ratio)) return 0
  return Math.min(count - 1, Math.max(0, Math.round(ratio * (count - 1))))
}

export const INTERVAL_LABEL = {'1m': '1 dk', '5m': '5 dk', '15m': '15 dk'} as const
export type ChartInterval = keyof typeof INTERVAL_LABEL

/** Değişim yüzdesi: "+0,42%" / "−1,10%" (Türkçe ondalık). */
export function changeText(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const text = Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return `${value < 0 ? '−' : '+'}${text}%`
}

/** Mum zamanı (saniye) → "14:07". */
export function clockText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  return new Date(seconds * 1000).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})
}

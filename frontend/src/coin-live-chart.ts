// Manuel işlem ekranındaki canlı mini grafik için saf hesap (ekrandan bağımsız, test edilebilir). Emir/hesap verisi yoktur.
export type ChartCandle = {time: number; close: number}
export type ChartShape = {line: string; area: string; first: number; last: number; min: number; max: number; lastX: number; lastY: number; changePct: number; tone: 'up' | 'down'}

/** Kapanışlardan SVG çizgi ve alan yolu üretir; en az 2 geçerli mum yoksa null. Y ekseni ters çevrilir (yüksek fiyat yukarıda). */
export function buildChart(candles: ReadonlyArray<ChartCandle> | null | undefined, width: number, height: number, pad = 4): ChartShape | null {
  const closes = (candles ?? []).map(candle => Number(candle.close)).filter(value => Number.isFinite(value) && value > 0)
  if (closes.length < 2 || !(width > 0) || !(height > 0)) return null
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min
  const innerW = width - pad * 2
  const innerH = height - pad * 2
  const points = closes.map((value, index) => {
    const x = pad + innerW * (index / (closes.length - 1))
    const y = span === 0 ? height / 2 : pad + innerH * (1 - (value - min) / span)
    return [Number(x.toFixed(2)), Number(y.toFixed(2))] as const
  })
  const line = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x} ${y}`).join(' ')
  const [lastX, lastY] = points[points.length - 1]
  const area = `${line} L${lastX} ${height - pad} L${points[0][0]} ${height - pad} Z`
  const first = closes[0]
  const last = closes[closes.length - 1]
  return {line, area, first, last, min, max, lastX, lastY, changePct: (last - first) / first * 100, tone: last >= first ? 'up' : 'down'}
}

export const INTERVAL_LABEL = {'1m': '1 dk', '5m': '5 dk', '15m': '15 dk'} as const
export type ChartInterval = keyof typeof INTERVAL_LABEL

/** Değişim yüzdesi: "+0,42%" / "−1,10%" (Türkçe ondalık). */
export function changeText(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const text = Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return `${value < 0 ? '−' : '+'}${text}%`
}

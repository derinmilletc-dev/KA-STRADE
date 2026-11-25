// Sembol karşılaştırma: ikinci sembol, görünen pencerenin ilk ortak mumunda ana grafiğin fiyatına eşitlenir (yüzde değişim aynı ölçekte).
export type CompareCandle = {time: number; close: number}
/** Ana grafikteki her mum için karşılaştırma değeri (ana fiyat biriminde); eşleşen zaman yoksa null. */
export function rebaseCompare(main: ReadonlyArray<{time: number; close: number}>, other: readonly CompareCandle[]): Array<number | null> {
  const byTime = new Map<number, number>()
  for (const c of other) if (Number.isFinite(c.close) && c.close > 0) byTime.set(c.time, c.close)
  let baseMain: number | null = null, baseOther: number | null = null
  for (const c of main) {
    const o = byTime.get(c.time)
    if (o !== undefined && Number.isFinite(c.close) && c.close > 0) { baseMain = c.close; baseOther = o; break }
  }
  if (baseMain === null || baseOther === null) return main.map(() => null)
  return main.map(c => { const o = byTime.get(c.time); return o === undefined ? null : baseMain * (o / baseOther) })
}
export const extentOf = (values: ReadonlyArray<number | null>): {low: number; high: number} | null => {
  const list = values.filter((v): v is number => v !== null && Number.isFinite(v))
  return list.length ? {low: Math.min(...list), high: Math.max(...list)} : null
}
export const mergeExtents = (a: {low: number; high: number} | null, b: {low: number; high: number} | null) => a && b ? {low: Math.min(a.low, b.low), high: Math.max(a.high, b.high)} : a ?? b
/** Getirilerin korelasyonu (-1..1); ortak mum azsa null. */
export function returnCorrelation(main: readonly CompareCandle[], other: readonly CompareCandle[]): number | null {
  const byTime = new Map(other.map(c => [c.time, c.close] as const))
  const a: number[] = [], b: number[] = []
  let pm: number | null = null, po: number | null = null
  for (const c of main) {
    const o = byTime.get(c.time)
    if (o === undefined) { pm = null; po = null; continue }
    if (pm !== null && po !== null && pm > 0 && po > 0) { a.push(c.close / pm - 1); b.push(o / po - 1) }
    pm = c.close; po = o
  }
  if (a.length < 20) return null
  const mean = (x: number[]) => x.reduce((s, v) => s + v, 0) / x.length
  const ma = mean(a), mb = mean(b)
  let cov = 0, va = 0, vb = 0
  for (let i = 0; i < a.length; i++) { cov += (a[i] - ma) * (b[i] - mb); va += (a[i] - ma) ** 2; vb += (b[i] - mb) ** 2 }
  return va > 0 && vb > 0 ? cov / Math.sqrt(va * vb) : null
}

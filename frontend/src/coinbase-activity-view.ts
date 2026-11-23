// "Son olaylar" için SAF gruplama: olaylar güne göre bölünür (en yeni önce), her gün için olay sayısı ve bilinen net K/Z.
export type ActivityRow = {time: string; pnl: string | null}
export type DayGroup<T extends ActivityRow> = {key: string; label: string; items: T[]; pnl: number | null}

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`

export function dayLabelOf(iso: string, now: Date = new Date()): string {
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return ''
  if (dayKey(d) === dayKey(now)) return 'Bugün'
  const y = new Date(now); y.setDate(now.getDate() - 1)
  return dayKey(d) === dayKey(y) ? 'Dün' : d.toLocaleDateString('tr-TR', {day: 'numeric', month: 'long'})
}

/** Olayları güne göre grupla. Gün içi sıra korunur; geçersiz tarihli olaylar atlanır. pnl: o günün bilinen kapanış K/Z toplamı (hiç yoksa null). */
export function groupByDay<T extends ActivityRow>(items: readonly T[], now: Date = new Date()): DayGroup<T>[] {
  const map = new Map<string, DayGroup<T>>()
  for (const item of items) {
    const d = new Date(item.time)
    if (!Number.isFinite(d.getTime())) continue
    const key = dayKey(d)
    let group = map.get(key)
    if (!group) { group = {key, label: dayLabelOf(item.time, now), items: [], pnl: null}; map.set(key, group) }
    group.items.push(item)
    const pnl = item.pnl === null || item.pnl === '' ? NaN : Number(item.pnl)
    if (Number.isFinite(pnl)) group.pnl = (group.pnl ?? 0) + pnl
  }
  return [...map.values()]
}

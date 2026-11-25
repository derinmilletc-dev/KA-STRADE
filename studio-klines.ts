// İkinci grafik için mum verisini doğrular. Bozuk satır varsa tümünü reddeder (yarım veriyle grafik çizilmez).
export type Kline = {time: number; open: number; high: number; low: number; close: number; volume: number}
export function parseKlines(value: unknown): Kline[] | null {
  if (!Array.isArray(value) || value.length < 2) return null
  const out: Kline[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object') return null
    const {time, open, high, low, close, volume} = row as Record<string, unknown>
    const numbers = [time, open, high, low, close, volume]
    if (!numbers.every(item => typeof item === 'number' && Number.isFinite(item))) return null
    const [t, o, h, l, c, v] = numbers as number[]
    if (h < l || h < Math.max(o, c) - 1e-9 || l > Math.min(o, c) + 1e-9) return null
    out.push({time: t, open: o, high: h, low: l, close: c, volume: v})
  }
  return out
}

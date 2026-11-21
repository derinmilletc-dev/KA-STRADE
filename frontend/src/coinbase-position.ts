// Coinbase açık pozisyon kartı için SAF hesaplar: yön, fiyat şeridi konumları, "stopu girişe çek" ve "yarısını kapat" uygunluğu.
// Ağ ve emir yok. Stop girişe çekilirken komisyon payı eklenir (açılış + kapanış), böylece stop çalışsa bile yaklaşık başa baş kapanır.

export type PosDir = 'LONG' | 'SHORT'
export const BREAKEVEN_FEE_PCT = 0.2

const pos = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null }

export type PositionView = {
  dir: PosDir | null
  contracts: number
  entry: number | null
  current: number | null
  stop: number | null
  target: number | null
  movePct: number | null          // girişten bu yana yönüne göre % (kârda +)
  breakeven: number | null        // girişe çekilmiş stop (komisyon payıyla)
  canBreakeven: boolean
  breakevenWhy: string
  half: number | null             // yarısını kapatmak için sözleşme sayısı
  bar: {stop: number | null; entry: number | null; current: number | null; target: number | null} | null  // 0..100
  expiryMs: number | null
}

export function positionView(input: {side: string | null; number_of_contracts: string | null; avg_entry_price: string | null; current_price: string | null; expiration_time?: string | null},
  protection?: {stop_price?: string | null; take_profit_price?: string | null} | null, now = Date.now()): PositionView {
  const raw = String(input.side ?? '').toUpperCase()
  const dir: PosDir | null = raw.endsWith('LONG') ? 'LONG' : raw.endsWith('SHORT') ? 'SHORT' : null
  const contracts = Math.max(0, Math.floor(Number(input.number_of_contracts) || 0))
  const entry = pos(input.avg_entry_price), current = pos(input.current_price)
  const stop = pos(protection?.stop_price), target = pos(protection?.take_profit_price)
  const sign = dir === 'SHORT' ? -1 : 1
  const movePct = entry && current && dir ? (current - entry) / entry * 100 * sign : null
  const breakeven = entry && dir ? entry * (1 + sign * BREAKEVEN_FEE_PCT / 100) : null
  let canBreakeven = false, breakevenWhy = ''
  if (!dir || !breakeven || !current) breakevenWhy = 'Fiyat okunamadı.'
  else if ((current - breakeven) * sign <= current * 0.0005) breakevenWhy = 'Fiyat girişin yeterince ötesine geçmedi; stop şu an girişe çekilemez.'
  else if (stop !== null && (stop - breakeven) * sign >= 0) breakevenWhy = 'Stop zaten girişte ya da daha iyi yerde.'
  else canBreakeven = true
  const half = contracts >= 2 ? Math.floor(contracts / 2) : null
  const points = [stop, entry, current, target].filter((value): value is number => value !== null)
  let bar: PositionView['bar'] = null
  if (points.length >= 2) {
    const lo = Math.min(...points), hi = Math.max(...points)
    const span = hi - lo
    if (span > 0) {
      // LONG: düşük fiyat solda; SHORT: ters çevrilir, böylece kâr yönü hep sağda (hedef sağda, stop solda)
      const at = (value: number | null) => value === null ? null : dir === 'SHORT' ? (hi - value) / span * 100 : (value - lo) / span * 100
      bar = {stop: at(stop), entry: at(entry), current: at(current), target: at(target)}
    }
  }
  const expiry = input.expiration_time ? Date.parse(input.expiration_time) : NaN
  const expiryMs = Number.isFinite(expiry) && new Date(expiry).getUTCFullYear() < 2050 && expiry - now < 30 * 86_400_000 ? expiry - now : null
  return {dir, contracts, entry, current, stop, target, movePct, breakeven, canBreakeven, breakevenWhy, half, bar, expiryMs}
}

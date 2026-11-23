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

/** Kapatılabilecek pay seçenekleri (sözleşme sayısı, küçükten büyüğe): ~%25, yarısı, tamamı. Tek sözleşmede yalnız tamamı. */
export function closeParts(contracts: number): number[] {
  if (!Number.isInteger(contracts) || contracts < 1) return []
  const set = new Set<number>([contracts])
  const half = Math.floor(contracts / 2), quarter = Math.floor(contracts / 4)
  if (half >= 1 && half < contracts) set.add(half)
  if (quarter >= 1 && quarter < half) set.add(quarter)
  return [...set].sort((a, b) => a - b)
}

export type ClosePreview = {fraction: number; now: number | null; atStop: number | null}
/** Seçilen payı kapatırsan yaklaşık K/Z (komisyon hariç) ve stop çalışırsa yaklaşık K/Z. K/Z fiyatla doğrusal kabul edilir;
 *  fiyat girişe çok yakınsa ya da veri eksikse null (uydurma yok). */
export function closePreview(view: PositionView, pnl: number | null, part: number): ClosePreview {
  const fraction = view.contracts > 0 ? Math.min(1, Math.max(0, part / view.contracts)) : 0
  if (pnl === null || !Number.isFinite(pnl)) return {fraction, now: null, atStop: null}
  const now = pnl * fraction
  const {entry, current, stop, dir} = view
  if (!entry || !current || !stop || !dir || pnl === 0) return {fraction, now, atStop: null}
  const sign = dir === 'SHORT' ? -1 : 1
  const move = (current - entry) * sign
  if (Math.abs(move) / entry <= 0.0005 || Math.sign(move) !== Math.sign(pnl)) return {fraction, now, atStop: null}
  return {fraction, now, atStop: pnl / move * (stop - entry) * sign * fraction}
}

type LiveQuote = {mid?: string | null; bid?: string | null; ask?: string | null} | null | undefined
type PositionLike = {side: string | null; number_of_contracts: string | null; avg_entry_price: string | null; current_price: string | null; unrealized_pnl: string | null}

/** Canlı fiyatı pozisyonun üstüne bindirir: güncel fiyat = alış/satış ortası; K/Z = (fiyat − giriş) × sözleşme × sözleşme boyutu.
 *  Fiyat ya da sözleşme boyutu güvenilir değilse pozisyon AYNEN döner (uydurma K/Z yok). */
export function overlayLive<T extends PositionLike>(position: T, quote: LiveQuote, contractSize: number | null): T {
  const bid = pos(quote?.bid), ask = pos(quote?.ask)
  const mid = pos(quote?.mid) ?? (bid !== null && ask !== null ? (bid + ask) / 2 : null)
  if (mid === null) return position
  const next: T = {...position, current_price: String(Number(mid.toPrecision(10)))}
  const raw = String(position.side ?? '').toUpperCase()
  const sign = raw.endsWith('LONG') ? 1 : raw.endsWith('SHORT') ? -1 : 0
  const entry = pos(position.avg_entry_price)
  const contracts = Math.floor(Number(position.number_of_contracts) || 0)
  if (sign !== 0 && entry !== null && contracts >= 1 && contractSize !== null && contractSize > 0) {
    // Coinbase'in pozisyon verisindeki unrealized_pnl fiyatın gerisinde kalıyor (fiyat 6,73→6,71 olurken -0,90'da sabit kaldı);
    // Coinbase ekranı K/Z'yi güncel fiyattan hesaplıyor. Bu yüzden K/Z her zaman fiyattan hesaplanır.
    next.unrealized_pnl = String(Number(((mid - entry) * contracts * contractSize * sign).toFixed(4)))
  }
  return next
}

/** Açık pozisyonların TOPLAM K/Z'si, Coinbase'deki gibi ödenen komisyon dahil. Tüm ekranlar (hesap kartı, pozisyon kartı,
 *  ana sayfa) bu tek hesabı kullanır; böylece her yerde aynı rakam görünür. Hiç pozisyon/K/Z yoksa null. */
export function netOpenPnl(positions: ReadonlyArray<{product_id?: unknown; unrealized_pnl?: unknown}> | null | undefined,
  fees: Record<string, string | null> | null | undefined): {value: number; withFees: boolean} | null {
  let total = 0, seen = 0, withFees = true
  for (const row of positions ?? []) {
    const raw = row?.unrealized_pnl
    const pnl = Number(raw)
    if (raw === null || raw === undefined || raw === '' || !Number.isFinite(pnl)) continue
    const feeRaw = typeof row.product_id === 'string' ? fees?.[row.product_id] : null
    const fee = Number(feeRaw)
    if (feeRaw !== null && feeRaw !== undefined && feeRaw !== '' && Number.isFinite(fee)) total += pnl - fee
    else { total += pnl; withFees = false }
    seen++
  }
  return seen === 0 ? null : {value: Number(total.toFixed(4)), withFees}
}

// Manuel pozisyon kapanış hedefi için saf hesaplar (ekran önizlemesi). Sunucudaki kuralla aynı:
// net = (fiyat − giriş) × miktar × yön − (giriş + fiyat) × miktar × komisyon. Veri eksikse null; uydurma yok.

export type CloseTargetMode = 'profit' | 'price'
export type CloseTargetRecord = {mode?: unknown; value?: unknown; side?: unknown; set_at?: unknown}

const ok = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0

/** Bu fiyatta kapanırsa net sonuç (USDT). */
export function netAtPrice(long: boolean, entry: number | null, quantity: number | null, price: number | null, feeRate: number): number | null {
  if (!ok(entry) || !ok(quantity) || !ok(price) || !(feeRate >= 0)) return null
  return (price - entry) * quantity * (long ? 1 : -1) - (entry + price) * quantity * feeRate
}

/** Net `profit` USDT için gereken kapanış fiyatı (komisyonlar dahil). */
export function priceForNetProfit(long: boolean, entry: number | null, quantity: number | null, profit: number, feeRate: number): number | null {
  if (!ok(entry) || !ok(quantity) || !Number.isFinite(profit) || !(feeRate >= 0) || feeRate >= 1) return null
  const price = long
    ? (profit + entry * quantity * (1 + feeRate)) / (quantity * (1 - feeRate))
    : (entry * quantity * (1 - feeRate) - profit) / (quantity * (1 + feeRate))
  return ok(price) ? price : null
}

/** Sunucudan gelen kayıt geçerliyse sade biçim; değilse null. */
export function activeCloseTarget(record: unknown): {mode: CloseTargetMode; value: number; side: 'ABOVE' | 'BELOW' | null} | null {
  if (!record || typeof record !== 'object') return null
  const raw = record as CloseTargetRecord
  const value = Number(raw.value)
  if ((raw.mode !== 'profit' && raw.mode !== 'price') || !ok(value)) return null
  return {mode: raw.mode, value, side: raw.side === 'ABOVE' || raw.side === 'BELOW' ? raw.side : null}
}

/** Kullanıcının yazdığı sayıyı okur ("1,5" ya da "1.5"). */
export function parseAmount(text: string): number | null {
  const value = Number(String(text).trim().replace(/\s/g, '').replace(',', '.'))
  return ok(value) ? value : null
}

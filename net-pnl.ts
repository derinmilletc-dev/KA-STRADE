// Komisyon sonrası NET kâr/zarar (açık pozisyonlar). Binance'in verdiği "gerçekleşmemiş K/Z" brüttür; kapatınca giriş ve çıkış
// komisyonu da kesilir. Net = brüt − (giriş + çıkış fiyatı) × miktar × taker oranı. Oran tahmindir (VIP/BNB indirimi hesaba göre değişir).
// Veri eksikse uydurma yok: null döner. Risk kapıları brüt değeri kullanmaya devam eder; bu yalnız GÖSTERİM içindir.
export const NET_TAKER_FEE_RATE = 0.0005

const num = (value: unknown): number | null => {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN
  return Number.isFinite(parsed) ? parsed : null
}

/** Tek pozisyonun net K/Z'si; brüt K/Z, giriş, anlık fiyat veya miktar eksikse null. */
export function positionNetPnl(position: unknown, rate = NET_TAKER_FEE_RATE): number | null {
  if (!position || typeof position !== 'object') return null
  const row = position as Record<string, unknown>
  const gross = num(row.unrealized_pnl), entry = num(row.entry_price), mark = num(row.mark_price ?? row.current_price), quantity = num(row.quantity)
  if (gross === null || entry === null || mark === null || quantity === null || entry <= 0 || mark <= 0 || quantity === 0) return null
  return gross - Math.abs(quantity) * (entry + mark) * rate
}

/** Hesap toplamı: tüm pozisyonlar hesaplanabiliyorsa net toplam. Aksi halde brüt toplam ve net:false (ekranda "brüt" denir). */
export function accountNetPnl(positions: unknown, grossTotal: unknown, rate = NET_TAKER_FEE_RATE): {value: number | null; net: boolean} {
  const gross = num(grossTotal)
  if (Array.isArray(positions)) {
    if (positions.length === 0) return {value: gross, net: true}  // açık pozisyon yok: komisyon yok, brüt = net
    const nets = positions.map(item => positionNetPnl(item, rate))
    if (nets.every((item): item is number => item !== null)) return {value: nets.reduce((sum, item) => sum + item, 0), net: true}
  }
  return {value: gross, net: false}
}

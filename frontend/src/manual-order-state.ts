// Manuel emir formunda coin değişimi ve analiz doldurma kuralları. Saf fonksiyonlar: ağ ve emir yok.
// Amaç: bir coinin fiyata bağlı değerleri (limit, stop, TP) başka coine yanlışlıkla taşınmasın.

export type PriceFields = {limit_price: string; stop_loss: string; tp1: string; tp2: string; tp3: string}

export const normalizeSymbol = (value: unknown): string => String(value ?? '').toUpperCase().replace('/', '').trim()

// Coin değişirse fiyata bağlı alanlar silinir; marjin, kaldıraç, yön, emir tipi ve hedef payı korunur.
// Aynı coin yeniden seçilirse form hiç değişmez (aynı nesne döner).
export function switchSymbol<T extends PriceFields & {symbol: string}>(order: T, nextSymbol: string): T {
  if (normalizeSymbol(order.symbol) === normalizeSymbol(nextSymbol)) return order
  return {...order, symbol: nextSymbol, limit_price: '', stop_loss: '', tp1: '', tp2: '', tp3: ''}
}

// "Analizden doldur" yalnız seçili coin, analizin coiniyle aynıysa çalışır. Değilse nedenini döndürür.
export function analysisFillBlock(orderSymbol: string, analysisSymbol: string): string | null {
  const selected = normalizeSymbol(orderSymbol)
  const analysed = normalizeSymbol(analysisSymbol)
  if (!analysed) return 'Analiz seviyeleri henüz yok.'
  if (selected === analysed) return null
  return `Analiz ${analysed} içindir, seçili coin ${selected || 'yok'}. Başka coinin seviyeleri bu coine uygulanmaz; önce ${analysed} coinini seç ya da stop ve hedefi elle gir.`
}

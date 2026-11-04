// Canlı fiyat: açık pozisyonların "şimdi kapatırsan" sonucunu hesaplayan saf yardımcılar (ağ yok, ekran yok).
// Sunucudaki pozisyon özeti 10–30 sn geriden gelebilir ve mark fiyatına göredir; piyasa emriyle kapanış ise
// LONG'da alış (bid), SHORT'ta satış (ask) fiyatından dolar. Bu yüzden ekrandaki kâr/zarar burada, Binance'in
// anlık en iyi alış/satış fiyatından yeniden hesaplanır. Fiyat taze değilse null döner; hiçbir değer uydurulmaz.

export type Quote = {bid: number; ask: number; at: number}
export type QuoteMap = Record<string, Quote>

/** Bağlantı açıkken bile bu süreden eski fiyat "canlı" sayılmaz (ms). Bağlantının kendisi ayrıca 10 sn sessizlikte düşürülür. */
export const QUOTE_FRESH_MS = 30_000
/** Piyasa emri komisyonu (taker), tek yön: %0,05. */
export const TAKER_FEE_RATE = 0.0005

export type CloseEstimate = {
  /** Piyasa emriyle kapanışta beklenen fiyat (LONG → bid, SHORT → ask). */
  exitPrice: number
  /** Komisyon hariç kâr/zarar (Binance'in gösterdiği gibi). */
  pnl: number
  /** Kapanış komisyonu düşülmüş tahmini net sonuç. */
  pnlAfterFee: number
  /** Fiyatın yaşı (ms). */
  ageMs: number
}

const finitePositive = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0

/** Binance bookTicker mesajını okur ({s, b, a} ya da {stream, data}). Bozuksa null. */
export function parseBookTicker(raw: unknown, receivedAt: number): {symbol: string; quote: Quote} | null {
  const outer = raw && typeof raw === 'object' ? raw as Record<string, unknown> : null
  const data = outer && outer.data && typeof outer.data === 'object' ? outer.data as Record<string, unknown> : outer
  if (!data || typeof data.s !== 'string') return null
  const bid = Number(data.b), ask = Number(data.a)
  if (!finitePositive(bid) || !finitePositive(ask) || ask < bid) return null
  return {symbol: data.s.toUpperCase(), quote: {bid, ask, at: receivedAt}}
}

/** Taze fiyat varsa "şimdi piyasa emriyle kapatırsan" tahmini; yoksa null. */
export function closeEstimate(input: {long: boolean; entry: number | null; quantity: number | null; quote: Quote | undefined; nowMs: number}): CloseEstimate | null {
  const {long, entry, quantity, quote, nowMs} = input
  if (!quote || !finitePositive(entry) || !finitePositive(quantity)) return null
  const ageMs = Math.max(0, nowMs - quote.at)
  if (ageMs > QUOTE_FRESH_MS) return null
  const exitPrice = long ? quote.bid : quote.ask
  if (!finitePositive(exitPrice)) return null
  const pnl = (exitPrice - entry) * (long ? 1 : -1) * quantity
  const pnlAfterFee = pnl - exitPrice * quantity * TAKER_FEE_RATE
  return {exitPrice, pnl, pnlAfterFee, ageMs}
}

/** Akış için sembol listesi: tekrarsız, sıralı, yalnız geçerli USDⓈ-M sembolleri. */
export function streamSymbols(symbols: ReadonlyArray<unknown>): string[] {
  const set = new Set<string>()
  for (const value of symbols) {
    const symbol = String(value ?? '').trim().toUpperCase()
    if (/^[A-Z0-9]{2,30}$/.test(symbol)) set.add(symbol)
  }
  return [...set].sort()
}

/** Binance USDⓈ-M public akış adresi (bookTicker /public yolunda yayınlanır). */
export function bookTickerUrl(symbols: ReadonlyArray<string>): string | null {
  if (symbols.length === 0) return null
  return `wss://fstream.binance.com/public/stream?streams=${symbols.map(symbol => `${symbol.toLowerCase()}@bookTicker`).join('/')}`
}

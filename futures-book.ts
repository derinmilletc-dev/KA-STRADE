// Binance USDⓈ-M Futures en iyi alış/satış fiyatı (bookTicker): saf yardımcılar.
// Pozisyon kapatılınca piyasa emri LONG'da en iyi alış (bid), SHORT'ta en iyi satış (ask) fiyatından dolar;
// ekrandaki kâr/zarar bu fiyattan hesaplanınca "kapatırsam ne olur" sorusunun cevabını gösterir.

export type FuturesBook = {symbol: string; bid: number; ask: number; at: number}
export type BookState = 'live' | 'late' | 'stale'

/** 1,5 sn'den eski fiyat "gecikiyor", 4 sn'den eskisi "güncel değil" sayılır. */
export const LATE_MS = 1500
export const STALE_MS = 4000

export function parseBookTicker(raw: unknown, symbol: string, at: number): FuturesBook | null {
  const data = typeof raw === 'object' && raw !== null ? raw as Record<string, unknown> : null
  const payload = data && typeof data.data === 'object' && data.data !== null ? data.data as Record<string, unknown> : data
  if (!payload) return null
  const sym = String(payload.s ?? payload.symbol ?? '').toUpperCase()
  const bid = Number(payload.b ?? payload.bidPrice)
  const ask = Number(payload.a ?? payload.askPrice)
  if (sym !== symbol.toUpperCase() || !(bid > 0) || !(ask > 0) || ask < bid) return null
  return {symbol: sym, bid, ask, at}
}

export function bookState(book: FuturesBook | null, now: number): BookState {
  if (!book) return 'stale'
  const age = now - book.at
  return age <= LATE_MS ? 'live' : age <= STALE_MS ? 'late' : 'stale'
}

/** Pozisyon şimdi kapatılırsa piyasa emrinin dolacağı yaklaşık fiyat. */
export function closePrice(side: 'LONG' | 'SHORT', book: FuturesBook): number {
  return side === 'LONG' ? book.bid : book.ask
}

/** Güncel değilse null: çağıran eski kaynağa (mum yayını / hesap) düşer ve ekranda gecikmeyi gösterir. */
export function freshClosePrice(side: 'LONG' | 'SHORT', book: FuturesBook | null, symbol: string, now: number): number | null {
  if (!book || book.symbol !== symbol.toUpperCase() || bookState(book, now) === 'stale') return null
  return closePrice(side, book)
}

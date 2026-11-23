// Coinbase Advanced Trade WebSocket "ticker" mesajlarının SAF ayrıştırıcısı (ağ yok). Bozuk/eksik satır sessizce atlanır.
export type TickQuote = {price: number; bid: number | null; ask: number | null; at: number}

const positive = (value: unknown): number | null => { const n = Number(value); return value !== '' && value != null && Number.isFinite(n) && n > 0 ? n : null }

/** Tek bir WS mesajından {ürün: fiyat} çıkarır. Yalnız ticker kanalı; snapshot ve update ikisi de geçerli. */
export function parseTicker(raw: unknown, now = Date.now()): Record<string, TickQuote> {
  const out: Record<string, TickQuote> = {}
  if (raw === null || typeof raw !== 'object') return out
  const message = raw as {channel?: unknown; events?: unknown}
  if (message.channel !== 'ticker' || !Array.isArray(message.events)) return out
  for (const event of message.events) {
    const tickers = event && typeof event === 'object' ? (event as {tickers?: unknown}).tickers : null
    if (!Array.isArray(tickers)) continue
    for (const item of tickers) {
      if (!item || typeof item !== 'object') continue
      const row = item as Record<string, unknown>
      const id = typeof row.product_id === 'string' ? row.product_id : ''
      const price = positive(row.price)
      if (!id || price === null) continue
      out[id] = {price, bid: positive(row.best_bid), ask: positive(row.best_ask), at: now}
    }
  }
  return out
}

export const subscribeMessage = (ids: string[], type: 'subscribe' | 'unsubscribe' = 'subscribe') =>
  [JSON.stringify({type, product_ids: ids, channel: 'ticker'}), JSON.stringify({type, product_ids: ids, channel: 'level2'}), JSON.stringify({type, channel: 'heartbeats'})]

// Emir defteri (level2): ticker yalnız işlem olunca gelir; az işlem gören vadelilerde fiyat donar. Defterden en iyi alış/satış
// her değişimde güncellenir, böylece fiyat Coinbase ekranındaki gibi anlık oynar.
export type Book = {bids: Map<number, number>; asks: Map<number, number>}
export type Books = Record<string, Book>

/** l2_data mesajını defterlere işler (snapshot defteri sıfırlar). Değişen ürünlerin listesini döner. */
export function applyL2(books: Books, raw: unknown): string[] {
  if (raw === null || typeof raw !== 'object') return []
  const message = raw as {channel?: unknown; events?: unknown}
  if (message.channel !== 'l2_data' || !Array.isArray(message.events)) return []
  const changed = new Set<string>()
  for (const event of message.events) {
    if (!event || typeof event !== 'object') continue
    const row = event as {type?: unknown; product_id?: unknown; updates?: unknown}
    const id = typeof row.product_id === 'string' ? row.product_id : ''
    if (!id || !Array.isArray(row.updates)) continue
    if (row.type === 'snapshot' || !books[id]) books[id] = {bids: new Map(), asks: new Map()}
    const book = books[id]
    for (const item of row.updates) {
      if (!item || typeof item !== 'object') continue
      const update = item as {side?: unknown; price_level?: unknown; new_quantity?: unknown}
      const side = update.side === 'bid' ? book.bids : update.side === 'offer' || update.side === 'ask' ? book.asks : null
      const price = positive(update.price_level), qty = Number(update.new_quantity)
      if (!side || price === null || !Number.isFinite(qty)) continue
      if (qty > 0) side.set(price, qty); else side.delete(price)
    }
    changed.add(id)
  }
  return [...changed]
}

/** Defterin tepesi: en iyi alış/satış ve ortası. Taraflardan biri boşsa ya da defter ters dönmüşse null (uydurma yok). */
export function topOfBook(book: Book | undefined, now = Date.now()): TickQuote | null {
  if (!book || book.bids.size === 0 || book.asks.size === 0) return null
  let bid = -Infinity, ask = Infinity
  for (const price of book.bids.keys()) if (price > bid) bid = price
  for (const price of book.asks.keys()) if (price < ask) ask = price
  if (!(bid > 0) || !(ask > bid)) return null
  return {price: Number(((bid + ask) / 2).toPrecision(10)), bid, ask, at: now}
}

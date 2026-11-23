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
  [JSON.stringify({type, product_ids: ids, channel: 'ticker'}), JSON.stringify({type, channel: 'heartbeats'})]

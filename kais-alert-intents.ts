// Kais ajanı — fiyat alarmı. Yalnızca site açıkken çalışır; emir vermez, veri uydurmaz (fiyat Binance herkese açık uç noktasından).
export type PriceAlert = {id: string; symbol: string; level: number; side: 'above' | 'below'}

const COINS = ['btc', 'eth', 'sol', 'bnb', 'xrp', 'doge', 'ada', 'avax', 'link', 'dot', 'ltc', 'trx', 'sui', 'arb', 'op', 'near', 'atom', 'inj', 'apt']
const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/,/g, '.')

/** "BTC 90 bini geçerse haber ver" / "ETH 3000 altına inerse uyar" → alarm. Emin değilse null. */
export function parseAlert(message: string): Omit<PriceAlert, 'id'> | null {
  const text = fold(message)
  if (text.split(' ').length > 14 || !/\b(haber ver|uyar|bildir|alarm|alert|notify|tell me|let me know)/.test(text)) return null
  const coin = COINS.find(name => new RegExp(`\\b${name}\\b`).test(text)); if (!coin) return null
  const hit = text.match(/(\d+(?:\.\d+)?)\s*(bin|k|milyon)?/g)?.map(part => part.match(/(\d+(?:\.\d+)?)\s*(bin|k|milyon)?/)!).find(m => Number(m[1]) > 0)
  if (!hit) return null
  const level = Number(hit[1]) * (hit[2] === 'bin' || hit[2] === 'k' ? 1000 : hit[2] === 'milyon' ? 1e6 : 1)
  const below = /\b(alt|dus|in|asagi|below|under|drop|fall)/.test(text)
  const above = /\b(ust|gec|asar|yuksel|cik|above|over|exceed|cross|rise)/.test(text)
  if (!level || (below === above)) return null
  return {symbol: `${coin.toUpperCase()}USDT`, level, side: above ? 'above' : 'below'}
}

export async function fetchPrice(symbol: string): Promise<number | null> {
  try {
    const response = await fetch(`https://api.binance.com/api/v3/ticker/price?symbol=${symbol}`)
    if (!response.ok) return null
    const price = Number((await response.json()).price)
    return Number.isFinite(price) && price > 0 ? price : null
  } catch { return null }
}

const KEY = 'kais-price-alerts'
export const loadAlerts = (): PriceAlert[] => { try { const rows = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(rows) ? rows : [] } catch { return [] } }
export const saveAlerts = (rows: PriceAlert[]) => { try { localStorage.setItem(KEY, JSON.stringify(rows.slice(-10))) } catch { /* yok say */ } }

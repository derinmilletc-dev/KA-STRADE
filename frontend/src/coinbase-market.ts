// Coinbase ürün listesi + Kais AI sinyali eşlemesi (SAF mantık). Uydurma yok: eşleşmeyen ürün için sinyal verilmez.
import type {SignalMap} from './coin-ranking'

export type CbProduct = {
  product_id: string; display: string; root: string | null; price: string | null; change_pct: string | null
  volume_usd: string | null; expiry: string | null; contract_size: string | null; tradable: boolean
}
export type CbRow = {symbol: string; display: string; price: number; change: number; volume: number; root: string | null; expiry: string | null}
export type CbQuote = {product_id: string; bid: string; ask: string; mid: string; spread_pct: string}

const num = (value: string | null): number => value === null ? NaN : Number(value)

/** Yalnızca işlem görebilen ürünler. Sunucu sırası (hacim) korunur. */
export function toRows(products: readonly CbProduct[]): CbRow[] {
  return products.filter(item => item.tradable).map(item => ({
    symbol: item.product_id, display: item.display || item.product_id, price: num(item.price), change: num(item.change_pct),
    volume: num(item.volume_usd), root: item.root, expiry: item.expiry,
  }))
}

/** Dayanak varlığın Binance analiz anahtarı (BTC → BTCUSDT). Dayanak bilinmiyorsa null. */
export const analysisSymbol = (root: string | null): string | null => root && /^[A-Z0-9]{2,10}$/.test(root) ? `${root}USDT` : null

/** Binance analiz evreninden, Coinbase ürün koduna göre anahtarlanmış sinyal haritası. Evrende olmayan ürün haritaya girmez. */
export function signalsForRows(rows: readonly CbRow[], universe: SignalMap | null): SignalMap | null {
  if (universe === null) return null
  const map: SignalMap = {}
  for (const row of rows) {
    const key = analysisSymbol(row.root)
    if (key && universe[key]) map[row.symbol] = universe[key]
  }
  return map
}

const clean = (value: string) => value.toUpperCase().replace(/[\s/_-]/g, '')

export function matchRows<T extends {symbol: string; display: string; root: string | null}>(rows: readonly T[], query: string): T[] {
  const q = clean(query.trim())
  if (!q) return [...rows]
  return rows.filter(row => clean(row.symbol).includes(q) || clean(row.display).includes(q) || (row.root !== null && clean(row.root).includes(q)))
}

export function priceText(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2}) : '—'
}
export const changeText = (value: number): string => Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—'

/** "2030-12-20T16:00:00Z" → "20.12.2030". Okunamıyorsa null (tahmin yok). */
export function expiryText(value: string | null): string | null {
  if (!value) return null
  const time = Date.parse(value)
  if (!Number.isFinite(time)) return null
  const d = new Date(time)
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')}.${d.getUTCFullYear()}`
}

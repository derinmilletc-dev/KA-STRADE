// Coinbase ürün listesi + Kais AI sinyali eşlemesi (SAF mantık). Uydurma yok: eşleşmeyen ürün için sinyal verilmez.
import type {SignalMap} from './coin-ranking'

export type CbProduct = {
  product_id: string; display: string; root: string | null; price: string | null; change_pct: string | null
  volume_usd: string | null; expiry: string | null; contract_size: string | null; price_increment?: string | null; margin_rate?: string | null; tradable: boolean
}
export type CbRow = {symbol: string; display: string; price: number; change: number; volume: number; root: string | null; expiry: string | null; minMargin?: number | null}
export type CbQuote = {product_id: string; bid: string | null; ask: string | null; mid: string | null; spread_pct: string | null; source?: 'book' | 'last' | 'none'; reason?: string}

const num = (value: string | null): number => value === null ? NaN : Number(value)

/** Yalnızca işlem görebilen ürünler. Sunucu sırası (hacim) korunur. */
export function toRows(products: readonly CbProduct[]): CbRow[] {
  return products.filter(item => item.tradable).map(item => ({
    symbol: item.product_id, display: item.display || item.product_id, price: num(item.price), change: num(item.change_pct),
    volume: num(item.volume_usd), root: item.root, expiry: item.expiry, minMargin: minMarginOf(num(item.price), num(item.contract_size), num(item.margin_rate ?? null)),
  }))
}

/** 1 sözleşme için borsanın istediği en az teminat (USD): fiyat × sözleşme boyutu × marjin oranı. Oran bilinmiyorsa null (tahmin uydurulmaz). */
export function minMarginOf(price: number, contractSize: number, rate: number): number | null {
  const value = price * contractSize * rate
  return [price, contractSize, rate].every(item => Number.isFinite(item) && item > 0) && Number.isFinite(value) ? Math.round(value * 100) / 100 : null
}

/** Listede coinin yanında: "en az ~104 USD" ve bakiyeye yetip yetmediği. Bilinmiyorsa null. */
export function minMarginNote(minMargin: number | null | undefined, spendable: number | null): {text: string; short: boolean} | null {
  if (minMargin === null || minMargin === undefined || !Number.isFinite(minMargin) || minMargin <= 0) return null
  const text = `en az ~${minMargin >= 100 ? Math.ceil(minMargin) : minMargin.toFixed(2).replace(/\.?0+$/, '')} USD`
  return {text, short: spendable !== null && Number.isFinite(spendable) && spendable > 0 && minMargin > spendable}
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

/** "2030-12-20T16:00:00Z" → "20.12.2030". Okunamıyorsa null (tahmin yok). Süresiz (perp) ürünlerin yer tutucu tarihi (2050+) gösterilmez. */
export function expiryText(value: string | null): string | null {
  if (!value) return null
  const time = Date.parse(value)
  if (!Number.isFinite(time)) return null
  const d = new Date(time)
  if (d.getUTCFullYear() >= 2050) return null
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')}.${d.getUTCFullYear()}`
}

/** Süresiz (perp) ürün mü: adında "Perp" geçer ya da vade tarihi yer tutucudur (2050+). */
export function isPerp(row: {display: string; expiry: string | null}): boolean {
  if (/perp/i.test(row.display)) return true
  const time = row.expiry ? Date.parse(row.expiry) : NaN
  return Number.isFinite(time) && new Date(time).getUTCFullYear() >= 2050
}

const MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']
/** Vade seçeneğinin kısa adı: "Perp" ya da "27 Kas" (yıl farklıysa "26 Şub 27"). */
export function variantLabel(row: {display: string; expiry: string | null}, now = Date.now()): string {
  if (isPerp(row)) return 'Perp'
  const time = row.expiry ? Date.parse(row.expiry) : NaN
  if (!Number.isFinite(time)) return row.display
  const d = new Date(time)
  const sameYear = d.getUTCFullYear() === new Date(now).getUTCFullYear()
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${sameYear ? '' : ` ${String(d.getUTCFullYear()).slice(2)}`}`
}

const expiryTime = (row: CbRow) => { const t = row.expiry ? Date.parse(row.expiry) : NaN; return Number.isFinite(t) ? t : Number.MAX_SAFE_INTEGER }

/** Her coin için tek satır: perp varsa o, yoksa en yakın vadeli ürün. Liste sırası (hacim) korunur.
 *  variants: coinin tüm ürünleri (perp önce, sonra vadeye göre). Dayanağı bilinmeyen ürün kendi başına kalır. */
export function groupRows(rows: readonly CbRow[]): {rows: CbRow[]; variants: Record<string, CbRow[]>} {
  const groups = new Map<string, CbRow[]>()
  const order: string[] = []
  for (const row of rows) {
    const key = row.root || row.symbol
    if (!groups.has(key)) { groups.set(key, []); order.push(key) }
    groups.get(key)!.push(row)
  }
  const variants: Record<string, CbRow[]> = {}
  const result: CbRow[] = []
  for (const key of order) {
    const list = [...groups.get(key)!].sort((a, b) => (isPerp(a) ? 0 : 1) - (isPerp(b) ? 0 : 1) || expiryTime(a) - expiryTime(b))
    variants[key] = list
    result.push(list[0])
  }
  return {rows: result, variants}
}

// Coinbase stop/hedef önerileri ve risk limitleri: SAF mantık (React yok). Sunucu kuralların sahibidir;
// öneriler sunucuda gerçek ön kontrolden (preflight) geçirilerek gelir, burada yalnız istek kurulur ve gösterim metni üretilir.
import type {Ticket} from './coinbase-manual'

// coinbase-manual.isPositiveDecimal ile aynı kural (node testinin .ts uzantı kısıtı yüzünden burada tekrar).
const isPositiveDecimal = (value: string): boolean => /^\d+(\.\d+)?$/.test(value.trim()) && Number(value) > 0

export type SuggestItem = {
  stop_pct: number; reward_r: number; ok: boolean; reasons: string[]
  stop_price: string; take_profit_price: string
  contracts: number | null; worst_case_loss_usd: string | null; leverage: string | null; notional_usd: string | null; reward_usd: string | null
}
export type SuggestResult = {
  items: SuggestItem[]; recommended_stop_pct: number | null; shared_reasons: string[]
  state: {bid: string; ask: string; open_orders: number; open_positions: number; max_open_orders: number}
}
export type LimitField = 'max_loss_usd' | 'daily_loss_limit_usd' | 'leverage_max' | 'stop_limit_gap_pct'
export type LimitsView = {
  custom: Partial<Record<LimitField, string>>
  effective: Record<LimitField, string>
  ceiling: Record<LimitField, string>
  bounds: Record<LimitField, [string, string]>
  max_open_orders: number
}

/** Öneri isteği. Hazır değilse (ürün/teminat yok) null: istek atılmaz. Sözleşme yazıldıysa kaldıraç gönderilmez (ticketPayload ile aynı kural). */
export function suggestPayload(ticket: Ticket): Record<string, unknown> | null {
  if (ticket.productId.trim() === '' || !isPositiveDecimal(ticket.marginUsd)) return null
  const body: Record<string, unknown> = {product_id: ticket.productId.trim(), side: ticket.side, margin_usd: ticket.marginUsd.trim()}
  if (ticket.limitPrice.trim() !== '') {
    if (!isPositiveDecimal(ticket.limitPrice)) return null
    body.entry_limit_price = ticket.limitPrice.trim()
  }
  if (ticket.contracts.trim() !== '') {
    if (!/^[1-9]\d{0,5}$/.test(ticket.contracts.trim())) return null
    body.contracts = Number(ticket.contracts.trim())
  } else if (/^\d+(\.\d+)?$/.test(ticket.leverage.trim()) && Number(ticket.leverage) >= 1) body.leverage = Number(ticket.leverage.trim())
  return body
}

const num = (value: string | null | undefined): number | null => {
  const parsed = Number(value)
  return value !== null && value !== undefined && value !== '' && Number.isFinite(parsed) ? parsed : null
}
const usd = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: value >= 100 ? 0 : 2})

/** Sunucunun uzun Türkçe sebebini kart altına sığacak kısa bir etikete çevirir. */
export function reasonShort(reason: string): string {
  const text = reason.toLowerCase()
  if (text.includes('kayıp sınır') || text.includes('en kötü kayıp')) return 'Kayıp limitine sığmıyor'
  if (text.includes('günlük zarar')) return 'Günlük zarar limiti'
  if (text.includes('kaldıraç')) return 'Kaldıraç sınırı'
  if (text.includes('alım gücü')) return 'Alım gücü yetersiz'
  if (text.includes('açık emir sınırı')) return 'Açık emir sınırı dolu'
  if (text.includes('stop girişe çok yakın')) return 'Stop çok yakın'
  if (text.includes('çok uzak')) return 'Stop çok uzak'
  if (text.includes('alış-satış farkı')) return 'Piyasa açık fark'
  if (text.includes('zaten açık pozisyon')) return 'Üründe pozisyon var'
  if (text.includes('en küçük miktar') || text.includes('1 sözleşme')) return 'Teminat az'
  return 'Ön kontrolden geçmez'
}

/** Kartın alt satırı: geçenlerde sözleşme + en kötü kayıp, geçmeyenlerde kısa sebep. */
export function itemSub(item: SuggestItem): string {
  if (!item.ok) return reasonShort(item.reasons[0] ?? '')
  const loss = num(item.worst_case_loss_usd)
  const parts = [item.contracts ? `${item.contracts} sözleşme` : null, loss !== null ? `en çok −${usd(loss)} USD` : null].filter(Boolean)
  return parts.join(' · ')
}

/** Kartın ikinci satırı (yalnız geçenlerde): hedefte beklenen kazanç. */
export function itemReward(item: SuggestItem): string | null {
  const reward = num(item.reward_usd)
  return item.ok && reward !== null && reward > 0 ? `Hedefte +${usd(reward)} USD · ${String(item.reward_r).replace('.', ',')}R` : null
}

/** Bir öneri o anki bilet ile birebir aynı mı (seçili göstermek için). */
export function itemSelected(item: SuggestItem, ticket: Pick<Ticket, 'stopPrice' | 'takeProfit'>): boolean {
  const same = (a: string, b: string) => a.trim() !== '' && Number(a) === Number(b)
  return same(item.stop_price, ticket.stopPrice) && same(item.take_profit_price, ticket.takeProfit)
}

/** Hiç öneri geçmiyorsa kullanıcıya NEDEN ve NE YAPMALI söylenir. */
export function blockedAdvice(result: SuggestResult): string | null {
  if (result.items.some(item => item.ok)) return null
  const first = result.shared_reasons[0] ?? result.items[0]?.reasons[0] ?? ''
  const short = reasonShort(first)
  if (short === 'Açık emir sınırı dolu') return `Açık emir sınırı dolu (${result.state.open_orders}/${result.state.max_open_orders}). Önce bir pozisyonu kapat ya da bekleyen emri iptal et.`
  if (short === 'Alım gücü yetersiz') return 'Alım gücü bu teminata yetmiyor. Teminatı düşür ya da hesaba bakiye aktar.'
  if (short === 'Teminat az') return 'Bu teminatla 1 sözleşme bile alınamıyor. Teminatı artır.'
  if (short === 'Günlük zarar limiti') return 'Günlük zarar limitine ulaştın; limitini Risk limitlerim bölümünden gözden geçirebilirsin.'
  if (short === 'Kayıp limitine sığmıyor') return 'İşlem başı zarar limitine hiçbir öneri sığmıyor. Teminatı düşür ya da limitini yükselt.'
  return first || 'Şu an hiçbir öneri ön kontrolden geçmiyor.'
}

/** Açık emir kapasitesi: her korumalı pozisyon 1 bracket (stop+hedef tek emir) kullanır. */
export function orderCapacity(openOrders: number | null, maxOrders: number): {used: number; max: number; free: number; text: string; tone: 'ok' | 'warn' | 'bad'} {
  const max = Math.max(1, Math.floor(maxOrders))
  const used = openOrders === null || !Number.isFinite(openOrders) ? 0 : Math.max(0, Math.floor(openOrders))
  const free = Math.max(0, max - used)
  const text = free === 0 ? 'Dolu: yeni işlem için önce bir emir/pozisyon kapat.' : free === 1 ? 'Son bir işlem daha açabilirsin.' : `${free} işlem daha açabilirsin.`
  return {used, max, free, text, tone: free === 0 ? 'bad' : free === 1 ? 'warn' : 'ok'}
}

export const UNLIMITED_AT = 1_000_000
/** 1.000.000 ve üstü "sınırsız" sayılır (sunucu varsayılanı fiilen sınırsız). */
export function limitLabel(field: LimitField, value: string | undefined): string {
  const parsed = num(value)
  if (parsed === null) return '—'
  if (field === 'stop_limit_gap_pct') return `%${(parsed * 100).toLocaleString('tr-TR', {maximumFractionDigits: 2})}`
  if (field === 'leverage_max') return `${parsed.toLocaleString('tr-TR', {maximumFractionDigits: 0})}x`
  return parsed >= UNLIMITED_AT ? 'Sınırsız' : `${usd(parsed)} USD`
}

/** Kaydet düğmesi için: formdaki değerlerden sunucuya gidecek yük + yerel hata. Boş alan "özel limit yok". */
export function limitsPayload(form: Record<LimitField, string>, view: Pick<LimitsView, 'bounds'>): {ok: true; body: Record<LimitField, string | null>} | {ok: false; field: LimitField; message: string} {
  const body = {} as Record<LimitField, string | null>
  const fields: LimitField[] = ['max_loss_usd', 'daily_loss_limit_usd', 'leverage_max', 'stop_limit_gap_pct']
  for (const field of fields) {
    const raw = form[field].trim().replace(',', '.')
    if (raw === '') { body[field] = null; continue }
    // Boşluklu/ondalıklı doğal yazım: gap alanı yüzde olarak girilir (0,3 = %0,3), sunucuya oran gider (0.003).
    const shown = Number(raw)
    const value = field === 'stop_limit_gap_pct' ? shown / 100 : shown
    const [low, high] = view.bounds[field].map(Number)
    if (!Number.isFinite(shown) || value < low || value > high) {
      const range = field === 'stop_limit_gap_pct' ? `%${low * 100} – %${high * 100}` : `${low} – ${high >= UNLIMITED_AT ? 'sınırsız' : high}`
      return {ok: false, field, message: `Değer ${range} aralığında olmalı.`}
    }
    body[field] = field === 'stop_limit_gap_pct' ? String(Number(value.toFixed(6))) : String(shown)
  }
  return {ok: true, body}
}

/** Sunucudaki değerden form metni: gap yüzde olarak gösterilir; "sınırsız" tavan boş bırakılır. */
export function limitFormValue(field: LimitField, custom: LimitsView['custom']): string {
  const value = custom[field]
  if (value === undefined) return ''
  return field === 'stop_limit_gap_pct' ? String(Number((Number(value) * 100).toFixed(4))) : value
}

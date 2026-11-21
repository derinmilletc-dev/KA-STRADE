// "Son olaylar" akışı: sunucunun ham olay listesini müşterinin okuyacağı sade satırlara çevirir.
// Ham borsa kodları (PARTIALLY_FILLED, NEW), teşhis satırları ve bağlantı gürültüsü gösterilmez;
// aynı işin art arda gelen parçaları tek satıra birleşir. Saf fonksiyon: ağ yok, ekran yok.

export type RawFeedEvent = {
  kind?: unknown; message?: unknown; created_at?: unknown; symbol?: unknown; status?: unknown
  side?: unknown; price?: unknown; quantity?: unknown; realized_pnl_fill?: unknown; reduce_only?: unknown
}
export type FeedItem = {id: string; at: string; tone: 'ok' | 'warn' | 'bad' | 'mute'; group: 'trade' | 'warn' | 'system'; title: string; detail?: string}

// Müşteriye hiçbir zaman gösterilmeyen teknik/teşhis olayları.
const HIDDEN = /^(LIVE_ORDER_TIMING|LIVE_ORDER_TEST|LIVE_DECISION_AUDIT|LIVE_STREAM_CONNECTED|LIVE_STREAM_RECONNECT|READ_ONLY_CONNECTED|LIVE_ORDER_UPDATE|LIVE_ACCOUNT_RATE_LIMIT_(WAIT|RECOVERED)|LIVE_PLAN_STALE_RETIRED|LIVE_PLAN_SUPERSEDED|LIVE_ENTRY_REMAINDER_SETTLED|LIVE_AUTO_CONSENT|LIVE_WEB_CONSENT|LIVE_MONITORING_CREDENTIALS_STALE|TP_MONITORING_PERSISTED)$/
const DEDUPE_WINDOW_MS = 60_000

const num = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) ? n : null }
const text = (value: unknown) => String(value ?? '').trim()
const coin = (symbol: unknown) => text(symbol).toUpperCase().replace(/USDT$/, '')
const price = (value: unknown) => { const n = num(value); return n !== null && n > 0 ? n.toLocaleString('tr-TR', {maximumFractionDigits: 8}) : null }
const signed = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toLocaleString('tr-TR', {maximumFractionDigits: 2})}`

function line(event: RawFeedEvent): Omit<FeedItem, 'id' | 'at'> | null {
  const kind = text(event.kind)
  const name = coin(event.symbol)
  const status = text(event.status).toUpperCase()
  if (HIDDEN.test(kind)) return null
  if (kind === 'LIVE_FILL') {
    // Parçalı dolumlar gösterilmez; yalnız işin tamamlandığı son dolum yazılır.
    if (status !== 'FILLED') return null
    const at = price(event.price)
    if (event.reduce_only === true) {
      const pnl = num(event.realized_pnl_fill)
      return {tone: pnl !== null && pnl < 0 ? 'warn' : 'ok', group: 'trade', title: `${name} pozisyonu kapandı`, detail: [at ? `fiyat ${at}` : '', pnl !== null && pnl !== 0 ? `${signed(pnl)} USDT (komisyon hariç)` : ''].filter(Boolean).join(' · ') || undefined}
    }
    const side = text(event.side).toUpperCase()
    return {tone: 'ok', group: 'trade', title: `${name} ${side === 'SELL' ? 'SHORT' : side === 'BUY' ? 'LONG' : ''} girişi doldu`.replace('  ', ' '), detail: at ? `giriş fiyatı ${at}` : undefined}
  }
  if (kind === 'LIVE_ALGO_UPDATE') {
    if (status === 'NEW') return {tone: 'ok', group: 'system', title: `${name} koruması kuruldu`, detail: 'Stop ve hedefler Binance’te'}
    if (status === 'REJECTED') return {tone: 'bad', group: 'warn', title: `${name} koruma emri reddedildi`, detail: 'Pozisyonunu kontrol et'}
    if (status === 'TRIGGERED' || status === 'FINISHED') return {tone: 'ok', group: 'trade', title: `${name} Stop/hedef çalıştı`}
    if (status === 'CANCELED' || status === 'CANCELLED' || status === 'EXPIRED') return {tone: 'mute', group: 'system', title: `${name} koruma emirleri temizlendi`}
    return null
  }
  const message = text(event.message)
  if (!message) return null
  const tone = /RECOVERED|RESTORED/.test(kind) ? 'ok' : /EMERGENCY|FAIL|BLOCK|UNKNOWN|BREACH|REJECT/.test(kind) ? 'bad' : /STALE|RATE_LIMIT|EXPIR|REAUTH|RETRY|MISSING|UNCERTAIN|FOREIGN/.test(kind) ? 'warn' : /^LIVE_(ENTRY|CLOSE|POSITION_CLOSED)/.test(kind) ? 'ok' : 'mute'
  const group = /^LIVE_(ENTRY|CLOSE|POSITION_CLOSED)/.test(kind) ? 'trade' : tone === 'warn' || tone === 'bad' ? 'warn' : 'system'
  return {tone, group, title: message}
}

/** Ham olaylar (yeniden eskiye) -> sade satırlar. Aynı başlık 60 sn içinde tekrar ederse tek satırda kalır. */
export function friendlyFeed(events: ReadonlyArray<RawFeedEvent> | null | undefined, limit = 14): FeedItem[] {
  const out: FeedItem[] = []
  const lastSeen = new Map<string, number>()
  for (const [index, event] of (events ?? []).entries()) {
    const built = line(event)
    if (!built) continue
    const at = text(event.created_at)
    const ms = Date.parse(at)
    const key = `${built.title}|${built.detail ?? ''}`
    const previous = lastSeen.get(key)
    if (previous !== undefined && Number.isFinite(ms) && Math.abs(previous - ms) <= DEDUPE_WINDOW_MS) continue
    if (Number.isFinite(ms)) lastSeen.set(key, ms)
    out.push({id: `${at}-${index}`, at, ...built})
    if (out.length >= limit) break
  }
  return out
}

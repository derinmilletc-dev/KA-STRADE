// Coinbase manuel işlem ekranının SAF mantığı (React yok): onay metinleri, hata çevirisi, form doğrulama, görünürlük.
// Sunucu kuralların asıl sahibidir; buradaki kontroller yalnızca açık hataları göndermeden yakalar.

export const CONFIRM = {
  open: 'COINBASE EMİR AÇ',
  close: 'COINBASE POZİSYON KAPAT',
  change: 'COINBASE DEĞİŞTİR',
  saveKey: 'COINBASE ANAHTARINI KAYDET',
  deleteKey: 'COINBASE ANAHTARINI SİL',
} as const

export type ApiFailure = {
  status: number
  message: string
  code: string | null
  critical: boolean
  reasons: string[]
  retryAfterSeconds: number | null
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null

/** Sekme görünür mü? Sunucuda Coinbase kapalıysa (404) ya da bu üye izinli değilse (401/403) HİÇ görünmez.
 *  Yükleme hatası (424 + COINBASE_LOAD_FAILED) görünür: sessiz kalmasın. */
export function tabVisible(status: number, body: unknown): boolean {
  if (status === 200) return true
  const record = asRecord(body)
  return status === 424 && record?.code === 'COINBASE_LOAD_FAILED'
}

/** Gizli anahtar gibi uzun/gizli parçaları mesajdan temizler. */
export function redact(message: string, secrets: readonly string[] = []): string {
  let text = message.replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)/g, '[GİZLİ ANAHTAR]')
  for (const secret of [...secrets].filter(value => value.length >= 8).sort((a, b) => b.length - a.length)) {
    text = text.split(secret).join('[GİZLİ]')
  }
  return text
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      const record = asRecord(item)
      const field = Array.isArray(record?.loc) ? String(record?.loc.at(-1) ?? '') : ''
      return record && typeof record.msg === 'string' ? (field ? `${field}: ${record.msg}` : record.msg) : 'Alan doğrulanamadı.'
    })
    return parts.join(' · ')
  }
  return ''
}

/** Sunucu cevabını kullanıcıya gösterilecek açık hataya çevirir. Mesaj asla boş kalmaz. */
export function parseFailure(status: number, body: unknown, secrets: readonly string[] = []): ApiFailure {
  const record = asRecord(body)
  let message = detailText(record?.detail)
  if (!message) {
    message = status === 429 ? 'Çok sık istek gönderildi. Biraz bekleyip tekrar dene.'
      : status === 401 ? 'Oturum bulunamadı. Yeniden giriş yap.'
      : status === 403 ? 'Bu işlem için izniniz yok.'
      : status >= 500 ? `Sunucu hatası (HTTP ${status}). İşlemin gidip gitmediği belirsiz: Coinbase'de pozisyon ve emirleri kontrol et.`
      : `İşlem tamamlanamadı (HTTP ${status}).`
  }
  const reasons = Array.isArray(record?.reasons) ? record.reasons.filter((item): item is string => typeof item === 'string').map(item => redact(item, secrets)) : []
  const retry = Number(record?.retry_after_seconds)
  return {
    status,
    message: redact(message, secrets),
    code: typeof record?.code === 'string' ? record.code : null,
    critical: record?.critical === true,
    reasons,
    retryAfterSeconds: Number.isFinite(retry) && retry > 0 ? Math.ceil(retry) : null,
  }
}

/** İstek hiç cevap almadan koptuysa: emir gitmiş olabilir. Bunu açıkça söyler. */
export function networkFailure(error: unknown, mutating: boolean): ApiFailure {
  const timeout = error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name)
  const base = timeout ? 'İstek zaman aşımına uğradı.' : 'Sunucuya ulaşılamadı.'
  const extra = mutating ? ' Emrin gidip gitmediği BİLİNMİYOR: tekrar göndermeden önce "Koruma durumu"nu yenile ve Coinbase\'de kontrol et.' : ' Biraz sonra tekrar dene.'
  return {status: 0, message: base + extra, code: 'NETWORK', critical: mutating, reasons: [], retryAfterSeconds: null}
}

export function newRequestId(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID()
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

export const PRODUCT_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$/
export const isProductId = (value: string): boolean => PRODUCT_PATTERN.test(value.trim())
export const isPositiveDecimal = (value: string): boolean => /^\d+(\.\d+)?$/.test(value.trim()) && Number(value) > 0

export type Ticket = {productId: string; side: 'BUY' | 'SELL'; stopPrice: string; marginUsd: string; contracts: string; limitPrice: string}
export const emptyTicket = (): Ticket => ({productId: '', side: 'BUY', stopPrice: '', marginUsd: '', contracts: '', limitPrice: ''})

/** Göndermeden önce açık eksikleri yakalar (sunucu yine de tüm kuralları denetler). */
export function ticketProblems(ticket: Ticket): string[] {
  const problems: string[] = []
  if (!isProductId(ticket.productId)) problems.push('Ürün kodunu yaz (örnek: bir vadeli ürün kodu).')
  if (!isPositiveDecimal(ticket.stopPrice)) problems.push('Stop fiyatı zorunlu ve sıfırdan büyük bir sayı olmalı. Stop'+"'suz işlem açılmaz.")
  if (!isPositiveDecimal(ticket.marginUsd)) problems.push('Kullanılacak teminat (USD) sıfırdan büyük bir sayı olmalı.')
  if (ticket.limitPrice.trim() !== '' && !isPositiveDecimal(ticket.limitPrice)) problems.push('Limit giriş fiyatı sıfırdan büyük bir sayı olmalı (boş bırakırsan piyasa fiyatından girilir).')
  if (ticket.contracts.trim() !== '' && !/^[1-9]\d{0,5}$/.test(ticket.contracts.trim())) problems.push('Sözleşme sayısı tam sayı olmalı (boş bırakırsan en büyük güvenli miktar seçilir).')
  return problems
}

export function ticketPayload(ticket: Ticket): Record<string, unknown> {
  const body: Record<string, unknown> = {
    product_id: ticket.productId.trim(), side: ticket.side, stop_price: ticket.stopPrice.trim(), margin_usd: ticket.marginUsd.trim(),
  }
  if (ticket.limitPrice.trim() !== '') body.entry_limit_price = ticket.limitPrice.trim()
  if (ticket.contracts.trim() !== '') body.contracts = Number(ticket.contracts.trim())
  return body
}

export const sideLabel = (side: string): string => side === 'BUY' ? 'ALIŞ (long)' : side === 'SELL' ? 'SATIŞ (short)' : side

export type ProtectionState = 'protected' | 'UNPROTECTED' | 'MISMATCH'
export function protectionTone(state: string): 'ok' | 'bad' | 'warn' {
  return state === 'protected' ? 'ok' : state === 'UNPROTECTED' ? 'bad' : 'warn'
}

/** Onay metni tam eşleşmeli (baştaki/sondaki boşluk önemsiz). */
export const confirmMatches = (typed: string, expected: string): boolean => typed.trim() === expected

export type OrderAction = 'open' | 'close' | 'reduce' | 'stop' | 'cleanup-orphans'
export const mutatingPath = (path: string): boolean => /^\/orders\/(open|close|reduce|stop|cleanup-orphans)$/.test(path)

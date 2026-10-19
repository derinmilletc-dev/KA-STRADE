// Moderatör ve admin premium-talep ekranlarının paylaştığı yalnızca veri/ayrıştırma kodu (arayüz veya ağ çağrısı içermez).
export type PremiumItem = {
  id: string; status: string; days: number; reason: string; target_user_id: string
  target_email: string | null; target_name: string | null; requester_email: string | null
  created_at: string; expires_at: string; decision_note: string | null
}
export const statusText: Record<string, string> = {
  pending: 'Admin onayı bekliyor', approved: 'Onaylandı', rejected: 'Reddedildi',
  cancelled: 'Geri çekildi', expired: 'Süresi doldu',
}
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null
const text = (v: unknown) => (typeof v === 'string' ? v : null)

export function parseItem(value: unknown): PremiumItem {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.status !== 'string'
    || typeof value.days !== 'number' || typeof value.reason !== 'string' || typeof value.target_user_id !== 'string') {
    throw new Error('Premium talebi doğrulanamadı.')
  }
  return {
    id: value.id, status: value.status, days: value.days, reason: value.reason, target_user_id: value.target_user_id,
    target_email: text(value.target_email), target_name: text(value.target_name), requester_email: text(value.requester_email),
    created_at: text(value.created_at) ?? '', expires_at: text(value.expires_at) ?? '', decision_note: text(value.decision_note),
  }
}
export function parseList(value: unknown): PremiumItem[] {
  if (!isRecord(value) || !Array.isArray(value.items)) throw new Error('Premium talepleri doğrulanamadı.')
  return value.items.map(parseItem)
}


// Çizimlerin sunucuyla eşitlenmesi. Kural: coin başına son yazan kazanır (updated_at, ms). Sunucu erişilemezse yerel kayıt
// çalışmaya devam eder; çizim asla kaybolmaz ve hata ekranı çıkarmaz.
import type {Drawing} from './chart-drawings'

export type SyncRecord = {drawings: Drawing[]; updatedAt: number}

export const SYNC_LIMIT = 50

/** Yerel ve uzak kaydı birleştirir: daha yeni olan kazanır; eşitse yerel. `push` yerel kaydın sunucuya gitmesi gerektiğini söyler.
 *  Özel durum: eşitleme öncesi çizilmiş yerel çizimler (damga 0) sunucudaki kayıtla birleştirilir, böylece hiçbiri kaybolmaz. */
export function mergeDrawings(local: SyncRecord, remote: SyncRecord | null, now: number = Date.now()): {record: SyncRecord; push: boolean; changedLocal: boolean} {
  if (remote === null) return {record: local, push: local.drawings.length > 0, changedLocal: false}
  if (local.updatedAt === 0 && local.drawings.length > 0) {
    const known = new Set(remote.drawings.map(item => item.id))
    const extra = local.drawings.filter(item => !known.has(item.id))
    if (extra.length === 0) return {record: remote, push: false, changedLocal: remote.drawings.length !== local.drawings.length || remote.updatedAt > 0}
    return {record: {drawings: [...remote.drawings, ...extra].slice(-SYNC_LIMIT), updatedAt: Math.max(remote.updatedAt + 1, now)}, push: true, changedLocal: true}
  }
  if (remote.updatedAt > local.updatedAt) return {record: remote, push: false, changedLocal: true}
  if (local.updatedAt > remote.updatedAt) return {record: local, push: true, changedLocal: false}
  return {record: local, push: false, changedLocal: false}
}

/** `sanitize` dışarıdan verilir (chart-drawings.sanitizeDrawings): bu dosya saf kalır ve doğrudan test edilebilir. */
export function parseRemote(value: unknown, sanitize: (list: unknown) => Drawing[]): SyncRecord | null {
  if (typeof value !== 'object' || value === null) return null
  const raw = value as {drawings?: unknown; updated_at?: unknown}
  const updatedAt = typeof raw.updated_at === 'number' && Number.isFinite(raw.updated_at) ? raw.updated_at : null
  if (updatedAt === null) return null
  return {drawings: sanitize(raw.drawings), updatedAt}
}

const STAMP_KEY = 'kaistrade-chart-drawings-stamp'
export function readStamp(symbol: string): number {
  try {
    const all: unknown = JSON.parse(localStorage.getItem(STAMP_KEY) ?? '{}')
    const value = typeof all === 'object' && all !== null ? (all as Record<string, unknown>)[symbol] : undefined
    return typeof value === 'number' && Number.isFinite(value) ? value : 0
  } catch { return 0 }
}
export function writeStamp(symbol: string, stamp: number) {
  try {
    const all: unknown = JSON.parse(localStorage.getItem(STAMP_KEY) ?? '{}')
    const next = typeof all === 'object' && all !== null && !Array.isArray(all) ? {...(all as Record<string, number>)} : {}
    next[symbol] = stamp
    localStorage.setItem(STAMP_KEY, JSON.stringify(next))
  } catch { /* yerel kayıt kapalı olabilir */ }
}

const BASE = '/api/v22/studio/drawings'
export async function fetchRemote(symbol: string, sanitize: (list: unknown) => Drawing[]): Promise<SyncRecord | null | 'unavailable' | 'denied'> {
  try {
    const response = await fetch(`${BASE}?symbol=${encodeURIComponent(symbol)}`, {credentials: 'include'})
    if (response.status === 404) return null
    if (response.status === 401 || response.status === 403) return 'denied'
    if (!response.ok) return 'unavailable'
    return parseRemote(await response.json(), sanitize)
  } catch { return 'unavailable' }
}
export async function pushRemote(symbol: string, record: SyncRecord, keepalive = false): Promise<boolean> {
  try {
    const response = await fetch(BASE, {
      method: 'PUT', credentials: 'include', keepalive, headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol, drawings: record.drawings, updated_at: record.updatedAt}),
    })
    return response.ok
  } catch { return false }
}

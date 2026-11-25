// Grafik alarmını sunucudaki fiyat alarmına bağlar: sayfa kapalıyken de sunucu izler ve telefona bildirim gönderir.
// Saf kısım (alarmDiff, applyServerState) test edilir; ağ çağrıları sunucu yoksa sessizce yerel alarma düşer.
import type {Drawing} from './chart-drawings'

const BASE = '/api/v22/price-alerts'
export type ServerAlert = {id: number; product_id: string; level: string; direction: string}
export type ServerState = {alerts: ServerAlert[]; fired: ServerAlert[]}

type AlarmLine = Extract<Drawing, {type: 'hline'}>
const isArmed = (item: Drawing): item is AlarmLine => item.type === 'hline' && !!item.alarm && !item.alarm.fired

/** Önceki ve yeni çizim listesine göre sunucuda açılacak ve silinecek alarmlar. */
export function alarmDiff(prev: Drawing[], next: Drawing[]): {create: AlarmLine[]; remove: number[]} {
  const create = next.filter(isArmed).filter(item => !item.alarm?.serverId)
  const keep = new Set(next.filter(item => item.type === 'hline' && item.alarm?.serverId).map(item => (item as AlarmLine).alarm!.serverId))
  const remove = [...new Set(prev.filter(item => item.type === 'hline' && item.alarm?.serverId && !item.alarm.fired).map(item => (item as AlarmLine).alarm!.serverId!))].filter(id => !keep.has(id))
  return {create, remove}
}

/** Sunucudaki durumu yerel çizimlere işler: çalan alarmlar "çaldı" olur, sunucuda silinmiş olanların bağı kopar. */
export function applyServerState(list: Drawing[], state: ServerState): {list: Drawing[]; firedLevels: number[]; changed: boolean} {
  const firedIds = new Set(state.fired.map(item => item.id))
  const activeIds = new Set(state.alerts.map(item => item.id))
  const firedLevels: number[] = []
  let changed = false
  const out = list.map(item => {
    if (item.type !== 'hline' || !item.alarm?.serverId || item.alarm.fired) return item
    const id = item.alarm.serverId
    if (firedIds.has(id)) { changed = true; firedLevels.push(item.price); return {...item, alarm: {...item.alarm, fired: true}} }
    if (!activeIds.has(id)) { changed = true; return {...item, alarm: {side: item.alarm.side}} }
    return item
  })
  return {list: changed ? out : list, firedLevels, changed}
}

async function call(path: string, init?: RequestInit): Promise<Response | null> {
  try { return await fetch(`${BASE}${path}`, {credentials: 'include', ...init}) } catch { return null }
}

export async function fetchServerState(): Promise<ServerState | 'denied' | null> {
  const response = await call('/alerts')
  if (!response) return null
  if (response.status === 401 || response.status === 403) return 'denied'
  if (!response.ok) return null
  try {
    const body = await response.json() as Partial<ServerState>
    return {alerts: Array.isArray(body.alerts) ? body.alerts : [], fired: Array.isArray(body.fired) ? body.fired : []}
  } catch { return null }
}

export async function createServerAlarm(symbol: string, price: number): Promise<number | null> {
  const response = await call('/alerts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({product_id: symbol, level: String(price)})})
  if (!response || !response.ok) return null
  try { const body = await response.json() as {alert?: {id?: number}}; return typeof body.alert?.id === 'number' ? body.alert.id : null } catch { return null }
}

export async function deleteServerAlarm(id: number): Promise<void> { await call(`/alerts/${id}`, {method: 'DELETE'}) }

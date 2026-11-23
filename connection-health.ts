// Sunucu bağlantı sağlığı: tek yerde toplanır, arayüz bandı ve "geri gelince hemen yenile" buna bakar.
// Kural: tarayıcı çevrimdışıysa 'offline'; sunucuya art arda 2 ağ/5xx hatası olursa 'degraded'; herhangi bir başarılı yanıt 'ok'.
// 4xx (yetki, doğrulama) ve iptal edilen istekler bağlantı sorunu sayılmaz.
export type ConnectionState = 'ok' | 'degraded' | 'offline'
export type ConnectionSnapshot = {state: ConnectionState; since: number; recoveredAt: number | null}

const GATEWAY_STATUSES = new Set([502, 503, 504, 521, 522, 523, 524])
const FAILURES_TO_DEGRADE = 2
const FAILURE_WINDOW_MS = 30_000

export function isConnectionFailure(outcome: {status?: number; error?: unknown}): boolean {
  if (outcome.error !== undefined) {
    const name = (outcome.error as {name?: string} | null)?.name
    return name !== 'AbortError'
  }
  return typeof outcome.status === 'number' && GATEWAY_STATUSES.has(outcome.status)
}

export function createConnectionHealth(now: () => number = Date.now, initiallyOnline = true) {
  let snapshot: ConnectionSnapshot = {state: initiallyOnline ? 'ok' : 'offline', since: now(), recoveredAt: null}
  let failures: number[] = []
  const listeners = new Set<() => void>()
  const recoveryListeners = new Set<() => void>()

  const set = (state: ConnectionState) => {
    if (snapshot.state === state) return
    const recovered = snapshot.state !== 'ok' && state === 'ok'
    snapshot = {state, since: now(), recoveredAt: recovered ? now() : snapshot.recoveredAt}
    listeners.forEach(listener => listener())
    if (recovered) recoveryListeners.forEach(listener => listener())
  }

  return {
    get: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener) } },
    onRecovered(listener: () => void) { recoveryListeners.add(listener); return () => { recoveryListeners.delete(listener) } },
    reportSuccess() { failures = []; set('ok') },
    reportFailure() {
      const at = now()
      failures = [...failures.filter(time => at - time <= FAILURE_WINDOW_MS), at]
      if (snapshot.state === 'ok' && failures.length >= FAILURES_TO_DEGRADE) set('degraded')
    },
    setBrowserOnline(online: boolean) {
      if (!online) { set('offline'); return }
      // İnternet döndü: sunucu henüz doğrulanmadı, ilk başarılı yanıta kadar 'degraded' say.
      if (snapshot.state === 'offline') set('degraded')
    },
  }
}

export const connectionHealth = createConnectionHealth(Date.now, typeof navigator === 'undefined' ? true : navigator.onLine !== false)

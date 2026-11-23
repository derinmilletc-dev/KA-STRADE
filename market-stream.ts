// Ortak Binance Spot piyasa yayını: tek WebSocket, birden çok abone.
// Aynı akışı (ör. btcusdt@ticker) isteyen herkes aynı bağlantıyı paylaşır; son abone ayrılınca akış bırakılır.
// Bağlantı kopunca üstel bekleme ile yeniden bağlanır, sessizce ölen bağlantıyı (veri gelmiyor) yakalar,
// sekme gizlenince kapanır, görününce / internet gelince hemen açılır.
export type StreamStatus = 'connecting' | 'live' | 'offline'
type SocketLike = {
  readyState: number
  send(data: string): void
  close(): void
  onopen: ((event?: unknown) => void) | null
  onmessage: ((event: {data: unknown}) => void) | null
  onclose: ((event?: unknown) => void) | null
  onerror: ((event?: unknown) => void) | null
}
type Timers = {set(callback: () => void, ms: number): unknown; clear(id: unknown): void}
type Options = {
  url?: string
  createSocket?: (url: string) => SocketLike
  timers?: Timers
  now?: () => number
  isHidden?: () => boolean
  silenceMs?: number
}
type Subscriber = {streams: Set<string>; onMessage: (stream: string, data: unknown) => void; onStatus?: (status: StreamStatus) => void}

const OPEN = 1
export const SPOT_STREAM_URL = 'wss://stream.binance.com:9443/stream'

export function createMarketStream(options: Options = {}) {
  const url = options.url ?? SPOT_STREAM_URL
  const createSocket = options.createSocket ?? ((target: string) => new WebSocket(target) as unknown as SocketLike)
  const timers: Timers = options.timers ?? {set: (callback, ms) => setTimeout(callback, ms), clear: id => clearTimeout(id as ReturnType<typeof setTimeout>)}
  const now = options.now ?? Date.now
  const isHidden = options.isHidden ?? (() => typeof document !== 'undefined' && document.hidden)
  const silenceMs = options.silenceMs ?? 20_000

  const subscribers = new Set<Subscriber>()
  const refCount = new Map<string, number>()
  let socket: SocketLike | null = null
  let status: StreamStatus = 'connecting'
  let attempt = 0
  let retryTimer: unknown = null
  let watchdog: unknown = null
  let lastMessage = 0
  let requestId = 0

  const setStatus = (next: StreamStatus) => {
    if (status === next) return
    status = next
    subscribers.forEach(subscriber => subscriber.onStatus?.(next))
  }
  const send = (method: 'SUBSCRIBE' | 'UNSUBSCRIBE', params: string[]) => {
    if (!params.length || !socket || socket.readyState !== OPEN) return
    socket.send(JSON.stringify({method, params, id: ++requestId}))
  }
  const stopWatchdog = () => { if (watchdog !== null) { timers.clear(watchdog); watchdog = null } }
  const startWatchdog = () => {
    stopWatchdog()
    const check = () => {
      watchdog = null
      if (!socket || !refCount.size) return
      if (now() - lastMessage > silenceMs) { socket.close(); return }
      watchdog = timers.set(check, Math.max(1000, silenceMs / 4))
    }
    watchdog = timers.set(check, Math.max(1000, silenceMs / 4))
  }
  const clearRetry = () => { if (retryTimer !== null) { timers.clear(retryTimer); retryTimer = null } }
  const shouldRun = () => refCount.size > 0 && !isHidden()

  const connect = () => {
    clearRetry()
    if (socket || !shouldRun()) return
    if (status === 'connecting') setStatus('connecting')
    let created: SocketLike
    try { created = createSocket(url) } catch { scheduleRetry(); return }
    socket = created
    created.onopen = () => {
      if (socket !== created) return
      attempt = 0; lastMessage = now()
      send('SUBSCRIBE', [...refCount.keys()])
      setStatus('live'); startWatchdog()
    }
    created.onmessage = event => {
      if (socket !== created) return
      lastMessage = now()
      let payload: {stream?: string; data?: unknown}
      try { payload = JSON.parse(String(event.data)) } catch { return }
      if (!payload.stream) return
      const stream = payload.stream
      subscribers.forEach(subscriber => { if (subscriber.streams.has(stream)) subscriber.onMessage(stream, payload.data) })
    }
    created.onerror = () => { try { created.close() } catch { /* zaten kapanıyor */ } }
    created.onclose = () => {
      if (socket !== created) return
      socket = null; stopWatchdog()
      if (!refCount.size || isHidden()) return
      setStatus('offline'); scheduleRetry()
    }
  }
  function scheduleRetry() {
    clearRetry()
    if (!shouldRun()) return
    const delay = Math.min(30_000, 1000 * 2 ** attempt++)
    retryTimer = timers.set(connect, delay)
  }
  const shutdown = () => {
    clearRetry(); stopWatchdog()
    const current = socket; socket = null
    if (current) { current.onclose = null; try { current.close() } catch { /* yok say */ } }
  }

  const resume = () => { if (!shouldRun()) return; attempt = 0; if (!socket) connect() }
  if (typeof document !== 'undefined' && typeof window !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (isHidden()) { shutdown(); if (refCount.size) setStatus('offline') } else resume()
    })
    window.addEventListener('online', resume)
  }

  return {
    status: () => status,
    subscribe(streams: string[], onMessage: Subscriber['onMessage'], onStatus?: Subscriber['onStatus']): () => void {
      const subscriber: Subscriber = {streams: new Set(streams.map(stream => stream.toLowerCase())), onMessage, onStatus}
      const added: string[] = []
      subscriber.streams.forEach(stream => {
        const count = refCount.get(stream) ?? 0
        refCount.set(stream, count + 1)
        if (count === 0) added.push(stream)
      })
      subscribers.add(subscriber)
      onStatus?.(status)
      if (socket) send('SUBSCRIBE', added); else connect()
      return () => {
        if (!subscribers.delete(subscriber)) return
        const removed: string[] = []
        subscriber.streams.forEach(stream => {
          const count = (refCount.get(stream) ?? 1) - 1
          if (count <= 0) { refCount.delete(stream); removed.push(stream) } else refCount.set(stream, count)
        })
        send('UNSUBSCRIBE', removed)
        if (!refCount.size) { shutdown(); status = 'connecting' }
      }
    },
  }
}

export const spotStream = createMarketStream()

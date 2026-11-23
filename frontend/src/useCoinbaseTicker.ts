// Açık pozisyonların anlık fiyatı: Coinbase'in herkese açık WebSocket "ticker" kanalı (saniyede birkaç güncelleme).
// Kopunca üstel bekleyişle yeniden bağlanır; sekme gizliyken kapanır. Ekran 400 ms'de bir yenilenir (gereksiz render yok).
import {useEffect, useRef, useState} from 'react'
import {applyL2, parseTicker, subscribeMessage, topOfBook, type Books, type TickQuote} from './coinbase-ticker'

const WS_URL = 'wss://advanced-trade-ws.coinbase.com'
const FLUSH_MS = 400
const MAX_BACKOFF_MS = 15000

export function useCoinbaseTicker(ids: string[], enabled: boolean): {quotes: Record<string, TickQuote>; at: number | null; connected: boolean} {
  const key = ids.join(',')
  const [state, setState] = useState<{quotes: Record<string, TickQuote>; at: number | null; connected: boolean}>({quotes: {}, at: null, connected: false})
  const buffer = useRef<Record<string, TickQuote>>({})

  useEffect(() => {
    if (!enabled || key === '' || typeof WebSocket === 'undefined') { setState(current => current.connected || current.at !== null ? {quotes: {}, at: null, connected: false} : current); return }
    const wanted = key.split(',')
    let socket: WebSocket | null = null
    let alive = true
    let retry = 0
    let retryTimer = 0
    let flushTimer = 0
    let books: Books = {}
    const dirty = new Set<string>()
    const flush = () => {
      flushTimer = 0
      const batch = buffer.current
      buffer.current = {}
      // Defter varsa fiyat = en iyi alış/satış ortası (her defter değişiminde); yoksa son işlem fiyatı.
      const now = Date.now()
      for (const id of dirty) { const top = topOfBook(books[id], now); if (top) batch[id] = top }
      dirty.clear()
      if (!alive || Object.keys(batch).length === 0) return
      setState(current => ({quotes: {...current.quotes, ...batch}, at: Date.now(), connected: true}))
    }
    const connect = () => {
      if (!alive || document.hidden) return
      try { socket = new WebSocket(WS_URL) } catch { schedule(); return }
      socket.onopen = () => { retry = 0; books = {}; for (const text of subscribeMessage(wanted)) socket?.send(text) }
      socket.onmessage = event => {
        let parsed: unknown
        try { parsed = JSON.parse(String(event.data)) } catch { return }
        const changed = applyL2(books, parsed)
        for (const id of changed) dirty.add(id)
        const ticks = parseTicker(parsed)
        for (const [id, tick] of Object.entries(ticks)) if (!books[id] || books[id].bids.size === 0) buffer.current[id] = tick
        if (changed.length === 0 && Object.keys(ticks).length === 0) return
        if (!flushTimer) flushTimer = window.setTimeout(flush, FLUSH_MS)
      }
      socket.onerror = () => { try { socket?.close() } catch { /* zaten kapalı */ } }
      socket.onclose = () => { socket = null; if (alive) { setState(current => current.connected ? {...current, connected: false} : current); schedule() } }
    }
    const schedule = () => {
      if (!alive) return
      window.clearTimeout(retryTimer)
      retryTimer = window.setTimeout(connect, Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry++))
    }
    const visibility = () => {
      if (document.hidden) { try { socket?.close() } catch { /* zaten kapalı */ } } else if (!socket) { retry = 0; connect() }
    }
    document.addEventListener('visibilitychange', visibility)
    connect()
    return () => {
      alive = false
      document.removeEventListener('visibilitychange', visibility)
      window.clearTimeout(retryTimer); window.clearTimeout(flushTimer)
      try { socket?.close() } catch { /* zaten kapalı */ }
    }
  }, [enabled, key])

  return state
}

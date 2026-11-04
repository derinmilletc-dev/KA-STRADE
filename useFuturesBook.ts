import {useEffect, useRef, useState} from 'react'
import {type FuturesBook, parseBookTicker} from './futures-book'

// Açık pozisyonun coini için Binance Futures en iyi alış/satış fiyatını canlı izler. Yalnız okunur, herkese açık veri.
// Birincil kaynak: bookTicker yayını (her fiyat değişiminde gelir). Yayın 1,5 sn susarsa REST ile saniyede bir yoklanır;
// yayın geri gelince yoklama kendiliğinden durur. Telefon uykudan dönünce beklemeden yeniden bağlanır.
const WS_URLS = ['wss://fstream.binance.com/public/ws', 'wss://fstream.binance.com/market/ws'] as const
const REST_URL = 'https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol='
const SAFE = /^[A-Z0-9]{2,30}$/
const RENDER_MS = 200
const SILENT_MS = 1500
const POLL_MS = 1000

export function useFuturesBook(symbol: string, enabled = true): FuturesBook | null {
  const [book, setBook] = useState<FuturesBook | null>(null)
  const latest = useRef<FuturesBook | null>(null)

  useEffect(() => {
    setBook(null); latest.current = null
    const upper = symbol.toUpperCase()
    if (!enabled || !SAFE.test(upper) || typeof WebSocket === 'undefined') return
    let active = true
    let socket: WebSocket | undefined
    let retry: number | undefined
    let source = 0
    let delay = 1000
    let lastWs = 0
    let polling = false
    let shown: FuturesBook | null = null

    const take = (next: FuturesBook | null, fromWs: boolean) => {
      if (!active || !next) return
      if (fromWs) lastWs = next.at
      if (!latest.current || next.at >= latest.current.at) latest.current = next
    }
    const render = window.setInterval(() => { if (latest.current !== shown) { shown = latest.current; setBook(shown) } }, RENDER_MS)

    const poll = async () => {
      if (!active || polling || document.hidden || Date.now() - lastWs < SILENT_MS) return
      polling = true
      try {
        const response = await fetch(`${REST_URL}${upper}`, {cache: 'no-store'})
        if (response.ok) take(parseBookTicker(await response.json(), upper, Date.now()), false)
      } catch { /* ağ yoksa bir sonraki turda yeniden denenir */ } finally { polling = false }
    }
    const poller = window.setInterval(() => void poll(), POLL_MS)

    const connect = () => {
      if (!active || document.hidden) return
      let opened = false
      try { socket = new WebSocket(`${WS_URLS[source]}/${upper.toLowerCase()}@bookTicker`) } catch { source = (source + 1) % WS_URLS.length; retry = window.setTimeout(connect, delay); return }
      const openTimer = window.setTimeout(() => { if (!opened) socket?.close() }, 6000)
      socket.onopen = () => { opened = true; window.clearTimeout(openTimer); delay = 1000 }
      socket.onmessage = event => {
        try { take(parseBookTicker(JSON.parse(String(event.data)), upper, Date.now()), true) } catch { /* bozuk mesajı yok say */ }
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => {
        window.clearTimeout(openTimer)
        if (!active) return
        if (!opened || Date.now() - lastWs > 5000) source = (source + 1) % WS_URLS.length
        retry = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 15000)
      }
    }
    connect()
    void poll()

    // Bağlı görünüp veri göndermeyen yayını yakala: 5 sn susarsa kapat, diğer adresle yeniden bağlan.
    const watchdog = window.setInterval(() => {
      if (!active || document.hidden) return
      if (socket?.readyState === WebSocket.OPEN && Date.now() - lastWs > 5000) socket.close()
    }, 2500)
    const resume = () => {
      if (!active || document.hidden) return
      void poll()
      const state = socket?.readyState
      if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) return
      window.clearTimeout(retry); delay = 1000; connect()
    }
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('online', resume)
    return () => {
      active = false
      window.clearInterval(render); window.clearInterval(poller); window.clearInterval(watchdog); window.clearTimeout(retry)
      document.removeEventListener('visibilitychange', resume); window.removeEventListener('online', resume)
      socket?.close()
    }
  }, [symbol, enabled])

  return book && book.symbol === symbol.toUpperCase() ? book : null
}

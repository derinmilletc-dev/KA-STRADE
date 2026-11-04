import {useEffect, useMemo, useRef, useState} from 'react'
import {bookTickerUrl, parseBookTicker, streamSymbols, type QuoteMap} from './live-quote'

// Açık pozisyonların sembolleri için Binance'ten en iyi alış/satış fiyatını canlı dinler (yalnız okuma; emir yok).
// Ekran en çok 4 kez/sn güncellenir. Sayfa gizliyken bağlantı kapanır; bağlantı koparsa artan aralıkla yeniden dener.
// Az işlem gören coinde en iyi fiyat saniyelerce değişmeyebilir (fiyat aynı demektir); bu yüzden tazelik bağlantıya
// bakılarak ölçülür: 10 sn hiç mesaj gelmezse ya da bağlantı koparsa fiyatlar boşaltılır ve ekran "gecikmeli" uyarısı gösterir.
const FLUSH_MS = 500
const SILENCE_MS = 10_000
const EMPTY: QuoteMap = {}

export function useLiveQuotes(symbols: ReadonlyArray<unknown>, enabled: boolean): {quotes: QuoteMap; now: number; connected: boolean} {
  const key = useMemo(() => streamSymbols(symbols).join(','), [symbols])
  const [quotes, setQuotes] = useState<QuoteMap>({})
  const [now, setNow] = useState(() => Date.now())
  const [connected, setConnected] = useState(false)
  const pending = useRef<QuoteMap>({})
  const lastMessage = useRef(0)
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden')

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [])

  useEffect(() => {
    const list = key ? key.split(',') : []
    const url = enabled && visible ? bookTickerUrl(list) : null
    if (!url || typeof WebSocket === 'undefined') { setConnected(false); return }
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let closed = false
    const open = () => {
      try { socket = new WebSocket(url) } catch { schedule(); return }
      socket.onopen = () => { attempt = 0; lastMessage.current = Date.now(); setConnected(true) }
      socket.onmessage = event => {
        lastMessage.current = Date.now()
        try {
          const parsed = parseBookTicker(JSON.parse(String(event.data)), Date.now())
          if (parsed) pending.current[parsed.symbol] = parsed.quote
        } catch { /* bozuk mesaj atlanır */ }
      }
      socket.onerror = () => { try { socket?.close() } catch { /* zaten kapalı */ } }
      socket.onclose = () => { setConnected(false); pending.current = {}; setQuotes({}); if (!closed) schedule() }
    }
    const schedule = () => {
      if (closed) return
      const delay = Math.min(15000, 500 * 2 ** attempt)
      attempt += 1
      retry = setTimeout(open, delay)
    }
    open()
    const flush = setInterval(() => {
      const at = Date.now()
      setNow(at)
      // Sessiz kalmış (yarı açık) bağlantı canlı sayılmaz: kapatılır, yeniden bağlanılır.
      if (socket && socket.readyState === WebSocket.OPEN && at - lastMessage.current > SILENCE_MS) { try { socket.close() } catch { /* zaten kapalı */ } }
      const batch = pending.current
      if (Object.keys(batch).length === 0) return
      pending.current = {}
      setQuotes(current => ({...current, ...batch}))
    }, FLUSH_MS)
    return () => {
      closed = true
      clearInterval(flush)
      if (retry) clearTimeout(retry)
      if (socket) { socket.onclose = null; try { socket.close() } catch { /* zaten kapalı */ } }
      setConnected(false)
      pending.current = {}
      setQuotes({})
    }
  }, [key, enabled, visible])

  return {quotes: connected ? quotes : EMPTY, now, connected}
}

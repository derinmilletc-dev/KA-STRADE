import { useEffect, useRef, useState } from 'react'
import { spotStream } from './market-stream'
import { API_BASE } from './api'

type TickerPayload = {c?:string;o?:string}
export type LiveTicker = {
  symbol: string
  price: number
  openPrice: number
  changePercent: number
  history: number[]
  direction: 'up'|'down'|null
  flash: 'up'|'down'|null
}
export type LiveTickerStatus = 'connecting'|'live'|'offline'
export type LiveTickerData = Record<string,LiveTicker>

const REST_URL = 'https://api.binance.com/api/v3/ticker/24hr?symbols='

function buildTicker(symbol:string,price:number,openPrice:number,history:number[] = [],previous?:LiveTicker):LiveTicker {
  const changePercent = openPrice ? ((price - openPrice) / openPrice) * 100 : 0
  const direction = previous && price !== previous.price ? price > previous.price ? 'up' : 'down' : previous?.direction || null
  return {symbol,price,openPrice,changePercent,history:[...history.slice(-59),price],direction,flash:direction}
}

export function useLiveTickers(symbols:string[]):{data:LiveTickerData;status:LiveTickerStatus} {
  const [data,setData] = useState<LiveTickerData>({})
  const [status,setStatus] = useState<LiveTickerStatus>('connecting')
  const pendingRef = useRef(new Map<string,{price:number;openPrice:number}>())
  const dataRef = useRef<LiveTickerData>({})
  const pollingTimerRef = useRef<number|null>(null)
  const flushTimerRef = useRef<number|null>(null)
  const stoppedRef = useRef(false)

  useEffect(() => {
    const normalizedSymbols = symbols.map(symbol => symbol.toUpperCase())
    stoppedRef.current = false

    const clearTimer = (ref:{current:number|null}) => { if (ref.current !== null) { window.clearTimeout(ref.current); ref.current = null } }
    const stopPolling = () => clearTimer(pollingTimerRef)
    const schedulePolling = () => {
      stopPolling()
      const poll = async () => {
        if (stoppedRef.current || document.hidden) return
        try {
          // Tarayıcı Binance'e ulaşamazsa (operatör engeli, uygulama içi tarayıcı) aynı veri sunucudan gelir.
          let response = await fetch(`${REST_URL}${encodeURIComponent(JSON.stringify(normalizedSymbols))}`).catch(() => null)
          if (!response?.ok) response = await fetch(`${API_BASE}/public/ticker?symbols=${encodeURIComponent(normalizedSymbols.join(','))}`)
          if (!response.ok) throw new Error('Market data unavailable')
          const payload = await response.json() as Array<{symbol:string;lastPrice:string;openPrice:string}>
          payload.forEach(item => queue(item.symbol,Number(item.lastPrice),Number(item.openPrice)))
          setStatus('live')
        } catch { setStatus('offline') }
        if (!stoppedRef.current && !document.hidden) pollingTimerRef.current = window.setTimeout(poll,10000)
      }
      void poll()
    }
    const flush = () => {
      flushTimerRef.current = null
      if (!pendingRef.current.size) return
      const pending = new Map(pendingRef.current)
      pendingRef.current.clear()
      setData(previous => {
        const next = {...previous}
        pending.forEach((value,symbol) => { next[symbol] = buildTicker(symbol,value.price,value.openPrice,previous[symbol]?.history,previous[symbol]) })
        dataRef.current = next
        return next
      })
      window.setTimeout(() => setData(previous => { const next = {...previous}; pending.forEach((_,symbol) => { if (next[symbol]) next[symbol] = {...next[symbol],flash:null} }); dataRef.current = next; return next }), 420)
    }
    function queue(symbol:string,price:number,openPrice:number) {
      if (!Number.isFinite(price) || !Number.isFinite(openPrice) || price <= 0) return
      pendingRef.current.set(symbol.toUpperCase(),{price,openPrice})
      if (flushTimerRef.current === null) flushTimerRef.current = window.setTimeout(flush,250)
    }
    // Tek ortak soket (market-stream.ts): aynı akışı isteyen diğer bileşenlerle paylaşılır; kopma/yeniden bağlanma orada yönetilir.
    const unsubscribe = spotStream.subscribe(
      normalizedSymbols.map(symbol => `${symbol.toLowerCase()}@miniTicker`),
      (name,payload) => {
        const ticker = payload as TickerPayload | undefined
        const symbol = name.split('@')[0]?.toUpperCase() || ''
        if (symbol && ticker?.c && ticker.o) queue(symbol,Number(ticker.c),Number(ticker.o))
      },
      next => {
        if (stoppedRef.current) return
        if (next === 'live') { stopPolling(); setStatus('live') }
        else if (next === 'offline') { setStatus('offline'); schedulePolling() }
        else if (!Object.keys(dataRef.current).length) setStatus('connecting')
      },
    )
    // Sekme geri gelince soket hâlâ kapalıysa REST yoklaması yeniden başlar.
    const onVisible = () => { if (!document.hidden && spotStream.status() !== 'live') schedulePolling() }
    document.addEventListener('visibilitychange',onVisible)
    return () => {
      stoppedRef.current = true
      document.removeEventListener('visibilitychange',onVisible)
      unsubscribe(); stopPolling(); clearTimer(flushTimerRef)
    }
  },[symbols.join(',')])

  return {data,status}
}

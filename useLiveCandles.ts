import {useEffect, useMemo, useRef, useState} from 'react'

// Grafiğin son mumunu Binance Futures'ın herkese açık fiyat yayınıyla canlı günceller.
// Yalnız okunur veri: hesap, anahtar veya emir yok. Yayın koparsa normal 30 sn yenileme sürer.
type Candle = {time: number; open: number; high: number; low: number; close: number; volume: number}
type Tick = Candle & {key: string}

const STREAM = 'wss://fstream.binance.com/ws'
const SAFE = /^[A-Z0-9]{2,30}$/
const INTERVALS = new Set(['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '1w'])

export function mergeLiveCandle(candles: Candle[], tick: Candle | null): Candle[] {
  const last = candles[candles.length - 1]
  if (!tick || !last || tick.time < last.time) return candles
  if (tick.time === last.time) return [...candles.slice(0, -1), tick]
  return [...candles.slice(1), tick]
}

export function useLiveCandles(symbol: string, interval: string, candles: Candle[]) {
  const key = `${symbol}|${interval}`
  const [tick, setTick] = useState<Tick | null>(null)
  const [connected, setConnected] = useState(false)
  const latest = useRef<Tick | null>(null)

  useEffect(() => {
    setTick(null); setConnected(false); latest.current = null
    const upper = symbol.toUpperCase()
    if (!SAFE.test(upper) || !INTERVALS.has(interval) || typeof WebSocket === 'undefined') return
    let socket: WebSocket | undefined
    let retry: number | undefined
    let delay = 3000
    let active = true
    let shown: Tick | null = null
    const flush = window.setInterval(() => { if (latest.current !== shown) { shown = latest.current; setTick(shown) } }, 500)
    const connect = () => {
      if (!active) return
      try { socket = new WebSocket(`${STREAM}/${upper.toLowerCase()}@kline_${interval}`) } catch { return }
      socket.onopen = () => { if (active) { delay = 3000; setConnected(true) } }
      socket.onerror = () => socket?.close()
      socket.onclose = () => {
        if (!active) return
        setConnected(false)
        retry = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 60000)
      }
      socket.onmessage = event => {
        if (!active) return
        try {
          const k = (JSON.parse(String(event.data)) as {k?: Record<string, unknown>}).k
          if (!k || String(k.s).toUpperCase() !== upper || k.i !== interval) return
          const candle = {time: Math.floor(Number(k.t) / 1000), open: Number(k.o), high: Number(k.h), low: Number(k.l), close: Number(k.c), volume: Number(k.v)}
          if (Object.values(candle).every(Number.isFinite)) latest.current = {...candle, key: `${upper}|${interval}`}
        } catch { /* bozuk mesajı yok say */ }
      }
    }
    connect()
    return () => { active = false; window.clearInterval(flush); window.clearTimeout(retry); socket?.close() }
  }, [symbol, interval])

  const current = tick && tick.key === `${symbol.toUpperCase()}|${interval}` ? tick : null
  const merged = useMemo(() => mergeLiveCandle(candles, current), [candles, current])
  return {candles: merged, price: current?.close ?? null, live: connected && current !== null && key !== ''}
}

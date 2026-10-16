import {useEffect, useMemo, useRef, useState} from 'react'

// Grafiğin son mumunu Binance Futures'ın herkese açık fiyat yayınıyla canlı günceller.
// Yalnız okunur veri: hesap, anahtar veya emir yok. Yayın koparsa normal 30 sn yenileme sürer.
type Candle = {time: number; open: number; high: number; low: number; close: number; volume: number}
type Tick = Candle & {key: string; priceOnly: boolean}

// Önce Futures yayını (grafikle aynı piyasa). Bağlanamazsa veya veri gelmezse Spot yayınına geçer; o durumda yalnız fiyat kullanılır.
// Binance 2026-04-23'ten sonra Futures piyasa yayınlarını yalnız /market yolundan gönderiyor; eski /ws bağlanıyor ama veri vermiyor.
const SOURCES = [{url: 'wss://fstream.binance.com/market/ws', priceOnly: false}, {url: 'wss://stream.binance.com:9443/ws', priceOnly: true}] as const
const SAFE = /^[A-Z0-9]{2,30}$/
const INTERVALS = new Set(['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '1w'])

export function mergeLiveCandle(candles: Candle[], tick: (Candle & {priceOnly?: boolean}) | null): Candle[] {
  const last = candles[candles.length - 1]
  if (!tick || !last || tick.time < last.time) return candles
  const price = tick.close
  const next = tick.priceOnly
    ? tick.time === last.time
      ? {...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price)}
      : {time: tick.time, open: last.close, high: Math.max(last.close, price), low: Math.min(last.close, price), close: price, volume: 0}
    : {time: tick.time, open: tick.open, high: tick.high, low: tick.low, close: tick.close, volume: tick.volume}
  return tick.time === last.time ? [...candles.slice(0, -1), next] : [...candles.slice(1), next]
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
    let source = 0
    let active = true
    let shown: Tick | null = null
    const flush = window.setInterval(() => { if (latest.current !== shown) { shown = latest.current; setTick(shown) } }, 500)
    const connect = () => {
      if (!active) return
      const {url, priceOnly} = SOURCES[source]
      let opened = false
      let received = false
      try { socket = new WebSocket(`${url}/${upper.toLowerCase()}@kline_${interval}`) } catch { source = (source + 1) % SOURCES.length; retry = window.setTimeout(connect, 1000); return }
      // Açılmayan bağlantı 8 sn sonra bırakılır; böylece engellenen yayında takılı kalmaz.
      const openTimer = window.setTimeout(() => { if (!opened) socket?.close() }, 8000)
      // Bağlanıp 10 sn içinde hiç fiyat göndermeyen yayın da bozuk sayılır ve diğer kaynağa geçilir.
      let silentTimer: number | undefined
      socket.onopen = () => {
        opened = true; window.clearTimeout(openTimer)
        if (!active) return
        delay = 3000; setConnected(true)
        silentTimer = window.setTimeout(() => { if (!received) { opened = false; socket?.close() } }, 10000)
      }
      socket.onerror = () => socket?.close()
      socket.onclose = () => {
        window.clearTimeout(openTimer); window.clearTimeout(silentTimer)
        if (!active) return
        setConnected(false)
        if (!opened) { source = (source + 1) % SOURCES.length; if (source !== 0) { retry = window.setTimeout(connect, 500); return } }
        retry = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 60000)
      }
      socket.onmessage = event => {
        if (!active) return
        try {
          const k = (JSON.parse(String(event.data)) as {k?: Record<string, unknown>}).k
          if (!k || String(k.s).toUpperCase() !== upper || k.i !== interval) return
          const candle = {time: Math.floor(Number(k.t) / 1000), open: Number(k.o), high: Number(k.h), low: Number(k.l), close: Number(k.c), volume: Number(k.v)}
          if (!Object.values(candle).every(Number.isFinite)) return
          received = true
          latest.current = {...candle, key: `${upper}|${interval}`, priceOnly}
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

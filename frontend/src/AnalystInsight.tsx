import {useEffect, useMemo, useState} from 'react'
import '../../manual-analysis-sheet.css'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import CandleReadBox from './CandleReadBox'
import CoinbaseAlerts from './CoinbaseAlerts'
import {binanceAlertCall, usePriceAlerts} from './price-alert-api'
import {intervalSeconds, readCandles, windowText, type ReadCandle} from './coin-live-read'

// Coin Analist sayfasında seçili coinin altına: seçili süreye göre otomatik mum yorumu + fiyat alarmı.
// Yalnız okuma ve bildirim: emir göndermez. Mumlar herkese açık fiyat verisinden gelir, 30 sn'de bir tazelenir.
type Props = {symbol: string; display: string; price: number | null; interval: string}

const parse = (rows: unknown): ReadCandle[] => (Array.isArray(rows) ? rows : []).map(row => {
  const item = row as Record<string, unknown>
  return {time: Number(item.time), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), volume: Number(item.volume ?? 0)}
}).filter(candle => [candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))

export default function AnalystInsight({symbol, display, price, interval}: Props) {
  const {alerts, reload} = usePriceAlerts(binanceAlertCall)
  const [state, setState] = useState<{key: string; candles: ReadCandle[] | null; error: boolean}>({key: '', candles: null, error: false})
  const key = `${symbol}|${interval}`

  useEffect(() => {
    if (!symbol || !intervalSeconds(interval)) return
    let alive = true
    let controller = new AbortController()
    const load = () => {
      controller.abort()
      controller = new AbortController()
      const current = controller
      void fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(symbol)}?interval=${interval}&limit=60`, {cache: 'no-store', signal: current.signal}).then(async response => {
        if (!response.ok) throw new Error('klines')
        const candles = parse(await response.json())
        if (alive && !current.signal.aborted) setState({key, candles, error: false})
      }).catch(error => { if (error?.name !== 'AbortError' && alive) setState(old => old.key === key && old.candles ? old : {key, candles: null, error: true}) })
    }
    setState(old => old.key === key ? old : {key, candles: null, error: false})
    load()
    const timer = window.setInterval(load, 30000)
    return () => { alive = false; controller.abort(); window.clearInterval(timer) }
  }, [symbol, interval, key])

  const seconds = intervalSeconds(interval)
  const candles = state.key === key ? state.candles : null
  const read = useMemo(() => candles && seconds ? readCandles(candles, windowText(seconds, candles.length)) : null, [candles, seconds])
  const failed = state.key === key && state.error
  const num = Number(price)

  return <div className="mxai">
    {read ? <CandleReadBox read={read} label={interval.toUpperCase()}/>
      : <p className="mxaiNote" role="status">{!seconds ? 'Bu süre için otomatik yorum yok.' : failed ? 'Mum verisi alınamadı, yorum çıkarılamadı.' : candles ? 'Yorum için yeterli mum yok.' : 'Otomatik yorum hazırlanıyor…'}</p>}
    <CoinbaseAlerts productId={symbol} display={display} price={Number.isFinite(num) && num > 0 ? num : null} alerts={alerts} onChanged={reload} call={binanceAlertCall}/>
  </div>
}

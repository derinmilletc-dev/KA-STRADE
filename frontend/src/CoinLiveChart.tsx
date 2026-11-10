import {useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import {useLiveCandles} from '../../useLiveCandles'
import {buildChart, changeText, INTERVAL_LABEL, type ChartInterval} from './coin-live-chart'
import {formatPrice} from './position-view'

// Manuel işlemde seçilen/incelenen coinin canlı hareketi: son 60 mum (REST) + Binance'ten canlı akan son mum.
// Yalnız okuma: hesap, anahtar ya da emir yok. Yayın koparsa "gecikmeli" yazar; grafik son bilinen veriyle durur.
type Candle = {time: number; open: number; high: number; low: number; close: number; volume: number}
type Props = {symbol: string; compact?: boolean; paused?: boolean}

const parse = (rows: unknown): Candle[] => (Array.isArray(rows) ? rows : []).map(row => {
  const item = row as Record<string, unknown>
  return {time: Number(item.time), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), volume: Number(item.volume ?? 0)}
}).filter(candle => [candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))

export default function CoinLiveChart({symbol, compact = false, paused = false}: Props) {
  const [interval, setIntervalValue] = useState<ChartInterval>('1m')
  const [reload, setReload] = useState(0)
  const [state, setState] = useState<{key: string; candles: Candle[] | null; error: boolean}>({key: '', candles: null, error: false})
  const key = `${symbol}|${interval}`

  useEffect(() => {
    if (paused || !symbol) return
    const controller = new AbortController()
    setState({key, candles: null, error: false})
    void fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(symbol)}?interval=${interval}&limit=60`, {cache: 'no-store', signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('klines')
      const candles = parse(await response.json())
      if (candles.length < 2) throw new Error('empty')
      if (!controller.signal.aborted) setState({key, candles, error: false})
    }).catch(error => { if (error?.name !== 'AbortError' && !controller.signal.aborted) setState({key, candles: null, error: true}) })
    return () => controller.abort()
  }, [symbol, interval, paused, reload])  // eslint-disable-line react-hooks/exhaustive-deps

  const base = state.key === key ? state.candles : null
  const live = useLiveCandles(paused ? '' : symbol, interval, base ?? [])
  const shape = useMemo(() => buildChart(live.candles, 300, compact ? 64 : 120), [live.candles, compact])
  const failed = state.key === key && state.error

  return <div className="mxlc" data-compact={compact || undefined} data-tone={shape?.tone} role="group" aria-label={`${symbol} canlı grafik`}>
    <div className="mxlcHead">
      <span className="mxlcPrice">{shape ? formatPrice(shape.last) : '—'}{shape && <em>{changeText(shape.changePct)} <small>son {INTERVAL_LABEL[interval]} × 60</small></em>}</span>
      <span className="mxlcLive" data-live={live.live || undefined}>{shape ? live.live ? '● Canlı' : 'Gecikmeli' : ''}</span>
    </div>
    {shape
      ? <svg className="mxlcSvg" viewBox={`0 0 300 ${compact ? 64 : 120}`} preserveAspectRatio="none" width="100%" height={compact ? 64 : 120} role="img" aria-label={`${symbol} fiyat çizgisi, ${changeText(shape.changePct)}`}>
        <path className="mxlcArea" d={shape.area}/>
        <path className="mxlcLine" d={shape.line} fill="none" vectorEffect="non-scaling-stroke"/>
        <circle className="mxlcDot" cx={shape.lastX} cy={shape.lastY} r="3.2" vectorEffect="non-scaling-stroke"/>
      </svg>
      : <p className="mxlcNote" role="status">{failed ? <>Grafik alınamadı. <button type="button" onClick={() => setReload(value => value + 1)}>Tekrar dene</button></> : 'Grafik yükleniyor…'}</p>}
    {!compact && <div className="mxlcTabs" role="group" aria-label="Grafik süresi">{(Object.keys(INTERVAL_LABEL) as ChartInterval[]).map(item => <button type="button" key={item} aria-pressed={interval === item} onClick={() => setIntervalValue(item)}>{INTERVAL_LABEL[item]}</button>)}</div>}
  </div>
}

import {useEffect, useId, useMemo, useRef, useState, type PointerEvent} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import {useLiveCandles} from '../../useLiveCandles'
import {buildChart, changeText, clockText, INTERVAL_LABEL, nearestIndex, type ChartInterval} from './coin-live-chart'
import {formatPrice} from './position-view'

// Manuel işlemde seçilen/incelenen coinin canlı hareketi: son 60 mum (REST) + Binance'ten canlı akan son mum.
// Parmağı grafik üstünde gezdirince o andaki fiyat ve saat görünür; bırakınca canlı fiyata döner.
// Yalnız okuma: hesap, anahtar ya da emir yok. Yayın koparsa "Gecikmeli" yazar; grafik son bilinen veriyle durur.
type Candle = {time: number; open: number; high: number; low: number; close: number; volume: number}
type Props = {symbol: string; compact?: boolean; paused?: boolean}

const WIDTH = 300
const parse = (rows: unknown): Candle[] => (Array.isArray(rows) ? rows : []).map(row => {
  const item = row as Record<string, unknown>
  return {time: Number(item.time), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), volume: Number(item.volume ?? 0)}
}).filter(candle => [candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))

export default function CoinLiveChart({symbol, compact = false, paused = false}: Props) {
  const [interval, setIntervalValue] = useState<ChartInterval>('1m')
  const [reload, setReload] = useState(0)
  const [scrub, setScrub] = useState<number | null>(null)
  const [state, setState] = useState<{key: string; candles: Candle[] | null; error: boolean}>({key: '', candles: null, error: false})
  const plot = useRef<HTMLDivElement | null>(null)
  const gradient = `mxlcg${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const key = `${symbol}|${interval}`
  const height = compact ? 72 : 132

  useEffect(() => {
    setScrub(null)
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
  const shape = useMemo(() => buildChart(live.candles, WIDTH, height, compact ? 5 : 8), [live.candles, height, compact])
  const failed = state.key === key && state.error
  const active = shape && scrub !== null ? shape.points[Math.min(scrub, shape.points.length - 1)] : null
  const shownValue = active ? active.value : shape?.last ?? null
  const shownChange = shape && shownValue !== null ? (shownValue - shape.first) / shape.first * 100 : null
  const tone = shownChange !== null && shownChange < 0 ? 'down' : 'up'

  const move = (event: PointerEvent<HTMLDivElement>) => {
    const box = plot.current?.getBoundingClientRect()
    if (!shape || !box || box.width <= 0) return
    setScrub(nearestIndex((event.clientX - box.left) / box.width, shape.points.length))
  }
  const guideLeft = active ? `${active.x / WIDTH * 100}%` : null

  return <div className="mxlc" data-compact={compact || undefined} data-tone={tone} role="group" aria-label={`${symbol} canlı grafik`}>
    <div className="mxlcHead">
      <span className="mxlcPrice">{shownValue !== null ? formatPrice(shownValue) : '—'}</span>
      {shownChange !== null && <span className="mxlcChip" data-tone={tone}>{changeText(shownChange)}</span>}
      <span className="mxlcLive" data-live={!active && live.live ? '1' : undefined}>
        {active ? clockText(active.time) : shape ? live.live ? <><i className="mxlcPulse" aria-hidden="true"/>Canlı</> : 'Gecikmeli' : ''}
      </span>
    </div>
    {shape
      ? <div className="mxlcPlot" ref={plot} style={{height}} onPointerDown={move} onPointerMove={move} onPointerLeave={() => setScrub(null)} onPointerCancel={() => setScrub(null)} onPointerUp={() => setScrub(null)}>
        <svg className="mxlcSvg" viewBox={`0 0 ${WIDTH} ${height}`} preserveAspectRatio="none" width="100%" height={height} role="img" aria-label={`${symbol} fiyat çizgisi, ${changeText(shape.changePct)}`}>
          <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".34"/><stop offset="100%" stopColor="currentColor" stopOpacity="0"/></linearGradient></defs>
          {[0.25, 0.5, 0.75].map(part => <line key={part} className="mxlcGrid" x1="0" x2={WIDTH} y1={height * part} y2={height * part} vectorEffect="non-scaling-stroke"/>)}
          <path className="mxlcArea" d={shape.area} fill={`url(#${gradient})`}/>
          <path className="mxlcLine" d={shape.line} fill="none" vectorEffect="non-scaling-stroke"/>
        </svg>
        {!compact && <><span className="mxlcAxis" data-pos="top">{formatPrice(shape.max)}</span><span className="mxlcAxis" data-pos="bottom">{formatPrice(shape.min)}</span></>}
        {guideLeft && <i className="mxlcGuide" style={{left: guideLeft}} aria-hidden="true"/>}
        <i className="mxlcDot" data-scrub={active ? '1' : undefined} style={{left: `${(active ? active.x : shape.lastX) / WIDTH * 100}%`, top: `${(active ? active.y : shape.lastY) / height * 100}%`}} aria-hidden="true"/>
      </div>
      : <p className="mxlcNote" style={{minHeight: height}} role="status">{failed ? <>Grafik alınamadı. <button type="button" onClick={() => setReload(value => value + 1)}>Tekrar dene</button></> : <><span className="mxlcSkel" aria-hidden="true"/>Grafik yükleniyor…</>}</p>}
    {!compact && <div className="mxlcFoot">
      <small>{shape ? `Son 60 mum · parmağınla gez` : ''}</small>
      <div className="mxlcTabs" role="group" aria-label="Grafik süresi">{(Object.keys(INTERVAL_LABEL) as ChartInterval[]).map(item => <button type="button" key={item} aria-pressed={interval === item} onClick={() => setIntervalValue(item)}>{INTERVAL_LABEL[item]}</button>)}</div>
    </div>}
  </div>
}

import {useEffect, useLayoutEffect, useRef, useState, type RefCallback, type ReactNode} from 'react'
import {Activity, BarChart3, Cable, Camera, Check, ChevronDown, Crosshair, Eraser, LockKeyhole, Minus, Trash2, TrendingUp, Bell, Power, RefreshCw, Settings2, SlidersHorizontal, Wallet, X, ZoomIn, ZoomOut, RotateCcw} from 'lucide-react'
import {type DecisionAnalysis, type TradeDecision, type TriggerMonitor} from './masterTradeDecision'
import {MasterTradeMetricTile, MasterTradeMetricVisual} from './MasterTradeLayout'
import {PremiumBoundary, useMemberAccess} from './premium-access'
import {trLabel} from './master-trade-labels'
import {analysisChartLabels, analysisChartRange, analysisPrice, analysisValue, autoTradePresentation, triggerPresentation, MASTER_MTF_INTERVALS} from './master-trade-presentation'
import {liveBlockerMessage, type SharedLiveStatus} from './frontend/src/LiveTradingPanel'
import {CoinIcon} from './src/components/CoinIcon'
import {MasterTradeAccordions} from './MasterTradeAccordions'
import {MasterTradeMarketWatch} from './MasterTradeMarketWatch'
import type {MarketScore} from './master-market-data'
import type {MasterMarketFeed} from './useMasterMarketQuotes'
import {TRADING_TIMEFRAMES} from './account-settings-api'
import {captureChart} from './chart-ai-client'
import {useLiveCandles} from './useLiveCandles'
import {drawingId, readDrawings, saveDrawings, type ChartPoint, type DrawMode, type Drawing} from './chart-drawings'
import './master-trade-reference.css'

type Candle = {time: number; open: number; high: number; low: number; close: number; volume: number}
type Props = {
  assistantSlotRef?: RefCallback<HTMLDivElement>;
  chartAiControl?: ReactNode;
  symbol: string; interval: string; query: string; onQuery: (value: string) => void;
  scores: readonly MarketScore[];
  marketFeed: MasterMarketFeed;
  onMarket: (symbol: string) => void; onInterval: (value: string) => void;
  onNavigate: (tab: 'analiz' | 'canli' | 'pozisyonlar' | 'baglanti') => void;
  onAutoTrade: () => void; onRefresh: () => void; refreshing: boolean;
  candles: Candle[]; analysis: DecisionAnalysis | null; decision: TradeDecision; trigger: TriggerMonitor;
  live: SharedLiveStatus | null; price: number | null; change?: number | null; volume?: number | null;
  levelsVisible: boolean; volumeVisible: boolean; onLevels: () => void; onVolume: () => void;
  timeline: Array<{time: string; message: string}>; error: string; dataIssue: string;
  analysisUpdatedAt?: string | null;
}

const numeric = (value: number | null | undefined, suffix = '', decimals = 0) => typeof value === 'number' && Number.isFinite(value)
  ? `${value.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: 2})}${suffix}` : '—'
const directionTone = (direction: string) => direction === 'LONG' ? 'positive' : direction === 'SHORT' ? 'negative' : 'neutral'
const candleTime = (time: number) => new Date(time < 1e12 ? time * 1000 : time).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'})

type ChartPrefs = {grid: boolean; axis: boolean; crosshair: boolean}
const CHART_PREFS_KEY = 'kaistrade-chart-prefs'
function readChartPrefs(): ChartPrefs {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(CHART_PREFS_KEY) ?? '{}')
    const value = typeof saved === 'object' && saved !== null ? saved as Record<string, unknown> : {}
    return {grid: value.grid !== false, axis: value.axis !== false, crosshair: value.crosshair === true}
  } catch {
    return {grid: true, axis: true, crosshair: false}
  }
}
function saveChartPrefs(prefs: ChartPrefs) {
  try { localStorage.setItem(CHART_PREFS_KEY, JSON.stringify(prefs)) } catch { /* tarayıcı kaydı kapalı olabilir */ }
}

// Analiz değerleri sunucudan 30 sn'de bir gelir; ne kadar önce güncellendiğini saniye saniye gösterir.
function UpdatedAgo({at}: {at: string | null | undefined}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const time = at ? new Date(at).getTime() : NaN
  if (!Number.isFinite(time)) return null
  const seconds = Math.max(0, Math.round((now - time) / 1000))
  return <span className="refUpdatedAgo" title="Analiz değerleri 30 saniyede bir yenilenir" aria-label={`Analiz ${seconds < 60 ? `${seconds} saniye` : `${Math.floor(seconds / 60)} dakika`} önce güncellendi`}><i/>{seconds < 60 ? `${seconds} sn önce` : `${Math.floor(seconds / 60)} dk önce`}</span>
}

// Mum kapanışına kalan süre: son mumun açılış zamanı + zaman dilimi uzunluğu. Saat, bilgisayarın saatine göre hesaplanır.
const INTERVAL_SECONDS: Record<string, number> = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '8h': 28800, '12h': 43200, '1d': 86400, '1w': 604800}
function CandleTimer({time, interval}: {time: number | undefined; interval: string}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const length = INTERVAL_SECONDS[interval]
  if (typeof time !== 'number' || !Number.isFinite(time) || !length) return null
  const closeAt = (time < 1e12 ? time * 1000 : time) + length * 1000
  const left = Math.max(0, Math.ceil((closeAt - now) / 1000))
  const pad = (value: number) => String(value).padStart(2, '0')
  const days = Math.floor(left / 86400), hours = Math.floor(left % 86400 / 3600), minutes = Math.floor(left % 3600 / 60), seconds = left % 60
  const text = `${days > 0 ? `${days}g ` : ''}${days > 0 || hours > 0 ? `${pad(hours)}:` : ''}${pad(minutes)}:${pad(seconds)}`
  return <span className="refCandleTimer" role="timer" data-urgent={left <= 10} title="Bu mumun kapanışına kalan süre"><i/>Mum {text}</span>
}

function ReferenceBars({candles, neutral = false}: {candles: Candle[]; neutral?: boolean}) {
  if (!candles.length) return <span>—</span>
  const changes = candles.slice(-12).map(candle => Math.abs(candle.close - candle.open))
  const maximum = Math.max(...changes, Number.EPSILON)
  return <svg className="refMetricBars" data-neutral={neutral} viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">{changes.map((change, index) => {
    const height = change / maximum * 25
    return <rect key={index} x={index * 8.3} y={28 - height} width="5.8" height={height}/>
  })}</svg>
}

function ReferenceChart({candles, symbol, interval, analysis, trigger, price, levelsVisible, volumeVisible, dataIssue, refreshing, premium, prefs, live, drawings, drawMode, onDraw, onErase, onDrawDone}: Pick<Props, 'candles' | 'symbol' | 'interval' | 'analysis' | 'trigger' | 'price' | 'levelsVisible' | 'volumeVisible' | 'dataIssue' | 'refreshing'> & {premium: boolean; prefs: ChartPrefs; live: boolean; drawings: Drawing[]; drawMode: DrawMode; onDraw: (drawing: Drawing) => void; onErase: (id: string) => void; onDrawDone: () => void}) {
  const [hover, setHover] = useState<number | null>(null)
  const [hoverY, setHoverY] = useState<number | null>(null)
  const [anchor, setAnchor] = useState<ChartPoint | null>(null)
  const [zoom, setZoom] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{x: number; shift: number; moved: boolean} | null>(null)
  const zoomState = useRef({count: 80, maxCount: 80, shift: 0, length: 0, plotWidth: 1})
  useEffect(() => { setZoom(null); setOffset(0) }, [symbol, interval])
  useEffect(() => {
    // Fare tekerleği grafiği yakınlaştırır/uzaklaştırır; sayfa kaymaz.
    const element = canvas.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest('svg[data-chart-ai-capture]')) return
      event.preventDefault()
      const {count, maxCount} = zoomState.current
      const next = Math.round(count * (event.deltaY < 0 ? .85 : 1.18))
      setZoom(Math.max(10, Math.min(maxCount, next === count ? count + (event.deltaY < 0 ? -1 : 1) : next)))
    }
    element.addEventListener('wheel', onWheel, {passive: false})
    return () => element.removeEventListener('wheel', onWheel)
  }, [])
  const canvas = useRef<HTMLDivElement>(null)
  useEffect(() => { if (drawMode !== 'trend') setAnchor(null) }, [drawMode])
  const [size, setSize] = useState({width: 900, height: 360})
  useLayoutEffect(() => {
    const element = canvas.current
    if (!element) return
    const measure = () => {
      const {width, height} = element.getBoundingClientRect()
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : {width, height})
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const plotWidth = Math.max(30, size.width - 176)
  const plotHeight = Math.max(1, size.height - 26)
  const priceHeight = plotHeight * .79
  // Yakınlaştırma: zoom = görünen mum sayısı (null = varsayılan), offset = sağdan kaç mum geride durulduğu (0 = en son mum).
  const fit = Math.min(80, Math.max(1, Math.floor((plotWidth - 20) / 7)))
  const maxCount = Math.max(1, Math.min(candles.length, Math.floor((plotWidth - 20) / 2)))
  const count = Math.max(Math.min(10, candles.length), Math.min(maxCount, zoom ?? fit))
  const shift = Math.max(0, Math.min(offset, candles.length - count))
  const visible = candles.slice(Math.max(0, candles.length - shift - count), candles.length - shift)
  const zoomed = zoom !== null || shift > 0
  const resetView = () => { setZoom(null); setOffset(0) }
  zoomState.current = {count, maxCount, shift, length: candles.length, plotWidth}
  const levels = [
    {label: 'TP3', value: analysis?.tp3, tone: 'tp'}, {label: 'TP2', value: analysis?.tp2, tone: 'tp'},
    {label: 'TP1', value: analysis?.tp1, tone: 'tp'}, {label: 'RESISTANCE', value: analysis?.resistance, tone: 'resistance'},
    {label: 'TRIGGER', value: trigger.triggerPrice, tone: 'trigger'}, {label: 'ENTRY', value: analysis?.entry, tone: 'entry'},
    {label: 'SUPPORT', value: analysis?.support, tone: 'support'}, {label: 'SL', value: analysis?.stop_loss, tone: 'stop'},
  ].filter((line): line is {label: string; value: number; tone: string} => typeof line.value === 'number' && Number.isFinite(line.value))
  const range = analysisChartRange(visible)
  const labels = range ? analysisChartLabels([
    ...(premium && levelsVisible ? levels : []),
    ...(typeof price === 'number' && Number.isFinite(price) ? [{label: '', value: price, tone: 'current'}] : []),
  ], range, size.height, priceHeight) : []
  const y = (value: number) => range ? (range.high - value) / (range.high - range.low) * priceHeight : 0
  const x = (index: number) => 10 + (index + .5) / visible.length * (plotWidth - 20)
  const bodyWidth = Math.max(1, Math.min(14, (plotWidth - 20) / visible.length * .7))
  const maximumVolume = Math.max(...visible.map(candle => candle.volume), 1)
  // Çizimler fiyat+zaman olarak saklanır; ekranda mum aralığına göre yerleştirilir.
  const step = visible.length > 1 ? visible[visible.length - 1].time - visible[visible.length - 2].time || 60 : 60
  const timeX = (time: number) => visible.length ? x((time - visible[0].time) / step) : 0
  const pointAt = (viewX: number, viewY: number): ChartPoint | null => {
    if (!range || !visible.length || viewY < 0 || viewY > priceHeight || viewX < 0 || viewX > plotWidth) return null
    const index = (viewX - 10) / (plotWidth - 20) * visible.length - .5
    return {time: visible[0].time + index * step, price: range.high - viewY / priceHeight * (range.high - range.low)}
  }
  const hoverPoint = hover !== null && hoverY !== null && drawMode === 'trend' && anchor ? pointAt(x(hover), hoverY) : null
  const selected = hover === null ? visible[visible.length - 1] : visible[Math.min(hover, visible.length - 1)]
  return <>
    <div className="refOhlc"><b>{symbol} · {interval}</b>{live && <span className="refLiveBadge" title="Son mum canlı güncelleniyor"><i/>CANLI</span>}{selected ? <><span>O {analysisPrice(selected.open)}</span><span>H {analysisPrice(selected.high)}</span><span>L {analysisPrice(selected.low)}</span><span>C {analysisPrice(selected.close)}</span></> : <span>—</span>}</div>
    <div className="refChartCanvas" ref={canvas}>
      {!range || dataIssue ? <div className="refChartEmpty" role="status"><div><BarChart3 aria-hidden="true"/><strong>{refreshing ? 'Veriler yükleniyor...' : 'Analiz yok'}</strong><p>{dataIssue || (refreshing ? `${symbol} mumları ve analizi alınıyor.` : 'Bu sembol için yeterli veri yok')}</p></div></div> : <>
        <svg data-chart-ai-capture="reference" viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="none" data-axis-low={range.low} data-axis-high={range.high} data-price-height={priceHeight} data-plot-height={plotHeight} aria-label={`${symbol} ${interval} candlestick chart`} data-draw-mode={drawMode} data-dragging={dragging} onClick={event => {
          if (drawMode !== 'hline' && drawMode !== 'trend' && drawMode !== 'alarm') return
          const box = event.currentTarget.getBoundingClientRect()
          const point = pointAt((event.clientX - box.left) / box.width * size.width, (event.clientY - box.top) / box.height * size.height)
          if (!point) return
          if (drawMode === 'alarm') {
            if (typeof price !== 'number' || !Number.isFinite(price)) return
            onDraw({id: drawingId(), type: 'hline', price: point.price, alarm: {side: point.price > price ? 'above' : 'below'}}); onDrawDone()
          } else if (drawMode === 'hline') { onDraw({id: drawingId(), type: 'hline', price: point.price}); onDrawDone() }
          else if (!anchor) setAnchor(point)
          else { onDraw({id: drawingId(), type: 'trend', a: anchor, b: point}); setAnchor(null); onDrawDone() }
        }} onDoubleClick={() => { if (drawMode === 'none') resetView() }}
        onPointerDown={event => {
          // Sol tuşla sürükleyince grafik geçmişe/ileriye kayar (çizim modunda değilken).
          if (drawMode !== 'none' || event.button !== 0) return
          drag.current = {x: event.clientX, shift: zoomState.current.shift, moved: false}
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerUp={event => { drag.current = null; setDragging(false); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
        onPointerCancel={() => { drag.current = null; setDragging(false) }}
        onMouseLeave={() => {setHover(null); setHoverY(null)}} onMouseMove={event => {
          if (drag.current) {
            const box = event.currentTarget.getBoundingClientRect()
            const {count, length, plotWidth: width} = zoomState.current
            const candlesMoved = Math.round((event.clientX - drag.current.x) / box.width * size.width / ((width - 20) / count))
            if (candlesMoved !== 0) { drag.current.moved = true; setDragging(true) }
            setOffset(Math.max(0, Math.min(length - count, drag.current.shift + candlesMoved)))
          }
          const box = event.currentTarget.getBoundingClientRect()
          setHoverY((event.clientY - box.top) / box.height * size.height)
          const index = Math.floor((event.clientX - box.left - 10) / (plotWidth - 20) * visible.length)
          setHover(Math.max(0, Math.min(visible.length - 1, index)))
        }}>
          <g className="refChartGrid" data-grid={prefs.grid}>{Array.from({length: 7}, (_, index) => {
            const tickY = 11 + index / 6 * (priceHeight - 22)
            return <g key={index}>{prefs.grid && <line x1="0" x2={plotWidth} y1={tickY} y2={tickY}/>}{prefs.axis && !labels.some(label => Math.abs(label.y - tickY) <= 21) && <text className="refPriceTick" data-price-y={tickY} x={plotWidth + 10} y={tickY} dominantBaseline="middle">{analysisPrice(range.high - tickY / priceHeight * (range.high - range.low))}</text>}</g>
          })}{prefs.grid && Array.from({length: 6}, (_, index) => <line key={`v${index}`} x1={index * plotWidth / 5} x2={index * plotWidth / 5} y1="0" y2={plotHeight}/>)}</g>
          {visible.map((candle, index) => <g key={candle.time} className={`refCandle ${candle.close >= candle.open ? 'up' : 'down'}`}><line x1={x(index)} x2={x(index)} y1={y(candle.high)} y2={y(candle.low)} vectorEffect="non-scaling-stroke"/><rect x={x(index) - bodyWidth / 2} y={y(Math.max(candle.open, candle.close))} width={bodyWidth} height={Math.max(1, Math.abs(y(candle.open) - y(candle.close)))}/>{volumeVisible && <rect className="refVolume" x={x(index) - bodyWidth / 2} y={plotHeight - candle.volume / maximumVolume * plotHeight * .15} width={bodyWidth} height={candle.volume / maximumVolume * plotHeight * .15}/>}</g>)}
          <g className="masterTradeChartLabels">{([] as typeof labels).map(label => <g key={`${label.label}-${label.value}`} data-level={label.label} className={`chartLevel level-${label.tone}`}><line x1="0" x2={plotWidth} y1={label.priceY} y2={label.priceY}/>{Math.abs(label.y - label.priceY) > 1 && <path d={`M ${plotWidth + 2} ${label.priceY} L ${plotWidth + 8} ${label.y}`} className="masterTradeLabelLeader"/>}</g>)}</g>
          <g className="refTimeAxis">{Array.from({length: 6}, (_, index) => {const candle = visible[Math.round(index / 5 * (visible.length - 1))]; return <text key={index} x={10 + index / 5 * (plotWidth - 20)} y={size.height - 6} textAnchor={index === 0 ? 'start' : index === 5 ? 'end' : 'middle'}>{candleTime(candle.time)}</text>})}</g>
          <g className="refDrawings" data-erasing={drawMode === 'erase'}>{drawings.map(drawing => {
            const line = drawing.type === 'hline'
              ? {x1: 0, x2: plotWidth, y1: y(drawing.price), y2: y(drawing.price)}
              : {x1: timeX(drawing.a.time), x2: timeX(drawing.b.time), y1: y(drawing.a.price), y2: y(drawing.b.price)}
            const alarm = drawing.type === 'hline' ? drawing.alarm : undefined
            return <g key={drawing.id} className={`refDrawing refDrawing--${drawing.type}${alarm ? ' refDrawing--alarm' : ''}${alarm?.fired ? ' refDrawing--fired' : ''}`}>
              <line {...line}/>
              {drawing.type === 'hline' && <text x={plotWidth - 6} y={line.y1 - 5} textAnchor="end">{alarm ? (alarm.fired ? 'ALARM ÇALDI · ' : 'ALARM · ') : ''}{analysisPrice(drawing.price)}</text>}
              {/* Sağ tık her zaman siler; sol tık yalnız silgi seçiliyken siler. */}
              <line {...line} className="refDrawingHit" aria-label="Çizimi sil (sağ tık)" onContextMenu={event => {event.preventDefault(); event.stopPropagation(); onErase(drawing.id)}}
                onClick={event => {if (drawMode !== 'erase') return; event.stopPropagation(); onErase(drawing.id)}}><title>Silmek için sağ tıkla</title></line>
            </g>
          })}{anchor && <g className="refDrawing refDrawing--trend refDrawing--draft"><circle cx={timeX(anchor.time)} cy={y(anchor.price)} r="3.5"/>{hoverPoint && <line x1={timeX(anchor.time)} y1={y(anchor.price)} x2={timeX(hoverPoint.time)} y2={y(hoverPoint.price)}/>}</g>}</g>
          {hover !== null && <line className="refCrosshair" x1={x(Math.min(hover, visible.length - 1))} x2={x(Math.min(hover, visible.length - 1))} y1="0" y2={plotHeight}/>}
          {prefs.crosshair && hover !== null && hoverY !== null && hoverY >= 0 && hoverY <= priceHeight && <g className="refCrosshairFull" aria-hidden="true">
            <line x1="0" x2={plotWidth} y1={hoverY} y2={hoverY}/>
            <rect className="refCrosshairTag" x={plotWidth + 4} y={hoverY - 10} width="96" height="20" rx="4"/>
            <text x={plotWidth + 10} y={hoverY} dominantBaseline="middle">{analysisPrice(range.high - hoverY / priceHeight * (range.high - range.low))}</text>
            <rect className="refCrosshairTag" x={Math.max(0, x(Math.min(hover, visible.length - 1)) - 26)} y={size.height - 20} width="52" height="18" rx="4"/>
            <text x={x(Math.min(hover, visible.length - 1))} y={size.height - 7} textAnchor="middle">{candleTime(visible[Math.min(hover, visible.length - 1)].time)}</text>
          </g>}
        </svg>
        <div className="refZoomTools" role="group" aria-label="Grafik yakınlaştırma">
          <button type="button" aria-label="Uzaklaştır" title="Uzaklaştır (fare tekerleği)" disabled={count >= maxCount} onClick={() => setZoom(Math.min(maxCount, Math.round(count * 1.3)))}><ZoomOut/></button>
          <button type="button" aria-label="Yakınlaştır" title="Yakınlaştır (fare tekerleği)" disabled={count <= 10} onClick={() => setZoom(Math.max(10, Math.round(count / 1.3)))}><ZoomIn/></button>
          {zoomed && <button type="button" className="refZoomReset" title="Varsayılan görünüme ve son mumlara dön (çift tık)" onClick={resetView}><RotateCcw/>Son mumlar</button>}
        </div>
        <div className="masterTradeChartPills">{labels.map(label => <span key={`${label.label}-${label.value}`} className={`level-${label.tone}`} data-edge={label.edge} data-price-y={label.priceY} data-label-y={label.y} style={{top: label.y - 11}} title={`${label.label} ${analysisPrice(label.value)}`}>{label.edge === 'above' ? '↑ ' : label.edge === 'below' ? '↓ ' : ''}{label.label}{label.label ? ' ' : ''}{analysisPrice(label.value)}</span>)}</div>
      </>}
    </div>
  </>
}

export default function MasterTradeReference(props: Props) {
  const {premium, ready, userId, openUpgrade} = useMemberAccess()
  const [notice, setNotice] = useState('')
  const [prefs, setPrefs] = useState<ChartPrefs>(readChartPrefs)
  const [downloading, setDownloading] = useState(false)
  const liveChart = useLiveCandles(props.symbol, props.interval, props.candles)
  const [drawMode, setDrawMode] = useState<DrawMode>('none')
  const [drawings, setDrawings] = useState<{symbol: string; list: Drawing[]}>(() => ({symbol: props.symbol, list: readDrawings(props.symbol)}))
  const symbolDrawings = drawings.symbol === props.symbol ? drawings.list : readDrawings(props.symbol)
  const changeDrawings = (next: Drawing[]) => { setDrawings({symbol: props.symbol, list: next}); saveDrawings(props.symbol, next) }
  const pickTool = (mode: DrawMode) => setDrawMode(current => current === mode ? 'none' : mode)
  // --- Fiyat alarmı: yalnız bu sayfa açıkken ve canlı fiyat akarken çalışır; tarayıcıda saklanır. ---
  const audio = useRef<AudioContext | null>(null)
  const [alarmToast, setAlarmToast] = useState('')
  const primeAlarm = () => {
    try {
      audio.current ??= new AudioContext()
      void audio.current.resume()
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') void Notification.requestPermission()
    } catch { /* ses veya bildirim kapalı olabilir; ekrandaki uyarı yine çıkar */ }
  }
  const beep = () => {
    try {
      const ctx = audio.current
      if (!ctx) return
      void ctx.resume();
      [0, .3, .6].forEach(delay => {
        const osc = ctx.createOscillator(), gain = ctx.createGain()
        osc.frequency.value = 880; gain.gain.value = .15
        osc.connect(gain); gain.connect(ctx.destination)
        osc.start(ctx.currentTime + delay); osc.stop(ctx.currentTime + delay + .18)
      })
    } catch { /* ses çalınamadı */ }
  }
  useEffect(() => {
    // Sayfa yenilenince tarayıcı sesi engeller; ilk tıklamada ses izni alınır.
    const arm = () => primeAlarm()
    document.addEventListener('pointerdown', arm, {once: true})
    return () => document.removeEventListener('pointerdown', arm)
  }, [])
  const alarmPrice = liveChart.price
  useEffect(() => {
    if (typeof alarmPrice !== 'number' || !Number.isFinite(alarmPrice)) return
    const hit = symbolDrawings.filter(item => item.type === 'hline' && item.alarm && !item.alarm.fired
      && (item.alarm.side === 'above' ? alarmPrice >= item.price : alarmPrice <= item.price))
    if (!hit.length) return
    changeDrawings(symbolDrawings.map(item => hit.includes(item) && item.type === 'hline' && item.alarm ? {...item, alarm: {...item.alarm, fired: true}} : item))
    const message = hit.map(item => item.type === 'hline' ? `${props.symbol} fiyatı ${analysisPrice(item.price)} seviyesine ulaştı` : '').join(' · ')
    setAlarmToast(message); beep()
    try { if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification('KaisTrade fiyat alarmı', {body: message}) } catch { /* bildirim gösterilemedi */ }
  }, [alarmPrice])
  useEffect(() => { setDrawMode('none') }, [props.symbol])
  useEffect(() => {
    if (drawMode === 'none') return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setDrawMode('none') }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawMode])
  useEffect(() => {
    // Açık grafik menüleri (Göstergeler, Grafik ayarları) dışarı tıklanınca veya Esc ile kapanır.
    const menus = () => document.querySelectorAll<HTMLDetailsElement>('.masterReferenceAnalysis :is(.refChartSettings, .refIndicators)[open]')
    const onPointer = (event: PointerEvent) => {
      menus().forEach(menu => { if (!(event.target instanceof Node && menu.contains(event.target))) menu.open = false })
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') menus().forEach(menu => { menu.open = false }) }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('pointerdown', onPointer); document.removeEventListener('keydown', onKey) }
  }, [])
  const updatePrefs = (change: Partial<ChartPrefs>) => setPrefs(current => {const next = {...current, ...change}; saveChartPrefs(next); return next})
  const downloadChart = async () => {
    setDownloading(true)
    try {
      const svg = document.querySelector<SVGSVGElement>('.masterReferenceAnalysis svg[data-chart-ai-capture="reference"]')
      const blob = await captureChart(svg)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${props.symbol}-${props.interval}-${new Date().toISOString().slice(0, 16).replace(':', '-')}.png`
      document.body.appendChild(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure: unknown) {
      setNotice(failure instanceof Error ? failure.message : 'Grafik görüntüsü alınamadı.')
    } finally {
      setDownloading(false)
    }
  }
  const {decision, trigger, analysis, live} = props
  // Fiyat kartı canlı yayından beslenir; 24 saatlik değişim, son bilinen değişimden türetilen açılış fiyatına göre hesaplanır.
  const cardPrice = liveChart.price ?? props.price
  const dayOpen = typeof props.price === 'number' && typeof props.change === 'number' && props.change > -100 ? props.price / (1 + props.change / 100) : null
  const cardChange = liveChart.price !== null && dayOpen ? (liveChart.price / dayOpen - 1) * 100 : props.change
  const available = Boolean(analysis && props.candles.length >= 2)
  const locked = live?.real_trading_locked !== false
  const recovery = Boolean(live?.emergency?.active || live?.reconciliation_required || live?.recovery_error || live?.execution_state === 'UNKNOWN')
  const gatesReady = Boolean(premium && live?.connected && !locked && live.armed && live.authorization?.valid === true && live.consent?.active === true && live.policy_acknowledged === true && live.readiness?.ready === true && live.recovery_ready === true && !recovery && live.readiness.gates?.length && live.readiness.gates.every(gate => gate.passed === true))
  const enabled = live?.live_auto_trade === true && gatesReady
  const blocker = !premium ? 'Auto Trade için Premium üyelik gerekli.' : liveBlockerMessage(live, Boolean(live?.armed && !locked))
  const autoText = !live ? '—' : live.live_auto_trade && !enabled ? locked ? 'SCANNING ONLY · LOCKED' : 'ON · GATES BLOCKED' : autoTradePresentation('LIVE', live.live_auto_trade).label
  const autoStatus = live && !gatesReady && !live.live_auto_trade ? `${autoText} · Kilitli` : autoText
  const triggerStatus = triggerPresentation(trigger.lifecycle, trigger.available)
  const explainLock = () => {if (!premium) openUpgrade('Auto Trade'); else setNotice(blocker === 'No LIVE blocker.' ? 'Mevcut Canlı İşlem güvenlik ve onay akışını tamamlayın.' : blocker)}
  // LİMİT/PİYASA burada emir göndermez; kullanıcıyı emirlerin verildiği Canlı İşlem sekmesine götürür.
  const orderTab = () => {if (!premium) openUpgrade('Manuel emir'); else props.onNavigate('canli')}
  const reasons = available ? [...new Set([...decision.reasons, ...(decision.direction === 'LONG' ? decision.longCase : decision.direction === 'SHORT' ? decision.shortCase : [])])].slice(0, 4) : []
  const action = !available ? 'Analiz yok' : ['WAIT', 'WATCH', 'NO TRADE'].includes(decision.status) ? decision.status === 'NO TRADE' ? 'BEKLE / NO TRADE' : 'BEKLE' : decision.direction === 'LONG' ? 'AL' : decision.direction === 'SHORT' ? 'SAT' : 'BEKLE'
  const tabs = [{id: 'analiz', label: 'Analiz', Icon: BarChart3}, {id: 'canli', label: 'Canlı İşlem', Icon: Activity}, {id: 'pozisyonlar', label: 'Pozisyonlar', Icon: Wallet}, {id: 'baglanti', label: 'Bağlantı', Icon: Cable}] as const
  return <section className="masterReferenceAnalysis" data-layout-tab="analiz">
    <header className="refHeader masterTradeTerminalHeader">
      <a className="refBrand" href="/" aria-label="Kaistrade ana sayfa"><Activity/><span>kais<b>trade</b></span></a>
      <nav role="tablist" aria-label="Master Trade sections">{tabs.map(({id, label, Icon}) => <button key={id} id={`masterTradeTab-${id}`} type="button" role="tab" aria-selected={id === 'analiz'} aria-controls={`masterTradePanel-${id}`} onClick={() => props.onNavigate(id)}><Icon/>{label}</button>)}</nav>
      <span className="refConnection" data-connected={live?.connected === true}><i/>{live?.connected ? 'BAĞLI' : 'BAĞLANTI YOK'}{locked && <><LockKeyhole/> KİLİTLİ</>}</span>
      <div className="masterTradeProfileSlot" aria-label="Profil menüsü"/>{props.assistantSlotRef && <div className="assistantMasterSlot" ref={props.assistantSlotRef}/>}
    </header>
    <main className="refWorkspace masterAnalysis" role="tabpanel" aria-labelledby="masterTradeTab-analiz" id="masterTradePanel-analiz">
      <MasterTradeMarketWatch symbol={props.symbol} query={props.query} onQuery={props.onQuery} onMarket={props.onMarket} scores={props.scores} feed={props.marketFeed}/>
      <section className="refMetrics" aria-label="Primary trade decision">
        <article className="refCard"><small>PİYASA FİYATI</small><CandleTimer time={liveChart.candles[liveChart.candles.length - 1]?.time} interval={props.interval}/><strong>{analysisPrice(cardPrice)} <em data-tone={typeof cardChange === 'number' && cardChange < 0 ? 'negative' : 'positive'}>{typeof cardChange === 'number' ? `${cardChange >= 0 ? '+' : ''}${numeric(cardChange, '%', 2)}` : '—'}</em></strong><span>{props.symbol} · <span aria-label="24 saat hacim" title={`${numeric(props.volume, ' USDT', 2)} · 24h`}>{typeof props.volume === 'number' ? `${props.volume.toLocaleString('en-US', {notation: 'compact', maximumFractionDigits: 2})} USDT` : '—'}</span></span></article>
        <article className="refCard"><small>SİNYAL / DURUM</small><UpdatedAgo at={props.analysisUpdatedAt}/><strong className="refSignal" data-tone={directionTone(decision.direction)}>{available ? trLabel(decision.status) : 'Analiz yok'}</strong><span>{trLabel(analysis?.trend)}</span></article>
        <article className="refCard"><small>MTF / YÖN</small><strong><b className="refPill" data-tone={directionTone(decision.direction)}>{available ? decision.direction : '—'}</b></strong><span>{decision.mtfScore === null ? '—' : `${decision.mtfConfirmed} / ${decision.mtfTotal} teyit`}</span></article>
        <article className="refCard"><small>GÜVEN</small><strong><b className="refPill">{numeric(decision.confidenceScore, '%')}</b></strong><span>{trLabel(decision.signalStrength)}</span></article>
        <article className="refCard"><small>RİSK / ÖDÜL</small><strong>{decision.riskReward === null ? '—' : `1 : ${numeric(decision.riskReward, '', 2)}`}</strong><span>{trLabel(decision.entryQuality)} · Hedef R/Ö; canlıda TP1'de %60, kalanı TP3'te.</span></article>
      </section>
      <section className="refCenter">
        <section className="refCard refChart">
          <div className="refToolbar" aria-label="Market chart controls"><span title="Mum grafiği"><BarChart3/><ChevronDown/></span><select aria-label="Zaman dilimi" value={props.interval} onChange={event => props.onInterval(event.target.value)}>{TRADING_TIMEFRAMES.map(range => <option key={range}>{range}</option>)}</select>
            <details className="refIndicators"><summary><SlidersHorizontal/> Göstergeler <ChevronDown/></summary><div><button type="button" aria-pressed={props.levelsVisible} onClick={props.onLevels}>SEVİYELER</button><button type="button" aria-pressed={props.volumeVisible} onClick={props.onVolume}>HACİM</button></div></details>
            {props.chartAiControl}
            <div className="refDrawingTools">
              <button type="button" aria-label="Artı imleci" title="Artı imleci: fiyat ve saati göster" aria-pressed={prefs.crosshair} onClick={() => updatePrefs({crosshair: !prefs.crosshair})}><Crosshair/></button>
              <span className="refToolDivider" aria-hidden="true"/>
              <button type="button" aria-label="Yatay çizgi çiz" title="Yatay çizgi: grafikte bir fiyata tıkla" aria-pressed={drawMode === 'hline'} onClick={() => pickTool('hline')}><Minus/></button>
              <button type="button" aria-label="Trend çizgisi çiz" title="Trend çizgisi: iki noktaya tıkla" aria-pressed={drawMode === 'trend'} onClick={() => pickTool('trend')}><TrendingUp/></button>
              <button type="button" aria-label="Fiyat alarmı kur" title="Alarm: bir fiyata tıkla; fiyat oraya gelince uyarır (sayfa açıkken)" aria-pressed={drawMode === 'alarm'} onClick={() => {pickTool('alarm'); primeAlarm()}}><Bell/></button>
              <button type="button" aria-label="Çizim sil" title="Silgi: silmek istediğin çizgiye tıkla (ya da çizgiye sağ tıkla)" aria-pressed={drawMode === 'erase'} disabled={!symbolDrawings.length} onClick={() => pickTool('erase')}><Eraser/></button>
              <button type="button" aria-label="Tüm çizimleri sil" title="Bu coindeki tüm çizimleri sil" disabled={!symbolDrawings.length} onClick={() => { if (window.confirm(`${props.symbol} grafiğindeki tüm çizimler silinsin mi?`)) { changeDrawings([]); setDrawMode('none') } }}><Trash2/></button>
              <span className="refToolDivider" aria-hidden="true"/>
              <details className="refChartSettings">
                <summary aria-label="Grafik ayarları" title="Grafik ayarları"><Settings2/></summary>
                <div role="group" aria-label="Grafik ayarları">
                  <label><input type="checkbox" checked={prefs.grid} onChange={() => updatePrefs({grid: !prefs.grid})}/>Izgara çizgileri</label>
                  <label><input type="checkbox" checked={props.volumeVisible} onChange={props.onVolume}/>Hacim çubukları</label>
                  <label><input type="checkbox" checked={props.levelsVisible} onChange={props.onLevels}/>Seviye etiketleri</label>
                  <label><input type="checkbox" checked={prefs.axis} onChange={() => updatePrefs({axis: !prefs.axis})}/>Fiyat ekseni yazıları</label>
                </div>
              </details>
              <button type="button" aria-label="Grafik görüntüsünü indir" title="Grafik görüntüsünü indir" disabled={downloading} onClick={() => void downloadChart()}><Camera/></button>
            </div>
            <div className="refOrderTools">{['LİMİT', 'PİYASA'].map(label => <button type="button" key={label} title="Emirler Canlı İşlem sekmesinden verilir" onClick={orderTab}>{label}</button>)}<button type="button" disabled={props.refreshing} onClick={props.onRefresh}><RefreshCw/>YENİLE</button></div>
          </div>
          {props.error && <p className="refError" role="alert">{props.error}</p>}
          <ReferenceChart {...props} candles={liveChart.candles} price={liveChart.price ?? props.price} live={liveChart.live} premium={premium} prefs={prefs}
            drawings={symbolDrawings} drawMode={drawMode} onDraw={drawing => changeDrawings([...symbolDrawings, drawing])}
            onErase={id => { const next = symbolDrawings.filter(item => item.id !== id); changeDrawings(next); if (!next.length) setDrawMode('none') }}
            onDrawDone={() => setDrawMode('none')}/>
          {alarmToast && <p className="refAlarmToast" role="alert"><Bell/>{alarmToast}<button type="button" aria-label="Alarm uyarısını kapat" onClick={() => setAlarmToast('')}><X/></button></p>}
          {drawMode !== 'none' && <p className="refDrawHint" role="status">{drawMode === 'alarm' ? 'Alarm kurmak istediğin fiyata tıkla.' : drawMode === 'hline' ? 'Çizgiyi koymak istediğin fiyata tıkla.' : drawMode === 'trend' ? 'Başlangıç ve bitiş noktasına tıkla.' : 'Silmek istediğin çizgiye tıkla.'} <span>Vazgeçmek için Esc.</span></p>}
        </section>
        <div className="refIndicatorGrid" aria-label="Market indicators">
          <MasterTradeMetricTile label="TREND" value={trLabel(analysis?.trend)} tone={directionTone(decision.direction)}><ReferenceBars candles={liveChart.candles}/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="MOMENTUM" value={trLabel(analysis?.momentum)} tone={analysis?.momentum === 'POSITIVE' ? 'positive' : analysis?.momentum === 'NEGATIVE' ? 'negative' : 'neutral'}><ReferenceBars candles={liveChart.candles} neutral/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="RSI" value={numeric(analysis?.rsi, '', 2)}><MasterTradeMetricVisual kind="rsi" value={analysis?.rsi}/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="MACD" value={analysisValue(analysis?.macd)} tone={typeof analysis?.macd === 'number' ? analysis.macd >= 0 ? 'positive' : 'negative' : 'neutral'}><MasterTradeMetricVisual kind="macd" value={analysis?.macd}/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="HACİM" value={numeric(analysis?.volume_ratio, 'x')}><MasterTradeMetricVisual kind="volume" volumes={liveChart.candles.slice(-14).map(candle => candle.volume)}/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="GÜVEN" value={numeric(decision.confidenceScore, '%')} tone={decision.confidenceScore === null ? 'neutral' : 'positive'}><MasterTradeMetricVisual kind="confidence" value={decision.confidenceScore}/></MasterTradeMetricTile>
          <MasterTradeMetricTile label="FIRSAT" value={numeric(decision.opportunityScore)}><MasterTradeMetricVisual kind="confidence" value={decision.opportunityScore}/></MasterTradeMetricTile>
        </div>
        <div className="refMtfStrip"><span>MTF TEYİDİ {decision.mtfScore === null ? '—' : `${decision.mtfConfirmed} / ${decision.mtfTotal}`}</span><div className="refMtfPills">{MASTER_MTF_INTERVALS.map(timeframe => {const row = decision.mtfRows.find(row => row.timeframe === timeframe); return <span key={timeframe} data-tone={directionTone(row?.direction ?? 'NEUTRAL')}>{timeframe} <b>{row?.available ? row.direction : '—'}</b></span>})}</div></div>
      </section>
      <aside className="refRight" aria-label="Analysis decision panel">
        <section className="refCard refReport" aria-label="Trade decision analysis">
          <h2>AI ANALİZ RAPORU <small>{props.symbol}</small></h2>
          <div className="refReportSymbol"><CoinIcon symbol={props.symbol} size={40}/><div><strong>{props.symbol}</strong><span>{numeric(decision.confidenceScore, '%')} güven</span></div><b className="refDecisionBadge" data-tone={action === 'AL' ? 'positive' : action === 'SAT' ? 'negative' : 'neutral'}>{action}</b></div>
          <p className="refDecisionSummary">{available ? `${trLabel(decision.status)} · ${trLabel(decision.marketRegime)}` : '—'}</p>
          <PremiumBoundary label="Why this decision" compact><ul className="refReasons">{reasons.length ? reasons.map(reason => <li key={reason}>{/not |unavailable|missing|failed/i.test(reason) ? <X className="negative"/> : <Check className="positive"/>}<span>{trLabel(reason)}</span></li>) : <li>—</li>}</ul></PremiumBoundary>
          <div className="refTargets" data-locked={!premium}><h3>OLASI HEDEFLER</h3>{[['TP1', analysis?.tp1], ['TP2', analysis?.tp2], ['TP3', analysis?.tp3], ['Stop Loss', analysis?.stop_loss]].map(([label, value]) => <div key={label} className={label === 'Stop Loss' ? 'refStop' : ''}><span>{label}</span><strong aria-hidden={!premium}>{premium && typeof value === 'number' ? analysisPrice(value) : '—'}</strong></div>)}{!premium && <button type="button" disabled={!ready || !userId} onClick={() => openUpgrade('Giriş / SL / TP seviyeleri')} aria-label="Olası hedefler · Premium"><LockKeyhole/></button>}</div>
        </section>
        <section className="refCard refAutoTrade" data-enabled={enabled}><header><Power/><h2>AUTO TRADE</h2><button type="button" role="switch" aria-label="Auto Trade" aria-checked={enabled} disabled={!gatesReady} className="refToggle" onClick={props.onAutoTrade}><span/></button>{!gatesReady && <button type="button" className="refLockInfo" aria-label="Auto Trade neden kilitli?" onClick={explainLock}><LockKeyhole/></button>}<button type="button" className="refAutoSettings" aria-label="Auto Trade ayarlarına git" onClick={props.onAutoTrade}><Settings2/></button></header><p>{autoStatus}</p><small>Ayrı güvenlik kapıları gerekir</small><button type="button" className="refAutoLink" onClick={props.onAutoTrade}>{autoTradePresentation('LIVE', live?.live_auto_trade).action}</button></section>
        {notice && <div className="refNotice" role="status">{notice}<button type="button" aria-label="Bilgiyi kapat" onClick={() => setNotice('')}><X/></button></div>}
        <section className="refCard refTrigger" aria-label="Trigger monitor">
          <h2>TETİK İZLEYİCİ <span className="refSnapshotChip">{trigger.available ? 'CANLI GÖRÜNÜM' : '—'}</span></h2>
          <span className="refTriggerStatus" data-tone={triggerStatus.tone}>{trLabel(triggerStatus.label)}</span>
          <div className="refTriggerGrid">
            <div><small>GÜNCEL</small><strong>{analysisPrice(trigger.currentPrice)}</strong></div>
            <div><small>{trigger.direction === 'SHORT' ? 'SHORT TETİĞİ (ALTINDA)' : 'LONG TETİĞİ (ÜSTÜNDE)'}</small><strong>{analysisPrice(trigger.triggerPrice)}</strong></div>
            <div><small>MESAFE (%)</small><strong>{numeric(trigger.distancePct, '%', 2)}</strong></div>
            <div><small>DURUM</small><strong>{trigger.available ? trLabel(trigger.lifecycle) : '—'}</strong></div>
          </div>
          <div className="refTriggerEvents"><span data-active={trigger.available && ['TRIGGERED', 'CONFIRMED'].includes(trigger.lifecycle)}>Triggered <b>{trigger.available && ['TRIGGERED', 'CONFIRMED'].includes(trigger.lifecycle) ? trigger.lifecycle : '—'}</b></span><span data-active={trigger.available && trigger.lifecycle === 'EXPIRED'}>Expired <b>{trigger.available && trigger.lifecycle === 'EXPIRED' ? 'EXPIRED' : '—'}</b></span></div>
          <PremiumBoundary label="Trigger monitor" compact>
            <MasterTradeAccordions decision={decision} trigger={trigger} analysis={analysis} timeline={props.timeline}/>
          </PremiumBoundary>
          <p className="refSafety">Nihai karar emir göndermez ve Auto Trade izni vermez; yalnız bir gösterim puanıdır.</p>
        </section>
      </aside>
    </main>
  </section>
}

import {useId} from 'react'
import {INDICATORS, MAX_ACTIVE_INDICATORS, type IndicatorId, type IndicatorSet, type Series} from './studio-indicators'
import {bandPaths, segmentPaths, symmetricExtent, valueText} from './studio-overlay-paths'

// Grafik üstü ve alt panel gösterge katmanları. Yalnız görünüm: emir, analiz ya da veri akışına dokunmaz.
export const INDICATOR_COLORS: Record<IndicatorId | 'signal', string> = {
  ema20: '#f5b301', ema50: '#4c9aff', ema200: '#b36bff', vwap: '#2dd4bf', bollinger: '#9fb3c8', rsi: '#c084fc', macd: '#38bdf8', signal: '#fb923c',
}
const LINES: Array<{id: 'ema20' | 'ema50' | 'ema200' | 'vwap'; width: number; dash?: string}> = [
  {id: 'ema200', width: 1.6}, {id: 'ema50', width: 1.4}, {id: 'ema20', width: 1.3}, {id: 'vwap', width: 1.3, dash: '5 4'},
]
export const SUB_PANE_HEIGHT = 92

type Layout = {from: number; count: number; xOf: (visibleIndex: number) => number}

/** Fiyat bölmesinin üstüne çizilen katman: EMA'lar, VWAP, Bollinger bandı. */
export function PriceOverlays({set, from, count, xOf, yOf, plotWidth, priceHeight}: Layout & {set: IndicatorSet; yOf: (value: number) => number; plotWidth: number; priceHeight: number}) {
  const clip = useId().replace(/:/g, '')
  const to = from + count
  const band = set.bollinger ? bandPaths(set.bollinger.upper, set.bollinger.lower, from, to, xOf, yOf) : []
  return <g className="refIndicatorLayer" aria-hidden="true" clipPath={`url(#${clip})`}>
    <defs><clipPath id={clip}><rect x="0" y="0" width={plotWidth} height={priceHeight}/></clipPath></defs>
    {band.map((d, index) => <path key={`b${index}`} d={d} fill={INDICATOR_COLORS.bollinger} fillOpacity=".08" stroke="none"/>)}
    {set.bollinger && [set.bollinger.upper, set.bollinger.mid, set.bollinger.lower].map((series, row) => segmentPaths(series, from, to, xOf, yOf).map((d, index) =>
      <path key={`bb${row}-${index}`} d={d} fill="none" stroke={INDICATOR_COLORS.bollinger} strokeWidth={row === 1 ? 1 : 1.1} strokeDasharray={row === 1 ? '3 3' : undefined} strokeOpacity={row === 1 ? .7 : .9} vectorEffect="non-scaling-stroke"/>))}
    {LINES.map(line => segmentPaths(set[line.id], from, to, xOf, yOf).map((d, index) =>
      <path key={`${line.id}-${index}`} d={d} fill="none" stroke={INDICATOR_COLORS[line.id]} strokeWidth={line.width} strokeDasharray={line.dash} strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>))}
  </g>
}

const valueAt = (series: Series | undefined, index: number): number | null => {
  const value = series?.[index]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** OHLC satırının yanındaki küçük göstergeler: imleç altındaki mumun değerleri (yoksa son mum). */
export function IndicatorLegend({set, enabled, index}: {set: IndicatorSet; enabled: readonly IndicatorId[]; index: number}) {
  if (!enabled.length) return null
  const chip = (key: string, label: string, color: string, value: number | null) =>
    <span key={key} className="refIndChip" style={{['--ind' as string]: color}}><i aria-hidden="true"/>{label} <b>{valueText(value)}</b></span>
  return <span className="refIndLegend" aria-label="Gösterge değerleri">{enabled.flatMap(id => {
    switch (id) {
      case 'ema20': case 'ema50': case 'ema200': case 'vwap': return [chip(id, INDICATORS.find(item => item.id === id)!.label.replace(' (günlük)', ''), INDICATOR_COLORS[id], valueAt(set[id], index))]
      case 'bollinger': return [chip(id, 'BB', INDICATOR_COLORS.bollinger, valueAt(set.bollinger?.mid, index))]
      default: return []
    }
  })}</span>
}

type PaneProps = Layout & {set: IndicatorSet; enabled: readonly IndicatorId[]; width: number; plotWidth: number; hover: number | null}

/** RSI ve MACD alt panelleri: ana grafikle aynı zaman ekseni ve aynı yatay ölçek. */
export function SubPanes({set, enabled, from, count, xOf, width, plotWidth, hover}: PaneProps) {
  const panes = (['rsi', 'macd'] as const).filter(id => enabled.includes(id))
  if (!panes.length || count < 1) return null
  const to = from + count
  const index = hover === null ? to - 1 : Math.min(to - 1, from + hover)
  const inner = SUB_PANE_HEIGHT - 20
  return <div className="refSubPanes" data-count={panes.length}>{panes.map(id => {
    if (id === 'rsi') {
      const y = (value: number) => 10 + (100 - value) / 100 * inner
      const last = valueAt(set.rsi, index)
      return <svg key={id} className="refSubPane" data-pane="rsi" viewBox={`0 0 ${width} ${SUB_PANE_HEIGHT}`} preserveAspectRatio="none" role="img" aria-label={`RSI 14: ${valueText(last, 1)}`}>
        <rect className="refPaneBand" x="0" y={y(70)} width={plotWidth} height={y(30) - y(70)}/>
        {[70, 50, 30].map(level => <line key={level} className="refPaneGuide" data-level={level} x1="0" x2={plotWidth} y1={y(level)} y2={y(level)} vectorEffect="non-scaling-stroke"/>)}
        {[70, 30].map(level => <text key={`t${level}`} className="refPaneTick" x={plotWidth + 10} y={y(level)} dominantBaseline="middle">{level}</text>)}
        {segmentPaths(set.rsi, from, to, xOf, y).map((d, i) => <path key={i} d={d} fill="none" stroke={INDICATOR_COLORS.rsi} strokeWidth="1.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>)}
        <text className="refPaneTitle" x="10" y="12">RSI 14 <tspan data-tone={last === null ? undefined : last >= 70 ? 'hot' : last <= 30 ? 'cold' : undefined}>{valueText(last, 1)}</tspan></text>
      </svg>
    }
    const m = set.macd
    const extent = symmetricExtent([m?.macd, m?.signal, m?.histogram], from, to)
    const y = (value: number) => 10 + (extent - value) / (2 * extent) * inner
    const step = count > 1 ? Math.abs(xOf(1) - xOf(0)) : 6
    const bar = Math.max(1, Math.min(10, step * .7))
    const hist = valueAt(m?.histogram, index)
    return <svg key={id} className="refSubPane" data-pane="macd" viewBox={`0 0 ${width} ${SUB_PANE_HEIGHT}`} preserveAspectRatio="none" role="img" aria-label={`MACD histogram: ${valueText(hist, 4)}`}>
      <line className="refPaneGuide" x1="0" x2={plotWidth} y1={y(0)} y2={y(0)} vectorEffect="non-scaling-stroke"/>
      {Array.from({length: count}, (_, i) => {
        const value = valueAt(m?.histogram, from + i)
        if (value === null) return null
        const previous = valueAt(m?.histogram, from + i - 1)
        const growing = previous === null || Math.abs(value) >= Math.abs(previous)
        const top = Math.min(y(value), y(0)), height = Math.max(.5, Math.abs(y(value) - y(0)))
        return <rect key={i} className="refMacdBar" data-up={value >= 0} data-strong={growing} x={xOf(i) - bar / 2} y={top} width={bar} height={height}/>
      })}
      {segmentPaths(m?.macd, from, to, xOf, y).map((d, i) => <path key={`m${i}`} d={d} fill="none" stroke={INDICATOR_COLORS.macd} strokeWidth="1.4" vectorEffect="non-scaling-stroke"/>)}
      {segmentPaths(m?.signal, from, to, xOf, y).map((d, i) => <path key={`s${i}`} d={d} fill="none" stroke={INDICATOR_COLORS.signal} strokeWidth="1.4" vectorEffect="non-scaling-stroke"/>)}
      <text className="refPaneTitle" x="10" y="12">MACD 12·26·9 <tspan data-tone={hist === null ? undefined : hist >= 0 ? 'up' : 'down'}>{valueText(hist, 4)}</tspan></text>
    </svg>
  })}</div>
}

/** "Göstergeler" menüsündeki seçim listesi. */
export function IndicatorPicker({enabled, onToggle}: {enabled: readonly IndicatorId[]; onToggle: (id: IndicatorId) => void}) {
  const full = enabled.length >= MAX_ACTIVE_INDICATORS
  return <div className="refIndPicker" role="group" aria-label="Teknik göstergeler">
    <small>TEKNİK GÖSTERGELER</small>
    {INDICATORS.map(item => {
      const on = enabled.includes(item.id)
      return <button type="button" key={item.id} aria-pressed={on} disabled={!on && full} title={item.hint} onClick={() => onToggle(item.id)} style={{['--ind' as string]: INDICATOR_COLORS[item.id]}}>
        <i aria-hidden="true"/><span>{item.label}</span><em>{item.pane === 'sub' ? 'alt panel' : 'grafikte'}</em>
      </button>
    })}
  </div>
}

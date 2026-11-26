import {useEffect, useMemo, useRef, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import {currentLang} from '../../i18n'
import {computeDcaSuitability, type SuitCandle} from './gridSuitability'

// DCA uygunluk kartı: seçilen coin + yön + ek alım aralığı için "şu an DCA yapmak uygun mu?" Yalnız bilgi verir, hiçbir şeyi engellemez.
// AI yön puanından AYRIDIR: AI "bu yönde sinyal güçlü mü", bu kart "piyasa yapısı DCA'yı kaldırır mı" der. Mum verisi /klines yolundan gelir.

type Props = {symbol: string; side: 'LONG' | 'SHORT'; price: number; stepPct: number; safetyOrders: number; stopGapPct: number; aiConfidence?: number | null; aiAgrees?: boolean}
type Frames = {symbol: string; c15: SuitCandle[]; c1h: SuitCandle[]; c4h: SuitCandle[]}

const parse = (rows: unknown): SuitCandle[] => (Array.isArray(rows) ? rows : []).map(row => {
  const item = row as Record<string, unknown>
  return {time: Number(item.time), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), volume: Number(item.volume ?? 0)}
}).filter(candle => [candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))

const TXT = {
  tr: {kicker: 'DCA UYGUNLUĞU', loading: 'Piyasa okunuyor…', more: 'Ayrıntıları göster', noData: 'Piyasa verisi okunamadı; puan hesaplanamadı.', ai: 'AI yönü', dca: 'DCA uygunluğu',
    bars: {trend: 'Trend uyumu', vol: 'Oynaklık / aralık', recovery: 'Toparlanma', stretch: 'Fiyat uzaması'},
    trendL: {with: 'Yönünde', flat: 'Yatay', against: 'Ters', strongAgainst: 'Güçlü ters'}, volL: {calm: 'Sakin', ok: 'Uygun', fast: 'Hızlı', wild: 'Çok sert'},
    recL: {yes: 'Var', partial: 'Kısmi', no: 'Yok'}, strL: {normal: 'Normal', high: 'Yüksek', extreme: 'Aşırı'},
    conflict: 'AI yönü güçlü ama DCA için piyasa yapısı şu an uygun değil.',
    note: 'DCA uygunluğu, mevcut piyasa koşullarının bu DCA yapısına ne kadar uygun olduğunun değerlendirmesidir; kâr tahmini ya da garantisi değildir. Düşük puan botu başlatmayı engellemez.'},
  en: {kicker: 'DCA SUITABILITY', loading: 'Reading the market…', more: 'Show details', noData: 'Market data could not be read; score unavailable.', ai: 'AI direction', dca: 'DCA suitability',
    bars: {trend: 'Trend fit', vol: 'Volatility / step', recovery: 'Recovery', stretch: 'Price stretch'},
    trendL: {with: 'With you', flat: 'Flat', against: 'Against', strongAgainst: 'Strong against'}, volL: {calm: 'Calm', ok: 'Fits', fast: 'Fast', wild: 'Very sharp'},
    recL: {yes: 'Yes', partial: 'Partial', no: 'None'}, strL: {normal: 'Normal', high: 'High', extreme: 'Extreme'},
    conflict: 'The AI direction is strong, but market structure is not favorable for a DCA right now.',
    note: 'DCA suitability assesses how well current market conditions fit this DCA setup; it is not a profit prediction or guarantee. A low score does not block starting the bot.'}
} as const

export default function DcaSuitabilityCard({symbol, side, price, stepPct, safetyOrders, stopGapPct, aiConfidence, aiAgrees}: Props) {
  const [frames, setFrames] = useState<Frames | null>(null)
  const [failed, setFailed] = useState(false)
  const lang = currentLang()
  const t = TXT[lang]

  useEffect(() => {
    if (!symbol) { setFrames(null); setFailed(false); return }
    let cancelled = false
    const controller = new AbortController()
    const load = async () => {
      try {
        const get = async (interval: string, limit: number) => {
          const response = await fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(symbol)}?interval=${interval}&limit=${limit}`, {cache: 'no-store', signal: controller.signal})
          if (!response.ok) throw new Error('klines')
          return parse(await response.json())
        }
        const [c15, c1h, c4h] = await Promise.all([get('15m', 120), get('1h', 150), get('4h', 100)])
        if (!cancelled) { setFrames({symbol, c15, c1h, c4h}); setFailed(false) }
      } catch (error) {
        if (!cancelled && (error as {name?: string})?.name !== 'AbortError') setFailed(true)
      }
    }
    setFrames(null); setFailed(false)
    void load()
    const timer = window.setInterval(() => { void load() }, 60_000)
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer) }
  }, [symbol])

  const result = useMemo(() => frames && frames.symbol === symbol && price > 0
    ? computeDcaSuitability({side, price, stepPct, safetyOrders, stopGapPct, c15: frames.c15, c1h: frames.c1h, c4h: frames.c4h}, lang) : null,
  [frames, symbol, side, price, stepPct, safetyOrders, stopGapPct, lang])

  if (!symbol) return null
  const bar = (label: string, value: number, text: string) => <div className="dsBar" key={label}>
    <span>{label}</span><i aria-hidden="true"><u data-tone={value >= 65 ? 'good' : value >= 35 ? 'mid' : 'low'} style={{width: `${Math.max(4, value)}%`}}/></i><b>{text}</b>
  </div>
  const conflict = result?.ok && aiAgrees && Number(aiConfidence) >= 80 && result.score < 55
  const body = failed && !result ? <p className="dwHint">{t.noData}</p>
    : !result ? <p className="dwHint">{t.loading}</p>
    : !result.ok ? <p className="dwHint">{result.headline}</p>
    : <>
      <div className="dsTop" data-level={result.level}>
        <span className="dsRing" style={{background: `conic-gradient(var(--ds-c) 0 ${result.score}%, rgba(255,255,255,.08) 0)`}}><b>{result.score}</b></span>
        <span className="dsHead"><b>{result.headline}</b><small>{result.detail}</small></span>
      </div>
      {Number.isFinite(Number(aiConfidence)) && aiAgrees && <div className="dsPair" aria-label="AI ve DCA puanı">
        <span><small>{t.ai}</small><b className="up">%{Math.round(Number(aiConfidence))}</b></span>
        <span><small>{t.dca}</small><b data-level={result.level}>{result.score}/100</b></span>
      </div>}
      {conflict && <p className="dsConflict" role="status">⚠ {t.conflict}</p>}
      <details className="dsMore"><summary>{t.more}</summary>
      <div className="dsBars">
        {bar(t.bars.trend, result.parts.trend, t.trendL[result.trend])}
        {bar(t.bars.vol, result.parts.vol, t.volL[result.volState])}
        {bar(t.bars.recovery, result.parts.recovery, t.recL[result.recovery])}
        {bar(t.bars.stretch, result.parts.stretch, t.strL[result.stretch])}
      </div>
      </details>
      {result.warnings.length > 0 && <ul className="dsWarn" role="status">{result.warnings.map(text => <li key={text}>{text}</li>)}</ul>}
    </>
  return <div className="ds" role="group" aria-label={t.kicker} data-testid="dca-suitability">
    <small className="dsKicker">{t.kicker}</small>
    {body}
    <p className="dsNote">{t.note}</p>
  </div>
}

// ---- Coin listesi için hafif uygunluk etiketi: yalnız görünen birkaç coin için (önbellekli, 60 sn), Dengeli plan varsayımıyla.
export type SuitChip = {score: number; level: 'good' | 'caution' | 'poor'}
const CHIP_CACHE = new Map<string, {at: number; frames: Frames}>()
const CHIP_TTL = 60_000

export function useDcaSuitMap(items: Array<{symbol: string; price: number}>, side: 'LONG' | 'SHORT', enabled: boolean): Record<string, SuitChip | null> {
  const [tick, setTick] = useState(0)
  const key = items.map(item => item.symbol).join(',')
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useEffect(() => {
    if (!enabled || !items.length) return
    let cancelled = false
    const queue = items.filter(item => { const hit = CHIP_CACHE.get(item.symbol); return !hit || Date.now() - hit.at > CHIP_TTL })
    const worker = async () => {
      for (let item = queue.shift(); item && !cancelled; item = queue.shift()) {
        try {
          const get = async (interval: string, limit: number) => {
            const response = await fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(item!.symbol)}?interval=${interval}&limit=${limit}`, {cache: 'no-store'})
            if (!response.ok) throw new Error('klines')
            return parse(await response.json())
          }
          const [c15, c1h, c4h] = await Promise.all([get('15m', 120), get('1h', 150), get('4h', 100)])
          CHIP_CACHE.set(item.symbol, {at: Date.now(), frames: {symbol: item.symbol, c15, c1h, c4h}})
          if (!cancelled && live.current) setTick(n => n + 1)
        } catch { /* bu coin için etiket gösterilmez */ }
      }
    }
    void Promise.all([worker(), worker()])
    return () => { cancelled = true }
  }, [key, enabled])
  return useMemo(() => {
    const out: Record<string, SuitChip | null> = {}
    for (const item of items) {
      const hit = CHIP_CACHE.get(item.symbol)
      if (!hit || !(item.price > 0)) { out[item.symbol] = null; continue }
      const r = computeDcaSuitability({side, price: item.price, stepPct: 1.5, safetyOrders: 3, stopGapPct: 1.5, c15: hit.frames.c15, c1h: hit.frames.c1h, c4h: hit.frames.c4h})
      out[item.symbol] = r.ok ? {score: r.score, level: r.level} : null
    }
    return out
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, side, tick])
}

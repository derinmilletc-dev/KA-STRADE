import {useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import {currentLang} from '../../i18n'
import {computeSuitability, type SuitCandle, type SuitDirection} from './gridSuitability'

// Grid uygunluk puanı kartı: seçilen coin + seçilen aralık için "şu an grid'e uygun mu?" Yalnız bilgi verir, hiçbir şeyi engellemez.
// Mum verisi arka plandaki /klines yolundan gelir (Binance doğrudan çağrılmaz); yalnız okuma, hesap/anahtar/emir yok.

type Props = {symbol: string; price: number; lower: number; upper: number; side: 'LONG' | 'SHORT'; active: boolean}
type Frames = {symbol: string; c15: SuitCandle[]; c1h: SuitCandle[]; c4h: SuitCandle[]}

const parse = (rows: unknown): SuitCandle[] => (Array.isArray(rows) ? rows : []).map(row => {
  const item = row as Record<string, unknown>
  return {time: Number(item.time), open: Number(item.open), high: Number(item.high), low: Number(item.low), close: Number(item.close), volume: Number(item.volume ?? 0)}
}).filter(candle => [candle.time, candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))

const REFRESH_MS = 60_000

const TXT = {
  tr: {kicker: 'GRID UYGUNLUĞU', market: 'Piyasa', vol: 'Oynaklık', pos: 'Fiyatın yeri', mom: 'Momentum', dir: 'Tercih edilen yön', loading: 'Piyasa okunuyor…', noData: 'Piyasa verisi okunamadı; puan hesaplanamadı.', needRange: 'Alt ve üst sınırı girince puan çıkar.',
    regime: {range: 'YATAY ↔', mild: 'HAFİF TREND', strong: 'GÜÇLÜ TREND', extreme: 'ÇOK SERT'}, volL: {low: 'DÜŞÜK', ideal: 'İDEAL', high: 'YÜKSEK'},
    posL: {center: 'ORTA ✓', edge: 'KENARA DOĞRU', near: 'KENARA YAKIN', outside: 'ARALIK DIŞI'}, momL: {neutral: 'NÖTR ↔', mixed: 'KARIŞIK', strong: 'GÜÇLÜ ↗↘'},
    dirL: {LONG: 'LONG', SHORT: 'SHORT', NEUTRAL: 'NÖTR'} as Record<SuitDirection, string>,
    note: 'Grid uygunluğu, mevcut piyasa koşullarının değerlendirmesidir; kâr tahmini ya da garantisi değildir. Düşük puan grid başlatmayı engellemez.', dirNote: 'Yön önerisi "kesin kazanır" demek değildir; koşullar o tarafta daha uygundur.'},
  en: {kicker: 'GRID SUITABILITY', market: 'Market', vol: 'Volatility', pos: 'Price position', mom: 'Momentum', dir: 'Preferred direction', loading: 'Reading the market…', noData: 'Market data could not be read; score unavailable.', needRange: 'Enter the lower and upper limits to get a score.',
    regime: {range: 'RANGE ↔', mild: 'MILD TREND', strong: 'STRONG TREND', extreme: 'VERY SHARP'}, volL: {low: 'LOW', ideal: 'IDEAL', high: 'HIGH'},
    posL: {center: 'CENTER ✓', edge: 'TOWARD EDGE', near: 'NEAR EDGE', outside: 'OUTSIDE RANGE'}, momL: {neutral: 'NEUTRAL ↔', mixed: 'MIXED', strong: 'STRONG ↗↘'},
    dirL: {LONG: 'LONG', SHORT: 'SHORT', NEUTRAL: 'NEUTRAL'} as Record<SuitDirection, string>,
    note: 'Grid suitability is an assessment of current market conditions, not a profit prediction or guarantee. A low score does not block starting a grid.', dirNote: 'The direction hint does not mean "will win"; conditions are simply better on that side.'}
} as const

export default function GridSuitabilityCard({symbol, price, lower, upper, side, active}: Props) {
  const [frames, setFrames] = useState<Frames | null>(null)
  const [failed, setFailed] = useState(false)
  const lang = currentLang()
  const t = TXT[lang]

  useEffect(() => {
    if (!active || !symbol) { setFrames(null); setFailed(false); return }
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
    const timer = window.setInterval(() => { void load() }, REFRESH_MS)
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer) }
  }, [symbol, active])

  const result = useMemo(() => frames && frames.symbol === symbol && lower > 0 && upper > lower
    ? computeSuitability({price, lower, upper, side, c15: frames.c15, c1h: frames.c1h, c4h: frames.c4h}, lang) : null, [frames, symbol, price, lower, upper, side, lang])

  if (!symbol) return null
  const body = !(lower > 0 && upper > lower) ? <p className="liveWsHint">{t.needRange}</p>
    : failed && !result ? <p className="liveWsHint">{t.noData}</p>
    : !result ? <p className="liveWsHint">{t.loading}</p>
    : !result.ok ? <p className="liveWsHint">{result.headline}</p>
    : <>
      <div className="mgsHead" data-level={result.level}>
        <span className="mgpRing" style={{background: `conic-gradient(var(--mgs-c) 0 ${result.score}%, #1a283a 0)`}}><b>{result.score}</b></span>
        <span><b>{result.score} / 100 · {result.headline}</b><small>{result.detail}</small></span>
      </div>
      <dl className="mgsGrid">
        <div><dt>{t.market}</dt><dd>{t.regime[result.regime]}</dd></div>
        <div><dt>{t.vol}</dt><dd>{t.volL[result.vol]}</dd></div>
        <div><dt>{t.pos}</dt><dd>{t.posL[result.pos]}</dd></div>
        <div><dt>{t.mom}</dt><dd>{t.momL[result.momentum]}</dd></div>
        <div className="mgsDir"><dt>{t.dir}</dt><dd>{t.dirL[result.direction]}</dd></div>
      </dl>
      {result.warnings.length > 0 && <ul className="mgsWarn" role="status">{result.warnings.map(text => <li key={text}>{text}</li>)}</ul>}
      <p className="liveWsHint">{t.dirNote}</p>
    </>
  return <div className="mgs" role="group" aria-label={t.kicker}>
    <small className="mwzKicker">{t.kicker}</small>
    {body}
    <p className="liveWsHint mgsNote">{t.note}</p>
  </div>
}

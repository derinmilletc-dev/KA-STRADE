// Analiz Stüdyosu · akıllı yorum motoru: canlı mumlardan ve analiz kararından kısa, okunur yorumlar üretir (ücretsiz, anında; yapay zeka çağırmaz).
// Çıktının `summary` metni Kais AI'a Sor'a da eklenebilir: yapay zeka grafiği yorumlarken motorun okumasını da görür.
import {atr, bollinger, ema, macd, rsi, sma} from './studio-indicators'

export type InsightCandle = {time: number; open: number; high: number; low: number; close: number; volume?: number}
export type InsightTone = 'up' | 'down' | 'warn' | 'info'
export type Insight = {id: string; tone: InsightTone; title: string; text: string}
export type InsightInput = {
  candles: readonly InsightCandle[]; price?: number | null
  analysis?: {direction?: string | null; support?: number | null; resistance?: number | null; entry?: number | null; stop_loss?: number | null; tp1?: number | null} | null
  decision?: {direction?: string | null; confidenceScore?: number | null; mtfConfirmed?: number | null; mtfTotal?: number | null; riskReward?: number | null} | null
}
export type InsightResult = {ready: boolean; verdict: {tone: InsightTone; text: string}; items: Insight[]; summary: string}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const last = <T,>(list: readonly T[]): T | undefined => list[list.length - 1]
const lastNum = (series: ReadonlyArray<number | null>): number | null => { for (let i = series.length - 1; i >= 0; i--) { const v = series[i]; if (finite(v)) return v } return null }
const pct = (value: number, digits = 1) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(digits).replace('.', ',')}%`
const num = (value: number) => value >= 1000 ? value.toLocaleString('tr-TR', {maximumFractionDigits: 1}) : value.toFixed(value >= 10 ? 2 : 4).replace('.', ',')

export const MIN_INSIGHT_CANDLES = 30

/** Mum formu: yalnız net olanları söyler (yutan mum, uzun fitil). */
export function candleRead(candles: readonly InsightCandle[]): Insight | null {
  const a = candles[candles.length - 2], b = last(candles)
  if (!a || !b) return null
  const body = Math.abs(b.close - b.open), range = b.high - b.low
  if (range <= 0) return null
  const upper = b.high - Math.max(b.open, b.close), lower = Math.min(b.open, b.close) - b.low
  const prevBody = Math.abs(a.close - a.open)
  if (prevBody > 0 && body > prevBody * 1.2 && b.close > b.open && a.close < a.open && b.close >= a.open && b.open <= a.close) return {id: 'candle', tone: 'up', title: 'Mum', text: 'Son mum bir önceki düşüş mumunu yuttu (yükseliş yutan): alıcılar devreye girdi.'}
  if (prevBody > 0 && body > prevBody * 1.2 && b.close < b.open && a.close > a.open && b.open >= a.close && b.close <= a.open) return {id: 'candle', tone: 'down', title: 'Mum', text: 'Son mum bir önceki yükseliş mumunu yuttu (düşüş yutan): satıcılar devreye girdi.'}
  if (lower > body * 2 && lower > range * 0.55) return {id: 'candle', tone: 'up', title: 'Mum', text: 'Uzun alt fitil: aşağıda satış reddedildi, alıcı tepkisi var.'}
  if (upper > body * 2 && upper > range * 0.55) return {id: 'candle', tone: 'down', title: 'Mum', text: 'Uzun üst fitil: yukarıda alış reddedildi, satıcı baskısı var.'}
  return null
}

export function buildInsights(input: InsightInput): InsightResult {
  const candles = input.candles
  const empty: InsightResult = {ready: false, verdict: {tone: 'info', text: 'Yorum için yeterli mum verisi yok.'}, items: [], summary: ''}
  if (candles.length < MIN_INSIGHT_CANDLES) return empty
  const closes = candles.map(c => c.close)
  const price = finite(input.price) && input.price > 0 ? input.price : closes[closes.length - 1]
  const items: Insight[] = []
  let score = 0 // + yükseliş, − düşüş

  // Trend: EMA20 / EMA50 / EMA200 (200 yoksa 50'ye kadar)
  const e20 = lastNum(ema(closes, 20)), e50 = lastNum(ema(closes, 50)), e200 = candles.length >= 200 ? lastNum(ema(closes, 200)) : null
  const e20Prev = ema(closes, 20)[closes.length - 6]
  if (finite(e20) && finite(e50)) {
    const above20 = price > e20, above50 = price > e50, slope = finite(e20Prev) ? (e20 - e20Prev) / e20Prev * 100 : 0
    if (above20 && above50 && e20 > e50) { score += 2; items.push({id: 'trend', tone: 'up', title: 'Trend', text: `Fiyat EMA20 ve EMA50 üstünde, ortalamalar yükseliş dizilişinde${finite(e200) ? (price > e200 ? '; EMA200 de altta destek' : '; ama EMA200 hâlâ üstte direnç') : ''}.`}) }
    else if (!above20 && !above50 && e20 < e50) { score -= 2; items.push({id: 'trend', tone: 'down', title: 'Trend', text: `Fiyat EMA20 ve EMA50 altında, ortalamalar düşüş dizilişinde${finite(e200) ? (price < e200 ? '; EMA200 de üstte baskı yapıyor' : '; ama EMA200 altta destek veriyor') : ''}.`}) }
    else { items.push({id: 'trend', tone: 'warn', title: 'Trend', text: `Ortalamalar karışık (EMA20 ${above20 ? 'üstünde' : 'altında'}, EMA50 ${above50 ? 'üstünde' : 'altında'}): net trend yok${Math.abs(slope) < .15 ? ', yatay seyir' : ''}.`}) }
  }

  // Momentum: RSI + MACD histogramı
  const r = lastNum(rsi(closes, 14))
  const m = macd(closes), hist = lastNum(m.histogram), histPrev = m.histogram[closes.length - 2]
  if (finite(r)) {
    const rising = finite(hist) && finite(histPrev) ? hist > histPrev : null
    const macdText = finite(hist) ? (hist > 0 ? 'MACD pozitif' : 'MACD negatif') + (rising === null ? '' : rising ? ', ivme artıyor' : ', ivme azalıyor') : ''
    if (r >= 70) { score -= 1; items.push({id: 'momentum', tone: 'warn', title: 'Momentum', text: `RSI ${r.toFixed(0)}: aşırı alım bölgesi, yükselişte yorulma/geri çekilme riski. ${macdText}.`}) }
    else if (r <= 30) { score += 1; items.push({id: 'momentum', tone: 'warn', title: 'Momentum', text: `RSI ${r.toFixed(0)}: aşırı satım bölgesi, tepki yükselişi olasılığı artıyor. ${macdText}.`}) }
    else { const t: InsightTone = finite(hist) ? (hist > 0 ? 'up' : 'down') : 'info'; score += t === 'up' ? 1 : t === 'down' ? -1 : 0; items.push({id: 'momentum', tone: t, title: 'Momentum', text: `RSI ${r.toFixed(0)} (nötr bölge). ${macdText}.`}) }
  }

  // Volatilite: ATR% ve Bollinger konumu
  const a = lastNum(atr(candles.map(c => ({...c, volume: c.volume ?? 0}))))
  const bb = bollinger(closes, 20, 2), up = lastNum(bb.upper), lo = lastNum(bb.lower)
  if (finite(a) && price > 0) {
    const atrPct = a / price * 100
    let text = `ATR %${atrPct.toFixed(2).replace('.', ',')}: ${atrPct > 1.2 ? 'oynaklık yüksek, stop mesafesini geniş tut' : atrPct < .35 ? 'oynaklık düşük, sıkışma sonrası sert hareket gelebilir' : 'oynaklık normal'}.`
    let tone: InsightTone = 'info'
    if (finite(up) && finite(lo) && up > lo) {
      const pos = (price - lo) / (up - lo)
      if (pos > .95) { text += ' Fiyat Bollinger üst bandında.'; tone = 'warn' } else if (pos < .05) { text += ' Fiyat Bollinger alt bandında.'; tone = 'warn' }
    }
    items.push({id: 'volatility', tone, title: 'Volatilite', text})
  }

  // Hacim
  const vols = candles.map(c => c.volume ?? 0), vAvg = lastNum(sma(vols, 20)), v = last(vols)
  if (finite(vAvg) && vAvg > 0 && finite(v) && v > 0) {
    const ratio = v / vAvg, bull = (last(candles)?.close ?? 0) >= (last(candles)?.open ?? 0)
    if (ratio >= 1.5) items.push({id: 'volume', tone: bull ? 'up' : 'down', title: 'Hacim', text: `Hacim ortalamanın ${ratio.toFixed(1).replace('.', ',')} katı: ${bull ? 'yükseliş' : 'düşüş'} güçlü hacimle destekleniyor.`})
    else if (ratio <= .5) items.push({id: 'volume', tone: 'warn', title: 'Hacim', text: 'Hacim zayıf: bu hareketin arkasında güçlü katılım yok, kırılımlara temkinli yaklaş.'})
  }

  // Seviye: destek / direnç mesafesi
  const sup = input.analysis?.support, res = input.analysis?.resistance
  const levelBits: string[] = []
  let levelTone: InsightTone = 'info'
  if (finite(res) && res > price) { const d = (res - price) / price * 100; levelBits.push(`direnç ${num(res)} (${pct(d)})`); if (d < .4) levelTone = 'warn' }
  if (finite(sup) && sup < price) { const d = (sup - price) / price * 100; levelBits.push(`destek ${num(sup)} (${pct(d)})`); if (Math.abs(d) < .4) levelTone = 'warn' }
  if (levelBits.length) items.push({id: 'levels', tone: levelTone, title: 'Seviyeler', text: `En yakın ${levelBits.join(' · ')}.${levelTone === 'warn' ? ' Fiyat bir seviyeye çok yakın: tepki beklenebilir.' : ''}`})

  const cr = candleRead(candles)
  if (cr) { score += cr.tone === 'up' ? 1 : cr.tone === 'down' ? -1 : 0; items.push(cr) }

  // Plan: analiz yönü, MTF uyumu, R/Ö
  const dir = input.decision?.direction ?? input.analysis?.direction
  const planBits: string[] = []
  if (dir === 'LONG' || dir === 'SHORT') planBits.push(`Analiz ${dir} diyor${finite(input.decision?.confidenceScore) ? ` (güven %${Math.round(input.decision.confidenceScore)})` : ''}`)
  if (finite(input.decision?.mtfConfirmed) && finite(input.decision?.mtfTotal) && input.decision.mtfTotal > 0) planBits.push(`MTF ${input.decision.mtfConfirmed}/${input.decision.mtfTotal} uyumlu`)
  if (finite(input.decision?.riskReward)) planBits.push(`R/Ö 1:${input.decision.riskReward.toFixed(1).replace('.', ',')}`)
  if (planBits.length) {
    const agrees = (dir === 'LONG' && score > 0) || (dir === 'SHORT' && score < 0)
    const against = (dir === 'LONG' && score < 0) || (dir === 'SHORT' && score > 0)
    items.push({id: 'plan', tone: dir === 'LONG' || dir === 'SHORT' ? (agrees ? (dir === 'LONG' ? 'up' : 'down') : against ? 'warn' : 'info') : 'info', title: 'Plan', text: `${planBits.join(' · ')}.${against ? ' Dikkat: canlı mum yorumu plan yönüyle çelişiyor.' : agrees ? ' Canlı mum yorumu da plan yönünü destekliyor.' : ''}`})
  }

  const verdict: {tone: InsightTone; text: string} = score >= 3 ? {tone: 'up', text: 'Genel görünüm yükseliş yönlü'} : score <= -3 ? {tone: 'down', text: 'Genel görünüm düşüş yönlü'}
    : score >= 1 ? {tone: 'up', text: 'Hafif yükseliş eğilimi'} : score <= -1 ? {tone: 'down', text: 'Hafif düşüş eğilimi'} : {tone: 'warn', text: 'Net yön yok, bekle-gör bölgesi'}
  const summary = [`Motor yorumu (${verdict.text.toLowerCase()}):`, ...items.map(item => `${item.title}: ${item.text}`)].join(' ').replace(/\s+/g, ' ').slice(0, 520)
  return {ready: true, verdict, items, summary}
}

/** Kais AI'a Sor ile paylaşılan en son motor özeti (sembol + zaman dilimine bağlı; bayatsa kullanılmaz). */
let engine: {key: string; at: number; summary: string} | null = null
export const publishEngineNote = (key: string, summary: string) => { engine = summary ? {key, at: Date.now(), summary} : null }
export const readEngineNote = (key: string, maxAgeMs = 120_000): string => engine && engine.key === key && Date.now() - engine.at <= maxAgeMs ? engine.summary : ''
/** Soru 800 karakteri aşamaz: kullanıcının sorusu korunur, motor notu sığdığı kadar eklenir. */
export function withEngineNote(question: string, note: string, limit = 800): string {
  const q = question.trim()
  if (!note) return q
  const joined = `${q}\n\n${note}`
  if (joined.length <= limit) return joined
  const room = limit - q.length - 2
  return room >= 120 ? `${q}\n\n${note.slice(0, room - 1).trimEnd()}…` : q
}

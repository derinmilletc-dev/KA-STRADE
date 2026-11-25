// Analiz Stüdyosu · akıllı yorum motoru: canlı mumlardan ve analiz kararından kısa, okunur yorumlar üretir (ücretsiz, anında; yapay zeka çağırmaz).
// Çıktının `summary` metni Kais AI'a Sor'a da eklenebilir: yapay zeka grafiği yorumlarken motorun okumasını da görür.
import {atr, bollinger, ema, macd, rsi, sma} from './studio-indicators'

export type InsightCandle = {time: number; open: number; high: number; low: number; close: number; volume?: number}
export type InsightTone = 'up' | 'down' | 'warn' | 'info'
export type Insight = {id: string; tone: InsightTone; title: string; text: string; vote?: -1 | 0 | 1; weight?: number}
export type InsightInput = {
  candles: readonly InsightCandle[]; price?: number | null
  analysis?: {direction?: string | null; support?: number | null; resistance?: number | null; entry?: number | null; stop_loss?: number | null; tp1?: number | null} | null
  decision?: {direction?: string | null; confidenceScore?: number | null; mtfConfirmed?: number | null; mtfTotal?: number | null; riskReward?: number | null} | null
  /** Üst zaman dilimlerinin mumları (ör. 1s, 4s, 1g); yoksa MTF okuması atlanır. */
  mtf?: ReadonlyArray<{tf: string; candles: readonly InsightCandle[]}>
  /** Bu coinde açık pozisyon varsa yönetim önerileri üretilir. */
  position?: {side: 'LONG' | 'SHORT'; entry: number; stop: number | null; tp1: number | null} | null
}
export type Bias = 'up' | 'down' | 'flat'
export type Confidence = {up: number; down: number; neutral: number; total: number; pct: number; dir: Bias}
export type StructureRead = {kind: 'up' | 'down' | 'range'; bos: 'up' | 'down' | null; squeeze: boolean; text: string}
export type InsightResult = {
  ready: boolean; verdict: {tone: InsightTone; text: string}; items: Insight[]; summary: string
  confidence: Confidence; advice: Insight[]; mtf: Array<{tf: string; bias: Bias | null}>; structure: StructureRead | null
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const last = <T,>(list: readonly T[]): T | undefined => list[list.length - 1]
const lastNum = (series: ReadonlyArray<number | null>): number | null => { for (let i = series.length - 1; i >= 0; i--) { const v = series[i]; if (finite(v)) return v } return null }
const pct = (value: number, digits = 1) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(digits).replace('.', ',')}%`
const pa = (value: number, digits = 1) => `%${Math.abs(value).toFixed(digits).replace('.', ',')}`
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

// ---------------------------------------------------------------- yapı: tepe/dip dizilimi, yapı kırılımı, sıkışma
type Pivot = {i: number; v: number}
export function swings(candles: readonly InsightCandle[], k = 3): {highs: Pivot[]; lows: Pivot[]} {
  const highs: Pivot[] = [], lows: Pivot[] = []
  for (let i = k; i < candles.length - k; i++) {
    let hi = true, lo = true
    for (let j = 1; j <= k; j++) {
      if (!(candles[i].high > candles[i - j].high && candles[i].high >= candles[i + j].high)) hi = false
      if (!(candles[i].low < candles[i - j].low && candles[i].low <= candles[i + j].low)) lo = false
    }
    if (hi) highs.push({i, v: candles[i].high})
    if (lo) lows.push({i, v: candles[i].low})
  }
  return {highs, lows}
}

export function structureRead(candles: readonly InsightCandle[]): StructureRead | null {
  if (candles.length < 40) return null
  const {highs, lows} = swings(candles)
  if (highs.length < 2 || lows.length < 2) return null
  const [h2, h1] = highs.slice(-2), [l2, l1] = lows.slice(-2)
  const kind: StructureRead['kind'] = h1.v > h2.v && l1.v > l2.v ? 'up' : h1.v < h2.v && l1.v < l2.v ? 'down' : 'range'
  const close = candles[candles.length - 1].close
  const bos: StructureRead['bos'] = close > h1.v ? 'up' : close < l1.v ? 'down' : null
  const closes = candles.map(c => c.close), bb = bollinger(closes, 20, 2)
  const widths: number[] = []
  for (let i = Math.max(0, closes.length - 100); i < closes.length; i++) { const u = bb.upper[i], l = bb.lower[i], m = bb.mid[i]; if (finite(u) && finite(l) && finite(m) && m > 0) widths.push((u - l) / m) }
  const now = widths[widths.length - 1]
  const squeeze = widths.length >= 40 && finite(now) && widths.filter(w => w <= now).length / widths.length <= 0.15
  let text = kind === 'up' ? 'Yükselen tepe ve dipler (HH/HL): yapı yükseliş yönlü.' : kind === 'down' ? 'Alçalan tepe ve dipler (LH/LL): yapı düşüş yönlü.' : 'Tepe/dipler birbirini onaylamıyor: yapı yatay/karışık.'
  if (bos === 'up') text += ` Son tepenin (${num(h1.v)}) üstünde kapanış: yukarı yapı kırılımı${kind === 'down' ? ', trend dönüşü sinyali olabilir' : ''}.`
  else if (bos === 'down') text += ` Son dibin (${num(l1.v)}) altında kapanış: aşağı yapı kırılımı${kind === 'up' ? ', trend dönüşü sinyali olabilir' : ''}.`
  if (squeeze) text += ' Bollinger bantları son dönemin en dar seviyesinde: sıkışma, sert hareket öncesi olabilir.'
  return {kind, bos, squeeze, text}
}

// ---------------------------------------------------------------- zaman dilimi eğilimi (üst zaman dilimleri için hafif okuma)
export function tfBias(candles: readonly InsightCandle[]): Bias | null {
  if (candles.length < 55) return null
  const closes = candles.map(c => c.close), price = closes[closes.length - 1]
  const e20 = lastNum(ema(closes, 20)), e50 = lastNum(ema(closes, 50)), r = lastNum(rsi(closes, 14))
  if (!finite(e20) || !finite(e50)) return null
  let score = (price > e20 ? 1 : -1) + (price > e50 ? 1 : -1) + (e20 > e50 ? 1 : -1)
  if (finite(r)) score += r > 55 ? 1 : r < 45 ? -1 : 0
  const st = structureRead(candles)
  if (st) score += st.kind === 'up' ? 1 : st.kind === 'down' ? -1 : 0
  return score >= 3 ? 'up' : score <= -3 ? 'down' : 'flat'
}
const BIAS_WORD: Record<Bias, string> = {up: 'yükseliş', down: 'düşüş', flat: 'yatay'}
const TF_LABEL: Record<string, string> = {'1m': '1dk', '3m': '3dk', '5m': '5dk', '15m': '15dk', '30m': '30dk', '1h': '1s', '2h': '2s', '4h': '4s', '6h': '6s', '12h': '12s', '1d': '1g', '1w': '1h'}
export const tfLabel = (tf: string) => TF_LABEL[tf] ?? tf

/** Düşük zaman diliminden bakılan grafiğin üst zaman dilimleriyle uyumu. */
export function mtfRead(current: Bias, rows: ReadonlyArray<{tf: string; bias: Bias | null}>): Insight | null {
  const known = rows.filter((row): row is {tf: string; bias: Bias} => row.bias !== null)
  if (!known.length) return null
  const list = known.map(row => `${tfLabel(row.tf)} ${BIAS_WORD[row.bias]}`).join(', ')
  const same = known.filter(row => current !== 'flat' && row.bias === current).length
  const opposite = known.filter(row => current !== 'flat' && row.bias !== 'flat' && row.bias !== current).length
  if (current === 'flat') return {id: 'mtf', tone: 'info', title: 'Zaman dilimleri', text: `Bu grafik yatay; üst zaman dilimleri: ${list}. Yönü üst zaman dilimi belirler.`, vote: 0, weight: 2}
  if (opposite > 0 && opposite >= same) return {id: 'mtf', tone: 'warn', title: 'Zaman dilimleri', text: `Bu grafik ${BIAS_WORD[current]} ama üst zaman dilimleri ters (${list}): hareket geri çekilme/düzeltme olabilir, karşı trendde işlem riskli.`, vote: current === 'up' ? -1 : 1, weight: 2}
  if (same === known.length) return {id: 'mtf', tone: current === 'up' ? 'up' : 'down', title: 'Zaman dilimleri', text: `Tüm üst zaman dilimleri aynı yönde (${list}): ${BIAS_WORD[current]} sinyali daha güvenilir.`, vote: current === 'up' ? 1 : -1, weight: 2}
  return {id: 'mtf', tone: 'info', title: 'Zaman dilimleri', text: `Zaman dilimleri karışık (${list}): net uyum yok.`, vote: 0, weight: 2}
}

// ---------------------------------------------------------------- ana motor
export function buildInsights(input: InsightInput): InsightResult {
  const candles = input.candles
  const noConf: Confidence = {up: 0, down: 0, neutral: 0, total: 0, pct: 0, dir: 'flat'}
  const empty: InsightResult = {ready: false, verdict: {tone: 'info', text: 'Yorum için yeterli mum verisi yok.'}, items: [], summary: '', confidence: noConf, advice: [], mtf: [], structure: null}
  if (candles.length < MIN_INSIGHT_CANDLES) return empty
  const closes = candles.map(c => c.close)
  const price = finite(input.price) && input.price > 0 ? input.price : closes[closes.length - 1]
  const items: Insight[] = []
  const vote = (item: Insight, v: -1 | 0 | 1, weight = 1): Insight => ({...item, vote: v, weight})

  // Trend: EMA20 / EMA50 / EMA200
  const e20s = ema(closes, 20)
  const e20 = lastNum(e20s), e50 = lastNum(ema(closes, 50)), e200 = candles.length >= 200 ? lastNum(ema(closes, 200)) : null
  const e20Prev = e20s[closes.length - 6]
  let currentBias: Bias = 'flat'
  if (finite(e20) && finite(e50)) {
    const above20 = price > e20, above50 = price > e50, slope = finite(e20Prev) ? (e20 - e20Prev) / e20Prev * 100 : 0
    if (above20 && above50 && e20 > e50) { currentBias = 'up'; items.push(vote({id: 'trend', tone: 'up', title: 'Trend', text: `Fiyat EMA20 ve EMA50 üstünde, ortalamalar yükseliş dizilişinde${finite(e200) ? (price > e200 ? '; EMA200 de altta destek' : '; ama EMA200 hâlâ üstte direnç') : ''}.`}, 1, 2)) }
    else if (!above20 && !above50 && e20 < e50) { currentBias = 'down'; items.push(vote({id: 'trend', tone: 'down', title: 'Trend', text: `Fiyat EMA20 ve EMA50 altında, ortalamalar düşüş dizilişinde${finite(e200) ? (price < e200 ? '; EMA200 de üstte baskı yapıyor' : '; ama EMA200 altta destek veriyor') : ''}.`}, -1, 2)) }
    else items.push(vote({id: 'trend', tone: 'warn', title: 'Trend', text: `Ortalamalar karışık (EMA20 ${above20 ? 'üstünde' : 'altında'}, EMA50 ${above50 ? 'üstünde' : 'altında'}): net trend yok${Math.abs(slope) < .15 ? ', yatay seyir' : ''}.`}, 0, 2))
  }

  // Yapı
  const structure = structureRead(candles)
  if (structure) items.push(vote({id: 'structure', tone: structure.kind === 'up' ? 'up' : structure.kind === 'down' ? 'down' : structure.squeeze ? 'warn' : 'info', title: 'Yapı', text: structure.text}, structure.bos === 'up' ? 1 : structure.bos === 'down' ? -1 : structure.kind === 'up' ? 1 : structure.kind === 'down' ? -1 : 0, 2))

  // Momentum: RSI + MACD histogramı
  const r = lastNum(rsi(closes, 14))
  const m = macd(closes), hist = lastNum(m.histogram), histPrev = m.histogram[closes.length - 2]
  if (finite(r)) {
    const rising = finite(hist) && finite(histPrev) ? hist > histPrev : null
    const macdText = finite(hist) ? (hist > 0 ? 'MACD pozitif' : 'MACD negatif') + (rising === null ? '' : rising ? ', ivme artıyor' : ', ivme azalıyor') : ''
    if (r >= 70) items.push(vote({id: 'momentum', tone: 'warn', title: 'Momentum', text: `RSI ${r.toFixed(0)}: aşırı alım bölgesi, yükselişte yorulma/geri çekilme riski. ${macdText}.`}, -1))
    else if (r <= 30) items.push(vote({id: 'momentum', tone: 'warn', title: 'Momentum', text: `RSI ${r.toFixed(0)}: aşırı satım bölgesi, tepki yükselişi olasılığı artıyor. ${macdText}.`}, 1))
    else { const t: InsightTone = finite(hist) ? (hist > 0 ? 'up' : 'down') : 'info'; items.push(vote({id: 'momentum', tone: t, title: 'Momentum', text: `RSI ${r.toFixed(0)} (nötr bölge). ${macdText}.`}, t === 'up' ? 1 : t === 'down' ? -1 : 0)) }
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
  let volumeRatio: number | null = null
  if (finite(vAvg) && vAvg > 0 && finite(v) && v > 0) {
    const ratio = v / vAvg, bull = (last(candles)?.close ?? 0) >= (last(candles)?.open ?? 0)
    volumeRatio = ratio
    if (ratio >= 1.5) items.push(vote({id: 'volume', tone: bull ? 'up' : 'down', title: 'Hacim', text: `Hacim ortalamanın ${ratio.toFixed(1).replace('.', ',')} katı: ${bull ? 'yükseliş' : 'düşüş'} güçlü hacimle destekleniyor.`}, bull ? 1 : -1))
    else if (ratio <= .5) items.push(vote({id: 'volume', tone: 'warn', title: 'Hacim', text: 'Hacim zayıf: bu hareketin arkasında güçlü katılım yok, kırılımlara temkinli yaklaş.'}, 0))
  }

  // Seviye: destek / direnç mesafesi
  const sup = input.analysis?.support, res = input.analysis?.resistance
  const levelBits: string[] = []
  let levelTone: InsightTone = 'info'
  if (finite(res) && res > price) { const d = (res - price) / price * 100; levelBits.push(`direnç ${num(res)} (${pct(d)})`); if (d < .4) levelTone = 'warn' }
  if (finite(sup) && sup < price) { const d = (sup - price) / price * 100; levelBits.push(`destek ${num(sup)} (${pct(d)})`); if (Math.abs(d) < .4) levelTone = 'warn' }
  if (levelBits.length) items.push({id: 'levels', tone: levelTone, title: 'Seviyeler', text: `En yakın ${levelBits.join(' · ')}.${levelTone === 'warn' ? ' Fiyat bir seviyeye çok yakın: tepki beklenebilir.' : ''}`})

  const cr = candleRead(candles)
  if (cr) items.push(vote(cr, cr.tone === 'up' ? 1 : cr.tone === 'down' ? -1 : 0))

  // Üst zaman dilimleri
  const mtfRows = (input.mtf ?? []).map(row => ({tf: row.tf, bias: tfBias(row.candles)}))
  const mtfItem = mtfRead(currentBias, mtfRows)
  if (mtfItem) items.push(mtfItem)

  // Güven: oy veren sinyallerin dağılımı (ağırlıklı yüzde, sayımla gösterilir)
  const voters = items.filter(item => item.vote !== undefined)
  const conf: Confidence = {up: 0, down: 0, neutral: 0, total: voters.length, pct: 0, dir: 'flat'}
  let wUp = 0, wDown = 0, wAll = 0
  for (const item of voters) {
    const w = item.weight ?? 1
    wAll += w
    if (item.vote === 1) { conf.up++; wUp += w } else if (item.vote === -1) { conf.down++; wDown += w } else conf.neutral++
  }
  if (wAll > 0) { conf.pct = Math.round(Math.abs(wUp - wDown) / wAll * 100); conf.dir = wUp > wDown ? 'up' : wDown > wUp ? 'down' : 'flat' }
  if (conf.pct < 15) conf.dir = 'flat'

  // Plan: analiz yönü, MTF uyumu, R/Ö (karşılaştırma canlı güvenle yapılır)
  const dir = input.decision?.direction ?? input.analysis?.direction
  const planBits: string[] = []
  if (dir === 'LONG' || dir === 'SHORT') planBits.push(`Analiz ${dir} diyor${finite(input.decision?.confidenceScore) ? ` (güven %${Math.round(input.decision.confidenceScore)})` : ''}`)
  if (finite(input.decision?.mtfConfirmed) && finite(input.decision?.mtfTotal) && input.decision.mtfTotal > 0) planBits.push(`MTF ${input.decision.mtfConfirmed}/${input.decision.mtfTotal} uyumlu`)
  if (finite(input.decision?.riskReward)) planBits.push(`R/Ö 1:${input.decision.riskReward.toFixed(1).replace('.', ',')}`)
  const planWant: Bias = dir === 'LONG' ? 'up' : dir === 'SHORT' ? 'down' : 'flat'
  const agrees = planWant !== 'flat' && conf.dir === planWant, against = planWant !== 'flat' && conf.dir !== 'flat' && conf.dir !== planWant
  if (planBits.length) items.push({id: 'plan', tone: planWant === 'flat' ? 'info' : agrees ? (planWant === 'up' ? 'up' : 'down') : against ? 'warn' : 'info', title: 'Plan', text: `${planBits.join(' · ')}.${against ? ' Dikkat: canlı mum yorumu plan yönüyle çelişiyor.' : agrees ? ' Canlı mum yorumu da plan yönünü destekliyor.' : ''}`})

  // Öneriler: plana özel uyarılar ve açık pozisyon yönetimi
  const advice = buildAdvice({price, atr: finite(a) ? a : null, volumeRatio, input, conf, dir, currentBias})

  const strong = conf.pct >= 55
  const verdict: {tone: InsightTone; text: string} = conf.dir === 'up' ? {tone: 'up', text: strong ? 'Genel görünüm yükseliş yönlü' : 'Hafif yükseliş eğilimi'}
    : conf.dir === 'down' ? {tone: 'down', text: strong ? 'Genel görünüm düşüş yönlü' : 'Hafif düşüş eğilimi'} : {tone: 'warn', text: 'Net yön yok, bekle-gör bölgesi'}
  const dirWord = conf.dir === 'up' ? 'yükseliş' : conf.dir === 'down' ? 'düşüş' : 'yön yok'
  const parts = [`Motor yorumu: ${verdict.text.toLowerCase()} (${conf.total} sinyalden ${conf.dir === 'up' ? conf.up : conf.dir === 'down' ? conf.down : conf.neutral} ${dirWord}, güç %${conf.pct}).`]
  const order = ['mtf', 'structure', 'trend', 'momentum', 'volume', 'levels', 'candle', 'volatility', 'plan']
  for (const alert of advice.slice(0, 2)) parts.push(`Uyarı: ${alert.text}`)
  for (const id of order) { const item = items.find(x => x.id === id); if (item) parts.push(`${item.title}: ${item.text}`) }
  const summary = parts.join(' ').replace(/\s+/g, ' ').slice(0, 560)
  return {ready: true, verdict, items, summary, confidence: conf, advice, mtf: mtfRows, structure}
}

type AdviceCtx = {price: number; atr: number | null; volumeRatio: number | null; input: InsightInput; conf: Confidence; dir?: string | null; currentBias: Bias}
function buildAdvice(ctx: AdviceCtx): Insight[] {
  const {price, atr: a, input, conf} = ctx
  const out: Insight[] = []
  const pos = input.position
  if (pos && finite(pos.entry) && pos.entry > 0) {
    const long = pos.side === 'LONG'
    const risk = finite(pos.stop) ? Math.abs(pos.entry - pos.stop) : null
    const move = long ? price - pos.entry : pos.entry - price
    const r = risk && risk > 0 ? move / risk : null
    const stopWorse = finite(pos.stop) ? (long ? pos.stop < pos.entry : pos.stop > pos.entry) : false
    if (r !== null) out.push({id: 'pos-r', tone: r >= 0 ? 'up' : 'down', title: 'Pozisyon', text: `Pozisyon şu an ${r >= 0 ? '+' : '−'}${Math.abs(r).toFixed(1).replace('.', ',')}R ${r >= 0 ? 'kârda' : 'zararda'} (R = giriş-stop mesafesi).`})
    if (r !== null && r >= 1 && stopWorse) out.push({id: 'pos-be', tone: 'up', title: 'Öneri', text: 'Kâr 1R\'yi geçti ama stop hâlâ girişin gerisinde: stopu girişe çekmek (başa baş) zararı kapatır.'})
    if (finite(pos.tp1) && a) { const d = Math.abs(pos.tp1 - price); if ((long ? price < pos.tp1 : price > pos.tp1) && d <= a * .5) out.push({id: 'pos-tp', tone: 'up', title: 'Öneri', text: `TP1'e çok yakın (${pa(d / price * 100)}): kısmi kâr almayı düşün.`}) }
    if (finite(pos.stop) && a) { const d = Math.abs(price - pos.stop); if ((long ? price > pos.stop : price < pos.stop) && d <= a * .5) out.push({id: 'pos-stop', tone: 'down', title: 'Uyarı', text: `Fiyat stopa çok yakın (${pa(d / price * 100)}): pozisyon zorlanıyor, planın geçerliliğini gözden geçir.`}) }
    const want: Bias = long ? 'up' : 'down'
    if (conf.dir !== 'flat' && conf.dir !== want && conf.pct >= 30) out.push({id: 'pos-against', tone: 'warn', title: 'Uyarı', text: `Canlı yorum pozisyonunun tersine döndü (${conf.total} sinyalden ${conf.dir === 'up' ? conf.up : conf.down}'i ${conf.dir === 'up' ? 'yükseliş' : 'düşüş'}): stopu sıkılaştırmayı düşün.`})
    else if (conf.dir === want && conf.pct >= 30) out.push({id: 'pos-with', tone: 'up', title: 'Durum', text: 'Canlı yorum pozisyon yönünü destekliyor.'})
    return out
  }
  const dir = ctx.dir
  if (dir !== 'LONG' && dir !== 'SHORT') return out
  const long = dir === 'LONG'
  const an = input.analysis
  const entry = finite(an?.entry) ? an.entry : price
  if (finite(an?.stop_loss) && a) {
    const dist = Math.abs(entry - an.stop_loss)
    if (dist < a * .8) out.push({id: 'adv-stop-tight', tone: 'warn', title: 'Uyarı', text: `Stop mesafesi (${pa(dist / entry * 100)}) ATR'nin altında: piyasa gürültüsüyle tetiklenebilir, stopu biraz aç ya da girişi iyileştir.`})
    else if (dist > a * 4) out.push({id: 'adv-stop-wide', tone: 'warn', title: 'Uyarı', text: `Stop mesafesi (${pa(dist / entry * 100)}) ATR'nin 4 katından geniş: kaybın büyük olabilir, teminatı küçült.`})
  }
  const wall = long ? an?.resistance : an?.support
  if (finite(wall) && a) {
    const d = long ? wall - price : price - wall
    if (d > 0 && d < a * .6) out.push({id: 'adv-wall', tone: 'warn', title: 'Uyarı', text: `${long ? 'Direnç' : 'Destek'} (${num(wall)}) girişe çok yakın (${pa(d / price * 100)}): ilk hedefe varmadan tepki alabilirsin.`})
  }
  if (finite(input.decision?.riskReward) && input.decision.riskReward < 1) out.push({id: 'adv-rr', tone: 'warn', title: 'Uyarı', text: `Risk/ödül 1:${input.decision.riskReward.toFixed(1).replace('.', ',')}: kazanç potansiyeli riskten düşük.`})
  if (ctx.volumeRatio !== null && ctx.volumeRatio < .6) out.push({id: 'adv-vol', tone: 'warn', title: 'Uyarı', text: 'Hacim zayıf: hacimsiz hareketlerde kırılımlar sık geri döner.'})
  const want: Bias = long ? 'up' : 'down'
  if (conf.dir !== 'flat' && conf.dir !== want && conf.pct >= 30) out.push({id: 'adv-conflict', tone: 'warn', title: 'Uyarı', text: `Canlı yorum ${long ? 'LONG' : 'SHORT'} planının tersini gösteriyor: girişi bekletmeyi düşün.`})
  return out
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

// ---------------------------------------------------------------- Kais AI yorumu: ChartAiPanel sonucu bitirince yayınlar, çip altında gösterir
type AiNote = {key: string; at: number; text: string}
let ai: AiNote | null = null
const aiListeners = new Set<() => void>()
export const publishAiNote = (key: string, text: string) => { ai = text.trim() ? {key, at: Date.now(), text: text.trim()} : null; aiListeners.forEach(listener => listener()) }
export const readAiNote = (key: string, maxAgeMs = 30 * 60_000): AiNote | null => ai && ai.key === key && Date.now() - ai.at <= maxAgeMs ? ai : null
export const subscribeAiNote = (listener: () => void) => { aiListeners.add(listener); return () => { aiListeners.delete(listener) } }
export const OPEN_CHART_AI_EVENT = 'kaistrade:chart-ai-open'
/** AI sonucundan çipte gösterilecek kısa bölüm: başlıkları atar, ilk iki paragrafı kısaltır. */
export function aiPreview(text: string, limit = 380): string {
  const blocks = text.split(/\n{2,}/).map(block => block.split('\n').slice(1).join(' ').trim() || block.trim()).filter(Boolean)
  const joined = blocks.slice(0, 2).join(' ').replace(/\s+/g, ' ')
  return joined.length <= limit ? joined : `${joined.slice(0, limit - 1).trimEnd()}…`
}

const TF_SECONDS: Record<string, number> = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '12h': 43200, '1d': 86400, '1w': 604800}
const TF_LADDER = ['5m', '15m', '1h', '4h', '1d']
/** Bu zaman diliminin üstündeki, okunmaya değer en çok 3 zaman dilimi (ör. 15dk → 1s, 4s, 1g). */
export function higherTimeframes(interval: string): string[] {
  const seconds = TF_SECONDS[interval]
  if (!seconds) return []
  return TF_LADDER.filter(tf => TF_SECONDS[tf] > seconds * 1.5).slice(0, 3)
}

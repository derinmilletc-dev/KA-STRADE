// Grid uygunluk puanı — SAF fonksiyonlar, ağ/emir yok. Yalnızca ekranda bilgi verir, grid emir mantığına dokunmaz.
// Puan: "ŞU AN, seçtiğin aralık için piyasa koşulları grid'e ne kadar uygun?" Kâr tahmini veya garantisi DEĞİLDİR.
// Ağırlık: piyasa durumu %45, oynaklık %20, fiyatın aralıktaki yeri %20, momentum %15.

export type SuitCandle = {time: number; open: number; high: number; low: number; close: number; volume: number}
export type SuitLang = 'tr' | 'en'
export type SuitLevel = 'good' | 'caution' | 'poor'
export type SuitRegime = 'range' | 'mild' | 'strong' | 'extreme'
export type SuitVol = 'low' | 'ideal' | 'high'
export type SuitPos = 'center' | 'edge' | 'near' | 'outside'
export type SuitMomentum = 'neutral' | 'mixed' | 'strong'
export type SuitDirection = 'LONG' | 'SHORT' | 'NEUTRAL'

export type SuitInput = {
  price: number
  lower: number
  upper: number
  side: 'LONG' | 'SHORT'
  c15: readonly SuitCandle[]
  c1h: readonly SuitCandle[]
  c4h: readonly SuitCandle[]
  now?: number   // saniye; verilmezse şimdiki zaman (veri tazeliği kontrolü için)
}

export type SuitResult = {
  ok: boolean
  score: number
  level: SuitLevel
  regime: SuitRegime
  vol: SuitVol
  pos: SuitPos
  momentum: SuitMomentum
  direction: SuitDirection
  parts: {regime: number; vol: number; pos: number; momentum: number}
  adx1h: number | null
  adx4h: number | null
  expectedMovePct: number | null
  rangePct: number
  headline: string
  detail: string
  warnings: string[]
}

export const SUIT_MIN_CANDLES = 60
const clamp = (value: number, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, value))
const lerp = (x: number, x0: number, x1: number, y0: number, y1: number) => x0 === x1 ? y1 : y0 + (y1 - y0) * clamp((x - x0) / (x1 - x0), 0, 1)

/** Wilder ADX; yeterli mum yoksa null. */
export function adx(candles: readonly SuitCandle[], period = 14): number | null {
  if (candles.length < period * 2 + 1) return null
  let trS = 0, pS = 0, mS = 0
  const dxs: number[] = []
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], p = candles[i - 1]
    const tr = Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close))
    const up = c.high - p.high, down = p.low - c.low
    const plus = up > down && up > 0 ? up : 0, minus = down > up && down > 0 ? down : 0
    if (i <= period) { trS += tr; pS += plus; mS += minus } else { trS = trS - trS / period + tr; pS = pS - pS / period + plus; mS = mS - mS / period + minus }
    if (i >= period && trS > 0) {
      const pi = 100 * pS / trS, mi = 100 * mS / trS
      dxs.push(pi + mi > 0 ? 100 * Math.abs(pi - mi) / (pi + mi) : 0)
    }
  }
  if (dxs.length < period) return null
  let value = dxs.slice(0, period).reduce((s, v) => s + v, 0) / period
  for (let i = period; i < dxs.length; i++) value = (value * (period - 1) + dxs[i]) / period
  return Number.isFinite(value) ? value : null
}

export function atrPct(candles: readonly SuitCandle[], period = 14): number | null {
  if (candles.length < period + 2) return null
  const tr = candles.map((c, i) => i === 0 ? c.high - c.low : Math.max(c.high - c.low, Math.abs(c.high - candles[i - 1].close), Math.abs(c.low - candles[i - 1].close)))
  let prev = tr.slice(1, period + 1).reduce((s, v) => s + v, 0) / period
  for (let i = period + 1; i < candles.length; i++) prev = (prev * (period - 1) + tr[i]) / period
  const last = candles[candles.length - 1].close
  return last > 0 && Number.isFinite(prev) ? prev / last * 100 : null
}

export function emaLast(values: readonly number[], period: number): number | null {
  if (values.length < period) return null
  const k = 2 / (period + 1)
  let e = values.slice(0, period).reduce((s, v) => s + v, 0) / period
  for (let i = period; i < values.length; i++) e = values[i] * k + e * (1 - k)
  return e
}

/** Bir zaman diliminin eğimi: +1 yukarı, -1 aşağı, 0 yatay (EMA20-EMA50 farkı ATR'ye göre). */
export function trendSign(candles: readonly SuitCandle[]): -1 | 0 | 1 {
  const closes = candles.map(c => c.close)
  const fast = emaLast(closes, 20), slow = emaLast(closes, 50), a = atrPct(candles)
  if (fast === null || slow === null || a === null || fast <= 0) return 0
  const dist = Math.abs(fast - slow) / fast * 100
  if (dist < a * 0.5) return 0
  return fast > slow ? 1 : -1
}

function regimeScore(a1: number | null, a4: number | null): {score: number; regime: SuitRegime} {
  const parts: Array<[number, number]> = []
  if (a1 !== null) parts.push([a1, 0.6])
  if (a4 !== null) parts.push([a4, 0.4])
  if (!parts.length) return {score: 50, regime: 'mild'}
  const weight = parts.reduce((sum, [, w]) => sum + w, 0)
  const adxValue = parts.reduce((sum, [v, w]) => sum + v * w, 0) / weight
  const score = adxValue < 18 ? 100 : adxValue < 25 ? lerp(adxValue, 18, 25, 100, 62) : adxValue < 35 ? lerp(adxValue, 25, 35, 62, 22) : lerp(adxValue, 35, 50, 22, 0)
  return {score, regime: adxValue < 20 ? 'range' : adxValue < 28 ? 'mild' : adxValue < 38 ? 'strong' : 'extreme'}
}

function volScore(expectedPct: number | null, rangePct: number): {score: number; vol: SuitVol} {
  if (expectedPct === null || !(rangePct > 0)) return {score: 50, vol: 'ideal'}
  const ratio = expectedPct / rangePct   // beklenen günlük hareket / grid aralığı
  const score = ratio < 0.25 ? lerp(ratio, 0, 0.25, 20, 55) : ratio < 0.5 ? lerp(ratio, 0.25, 0.5, 55, 100) : ratio <= 1.2 ? 100 : ratio < 2.5 ? lerp(ratio, 1.2, 2.5, 100, 15) : lerp(ratio, 2.5, 4, 15, 0)
  return {score, vol: ratio < 0.3 ? 'low' : ratio > 1.8 ? 'high' : 'ideal'}
}

function posScore(price: number, lower: number, upper: number): {score: number; pos: SuitPos} {
  if (!(upper > lower) || !(price > 0)) return {score: 0, pos: 'outside'}
  if (price < lower || price > upper) return {score: 0, pos: 'outside'}
  const d = Math.abs((price - lower) / (upper - lower) - 0.5) * 2   // 0 = tam orta, 1 = kenar
  const score = d <= 0.4 ? 100 : d <= 0.8 ? lerp(d, 0.4, 0.8, 100, 40) : lerp(d, 0.8, 1, 40, 5)
  return {score, pos: d <= 0.4 ? 'center' : d <= 0.8 ? 'edge' : 'near'}
}

function momentumScore(signs: readonly number[], move24Pct: number | null): {score: number; momentum: SuitMomentum} {
  const up = signs.filter(s => s > 0).length, down = signs.filter(s => s < 0).length
  const aligned = Math.max(up, down)
  let score = aligned === 0 ? 100 : aligned === 1 ? 80 : aligned === 2 ? 45 : 8
  if (up > 0 && down > 0) score = Math.max(score, 60)   // yönler birbirini yiyor: yatay sayılır
  if (move24Pct !== null && move24Pct > 8) score *= 0.5
  else if (move24Pct !== null && move24Pct > 5) score *= 0.75
  const momentum: SuitMomentum = aligned >= 3 || (move24Pct !== null && move24Pct > 8) ? 'strong' : aligned === 0 || (up > 0 && down > 0) ? 'neutral' : 'mixed'
  return {score, momentum}
}

const T = {
  tr: {
    good: 'Uygun', caution: 'Dikkat', poor: 'Riskli',
    h: {good: 'Koşullar grid için uygun', caution: 'Grid için koşullar orta', poor: 'Grid için koşullar zayıf'},
    regime: {range: 'Piyasa yatay, grid için iyi.', mild: 'Hafif trend var.', strong: 'Güçlü trend var, fiyat aralıktan çıkabilir.', extreme: 'Çok sert trend var, grid için risk yüksek.'},
    vol: {low: 'Hareket aralığa göre çok az, kademeler zor dolar.', ideal: 'Oynaklık aralığına uygun.', high: 'Oynaklık aralığa göre çok yüksek, fiyat aralıktan çıkabilir.'},
    pos: {center: 'Fiyat aralığın ortasında.', edge: 'Fiyat aralığın kenarına doğru.', near: 'Fiyat aralığın kenarına çok yakın.', outside: 'Fiyat seçtiğin aralığın dışında.'},
    wrongSide: (side: string, dir: string) => `Seçtiğin yön ${side}, piyasa eğimi ${dir === 'LONG' ? 'yukarı' : 'aşağı'}.`,
    noData: 'Piyasa verisi okunamadı; puan hesaplanamadı.',
    stale: 'Piyasa verisi gecikmiş; güvenilir puan gösterilemiyor.'
  },
  en: {
    good: 'Suitable', caution: 'Caution', poor: 'Risky',
    h: {good: 'Conditions suit a grid', caution: 'Grid conditions are moderate', poor: 'Grid conditions are weak'},
    regime: {range: 'Market is ranging, good for a grid.', mild: 'Mild trend.', strong: 'Strong trend; price may leave the range.', extreme: 'Very sharp trend; high risk for a grid.'},
    vol: {low: 'Movement is small for this range; levels may rarely fill.', ideal: 'Volatility fits the range.', high: 'Volatility is high for this range; price may leave it.'},
    pos: {center: 'Price is near the middle of the range.', edge: 'Price is drifting toward the edge of the range.', near: 'Price is very close to the edge of the range.', outside: 'Price is outside the range you chose.'},
    wrongSide: (side: string, dir: string) => `You chose ${side}, but the market leans ${dir === 'LONG' ? 'up' : 'down'}.`,
    noData: 'Market data could not be read; score unavailable.',
    stale: 'Market data is stale; a reliable score cannot be shown.'
  }
} as const

export const suitLabels = (lang: SuitLang) => ({good: T[lang].good, caution: T[lang].caution, poor: T[lang].poor})

export function computeSuitability(input: SuitInput, lang: SuitLang = 'tr'): SuitResult {
  const t = T[lang]
  const rangePct = input.lower > 0 && input.upper > input.lower ? (input.upper - input.lower) / ((input.upper + input.lower) / 2) * 100 : 0
  const empty: SuitResult = {ok: false, score: 0, level: 'poor', regime: 'mild', vol: 'ideal', pos: 'outside', momentum: 'mixed', direction: 'NEUTRAL', parts: {regime: 0, vol: 0, pos: 0, momentum: 0}, adx1h: null, adx4h: null, expectedMovePct: null, rangePct, headline: t.noData, detail: '', warnings: []}
  if (!(input.price > 0) || input.c1h.length < SUIT_MIN_CANDLES || input.c4h.length < 30 || input.c15.length < SUIT_MIN_CANDLES) return empty

  // Veri tazeliği: son mum çok eskiyse varsayılan/rastgele puan göstermek yerine puan üretilmez.
  const now = input.now ?? Date.now() / 1000
  const lastT = (list: readonly SuitCandle[]) => list[list.length - 1].time
  if (now - lastT(input.c15) > 15 * 60 * 3 || now - lastT(input.c1h) > 3600 * 3 || now - lastT(input.c4h) > 4 * 3600 * 3) return {...empty, headline: t.stale}

  const a1 = adx(input.c1h), a4 = adx(input.c4h)
  const reg = regimeScore(a1, a4)
  const hourAtr = atrPct(input.c1h)
  const expected = hourAtr === null ? null : hourAtr * Math.sqrt(24)   // yaklaşık günlük hareket
  const vol = volScore(expected, rangePct)
  const pos = posScore(input.price, input.lower, input.upper)
  const s15 = trendSign(input.c15), s1 = trendSign(input.c1h), s4 = trendSign(input.c4h)
  const last24 = input.c1h.slice(-25)
  const move24 = last24.length >= 25 && last24[0].close > 0 ? Math.abs(last24[last24.length - 1].close - last24[0].close) / last24[0].close * 100 : null
  const mom = momentumScore([s15, s1, s4], move24)

  const score = Math.round(clamp(reg.score * 0.45 + vol.score * 0.2 + pos.score * 0.2 + mom.score * 0.15))
  const level: SuitLevel = score >= 75 ? 'good' : score >= 50 ? 'caution' : 'poor'
  // Yön önerisi puandan bağımsız: yatay ise nötr, değilse 1sa ve 4sa eğimi.
  const lean = s4 !== 0 ? s4 : s1
  const direction: SuitDirection = reg.regime === 'range' || lean === 0 ? 'NEUTRAL' : lean > 0 ? 'LONG' : 'SHORT'
  const warnings: string[] = []
  if (direction !== 'NEUTRAL' && direction !== input.side && (reg.regime === 'strong' || reg.regime === 'extreme')) warnings.push(t.wrongSide(input.side, direction))
  if (pos.pos === 'outside' || pos.pos === 'near') warnings.push(t.pos[pos.pos])
  if (vol.vol !== 'ideal') warnings.push(t.vol[vol.vol])
  return {
    ok: true, score, level, regime: reg.regime, vol: vol.vol, pos: pos.pos, momentum: mom.momentum, direction,
    parts: {regime: Math.round(reg.score), vol: Math.round(vol.score), pos: Math.round(pos.score), momentum: Math.round(mom.score)},
    adx1h: a1, adx4h: a4, expectedMovePct: expected, rangePct,
    headline: t.h[level], detail: `${t.regime[reg.regime]} ${t.pos[pos.pos]}`, warnings
  }
}

// ======================================================================
// DCA uygunluk puanı (Grid'den ayrı). Soru: "Seçtiğim yönde fiyat ters devam ederse bu DCA yapısı bunu kaldırır mı?"
// Ağırlık: trend/yön uyumu %40, oynaklık-ek alım mesafesi %25, toparlanma yapısı %20, fiyatın aşırı uzaması %15.
// SAF fonksiyonlar, ağ/emir yok; AI yön puanıyla karıştırılmaz.
export type DcaSuitLevel = 'good' | 'caution' | 'poor'
export type DcaSuitInput = {
  side: 'LONG' | 'SHORT'
  price: number
  stepPct: number          // ek emirler arası aralık (%)
  safetyOrders: number
  stopGapPct: number       // son ek emrin ötesindeki stop mesafesi (%)
  c15: readonly SuitCandle[]
  c1h: readonly SuitCandle[]
  c4h: readonly SuitCandle[]
  now?: number             // saniye; verilmezse şimdiki zaman (veri tazeliği)
}
export type DcaSuitResult = {
  ok: boolean
  score: number
  level: DcaSuitLevel
  parts: {trend: number; vol: number; recovery: number; stretch: number}
  trend: 'with' | 'flat' | 'against' | 'strongAgainst'
  volState: 'calm' | 'ok' | 'fast' | 'wild'
  stretch: 'normal' | 'high' | 'extreme'
  recovery: 'yes' | 'partial' | 'no'
  spanPct: number
  expectedMovePct: number | null
  headline: string
  detail: string
  warnings: string[]
}


const DT = {
  tr: {
    h: {good: 'DCA için koşullar uygun', caution: 'DCA için koşullar orta', poor: 'DCA koşulları şu an uygun değil'},
    trend: {with: 'Trend senin yönünde.', flat: 'Piyasa yatay, ters güçlü trend yok.', against: 'Trend senin yönüne ters.', strongAgainst: 'Güçlü trend senin yönüne ters.'},
    warnAgainst: (side: string) => `${side} DCA seçtin ama güçlü trend ters yönde; ek alımlar zararı büyütebilir.`,
    warnVol: 'Oynaklık ek alım aralığına göre yüksek; ek alımlar hızla biter, stop yaklaşır.',
    warnStretch: 'Fiyat son hareketinde aşırı uzamış; tepeden/dipten başlama riski var.',
    warnRec: 'Toparlanma işareti yok; fiyat hâlâ yeni dip/tepe yapıyor.',
    noData: 'Piyasa verisi okunamadı; puan hesaplanamadı.', stale: 'Piyasa verisi gecikmiş; güvenilir puan gösterilemiyor.'
  },
  en: {
    h: {good: 'Conditions suit a DCA', caution: 'DCA conditions are moderate', poor: 'DCA conditions are unfavorable right now'},
    trend: {with: 'Trend is with your direction.', flat: 'Market is flat, no strong opposing trend.', against: 'Trend leans against your direction.', strongAgainst: 'A strong trend runs against your direction.'},
    warnAgainst: (side: string) => `You chose ${side} DCA but a strong trend runs the other way; extra buys can deepen the loss.`,
    warnVol: 'Volatility is high for this step size; safety orders may run out fast and the stop gets close.',
    warnStretch: 'Price looks overextended after its recent move; risk of starting at a top/bottom.',
    warnRec: 'No sign of recovery; price is still making new lows/highs.',
    noData: 'Market data could not be read; score unavailable.', stale: 'Market data is stale; a reliable score cannot be shown.'
  }
} as const

export function computeDcaSuitability(input: DcaSuitInput, lang: SuitLang = 'tr'): DcaSuitResult {
  const t = DT[lang]
  const spanPct = input.stepPct * input.safetyOrders + input.stopGapPct
  const empty: DcaSuitResult = {ok: false, score: 0, level: 'poor', parts: {trend: 0, vol: 0, recovery: 0, stretch: 0}, trend: 'flat', volState: 'ok', stretch: 'normal', recovery: 'partial', spanPct, expectedMovePct: null, headline: t.noData, detail: '', warnings: []}
  if (!(input.price > 0) || !(input.stepPct > 0) || input.c15.length < SUIT_MIN_CANDLES || input.c1h.length < SUIT_MIN_CANDLES || input.c4h.length < 30) return empty
  const now = input.now ?? Date.now() / 1000
  const last = (l: readonly SuitCandle[]) => l[l.length - 1].time
  if (now - last(input.c15) > 15 * 60 * 3 || now - last(input.c1h) > 3600 * 3 || now - last(input.c4h) > 4 * 3600 * 3) return {...empty, headline: t.stale}

  const dir = input.side === 'LONG' ? 1 : -1
  // 1) Trend / yön uyumu (%40): 4sa %45, 1sa %35, 15dk %20; yön ters ve ADX yüksekse sert düşer.
  const al = trendSign(input.c4h) * 0.45 * dir + trendSign(input.c1h) * 0.35 * dir + trendSign(input.c15) * 0.2 * dir   // -1..+1
  const a1 = adx(input.c1h), a4 = adx(input.c4h)
  const adxAvg = [a1, a4].filter((v): v is number => v !== null).reduce((s, v, _, arr) => s + v / arr.length, 0)
  const k = 0.5 + 0.5 * clamp((adxAvg - 15) / 20, 0, 1)
  const trendScore = al >= 0 ? 70 + 30 * al : 70 + 70 * al * k
  const trend = al > 0.3 ? 'with' : al >= -0.3 ? 'flat' : adxAvg >= 25 && al <= -0.55 ? 'strongAgainst' : 'against'

  // 2) Oynaklık / ek alım mesafesi (%25): beklenen günlük hareket, tüm DCA aralığına (ek alımlar + stop) göre.
  const hourAtr = atrPct(input.c1h), qAtr = atrPct(input.c15)
  const expected = hourAtr === null ? null : hourAtr * Math.sqrt(24)
  let volScore = 60
  if (expected !== null && spanPct > 0) {
    const r = expected / spanPct
    volScore = r < 0.35 ? 100 : r < 0.7 ? lerp(r, 0.35, 0.7, 100, 70) : r < 1.2 ? lerp(r, 0.7, 1.2, 70, 30) : lerp(r, 1.2, 2.2, 30, 0)
    if (qAtr !== null && qAtr > input.stepPct) volScore -= 15   // tek 15dk mum bile ek alım aralığından büyük
  }
  volScore = clamp(volScore)
  const volState = volScore >= 80 ? 'calm' : volScore >= 55 ? 'ok' : volScore >= 30 ? 'fast' : 'wild'

  // 3) Fiyatın aşırı uzaması (%15): seçilen yönde 1sa EMA20'den kaç ATR uzakta + 24 saatlik hareket.
  const ema20 = emaLast(input.c1h.map(c => c.close), 20)
  let stretchScore = 100
  if (ema20 !== null && hourAtr !== null && hourAtr > 0) {
    const ext = ((input.price - ema20) / ema20 * 100 * dir) / hourAtr   // ATR cinsinden, yönümüzde
    stretchScore = ext <= 1.5 ? 100 : ext < 3.5 ? lerp(ext, 1.5, 3.5, 100, 20) : lerp(ext, 3.5, 5, 20, 0)
  }
  const h24 = input.c1h.slice(-25)
  const move24 = h24.length >= 25 && h24[0].close > 0 ? (h24[h24.length - 1].close - h24[0].close) / h24[0].close * 100 * dir : null
  if (move24 !== null && move24 > 8) stretchScore *= 0.6
  stretchScore = clamp(stretchScore)
  const stretch = stretchScore >= 70 ? 'normal' : stretchScore >= 35 ? 'high' : 'extreme'

  // 4) Toparlanma yapısı (%20): 15dk yönümüze dönmüş mü, kapanış EMA21 üstünde/altında mı, yükselen dip / alçalan tepe var mı, yeni dip/tepe yok mu.
  const c15 = input.c15, closes15 = c15.map(c => c.close)
  const e21 = emaLast(closes15, 21)
  let rec = 0
  if (trendSign(c15) * dir > 0) rec += 35
  if (e21 !== null && (c15[c15.length - 1].close - e21) * dir > 0) rec += 30
  const recent = c15.slice(-8), before = c15.slice(-16, -8)
  const higherLow = dir > 0 ? Math.min(...recent.map(c => c.low)) > Math.min(...before.map(c => c.low)) : Math.max(...recent.map(c => c.high)) < Math.max(...before.map(c => c.high))
  if (higherLow) rec += 35
  const win = c15.slice(-24)
  const freshExtreme = dir > 0 ? c15[c15.length - 1].low <= Math.min(...win.map(c => c.low)) * 1.0005 : c15[c15.length - 1].high >= Math.max(...win.map(c => c.high)) * 0.9995
  if (freshExtreme) rec *= 0.5
  const recScore = clamp(rec)
  const recovery = recScore >= 65 ? 'yes' : recScore >= 35 ? 'partial' : 'no'

  const score = Math.round(clamp(trendScore * 0.4 + volScore * 0.25 + recScore * 0.2 + stretchScore * 0.15))
  const level: DcaSuitLevel = score >= 70 ? 'good' : score >= 45 ? 'caution' : 'poor'
  const warnings: string[] = []
  if (trend === 'strongAgainst' || trend === 'against') warnings.push(t.warnAgainst(input.side))
  if (volState === 'fast' || volState === 'wild') warnings.push(t.warnVol)
  if (stretch !== 'normal') warnings.push(t.warnStretch)
  if (recovery === 'no' && trend !== 'with') warnings.push(t.warnRec)
  return {ok: true, score, level, parts: {trend: Math.round(clamp(trendScore)), vol: Math.round(volScore), recovery: Math.round(recScore), stretch: Math.round(stretchScore)}, trend, volState, stretch, recovery, spanPct, expectedMovePct: expected, headline: t.h[level], detail: t.trend[trend], warnings}
}

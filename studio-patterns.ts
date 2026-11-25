// Mum formasyonu tanıma: klasik 1-3 mumluk formasyonlar. Saf mantık. Formasyon bir tahmin değil bağlam ipucudur; ekranda öyle belirtilir.
export type PatternCandle = {open: number; high: number; low: number; close: number}
export type PatternId = 'doji' | 'hammer' | 'shootingStar' | 'bullEngulf' | 'bearEngulf' | 'morningStar' | 'eveningStar' | 'soldiers' | 'crows'
export type PatternHit = {index: number; id: PatternId; bias: 'bull' | 'bear' | 'neutral'}
export const PATTERNS: Record<PatternId, {label: string; short: string; bias: PatternHit['bias']; hint: string}> = {
  doji: {label: 'Doji', short: 'D', bias: 'neutral', hint: 'Kararsızlık: açılış ve kapanış neredeyse aynı'},
  hammer: {label: 'Çekiç', short: 'Ç', bias: 'bull', hint: 'Düşüşte uzun alt fitil: alıcılar toparlıyor olabilir'},
  shootingStar: {label: 'Kayan yıldız', short: 'KY', bias: 'bear', hint: 'Yükselişte uzun üst fitil: satıcılar devralıyor olabilir'},
  bullEngulf: {label: 'Yutan boğa', short: 'YB', bias: 'bull', hint: 'Yeşil mum önceki kırmızıyı tamamen yutar'},
  bearEngulf: {label: 'Yutan ayı', short: 'YA', bias: 'bear', hint: 'Kırmızı mum önceki yeşili tamamen yutar'},
  morningStar: {label: 'Sabah yıldızı', short: 'SY', bias: 'bull', hint: '3 mum: düşüş, küçük gövde, güçlü yükseliş'},
  eveningStar: {label: 'Akşam yıldızı', short: 'AY', bias: 'bear', hint: '3 mum: yükseliş, küçük gövde, güçlü düşüş'},
  soldiers: {label: 'Üç beyaz asker', short: '3Y', bias: 'bull', hint: 'Üst üste üç güçlü yeşil mum'},
  crows: {label: 'Üç kara karga', short: '3K', bias: 'bear', hint: 'Üst üste üç güçlü kırmızı mum'},
}
const body = (c: PatternCandle) => Math.abs(c.close - c.open)
const range = (c: PatternCandle) => c.high - c.low
const up = (c: PatternCandle) => c.close > c.open
const down = (c: PatternCandle) => c.close < c.open
const upper = (c: PatternCandle) => c.high - Math.max(c.open, c.close)
const lower = (c: PatternCandle) => Math.min(c.open, c.close) - c.low
const valid = (c: PatternCandle | undefined): c is PatternCandle => !!c && [c.open, c.high, c.low, c.close].every(Number.isFinite) && c.high >= c.low && range(c) > 0
/** Önceki `n` mumun kapanış eğimi: >0 yükselen, <0 düşen bağlam. */
const trend = (candles: readonly PatternCandle[], index: number, n = 5) => index >= n ? candles[index - 1].close - candles[index - n].close : 0

export function detectAt(candles: readonly PatternCandle[], i: number): PatternHit[] {
  const c = candles[i], p = candles[i - 1], pp = candles[i - 2]
  if (!valid(c)) return []
  const hits: PatternHit[] = []
  const add = (id: PatternId) => hits.push({index: i, id, bias: PATTERNS[id].bias})
  const t = trend(candles, i)
  if (body(c) <= range(c) * 0.1) add('doji')
  else if (lower(c) >= body(c) * 2 && upper(c) <= body(c) * 0.5 && body(c) >= range(c) * 0.1 && t < 0) add('hammer')
  else if (upper(c) >= body(c) * 2 && lower(c) <= body(c) * 0.5 && body(c) >= range(c) * 0.1 && t > 0) add('shootingStar')
  if (valid(p)) {
    if (down(p) && up(c) && c.open <= p.close && c.close >= p.open && body(c) > body(p)) add('bullEngulf')
    if (up(p) && down(c) && c.open >= p.close && c.close <= p.open && body(c) > body(p)) add('bearEngulf')
  }
  if (valid(p) && valid(pp)) {
    const small = body(p) <= range(p) * 0.3
    const pMid = (pp.open + pp.close) / 2
    if (down(pp) && body(pp) >= range(pp) * 0.5 && small && up(c) && c.close > pMid && body(c) >= range(c) * 0.5) add('morningStar')
    if (up(pp) && body(pp) >= range(pp) * 0.5 && small && down(c) && c.close < pMid && body(c) >= range(c) * 0.5) add('eveningStar')
    const strong = (x: PatternCandle) => body(x) >= range(x) * 0.6
    if (up(pp) && up(p) && up(c) && strong(pp) && strong(p) && strong(c) && p.close > pp.close && c.close > p.close && p.open >= pp.open && c.open >= p.open) add('soldiers')
    if (down(pp) && down(p) && down(c) && strong(pp) && strong(p) && strong(c) && p.close < pp.close && c.close < p.close && p.open <= pp.open && c.open <= p.open) add('crows')
  }
  return hits
}

/** [from, to) aralığındaki formasyonlar; çok kalabalık olmasın diye mum başına en fazla bir (en bilgili) formasyon döner. */
export function detectPatterns(candles: readonly PatternCandle[], from = 0, to = candles.length): PatternHit[] {
  const order: PatternId[] = ['morningStar', 'eveningStar', 'soldiers', 'crows', 'bullEngulf', 'bearEngulf', 'hammer', 'shootingStar', 'doji']
  const out: PatternHit[] = []
  for (let i = Math.max(0, from); i < Math.min(candles.length, to); i++) {
    const hits = detectAt(candles, i)
    if (!hits.length) continue
    out.push(hits.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id))[0])
  }
  return out
}

// Manuel emir "Dalgaya göre" stop önerisi. Yalnız form alanını doldurmak için hesaplar; emir göndermez.
// Kural (basit ve açıklanabilir): 15 dk mumlarında ortalama gerçek aralık (ATR 14) ve son 12 mumun dibi/tepesi.
// Stop, son dibin (SHORT'ta tepenin) çeyrek ATR ötesine konur; ama girişten en az 1,5 ATR, en çok 3 ATR uzakta kalır.
// Böylece birkaç saniyelik normal dalgalanma stopu vurmaz, stop da gereksiz uzağa gitmez.
export type StopCandle = {time?: number; high: number; low: number; close: number}
export type VolatilityStop = {stop: number; distancePct: number; atr: number; atrPct: number; basis: 'swing' | 'atr_min' | 'atr_max' | 'pct_min'}

const ATR_PERIOD = 14
const SWING_BARS = 12
const MIN_ATR = 1.5
const MAX_ATR = 3
const SWING_BUFFER_ATR = 0.25
const MAX_DISTANCE_PCT = 15
// Çok sakin coinde bile stop girişe %0,6'dan yakın olmaz (komisyon + kayma payı ve anlık iğneler için).
const MIN_DISTANCE_PCT = 0.6

const valid = (c: StopCandle) => [c.high, c.low, c.close].every(Number.isFinite) && c.high >= c.low && c.low > 0

export function averageTrueRange(candles: readonly StopCandle[], period = ATR_PERIOD): number | null {
  if (candles.length < period + 1) return null
  const recent = candles.slice(-(period + 1))
  let total = 0
  for (let index = 1; index < recent.length; index++) {
    const {high, low} = recent[index], previous = recent[index - 1].close
    total += Math.max(high - low, Math.abs(high - previous), Math.abs(low - previous))
  }
  return total / period
}

/** Son mum hâlâ oluşuyor olabilir; `closedOnly` true ise hesaptan çıkarılır. */
export function suggestVolatilityStop(raw: readonly StopCandle[], entry: number, direction: 'LONG' | 'SHORT', closedOnly = true): VolatilityStop | null {
  if (!(entry > 0)) return null
  const candles = raw.filter(valid)
  const closed = closedOnly ? candles.slice(0, -1) : candles
  const atr = averageTrueRange(closed)
  if (atr === null || !(atr > 0)) return null
  const swingBars = closed.slice(-SWING_BARS)
  const long = direction === 'LONG'
  const swing = long ? Math.min(...swingBars.map(c => c.low)) : Math.max(...swingBars.map(c => c.high))
  const swingDistance = long ? entry - (swing - SWING_BUFFER_ATR * atr) : (swing + SWING_BUFFER_ATR * atr) - entry
  let distance = swingDistance
  let basis: VolatilityStop['basis'] = 'swing'
  if (!(distance >= MIN_ATR * atr)) { distance = MIN_ATR * atr; basis = 'atr_min' }
  if (distance > MAX_ATR * atr) { distance = MAX_ATR * atr; basis = 'atr_max' }
  let distancePct = Math.min(distance / entry * 100, MAX_DISTANCE_PCT)
  if (distancePct < MIN_DISTANCE_PCT) { distancePct = MIN_DISTANCE_PCT; basis = 'pct_min' }
  const stop = long ? entry * (1 - distancePct / 100) : entry * (1 + distancePct / 100)
  if (!(stop > 0)) return null
  return {stop, distancePct, atr, atrPct: atr / entry * 100, basis}
}

// Analiz stüdyosu göstergeleri: saf hesap (React yok), grafikteki mumlarla aynı uzunlukta diziler döner.
// Isınma süresindeki değerler null'dır (çizilmez). Hesaplar TradingView/Wilder tanımlarıyla uyumludur.
export type IndicatorCandle = {time: number; open: number; high: number; low: number; close: number; volume: number}
export type Series = Array<number | null>

export function sma(values: readonly number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null)
  if (!Number.isInteger(period) || period < 1) return out
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= period) sum -= values[i - period]
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

// İlk değer = ilk `period` mumun ortalaması (TradingView ile aynı tohumlama), sonra üstel ağırlık.
export function ema(values: readonly number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null)
  if (!Number.isInteger(period) || period < 1 || values.length < period) return out
  const k = 2 / (period + 1)
  let prev = 0
  for (let i = 0; i < period; i++) prev += values[i]
  prev /= period
  out[period - 1] = prev
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export type BollingerBands = {mid: Series; upper: Series; lower: Series}
export function bollinger(values: readonly number[], period = 20, mult = 2): BollingerBands {
  const mid = sma(values, period)
  const upper: Series = new Array(values.length).fill(null)
  const lower: Series = new Array(values.length).fill(null)
  for (let i = period - 1; i < values.length; i++) {
    const m = mid[i]
    if (m === null) continue
    let variance = 0
    for (let j = i - period + 1; j <= i; j++) variance += (values[j] - m) ** 2
    const deviation = Math.sqrt(variance / period)
    upper[i] = m + mult * deviation
    lower[i] = m - mult * deviation
  }
  return {mid, upper, lower}
}

const toMs = (time: number) => time < 1e12 ? time * 1000 : time

// Günlük (UTC 00:00) sıfırlanan VWAP: tipik fiyat (H+L+C)/3 × hacim.
export function vwap(candles: readonly IndicatorCandle[]): Series {
  const out: Series = new Array(candles.length).fill(null)
  let day = -1, volume = 0, value = 0
  for (let i = 0; i < candles.length; i++) {
    const candle = candles[i]
    const d = Math.floor(toMs(candle.time) / 86_400_000)
    if (d !== day) { day = d; volume = 0; value = 0 }
    const typical = (candle.high + candle.low + candle.close) / 3
    volume += candle.volume
    value += typical * candle.volume
    out[i] = volume > 0 ? value / volume : typical
  }
  return out
}

// Wilder RSI.
export function rsi(values: readonly number[], period = 14): Series {
  const out: Series = new Array(values.length).fill(null)
  if (values.length <= period || period < 1) return out
  let gain = 0, loss = 0
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1]
    if (change >= 0) gain += change; else loss -= change
  }
  gain /= period; loss /= period
  const at = (g: number, l: number) => l === 0 ? (g === 0 ? 50 : 100) : 100 - 100 / (1 + g / l)
  out[period] = at(gain, loss)
  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1]
    gain = (gain * (period - 1) + Math.max(change, 0)) / period
    loss = (loss * (period - 1) + Math.max(-change, 0)) / period
    out[i] = at(gain, loss)
  }
  return out
}

export type Macd = {macd: Series; signal: Series; histogram: Series}
export function macd(values: readonly number[], fast = 12, slow = 26, signalPeriod = 9): Macd {
  const fastLine = ema(values, fast), slowLine = ema(values, slow)
  const line: Series = values.map((_, i) => fastLine[i] !== null && slowLine[i] !== null ? (fastLine[i] as number) - (slowLine[i] as number) : null)
  const first = line.findIndex(value => value !== null)
  const signal: Series = new Array(values.length).fill(null)
  if (first >= 0) {
    const compact = ema(line.slice(first) as number[], signalPeriod)
    for (let i = 0; i < compact.length; i++) signal[first + i] = compact[i]
  }
  const histogram: Series = line.map((value, i) => value !== null && signal[i] !== null ? value - (signal[i] as number) : null)
  return {macd: line, signal, histogram}
}

export type IndicatorId = 'ema20' | 'ema50' | 'ema200' | 'bollinger' | 'vwap' | 'rsi' | 'macd'
export type IndicatorInfo = {id: IndicatorId; label: string; pane: 'price' | 'sub'; hint: string}
export const INDICATORS: readonly IndicatorInfo[] = [
  {id: 'ema20', label: 'EMA 20', pane: 'price', hint: 'Kısa vadeli üstel ortalama'},
  {id: 'ema50', label: 'EMA 50', pane: 'price', hint: 'Orta vadeli üstel ortalama'},
  {id: 'ema200', label: 'EMA 200', pane: 'price', hint: 'Uzun vadeli trend çizgisi'},
  {id: 'bollinger', label: 'Bollinger (20, 2)', pane: 'price', hint: 'Oynaklık bandı'},
  {id: 'vwap', label: 'VWAP (günlük)', pane: 'price', hint: 'Hacim ağırlıklı ortalama fiyat; her gün 00:00 UTC sıfırlanır'},
  {id: 'rsi', label: 'RSI (14)', pane: 'sub', hint: 'Momentum; 70 üstü aşırı alım, 30 altı aşırı satım'},
  {id: 'macd', label: 'MACD (12, 26, 9)', pane: 'sub', hint: 'Trend ve momentum kesişimi'},
]
const IDS = new Set<string>(INDICATORS.map(item => item.id))
export const MAX_ACTIVE_INDICATORS = 7
export const isIndicatorId = (value: unknown): value is IndicatorId => typeof value === 'string' && IDS.has(value)
export function sanitizeIndicators(value: unknown): IndicatorId[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<IndicatorId>()
  for (const item of value) if (isIndicatorId(item)) seen.add(item)
  return INDICATORS.filter(item => seen.has(item.id)).map(item => item.id)
}

export type IndicatorSet = {
  ema20?: Series; ema50?: Series; ema200?: Series
  bollinger?: BollingerBands; vwap?: Series
  rsi?: Series; macd?: Macd
}
// Yalnız açık olanlar hesaplanır; tam mum geçmişi üzerinden (ısınma için) hesaplanıp görünür pencereye kesilir.
export function computeIndicators(candles: readonly IndicatorCandle[], enabled: readonly IndicatorId[]): IndicatorSet {
  const on = new Set(enabled)
  const closes = candles.map(candle => candle.close)
  const out: IndicatorSet = {}
  if (on.has('ema20')) out.ema20 = ema(closes, 20)
  if (on.has('ema50')) out.ema50 = ema(closes, 50)
  if (on.has('ema200')) out.ema200 = ema(closes, 200)
  if (on.has('bollinger')) out.bollinger = bollinger(closes, 20, 2)
  if (on.has('vwap')) out.vwap = vwap(candles)
  if (on.has('rsi')) out.rsi = rsi(closes, 14)
  if (on.has('macd')) out.macd = macd(closes, 12, 26, 9)
  return out
}

// Eksen aralığını görünür pencerede gösterge çizgilerini de kapsayacak şekilde genişletmek için en düşük/en yüksek.
export function overlayExtent(set: IndicatorSet, from: number, to: number): {low: number; high: number} | null {
  let low = Infinity, high = -Infinity
  const scan = (series: Series | undefined) => {
    if (!series) return
    for (let i = Math.max(0, from); i < Math.min(series.length, to); i++) {
      const value = series[i]
      if (value !== null && Number.isFinite(value)) { if (value < low) low = value; if (value > high) high = value }
    }
  }
  scan(set.ema20); scan(set.ema50); scan(set.ema200); scan(set.vwap)
  scan(set.bollinger?.upper); scan(set.bollinger?.lower)
  return Number.isFinite(low) && Number.isFinite(high) ? {low, high} : null
}

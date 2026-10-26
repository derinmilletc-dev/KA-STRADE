// Coin seçiminde Kais AI analiz gösterimi. Saf fonksiyonlar: ağ ve emir yok, yalnız gösterim.
// Oranlar backend'in /api/analysis-universe sonucundaki long_score / short_score (0-100) değerleridir.
// Eski sunucu bu alanları göndermezse yalnız ana yönün güveni (confidence) gösterilir.

export const AI_DISCLAIMER = 'Yatırım tavsiyesi değildir. Kais AI analiz raporudur.'

export type Lean = 'LONG' | 'SHORT' | 'NEUTRAL'
export type CoinSignal = {
  lean: Lean
  long: number | null     // LONG tarafı oranı, bilinmiyorsa null
  short: number | null    // SHORT tarafı oranı, bilinmiyorsa null
  confidence: number      // ana yönün güveni (0-100)
  score: number           // karar skoru, eşitlikte sıralama için
}
export type SignalMap = Record<string, CoinSignal>
export type ConfidenceTone = 'high' | 'mid' | 'low'
export type CoinFilter = 'all' | 'long' | 'short' | 'strong'
export type CoinSort = 'confidence' | 'volume' | 'change'

export const STRONG_CONFIDENCE = 80

export function leanOf(direction: unknown): Lean {
  const value = String(direction ?? '').toUpperCase()
  return value === 'LONG' ? 'LONG' : value === 'SHORT' ? 'SHORT' : 'NEUTRAL'
}

export function confidenceTone(confidence: number): ConfidenceTone {
  return confidence >= STRONG_CONFIDENCE ? 'high' : confidence >= 65 ? 'mid' : 'low'
}

const clampPercent = (value: number) => Math.max(0, Math.min(100, Math.round(value)))
const optionalPercent = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? clampPercent(number) : null
}

// Cevap {results: [...]} ya da doğrudan dizi olabilir. Güveni okunamayan satır sessizce atlanır.
export function buildSignalMap(payload: unknown): SignalMap {
  const rows = Array.isArray(payload) ? payload : Array.isArray((payload as {results?: unknown})?.results) ? (payload as {results: unknown[]}).results : []
  const map: SignalMap = {}
  for (const item of rows) {
    const row = item as {symbol?: unknown; direction?: unknown; confidence?: unknown; final_decision_score?: unknown; long_score?: unknown; short_score?: unknown}
    const symbol = typeof row?.symbol === 'string' ? row.symbol : ''
    const confidence = Number(row?.confidence)
    if (!symbol || !Number.isFinite(confidence)) continue
    const score = Number(row.final_decision_score)
    const lean = leanOf(row.direction)
    const exactLong = optionalPercent(row.long_score)
    const exactShort = optionalPercent(row.short_score)
    const both = exactLong !== null && exactShort !== null
    const known = clampPercent(confidence)
    // Yalnız ana yönün oranı biliniyorsa diğer taraf uydurulmaz (null kalır).
    const long = both ? exactLong : lean === 'LONG' ? known : null
    const short = both ? exactShort : lean === 'SHORT' ? known : null
    map[symbol] = {lean, long, short, confidence: known, score: Number.isFinite(score) ? score : 0}
  }
  return map
}

export function leanText(signal: CoinSignal): string {
  if (signal.lean === 'LONG') return 'LONG ağır basıyor'
  if (signal.lean === 'SHORT') return 'SHORT ağır basıyor'
  return 'Net yön yok'
}

export function filterBySignal<T extends {symbol: string}>(coins: T[], signals: SignalMap | null, filter: CoinFilter): T[] {
  if (!signals || filter === 'all') return coins
  return coins.filter(coin => {
    const signal = signals[coin.symbol]
    if (!signal) return false
    if (filter === 'long') return signal.lean === 'LONG'
    if (filter === 'short') return signal.lean === 'SHORT'
    return signal.lean !== 'NEUTRAL' && signal.confidence >= STRONG_CONFIDENCE
  })
}

// Analizi olanlar güvene göre (eşitlikte karar skoruna göre) çoktan aza; analizi olmayanlar mevcut sırasıyla sona.
export function sortByConfidence<T extends {symbol: string}>(coins: T[], signals: SignalMap | null): T[] {
  if (!signals) return coins
  const known: T[] = []
  const unknown: T[] = []
  for (const coin of coins) (signals[coin.symbol] ? known : unknown).push(coin)
  known.sort((left, right) => signals[right.symbol].confidence - signals[left.symbol].confidence || signals[right.symbol].score - signals[left.symbol].score)
  return [...known, ...unknown]
}

// Seçilen sıralama kipine göre sıralar. Girdi değişmez. Hacim: liste zaten hacme göre gelir (sıra korunur).
export function sortCoins<T extends {symbol: string; change?: number}>(coins: T[], signals: SignalMap | null, sort: CoinSort): T[] {
  if (sort === 'confidence') return sortByConfidence(coins, signals)
  if (sort === 'change') return [...coins].sort((left, right) => Math.abs(Number(right.change) || 0) - Math.abs(Number(left.change) || 0))
  return coins
}

// Coin seçiminde Kais AI analiz sıralaması. Saf fonksiyonlar: ağ ve emir yok, yalnız gösterim.
// Güven oranı backend'in /api/analysis-universe sonucundaki "confidence" değeridir (0-100).

export const AI_DISCLAIMER = 'Yatırım tavsiyesi değildir. Kais AI analiz raporudur.'

export type SignalLabel = 'AL' | 'BEKLE' | 'SAT'
export type CoinSignal = {label: SignalLabel; confidence: number; score: number}
export type SignalMap = Record<string, CoinSignal>
export type ConfidenceTone = 'high' | 'mid' | 'low'

export function signalLabel(direction: unknown): SignalLabel {
  const value = String(direction ?? '').toUpperCase()
  return value === 'LONG' ? 'AL' : value === 'SHORT' ? 'SAT' : 'BEKLE'
}

export function confidenceTone(confidence: number): ConfidenceTone {
  return confidence >= 80 ? 'high' : confidence >= 65 ? 'mid' : 'low'
}

const clampPercent = (value: number) => Math.max(0, Math.min(100, Math.round(value)))

// Cevap {results: [...]} ya da doğrudan dizi olabilir. Güveni okunamayan satır sessizce atlanır.
export function buildSignalMap(payload: unknown): SignalMap {
  const rows = Array.isArray(payload) ? payload : Array.isArray((payload as {results?: unknown})?.results) ? (payload as {results: unknown[]}).results : []
  const map: SignalMap = {}
  for (const item of rows) {
    const row = item as {symbol?: unknown; direction?: unknown; confidence?: unknown; final_decision_score?: unknown}
    const symbol = typeof row?.symbol === 'string' ? row.symbol : ''
    const confidence = Number(row?.confidence)
    if (!symbol || !Number.isFinite(confidence)) continue
    const score = Number(row.final_decision_score)
    map[symbol] = {label: signalLabel(row.direction), confidence: clampPercent(confidence), score: Number.isFinite(score) ? score : 0}
  }
  return map
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

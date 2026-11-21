// Coinbase manuel emir: akıllı boyutlandırma ve seviye önerileri (SAF mantık, React yok).
// Tahmindir; asıl kuralları, yuvarlamayı ve kontrolü sunucu yapar. Veri eksikse null döner, hiçbir değer uydurulmaz.

export type Side = 'BUY' | 'SELL'
const finitePositive = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0

/** Fiyatın büyüklüğüne göre okunur ondalık (ör. 3003.5 → 2, 0.0123 → 6). Sunucu fiyat adımına yuvarlar. */
export function priceString(value: number): string {
  if (!finitePositive(value)) return ''
  const digits = value >= 1000 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 6 : 8
  return String(Number(value.toFixed(digits)))
}

/** Girişten yüzde uzaklıkta stop: LONG'da altta, SHORT'ta üstte. */
export function stopFromPct(side: Side, entry: number, pct: number): number | null {
  if (!finitePositive(entry) || !finitePositive(pct) || pct >= 100) return null
  return side === 'BUY' ? entry * (1 - pct / 100) : entry * (1 + pct / 100)
}

/** Stop mesafesinin `rr` katı kadar hedef (1R, 2R, 3R). Stop yanlış tarafta ise null. */
export function targetFromRR(side: Side, entry: number, stop: number, rr: number): number | null {
  if (!finitePositive(entry) || !finitePositive(stop) || !finitePositive(rr)) return null
  const risk = side === 'BUY' ? entry - stop : stop - entry
  if (!(risk > 0)) return null
  return side === 'BUY' ? entry + risk * rr : entry - risk * rr
}

export type Sizing = {contracts: number; notional: number; leverage: number}
/** Teminat ve kaldıraçtan sözleşme sayısı (aşağı yuvarlanır). Hiç sözleşme sığmıyorsa null. */
export function sizeFromLeverage(input: {margin: number; leverage: number; entry: number; contractSize: number}): Sizing | null {
  const {margin, leverage, entry, contractSize} = input
  if (![margin, leverage, entry, contractSize].every(finitePositive)) return null
  const perContract = entry * contractSize
  const contracts = Math.floor(margin * leverage / perContract + 1e-9)
  if (contracts < 1) return null
  const notional = contracts * perContract
  return {contracts, notional, leverage: notional / margin}
}

/** 1 sözleşmenin pozisyon değeri (USD). */
export const notionalPerContract = (entry: number, contractSize: number): number | null =>
  finitePositive(entry) && finitePositive(contractSize) ? entry * contractSize : null

export type RiskPreview = {lossUsd: number; gainUsd: number | null; stopPct: number; targetPct: number | null; rr: number | null; wrongSide: boolean}
/** Stopta/hedefte yaklaşık sonuç. Komisyon: açılış + kapanış, pozisyon değeri × oran. */
export function riskPreview(input: {side: Side; entry: number; stop: number; target: number | null; contracts: number; contractSize: number; feeRate?: number}): RiskPreview | null {
  const {side, entry, stop, target, contracts, contractSize} = input
  const feeRate = input.feeRate ?? 0.001
  if (![entry, stop, contracts, contractSize].every(finitePositive)) return null
  const risk = side === 'BUY' ? entry - stop : stop - entry
  const units = contracts * contractSize
  const wrongSide = !(risk > 0)
  const fees = units * entry * feeRate * 2
  const lossUsd = wrongSide ? 0 : units * risk + fees
  let gainUsd: number | null = null, targetPct: number | null = null, rr: number | null = null
  if (finitePositive(target)) {
    const reward = side === 'BUY' ? target - entry : entry - target
    if (reward > 0) {
      gainUsd = Math.max(0, units * reward - fees)
      targetPct = reward / entry * 100
      rr = !wrongSide ? reward / risk : null
    }
  }
  return {lossUsd, gainUsd, stopPct: Math.abs(risk) / entry * 100, targetPct, rr, wrongSide}
}

export type Band = {label: string; tone: 'low' | 'mid' | 'high'}
export function leverageBand(leverage: number): Band {
  if (!(leverage >= 1)) return {label: 'Kaldıraç seç', tone: 'low'}
  return leverage <= 3 ? {label: 'Düşük risk', tone: 'low'} : leverage <= 10 ? {label: 'Orta risk', tone: 'mid'} : {label: 'Yüksek risk', tone: 'high'}
}

/** Alım gücüne göre teminat kısayolları (%25, %50, %75, hepsi). Kullanılabilir tutar yoksa boş. */
export function marginChips(available: number | null): {label: string; value: string}[] {
  if (!finitePositive(available)) return []
  return ([[25, '%25'], [50, '%50'], [75, '%75'], [100, 'Hepsi']] as const).map(([pct, label]) => ({label, value: String(Math.floor(available * pct / 100 * 100) / 100)})).filter(item => Number(item.value) > 0)
}

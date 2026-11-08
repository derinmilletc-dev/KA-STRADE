// DCA botu hesap katmanı — SAF fonksiyonlar, ağ/emir yok. backend/app/dca_engine/plan.py ile AYNI hesabı yapar (önizleme).
// Funding ve kayma dahil DEĞİLDİR; likidasyon bakım teminatı oranı varsayımla tahmin edilir.

export type DcaSide = 'LONG' | 'SHORT'

export const DCA_LIMITS = {
  maxSafetyOrders: 5,
  maxVolumeMultiplier: 2,
  maxLeverage: 5,
  stepPct: [0.3, 10],
  takeProfitPct: [0.2, 20],
  stopGapPct: [0.3, 10],
  maxSpanPct: 20,
  maintenanceRate: 0.005,
  liqBufferPct: 1,
  takerFee: 0.0005,
  minOrderUsdt: 5,
} as const

export type DcaInput = {
  side: DcaSide
  price: number
  baseMargin: number
  safetyOrders: number
  stepPct: number
  volumeMultiplier: number
  takeProfitPct: number
  stopGapPct: number
  leverage: number
  filters?: {tickSize: number; stepSize: number; minNotional: number}
}

export type DcaStage = {
  index: number
  kind: 'BASE' | 'SAFETY'
  entry: number
  qty: number
  notional: number
  margin: number
  cumQty: number
  avgEntry: number
  tpPrice: number
  tpNetProfit: number
}

export type DcaResult = {
  valid: boolean
  errors: string[]
  stages: DcaStage[]
  stopPrice: number
  stopDistancePct: number
  spanPct: number
  totalMargin: number
  totalNotional: number
  worstLoss: number
  avgEntryFull: number
  liquidation: number
  liquidationGapPct: number
}

const decimals = (unit: number) => {
  const text = String(unit)
  if (text.includes('e-')) return Number(text.split('e-')[1])
  return text.includes('.') ? text.split('.')[1].length : 0
}
const snap = (value: number, unit: number, mode: 'round' | 'floor' | 'ceil') => {
  const q = value / unit
  const n = mode === 'floor' ? Math.floor(q + 1e-9) : mode === 'ceil' ? Math.ceil(q - 1e-9) : Math.round(q)
  return Number((n * unit).toFixed(decimals(unit)))
}
const finitePositive = (...values: number[]) => values.every(v => typeof v === 'number' && Number.isFinite(v) && v > 0)

export function computeDca(input: DcaInput): DcaResult {
  const L = DCA_LIMITS
  const errors: string[] = []
  const empty: DcaResult = {valid: false, errors, stages: [], stopPrice: 0, stopDistancePct: 0, spanPct: 0, totalMargin: 0, totalNotional: 0, worstLoss: 0, avgEntryFull: 0, liquidation: 0, liquidationGapPct: 0}
  const {side, price, baseMargin, safetyOrders, stepPct, volumeMultiplier, takeProfitPct, stopGapPct, leverage} = input
  if (!finitePositive(price, baseMargin, stepPct, volumeMultiplier, takeProfitPct, stopGapPct, leverage)) {
    errors.push('Fiyat, marjin, aralık, çarpan, hedef, stop ve kaldıraç sıfırdan büyük sayı olmalı.')
    return empty
  }
  if (!Number.isInteger(safetyOrders) || safetyOrders < 1 || safetyOrders > L.maxSafetyOrders) errors.push(`Ek emir sayısı 1 ile ${L.maxSafetyOrders} arasında olmalı.`)
  if (volumeMultiplier < 1 || volumeMultiplier > L.maxVolumeMultiplier) errors.push(`Hacim çarpanı 1 ile ${L.maxVolumeMultiplier} arasında olmalı.`)
  if (stepPct < L.stepPct[0] || stepPct > L.stepPct[1]) errors.push(`Ek emirler arası aralık %${L.stepPct[0]} ile %${L.stepPct[1]} arasında olmalı.`)
  if (takeProfitPct < L.takeProfitPct[0] || takeProfitPct > L.takeProfitPct[1]) errors.push(`Kâr hedefi %${L.takeProfitPct[0]} ile %${L.takeProfitPct[1]} arasında olmalı.`)
  if (stopGapPct < L.stopGapPct[0] || stopGapPct > L.stopGapPct[1]) errors.push(`Stop mesafesi %${L.stopGapPct[0]} ile %${L.stopGapPct[1]} arasında olmalı.`)
  if (!Number.isInteger(leverage) || leverage > L.maxLeverage) errors.push(`Kaldıraç en fazla ${L.maxLeverage}x olabilir (tam sayı).`)
  const f = input.filters
  if (!f || !(f.tickSize > 0) || !(f.stepSize > 0)) errors.push('Borsa fiyat/miktar kuralları okunamadı.')
  if (errors.length || !f) return empty

  const sign = side === 'LONG' ? 1 : -1
  const stages: DcaStage[] = []
  const baseQty = snap(baseMargin * leverage / price, f.stepSize, 'floor')
  let cumQty = 0
  let cumCost = 0
  for (let i = 0; i <= safetyOrders; i++) {
    const entry = i === 0 ? snap(price, f.tickSize, 'round') : snap(price * (1 - sign * stepPct / 100 * i), f.tickSize, side === 'LONG' ? 'floor' : 'ceil')
    const qty = i === 0 ? baseQty : snap(baseQty * volumeMultiplier ** i, f.stepSize, 'floor')
    cumQty += qty
    cumCost += qty * entry
    const avgEntry = cumQty > 0 ? cumCost / cumQty : entry
    const tpPrice = snap(avgEntry * (1 + sign * takeProfitPct / 100), f.tickSize, side === 'LONG' ? 'ceil' : 'floor')
    const tpNetProfit = Math.abs(tpPrice - avgEntry) * cumQty - (avgEntry + tpPrice) * cumQty * L.takerFee
    stages.push({index: i, kind: i === 0 ? 'BASE' : 'SAFETY', entry, qty, notional: qty * entry, margin: qty * entry / leverage, cumQty, avgEntry, tpPrice, tpNetProfit})
  }
  const lastEntry = stages[stages.length - 1].entry
  const stopPrice = snap(lastEntry * (1 - sign * stopGapPct / 100), f.tickSize, side === 'LONG' ? 'floor' : 'ceil')
  if (new Set(stages.map(s => s.entry)).size !== stages.length) errors.push('Yuvarlamadan sonra iki emir aynı fiyata düşüyor; aralığı genişlet.')
  for (const s of stages) {
    const label = s.index === 0 ? 'Taban emir' : `Ek emir ${s.index}`
    if (s.qty <= 0 || s.notional + 1e-9 < Math.max(f.minNotional, L.minOrderUsdt)) errors.push(`${label} borsanın en düşük emir değerinin altında; marjini artır.`)
    if (side === 'LONG' ? s.entry <= stopPrice : s.entry >= stopPrice) errors.push(`${label} stopun ötesinde.`)
  }
  if (stages.some(s => s.tpNetProfit <= 1e-12)) errors.push('Kâr hedefi işlem ücretini karşılamıyor; hedefi artır.')

  const totalNotional = stages.reduce((sum, s) => sum + s.notional, 0)
  const totalQty = stages[stages.length - 1].cumQty
  const avgEntryFull = stages[stages.length - 1].avgEntry
  const rawLoss = stages.reduce((sum, s) => sum + s.qty * sign * (s.entry - stopPrice), 0)
  const worstLoss = rawLoss + totalNotional * L.takerFee + totalQty * stopPrice * L.takerFee
  const spanPct = Math.abs(price - stopPrice) / price * 100
  const stopDistancePct = Math.abs(lastEntry - stopPrice) / lastEntry * 100
  const liquidation = avgEntryFull * (1 - sign / leverage + sign * L.maintenanceRate)
  const liquidationGapPct = sign * (stopPrice - liquidation) / avgEntryFull * 100
  if (spanPct > L.maxSpanPct + 1e-9) errors.push(`Taban fiyattan stopa uzaklık %${spanPct.toFixed(2)}, sınır %${L.maxSpanPct}; aralığı ya da ek emir sayısını azalt.`)
  if (liquidationGapPct < L.liqBufferPct) errors.push('Stop, tahmini likidasyona çok yakın ya da ötesinde; kaldıracı düşür, aralığı kısalt ya da stopu yakınlaştır.')
  return {valid: errors.length === 0, errors: [...new Set(errors)], stages, stopPrice, stopDistancePct, spanPct, totalMargin: totalNotional / leverage, totalNotional, worstLoss, avgEntryFull, liquidation, liquidationGapPct}
}

export type DcaPresetForm = {margin: string; safety: number; step: string; volume: string; tp: string; gap: string; leverage: number; recycle: boolean}
export type DcaPreset = {id: string; name: string; hint: string; form: DcaPresetForm}
export const DCA_PRESETS: DcaPreset[] = [
  {id: 'calm', name: 'Sakin', hint: 'Geniş aralık, düşük kaldıraç', form: {margin: '10', safety: 3, step: '2', volume: '1.2', tp: '1', gap: '2', leverage: 2, recycle: false}},
  {id: 'balanced', name: 'Dengeli', hint: 'Çoğu durum için', form: {margin: '10', safety: 3, step: '1.5', volume: '1.5', tp: '1', gap: '1.5', leverage: 3, recycle: false}},
  {id: 'bold', name: 'Atak', hint: 'Sık ek emir, hızlı hedef', form: {margin: '10', safety: 4, step: '1', volume: '1.6', tp: '0.8', gap: '1', leverage: 4, recycle: false}},
]

const NOTIONAL_ERROR = 'en düşük emir değerinin altında'
const NEXT_MARGIN = (m: number) => (m < 10 ? m + 0.5 : m < 50 ? m + 1 : m + 5)

/** Borsanın en düşük emir değerini geçen en küçük taban marjini bulur (diğer ayarlar sabit). Bulunamazsa null. */
export function minBaseMargin(input: DcaInput, ceiling = 500): {margin: number; total: number} | null {
  for (let margin = 0.5; margin <= ceiling + 1e-9; margin = NEXT_MARGIN(margin)) {
    const result = computeDca({...input, baseMargin: margin})
    if (!result.stages.length) return null
    if (!result.errors.some(error => error.includes(NOTIONAL_ERROR))) return {margin, total: result.totalMargin}
  }
  return null
}

/** Toplam marj sınırına (`cap`) sığan, geçerli plan veren EN BÜYÜK taban marjini bulur (diğer ayarlar sabit). Yoksa null. */
export function maxBaseMargin(input: DcaInput, cap: number, ceiling = 500): {margin: number; total: number} | null {
  let best: {margin: number; total: number} | null = null
  for (let margin = 0.5; margin <= ceiling + 1e-9; margin = NEXT_MARGIN(margin)) {
    const result = computeDca({...input, baseMargin: margin})
    if (!result.stages.length || result.totalMargin > cap + 1e-9) break
    if (result.valid) best = {margin, total: result.totalMargin}
  }
  return best
}

export type DcaFit = {state: 'ok' | 'over-cap' | 'invalid'; margin: number | null; total: number | null}
/** Bu coin ve bu ayarlarla plan toplam marj sınırına sığar mı? `ok`: sığar (margin = sığan en büyük taban marj), `over-cap`: yalnız sınırı aşarak geçerli olur, `invalid`: başka bir kural bozuyor. */
export function planFit(input: DcaInput, cap: number): DcaFit {
  const within = maxBaseMargin(input, cap)
  if (within) return {state: 'ok', margin: within.margin, total: within.total}
  const need = minBaseMargin(input)
  if (!need) return {state: 'invalid', margin: null, total: null}
  const result = computeDca({...input, baseMargin: need.margin})
  return {state: result.valid ? 'over-cap' : 'invalid', margin: need.margin, total: result.totalMargin}
}

export type DcaCapFix = {safety: number; volume: number; margin: number; total: number}
/** Sınıra sığmayan planı en az değişiklikle sığdırır: önce çarpanı, sonra ek emir sayısını azaltır (kaldıracı artırmaz). Mümkün değilse null. */
export function fitToCap(input: DcaInput, cap: number): DcaCapFix | null {
  for (let safety = input.safetyOrders; safety >= 1; safety--) {
    for (let tenths = Math.round(input.volumeMultiplier * 10); tenths >= 10; tenths--) {
      const volume = tenths / 10
      const within = maxBaseMargin({...input, safetyOrders: safety, volumeMultiplier: volume}, cap)
      if (within) return {safety, volume, margin: within.margin, total: within.total}
    }
  }
  return null
}

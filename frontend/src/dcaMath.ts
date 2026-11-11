// DCA botu hesap katmanı — SAF fonksiyonlar, ağ/emir yok. backend/app/dca_engine/plan.py ile AYNI hesabı yapar (önizleme).
// BU YALNIZCA YAKLAŞIK ÖNİZLEMEDİR: gerçek karar backend'indir (source of truth). Aynı maliyet sabitlerini (ücret, kayma, funding payı, asgari net kâr)
// backend/app/dca_engine/risk_config.py ile birebir kullanır; ancak borsa filtreleri, canlı fiyat ve gerçek likidasyon fiyatı backend'de belirlenir.
// Sabitlerin eşitliği backend/tests/fixtures/dca_parity.json ile her iki tarafta test edilir. Likidasyon bakım teminatı oranı varsayımla tahmin edilir.

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
  // --- risk_config.py ile AYNI değerler (yüzdeler % cinsinden) ---
  slippageMarketPct: 0.03,
  slippageLimitEntryPct: 0,
  slippageTpExitPct: 0,
  fundingAllowancePct: 0.03,
  minNetProfitUsdt: 0.01,
  minNetProfitPct: 0.02,
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
  tpNetProfit: number            // yalnız ücret düşülmüş
  tpNetProfitAfterCosts: number  // ücret + kayma + funding payı düşülmüş (tahmin; backend 'tp_net_profit_after_costs_usdt')
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
  let cumNotional = 0
  let entrySlippage = 0
  for (let i = 0; i <= safetyOrders; i++) {
    const entry = i === 0 ? snap(price, f.tickSize, 'round') : snap(price * (1 - sign * stepPct / 100 * i), f.tickSize, side === 'LONG' ? 'floor' : 'ceil')
    const qty = i === 0 ? baseQty : snap(baseQty * volumeMultiplier ** i, f.stepSize, 'floor')
    cumQty += qty
    cumCost += qty * entry
    cumNotional += qty * entry
    entrySlippage += qty * entry * (i === 0 ? L.slippageMarketPct : L.slippageLimitEntryPct) / 100
    const avgEntry = cumQty > 0 ? cumCost / cumQty : entry
    const tpPrice = snap(avgEntry * (1 + sign * takeProfitPct / 100), f.tickSize, side === 'LONG' ? 'ceil' : 'floor')
    const tpNetProfit = Math.abs(tpPrice - avgEntry) * cumQty - (avgEntry + tpPrice) * cumQty * L.takerFee
    const afterCosts = tpNetProfit - entrySlippage - tpPrice * cumQty * L.slippageTpExitPct / 100 - cumNotional * L.fundingAllowancePct / 100
    stages.push({index: i, kind: i === 0 ? 'BASE' : 'SAFETY', entry, qty, notional: qty * entry, margin: qty * entry / leverage, cumQty, avgEntry, tpPrice, tpNetProfit, tpNetProfitAfterCosts: afterCosts})
  }
  const lastEntry = stages[stages.length - 1].entry
  const stopPrice = snap(lastEntry * (1 - sign * stopGapPct / 100), f.tickSize, side === 'LONG' ? 'floor' : 'ceil')
  if (new Set(stages.map(s => s.entry)).size !== stages.length) errors.push('Yuvarlamadan sonra iki emir aynı fiyata düşüyor; aralığı genişlet.')
  for (const s of stages) {
    const label = s.index === 0 ? 'Taban emir' : `Ek emir ${s.index}`
    if (s.qty <= 0 || s.notional + 1e-9 < Math.max(f.minNotional, L.minOrderUsdt)) errors.push(`${label} borsanın en düşük emir değerinin altında; marjini artır.`)
    if (side === 'LONG' ? s.entry <= stopPrice : s.entry >= stopPrice) errors.push(`${label} stopun ötesinde.`)
  }
  let cumN = 0
  for (const s of stages) {  // backend ile aynı sıra: ilk yetersiz aşamada dur
    cumN += s.notional
    const needed = Math.max(L.minNetProfitUsdt, cumN * L.minNetProfitPct / 100)
    if (s.tpNetProfit <= 1e-12) { errors.push('Kâr hedefi işlem ücretini karşılamıyor; hedefi artır.'); break }
    if (s.tpNetProfitAfterCosts < needed - 1e-12) {
      errors.push(`Kâr hedefi, ücret + kayma + funding payı düşülünce yeterli net kâr bırakmıyor (en az ${needed.toFixed(4)} USDT gerekir, kalan ${s.tpNetProfitAfterCosts.toFixed(4)} USDT); hedefi artır.`)
      break
    }
  }

  const totalNotional = stages.reduce((sum, s) => sum + s.notional, 0)
  const totalQty = stages[stages.length - 1].cumQty
  const avgEntryFull = stages[stages.length - 1].avgEntry
  const rawLoss = stages.reduce((sum, s) => sum + s.qty * sign * (s.entry - stopPrice), 0)
  const slippage = stages.reduce((sum, s) => sum + s.notional * (s.index === 0 ? L.slippageMarketPct : L.slippageLimitEntryPct) / 100, 0) + totalQty * stopPrice * L.slippageMarketPct / 100
  const funding = totalNotional * L.fundingAllowancePct / 100
  const worstLoss = rawLoss + totalNotional * L.takerFee + totalQty * stopPrice * L.takerFee + slippage + funding
  const spanPct = Math.abs(price - stopPrice) / price * 100
  const stopDistancePct = Math.abs(lastEntry - stopPrice) / lastEntry * 100
  const liquidation = avgEntryFull * (1 - sign / leverage + sign * L.maintenanceRate)
  const liquidationGapPct = sign * (stopPrice - liquidation) / avgEntryFull * 100
  if (spanPct > L.maxSpanPct + 1e-9) errors.push(`Taban fiyattan stopa uzaklık %${spanPct.toFixed(2)}, sınır %${L.maxSpanPct}; aralığı ya da ek emir sayısını azalt.`)
  if (liquidationGapPct < L.liqBufferPct) errors.push('Stop, tahmini likidasyona çok yakın ya da ötesinde; kaldıracı düşür, aralığı kısalt ya da stopu yakınlaştır.')
  return {valid: errors.length === 0, errors: [...new Set(errors)], stages, stopPrice, stopDistancePct, spanPct, totalMargin: totalNotional / leverage, totalNotional, worstLoss, avgEntryFull, liquidation, liquidationGapPct}
}

export type DcaPresetForm = {margin: string; safety: number; step: string; volume: string; tp: string; gap: string; leverage: number; recycle: boolean}
export type DcaPreset = {id: string; name: string; hint: string; risk: 1 | 2 | 3 | 4 | 5; form: DcaPresetForm}
// Dizideki sıra sabit (indeks 1 = Dengeli, tarayıcı onu kullanır); ekranda `risk` değerine göre sıralanır.
export const DCA_PRESETS: DcaPreset[] = [
  {id: 'calm', name: 'Sakin', hint: 'Geniş aralık, düşük kaldıraç', risk: 2, form: {margin: '10', safety: 3, step: '2', volume: '1.2', tp: '1', gap: '2', leverage: 2, recycle: false}},
  {id: 'balanced', name: 'Dengeli', hint: 'Çoğu durum için', risk: 3, form: {margin: '10', safety: 3, step: '1.5', volume: '1.5', tp: '1', gap: '1.5', leverage: 3, recycle: false}},
  {id: 'bold', name: 'Atak', hint: 'Sık ek emir, hızlı hedef', risk: 5, form: {margin: '10', safety: 4, step: '1', volume: '1.6', tp: '0.8', gap: '1', leverage: 4, recycle: false}},
  {id: 'steady', name: 'Uzun nefes', hint: 'Çok geniş aralık, 1x kaldıraç, büyük hareketi bekler', risk: 1, form: {margin: '10', safety: 3, step: '3', volume: '1.1', tp: '1.5', gap: '3', leverage: 1, recycle: false}},
  {id: 'quick', name: 'Hızlı kâr', hint: 'Az ek alım, küçük hedefi çabuk toplar', risk: 4, form: {margin: '10', safety: 2, step: '0.8', volume: '1.3', tp: '0.6', gap: '0.8', leverage: 3, recycle: false}},
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
/** Taban marjini limite göre uyarlar, ASLA gereğinden fazla harcatmaz: plan zaten geçerli ve limit içindeyse marj aynen kalır;
 *  limiti aşıyorsa sığan en büyük marja İNER; borsa minimumunun altındaysa geçerli olan en küçük marja ÇIKAR. */
function planFitCap(input: DcaInput, cap: number): DcaFit {
  const current = computeDca(input)
  if (current.valid && current.totalMargin <= cap + 1e-9) return {state: 'ok', margin: input.baseMargin, total: current.totalMargin}
  if (current.stages.length && current.totalMargin > cap + 1e-9) {
    const within = maxBaseMargin(input, cap)
    if (within) return {state: 'ok', margin: within.margin, total: within.total}
  }
  const need = minBaseMargin(input)
  if (!need) return {state: 'invalid', margin: null, total: null}
  const result = computeDca({...input, baseMargin: need.margin})
  if (!result.valid) return {state: 'invalid', margin: need.margin, total: result.totalMargin}
  return {state: result.totalMargin <= cap + 1e-9 ? 'ok' : 'over-cap', margin: need.margin, total: result.totalMargin}
}

/** Toplam marj sınırına VE tek işlem en kötü zarar sınırına (maxLoss > 0 ise) sığan en yüksek yatırımı bulur; sığmazsa 'over-cap'. */
export function planFit(input: DcaInput, cap: number, maxLoss = 0): DcaFit {
  const base = planFitCap(input, cap)
  if (!(maxLoss > 0) || base.state !== 'ok' || !base.margin) return base
  let margin = base.margin
  for (let i = 0; i < 12; i++) {
    const r = computeDca({...input, baseMargin: margin})
    if (r.valid && r.worstLoss <= maxLoss + 1e-9) return {state: 'ok', margin, total: r.totalMargin}
    if (!r.valid || r.worstLoss <= 0) break
    const next = Math.floor(margin * Math.min(0.98, maxLoss / r.worstLoss) * 100) / 100
    if (!(next > 0) || next >= margin) break
    margin = next
  }
  const need = minBaseMargin(input)
  const probe = need ? computeDca({...input, baseMargin: need.margin}) : null
  return {state: 'over-cap', margin: need?.margin ?? null, total: probe?.totalMargin ?? null}
}

export type DcaCapFix = {safety: number; volume: number; margin: number; total: number}
/** Sınıra sığmayan planı en az değişiklikle sığdırır: önce çarpanı, sonra ek emir sayısını azaltır (kaldıracı artırmaz). Mümkün değilse null. */
export function fitToCap(input: DcaInput, cap: number, maxLoss = 0): DcaCapFix | null {
  for (let safety = input.safetyOrders; safety >= 1; safety--) {
    for (let tenths = Math.round(input.volumeMultiplier * 10); tenths >= 10; tenths--) {
      const volume = tenths / 10
      const fit = planFit({...input, safetyOrders: safety, volumeMultiplier: volume}, cap, maxLoss)
      if (fit.state === 'ok' && fit.margin && fit.total) return {safety, volume, margin: fit.margin, total: fit.total}
    }
  }
  return null
}

// Grid hesap ve risk katmanı — SAF fonksiyonlar, ağ/emir yok.
// Aşama 1'de sadece önizleme için kullanılır; ileride gerçek grid motoru da AYNI hesabı kullanacak.
// Not: Funding ücreti ve kayma (slippage) dahil DEĞİLDİR; sonuçlar tahmindir.

export type GridSide = 'LONG' | 'SHORT'

export type GridInput = {
  side: GridSide
  price: number
  lower: number
  upper: number
  count: number
  totalMargin: number
  leverage: number
  stop: number
}

export type GridLevel = {
  index: number
  entry: number
  target: number
  margin: number
  notional: number
  qty: number
  lossAtStop: number
  gainAtTarget: number
}

export type GridLimits = {
  maxPositions: number
  maxLossPerTrade: number
  maxTotalExposure: number
  maxMarginPerTrade?: number
  currentExposure: number
  openPositions: number
  dailyLossBudgetLeft: number | null
}

export type GridCheck = {id: string; ok: boolean; label: string; detail: string}

export type GridResult = {
  valid: boolean
  errors: string[]
  levels: GridLevel[]
  spacing: number
  spacingPct: number
  totalMargin: number
  totalNotional: number
  worstLoss: number
  bestGain: number
  fees: number
  checks: GridCheck[]
  allOk: boolean
}

export const GRID_MAKER_FEE = 0.0002
export const GRID_TAKER_FEE = 0.0005
export const GRID_MIN_COUNT = 2
export const GRID_MAX_COUNT = 20
export const GRID_MIN_NOTIONAL_PER_LEVEL = 5

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : NaN)

// Aynı grid + aynı kademe her zaman AYNI emir kimliğini üretir (kopma/yeniden başlatma sonrası çift emir gitmesin).
// Binance newClientOrderId: en fazla 36 karakter, [A-Za-z0-9-_].
export function gridClientOrderId(gridId: string, index: number, role: 'E' | 'T' | 'S'): string {
  const safe = gridId.replace(/[^A-Za-z0-9]/g, '').slice(0, 20)
  return `kg-${safe}-${String(index).padStart(2, '0')}${role}`.slice(0, 36)
}

export function computeGrid(input: GridInput, limits: GridLimits): GridResult {
  const errors: string[] = []
  const {side} = input
  const price = num(input.price), lower = num(input.lower), upper = num(input.upper)
  const count = Math.floor(num(input.count)), totalMargin = num(input.totalMargin)
  const leverage = num(input.leverage), stop = num(input.stop)

  if (!(price > 0)) errors.push('Güncel fiyat bilinmiyor.')
  if (!(lower > 0) || !(upper > 0)) errors.push('Alt ve üst sınır gerekli.')
  else if (lower >= upper) errors.push('Alt sınır, üst sınırdan küçük olmalı.')
  if (!(count >= GRID_MIN_COUNT && count <= GRID_MAX_COUNT)) errors.push(`Kademe sayısı ${GRID_MIN_COUNT}-${GRID_MAX_COUNT} arasında olmalı.`)
  if (!(totalMargin > 0)) errors.push('Toplam marjin gerekli.')
  if (!(leverage >= 1)) errors.push('Kaldıraç en az 1x olmalı.')
  if (!(stop > 0)) errors.push('Grid stop fiyatı gerekli.')
  else if (side === 'LONG' && stop >= lower) errors.push('LONG grid için stop, alt sınırın ALTINDA olmalı.')
  else if (side === 'SHORT' && stop <= upper) errors.push('SHORT grid için stop, üst sınırın ÜSTÜNDE olmalı.')

  if (errors.length) return {valid: false, errors, levels: [], spacing: 0, spacingPct: 0, totalMargin: 0, totalNotional: 0, worstLoss: 0, bestGain: 0, fees: 0, checks: [], allOk: false}

  const spacing = (upper - lower) / (count - 1)
  const perMargin = totalMargin / count
  const levels: GridLevel[] = []
  for (let i = 0; i < count; i++) {
    const entry = side === 'LONG' ? upper - spacing * i : lower + spacing * i
    const target = side === 'LONG' ? entry + spacing : entry - spacing
    const notional = perMargin * leverage
    const qty = notional / entry
    levels.push({index: i + 1, entry, target, margin: perMargin, notional, qty, lossAtStop: qty * Math.abs(entry - stop), gainAtTarget: qty * Math.abs(target - entry)})
  }

  const wrongSide = levels.filter(l => (side === 'LONG' ? l.entry >= price : l.entry <= price))
  const sideDetail = side === 'LONG'
    ? `${wrongSide.length} kademe güncel fiyatın üstünde; limit alış hemen dolar. Üst sınırı fiyatın altına çek.`
    : `${wrongSide.length} kademe güncel fiyatın altında; limit satış hemen dolar. Alt sınırı fiyatın üstüne çek.`
  if (wrongSide.length) errors.push(sideDetail)

  const totalNotional = levels.reduce((s, l) => s + l.notional, 0)
  const rawLoss = levels.reduce((s, l) => s + l.lossAtStop, 0)
  const entryFees = totalNotional * GRID_MAKER_FEE
  const stopFees = levels.reduce((s, l) => s + l.qty * stop, 0) * GRID_TAKER_FEE
  const worstLoss = rawLoss + entryFees + stopFees
  const rawGain = levels.reduce((s, l) => s + l.gainAtTarget, 0)
  const targetFees = levels.reduce((s, l) => s + l.qty * l.target, 0) * GRID_MAKER_FEE
  const fees = entryFees + targetFees
  const bestGain = rawGain - fees

  const checks: GridCheck[] = []
  const add = (id: string, ok: boolean, label: string, detail: string) => checks.push({id, ok, label, detail})
  const lvl = perMargin * leverage
  add('level_size', lvl >= GRID_MIN_NOTIONAL_PER_LEVEL, 'Kademe büyüklüğü',
    lvl >= GRID_MIN_NOTIONAL_PER_LEVEL
      ? `Her kademe yaklaşık ${lvl.toFixed(2)} USDT; borsa alt sınırının üstünde.`
      : `Her kademe ${lvl.toFixed(2)} USDT; borsa en az ${GRID_MIN_NOTIONAL_PER_LEVEL} USDT ister. Kademeyi azalt ya da marjini artır.`)
  const slots = limits.maxPositions - limits.openPositions
  add('positions', slots >= 1, 'Pozisyon sınırı',
    slots >= 1
      ? `Açık ${limits.openPositions} / ${limits.maxPositions}. Grid tek pozisyon gibi sayılır.`
      : `Açık pozisyon ${limits.openPositions}, sınır ${limits.maxPositions}. Yeni grid açılamaz.`)
  const exposureAfter = limits.currentExposure + totalNotional
  add('exposure', exposureAfter <= limits.maxTotalExposure + 1e-9, 'Toplam maruziyet (tüm kademeler dolarsa)',
    `Şimdiki ${limits.currentExposure.toFixed(2)} + grid ${totalNotional.toFixed(2)} = ${exposureAfter.toFixed(2)} USDT; sınır ${limits.maxTotalExposure} USDT.`)
  add('loss_per_trade', worstLoss <= limits.maxLossPerTrade + 1e-9, 'En kötü durum zararı',
    `Tüm kademeler dolup stop vurursa tahmini zarar ${worstLoss.toFixed(2)} USDT; tek işlem sınırı ${limits.maxLossPerTrade} USDT. Kademelerin zararı toplanır.`)
  if (limits.dailyLossBudgetLeft === null) add('daily_loss', false, 'Günlük zarar hakkı', 'Bugünkü kalan zarar hakkı okunamadı; güvenli tarafta kalmak için onaylanmaz.')
  else add('daily_loss', worstLoss <= limits.dailyLossBudgetLeft + 1e-9, 'Günlük zarar hakkı',
    `En kötü zarar ${worstLoss.toFixed(2)} USDT; bugün kalan hak ${limits.dailyLossBudgetLeft.toFixed(2)} USDT.`)
  if (typeof limits.maxMarginPerTrade === 'number' && limits.maxMarginPerTrade > 0) add('margin', totalMargin <= limits.maxMarginPerTrade + 1e-9, 'Toplam marjin sınırı', `Grid ${totalMargin.toFixed(2)} USDT; sınır ${limits.maxMarginPerTrade} USDT.`)
  add('side', wrongSide.length === 0, 'Kademe yerleşimi', wrongSide.length ? sideDetail : 'Tüm kademeler güncel fiyatın doğru tarafında.')

  const allOk = errors.length === 0 && checks.every(c => c.ok)
  return {valid: errors.length === 0, errors, levels, spacing, spacingPct: (spacing / price) * 100, totalMargin, totalNotional, worstLoss, bestGain, fees, checks, allOk}
}

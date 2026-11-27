// Grid kurulum ekranının "karar" katmanı: SAF fonksiyonlar, ağ/emir yok. Hesabı değiştirmez; mevcut computeGrid sonucunu
// düz Türkçeye çevirir ve (yalnız limitleri aşan planlar için) limitlere sığan en yakın kademe/marjin kombinasyonunu önerir.
import type {GridInput, GridLimits, GridResult} from './gridMath'
import type {FiltersState} from './gridAlerts'

const GRID_MIN_COUNT = 2, GRID_MAX_COUNT = 20 // gridMath.ts ile aynı (node testinde değer import'u olmasın diye burada)
type ComputeGrid = (input: GridInput, limits: GridLimits) => GridResult

export type GridReason = {key: string; text: string; unknown: boolean}

const UNKNOWN = /okunamadı|bilinmiyor|hesaplanamadı/

/** Başarısız kontrolleri kısa, anlaşılır cümlelere çevirir. Aynı konuyu tekrar eden kontroller tek satıra iner. */
export function explainGrid(result: GridResult, filters: FiltersState = 'ok'): GridReason[] {
  const out: GridReason[] = []
  const seen = new Set<string>()
  const add = (key: string, text: string, unknown = false) => { if (!seen.has(key)) { seen.add(key); out.push({key, text, unknown}) } }
  if (filters === 'error') add('filters', 'Borsa kuralları okunamadı; biraz sonra tekrar dene.', true)
  for (const error of result.errors) add(`err:${error}`, error)
  for (const check of result.checks) {
    if (check.ok) continue
    const unknown = UNKNOWN.test(check.detail)
    switch (check.id) {
      case 'level_size': add('size', 'Her kademe çok küçük kalıyor; kademeyi azalt ya da marjini artır.'); break
      case 'exchange_filters':
        if (filters === 'error') break
        add('size', unknown ? 'Borsa kuralları okunamadı; biraz sonra tekrar dene.' : 'Kademeler borsanın en küçük emir tutarının altında kalıyor.', unknown); break
      case 'daily_loss': case 'portfolio_risk':
        add('daily', unknown ? 'Bugünkü kalan zarar hakkı okunamadı; sayfayı yenileyip tekrar dene.' : 'En kötü zarar, bugünkü kalan zarar hakkını aşıyor.', unknown); break
      case 'loss_per_trade': add('loss', 'En kötü zarar, tek işlem zarar sınırını aşıyor.'); break
      case 'exposure': add('exposure', 'Toplam açık büyüklük sınırı aşılıyor (açık pozisyonlar dahil).'); break
      case 'margin': add('margin', 'Toplam marjin sınırı aşılıyor.'); break
      case 'positions': add('positions', 'Açık pozisyon sayısı sınırda; yeni grid açılamaz.'); break
      case 'liquidation': add('liq', 'Stop, tahmini likidasyona çok yakın; kaldıracı düşür ya da stopu yakınlaştır.'); break
      case 'side': add('side', 'Bazı kademeler fiyatın yanlış tarafında; aralığı fiyatın doğru tarafına çek.'); break
      default: add(check.id, `${check.label}: ${check.detail}`, unknown)
    }
  }
  return out
}

export type GridFix = {count: number; margin: number; changedCount: boolean; changedMargin: boolean}

/** Plan limit aşıyorsa, mevcut kademe/marjine en yakın ve TÜM kontrolleri geçen kombinasyonu bulur (önce marjini koru, sonra kademeyi).
 *  Veri okunamadığı (bilinmiyor) için geçmeyen planlarda öneri yoktur (null). Yalnız öneridir; uygulamak kullanıcının tıklamasıdır. */
export function suggestGridFix(computeGrid: ComputeGrid, input: GridInput, limits: GridLimits): GridFix | null {
  if (computeGrid(input, limits).allOk) return null
  const baseCount = Math.floor(Number(input.count)), baseMargin = Number(input.totalMargin)
  if (!(baseCount >= GRID_MIN_COUNT) || !(baseMargin > 0)) return null
  for (const factor of [1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]) {
    const margin = baseMargin >= 10 ? Math.floor(baseMargin * factor) : Math.floor(baseMargin * factor * 100) / 100
    if (!(margin >= 5)) break
    for (let count = Math.min(baseCount, GRID_MAX_COUNT); count >= GRID_MIN_COUNT; count--) {
      if (factor === 1 && count === baseCount) continue
      if (computeGrid({...input, totalMargin: margin, count}, limits).allOk) return {count, margin, changedCount: count !== baseCount, changedMargin: margin !== baseMargin}
    }
  }
  return null
}

// ---------------------------------------------------------------------------------------------
// Akıllı plan, marjin tavanı, risk cümlesi, canlı grid özeti. Hepsi SAF; emir/ağ yok.

export type SmartCandle = {high: number; low: number; close: number}
export type SmartPlan = {lower: number; upper: number; stop: number; count: number; margin: number; rangePct: number; movePct: number; spacingPct: number; fitted: boolean; ok: boolean}

const clampNum = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Son ~24 saatlik 1s mumdan hareket: (en yüksek-en düşük)/fiyat ve ortalama saatlik aralık, yüzde. Yetersiz veri → null. */
export function recentMove(candles: readonly SmartCandle[], price: number): {dayPct: number; hourPct: number} | null {
  const last = candles.slice(-24)
  if (last.length < 12 || !(price > 0)) return null
  const hi = Math.max(...last.map(c => c.high)), lo = Math.min(...last.map(c => c.low))
  if (!(hi > lo)) return null
  const hour = last.reduce((s, c) => s + (c.high - c.low), 0) / last.length
  return {dayPct: (hi - lo) / price * 100, hourPct: hour / price * 100}
}

const roundSig = (v: number) => Number(v.toPrecision(5))

/** Hareketli coinde geniş, sakin coinde dar aralık; kademe aralığı saatlik harekete göre. Sonra limit/borsa kontrolünden geçirilir
 *  (geçmezse marjin/kademe küçültülür). `evaluate` çağıranın computeGrid'idir. Plan yalnızca önerilir; uygulamak kullanıcının tıklamasıdır. */
export function smartGridPlan(input: {side: 'LONG' | 'SHORT'; price: number; candles: readonly SmartCandle[]; margin: number; leverage: number}, evaluate: (p: {lower: number; upper: number; count: number; margin: number; stop: number}) => {allOk: boolean}): SmartPlan | null {
  const {side, price, margin, leverage} = input
  const mv = recentMove(input.candles, price)
  if (!mv || !(margin > 0) || !(leverage >= 1)) return null
  const rangePct = clampNum(mv.dayPct * 0.6, 2, 10)
  const near = 0.5
  const spacingTarget = clampNum(mv.hourPct * 1.2, 0.5, 2)
  const wantCount = clampNum(Math.round((rangePct - near) / spacingTarget) + 1, 3, 8)
  const lowSide = (pct: number) => price * (1 - pct / 100), highSide = (pct: number) => price * (1 + pct / 100)
  const build = (count: number, m: number) => {
    const lower = side === 'LONG' ? lowSide(rangePct) : highSide(near)
    const upper = side === 'LONG' ? lowSide(near) : highSide(rangePct)
    const stop = side === 'LONG' ? lower * (1 - 0.015) : upper * (1 + 0.015)
    return {lower: roundSig(lower), upper: roundSig(upper), stop: roundSig(stop), count, margin: m}
  }
  const mk = (p: ReturnType<typeof build>, ok: boolean, fitted: boolean): SmartPlan => ({...p, rangePct, movePct: mv.dayPct, spacingPct: p.count > 1 ? (p.upper - p.lower) / (p.count - 1) / price * 100 : 0, fitted, ok})
  for (const factor of [1, 0.8, 0.6, 0.4, 0.25, 0.15, 0.1]) {
    const m = margin >= 10 ? Math.floor(margin * factor) : Math.floor(margin * factor * 100) / 100
    if (!(m >= 5)) break
    for (let count = wantCount; count >= 2; count--) {
      const p = build(count, m)
      if (evaluate(p).allOk) return mk(p, true, factor !== 1 || count !== wantCount)
    }
  }
  return mk(build(wantCount, margin), false, false)
}

/** Verilen aralık/kademe/kaldıraçla TÜM kontrolleri geçen en büyük toplam marjin (yaklaşık). Hiç geçen yoksa null. */
export function maxAffordableMargin(computeGrid: ComputeGrid, input: GridInput, limits: GridLimits): number | null {
  const ok = (m: number) => computeGrid({...input, totalMargin: m}, limits).allOk
  let best: number | null = null, prevFail = 0
  for (let m = 5; m <= 20000; m = Math.ceil(m * 1.15)) {
    if (ok(m)) best = m; else if (best !== null) { prevFail = m; break }
  }
  if (best === null) return null
  if (prevFail > best) { let lo = best, hi = prevFail; for (let i = 0; i < 12 && hi - lo > 1; i++) { const mid = Math.floor((lo + hi) / 2); if (ok(mid)) lo = mid; else hi = mid } best = lo }
  return best
}

/** Tek cümlelik risk özeti. Günlük hak bilinmiyorsa yüzde kısmı söylenmez (uydurma yok). */
export function riskSentence(worstLoss: number, bestGain: number, dailyLeft: number | null): {loss: string; gain: string; pct: number | null} {
  const pct = dailyLeft !== null && dailyLeft > 0 ? Math.round(worstLoss / dailyLeft * 100) : null
  return {loss: `$${worstLoss.toFixed(2)}`, gain: `$${bestGain.toFixed(2)}`, pct}
}

export type LiveGridStats = {cycles?: number; realized_usdt?: number; fees_usdt?: number; filled_levels?: number} | null | undefined

/** Canlı grid satırı için sade özet. levelCount: grid'in toplam kademesi. */
export function liveSummary(stats: LiveGridStats, levelCount: number) {
  const cycles = Number(stats?.cycles) || 0, net = Number(stats?.realized_usdt) || 0, filled = Number(stats?.filled_levels) || 0
  return {cycles, net, filled, levels: levelCount, text: cycles === 0 && filled === 0 ? 'Henüz kademe dolmadı; fiyat ilk girişe gelmeyi bekliyor.' : `${cycles} tur bitti, ${filled} kademe açık bekliyor.`}
}

/** Fiyat aralığın dışında mı? Giriş fiyatlarının en düşüğü/en yükseği ve stop ile karşılaştırır. */
export function rangeStatus(price: number, entries: readonly number[]): 'inside' | 'near-edge' | 'outside' | 'unknown' {
  const e = entries.filter(v => v > 0)
  if (!(price > 0) || e.length < 2) return 'unknown'
  const lo = Math.min(...e), hi = Math.max(...e), w = hi - lo
  if (price < lo - w * 0.02 || price > hi + w * 0.02) return 'outside'
  if (price < lo + w * 0.1 || price > hi - w * 0.1) return 'near-edge'
  return 'inside'
}

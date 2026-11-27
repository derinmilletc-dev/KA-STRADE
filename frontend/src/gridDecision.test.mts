// Çalıştırma: node frontend/src/gridDecision.test.mts   (Node 22.18+)
import assert from 'node:assert'
import * as g from './gridMath.ts'
import * as d from './gridDecision.ts'

const lim: any = {maxPositions: 5, maxLossPerTrade: 30, maxTotalExposure: 1000, currentExposure: 0, openPositions: 0, dailyLossBudgetLeft: 40, openPositionRisk: 0}
const F: any = {tickSize: 0.1, stepSize: 0.001, minNotional: 40}
const base: any = {side: 'LONG', price: 100, lower: 90, upper: 98, count: 5, totalMargin: 50, leverage: 2, stop: 88, filters: F}

// 50 USDT x2 = 100 büyüklük; 5 kademe = 20 USDT/kademe < 50 → "kademeler küçük"; çözüm: 2 kademe
let r = g.computeGrid(base, lim)
assert(!r.allOk)
let why = d.explainGrid(r)
assert(why.some(x => /küçük/.test(x.text)), 'küçük emir açıklaması')
let fix = d.suggestGridFix(g.computeGrid, base, lim)
assert(fix && fix.count === 2 && fix.margin === 50 && fix.changedCount && !fix.changedMargin, JSON.stringify(fix))
assert(g.computeGrid({...base, count: fix!.count, totalMargin: fix!.margin}, lim).allOk, 'öneri gerçekten tüm kontrolleri geçer')

// sorun yoksa öneri ve açıklama yok
const ok = {...base, count: 2}
assert.strictEqual(d.suggestGridFix(g.computeGrid, ok, lim), null)
assert.deepStrictEqual(d.explainGrid(g.computeGrid(ok, lim)), [])

// açık pozisyonlar sınırı zaten aşıyorsa çözüm bulunamaz (uydurma öneri yok)
const over = {...lim, currentExposure: 1200}
assert.strictEqual(d.suggestGridFix(g.computeGrid, base, over), null)
assert(d.explainGrid(g.computeGrid(ok, over)).some(x => /açık büyüklük/.test(x.text)))

// zarar hakkı okunamadı → tek cümle, öneri yok
const unk = {...lim, dailyLossBudgetLeft: null}
why = d.explainGrid(g.computeGrid(ok, unk))
assert.strictEqual(why.filter(x => x.key === 'daily').length, 1, 'günlük hak + portföy riski tek satır')
assert(why[0].unknown)
assert.strictEqual(d.suggestGridFix(g.computeGrid, ok, unk), null)

// zarar sınırı aşılıyorsa marjin düşürülerek çözüm önerilir
const tight = {...lim, maxLossPerTrade: 12, dailyLossBudgetLeft: 40}
const big: any = {...base, count: 2, totalMargin: 200, filters: F}
const f2 = d.suggestGridFix(g.computeGrid, big, tight)
assert(f2 && f2.margin < 200, JSON.stringify(f2))
assert(g.computeGrid({...big, totalMargin: f2!.margin, count: f2!.count}, tight).allOk)

// borsa kuralları okunamadı
assert(d.explainGrid(g.computeGrid({...ok, filters: undefined}, lim), 'error')[0].unknown)
console.log('gridDecision OK')

// ---- akıllı plan
const hours = (range: number) => Array.from({length: 30}, (_, i) => ({high: 100 + range / 2 + (i % 3), low: 100 - range / 2 - (i % 2), close: 100}))
const sm: any = d
const mv = sm.recentMove(hours(4), 100); assert(mv && mv.dayPct > 4 && mv.hourPct > 3)
assert.strictEqual(sm.recentMove(hours(4).slice(0, 5), 100), null, 'az mum → null')
const evalOf = (limits: any) => (p: any) => g.computeGrid({side: 'LONG', price: 100, lower: p.lower, upper: p.upper, count: p.count, totalMargin: p.margin, leverage: 2, stop: p.stop, filters: {tickSize: 0.01, stepSize: 0.001, minNotional: 5}} as any, limits)
const calm = sm.smartGridPlan({side: 'LONG', price: 100, candles: hours(0.5), margin: 60, leverage: 2}, evalOf(lim))
const wild = sm.smartGridPlan({side: 'LONG', price: 100, candles: hours(12), margin: 60, leverage: 2}, evalOf(lim))
assert(calm && wild && wild.rangePct > calm.rangePct, 'hareketli coin daha geniş aralık')
assert(calm.upper < 100 && calm.lower < calm.upper && calm.stop < calm.lower, 'LONG: aralık fiyatın altında, stop altında')
assert(calm.ok && wild.ok)
const sh = sm.smartGridPlan({side: 'SHORT', price: 100, candles: hours(4), margin: 60, leverage: 2}, (p: any) => g.computeGrid({side: 'SHORT', price: 100, lower: p.lower, upper: p.upper, count: p.count, totalMargin: p.margin, leverage: 2, stop: p.stop, filters: {tickSize: 0.01, stepSize: 0.001, minNotional: 5}} as any, lim))
assert(sh.lower > 100 && sh.stop > sh.upper, 'SHORT: aralık fiyatın üstünde')
const tightLim = {...lim, maxLossPerTrade: 0.3}
const fitted = sm.smartGridPlan({side: 'LONG', price: 100, candles: hours(12), margin: 400, leverage: 2}, evalOf({...lim, maxLossPerTrade: 8, dailyLossBudgetLeft: 40}))
assert(fitted && fitted.ok && fitted.fitted && fitted.margin < 400, JSON.stringify(fitted))
const none = sm.smartGridPlan({side: 'LONG', price: 100, candles: hours(12), margin: 60, leverage: 2}, evalOf({...lim, currentExposure: 5000}))
assert(none && !none.ok, 'sığmıyorsa ok=false (uydurma yok)')
// ---- marjin tavanı
const mm = sm.maxAffordableMargin(g.computeGrid, {...base, count: 2, totalMargin: 10}, {...lim, maxLossPerTrade: 12, dailyLossBudgetLeft: 40})
assert(mm && mm >= 10, String(mm))
assert(g.computeGrid({...base, count: 2, totalMargin: mm}, {...lim, maxLossPerTrade: 12, dailyLossBudgetLeft: 40}).allOk)
assert(!g.computeGrid({...base, count: 2, totalMargin: mm + 3}, {...lim, maxLossPerTrade: 12, dailyLossBudgetLeft: 40}).allOk, 'tavan gerçekten tavan')
assert.strictEqual(sm.maxAffordableMargin(g.computeGrid, {...base, count: 2}, {...lim, currentExposure: 5000}), null)
// ---- risk cümlesi, özet, aralık
assert.deepStrictEqual(sm.riskSentence(14.2, 3.1, 40), {loss: '$14.20', gain: '$3.10', pct: 36})
assert.strictEqual(sm.riskSentence(14.2, 3.1, null).pct, null)
assert(/Henüz/.test(sm.liveSummary({cycles: 0, filled_levels: 0}, 5).text))
assert(/2 tur/.test(sm.liveSummary({cycles: 2, realized_usdt: 0.4, filled_levels: 1}, 5).text))
assert.strictEqual(sm.rangeStatus(95, [90, 100]), 'inside')
assert.strictEqual(sm.rangeStatus(100.1, [90, 100]), 'near-edge')
assert.strictEqual(sm.rangeStatus(85, [90, 100]), 'outside')
assert.strictEqual(sm.rangeStatus(0, [90, 100]), 'unknown')
console.log('gridDecision smart OK')

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

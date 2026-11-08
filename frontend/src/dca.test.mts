// Çalıştırma: node frontend/src/dca.test.mts   (Node 22.18+; ek paket gerekmez)
// backend/tests/test_dca_plan.py ile aynı örnek: iki hesap aynı sayıları vermeli.
import assert from 'node:assert'
import {DCA_PRESETS, computeDca} from './dcaMath.ts'

const F = {tickSize: 0.01, stepSize: 0.001, minNotional: 5}
const base: any = {side: 'LONG', price: 100, baseMargin: 10, safetyOrders: 3, stepPct: 1.5, volumeMultiplier: 1.5, takeProfitPct: 1, stopGapPct: 1.5, leverage: 3, filters: F}
const near = (a: number, b: number, eps = 1e-6) => assert(Math.abs(a - b) < eps, `${a} ≉ ${b}`)

let r = computeDca(base)
assert.ok(r.valid, r.errors.join(' '))
assert.deepStrictEqual(r.stages.map(s => s.entry), [100, 98.5, 97, 95.5])
assert.deepStrictEqual(r.stages.map(s => s.qty), [0.3, 0.45, 0.675, 1.012])
near(r.stages[3].avgEntry, 236.446 / 2.437)
assert.strictEqual(r.stages[0].tpPrice, 101)
assert.strictEqual(r.stopPrice, 94.06)
near(r.totalNotional, 236.446, 1e-4)
near(r.totalMargin, 236.446 / 3, 1e-4)
const raw = 0.3 * 5.94 + 0.45 * 4.44 + 0.675 * 2.94 + 1.012 * 1.44
near(r.worstLoss, raw + 236.446 * 0.0005 + 2.437 * 94.06 * 0.0005, 1e-4)
assert.ok(r.stages.every((s, i, a) => i === 0 || a[i - 1].tpPrice > s.tpPrice), 'ortalama düştükçe hedef düşer')
assert.ok(r.liquidation < r.stopPrice && r.liquidationGapPct > 1)

r = computeDca({...base, side: 'SHORT'})
assert.ok(r.valid, r.errors.join(' '))
assert.deepStrictEqual(r.stages.map(s => s.entry), [100, 101.5, 103, 104.5])
assert.ok(r.stopPrice > 104.5 && r.liquidation > r.stopPrice && r.stages[0].tpPrice < 100)

assert.strictEqual(new Set(computeDca({...base, volumeMultiplier: 1}).stages.map(s => s.qty)).size, 1)
// reddedilenler
for (const [patch, text] of [
  [{safetyOrders: 6}, '1 ile 5'], [{safetyOrders: 2.5}, '1 ile 5'], [{volumeMultiplier: 2.5}, 'çarpan'], [{stepPct: 0.1}, 'aralık'],
  [{takeProfitPct: 0.05}, 'Kâr hedefi'], [{stopGapPct: 0.1}, 'Stop mesafesi'], [{leverage: 6}, 'Kaldıraç'], [{leverage: 2.5}, 'Kaldıraç'],
  [{baseMargin: 1, leverage: 2}, 'en düşük emir değerinin altında'], [{safetyOrders: 5, stepPct: 6, stopGapPct: 3}, 'uzaklık'],
  [{filters: undefined}, 'okunamadı'], [{price: Number.NaN}, 'sıfırdan büyük'],
] as [any, string][]) {
  const x = computeDca({...base, ...patch})
  assert.ok(!x.valid && x.errors.some(e => e.includes(text)), `${JSON.stringify(patch)} → ${x.errors.join(' | ')}`)
}
// hazır ayarlar: her fiyat düzeyinde ve iki yönde geçerli plan vermeli (arayüzde boş ekran çıkmasın)
for (const preset of DCA_PRESETS) {
  for (const price of [0.5, 2.5, 100, 2450]) {
    for (const side of ['LONG', 'SHORT'] as const) {
      const f = {tickSize: price / 10000, stepSize: price > 1000 ? 0.001 : price > 1 ? 0.01 : 1, minNotional: 5}
      const p = preset.form
      const x = computeDca({side, price, baseMargin: Number(p.margin), safetyOrders: p.safety, stepPct: Number(p.step), volumeMultiplier: Number(p.volume),
        takeProfitPct: Number(p.tp), stopGapPct: Number(p.gap), leverage: p.leverage, filters: f})
      assert.ok(x.valid, `${preset.id} ${side} @${price}: ${x.errors.join(' | ')}`)
    }
  }
}
console.log('dca.test.mts: tamam')

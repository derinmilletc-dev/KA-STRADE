import assert from 'node:assert/strict'
import {activeCloseTarget, netAtPrice, parseAmount, priceForNetProfit} from './close-target.ts'

const FEE = 0.0005
const near = (a: number | null, b: number, eps = 1e-9) => assert.ok(a !== null && Math.abs(a - b) < eps, `${a} ≈ ${b}`)

// Net kâr için bulunan fiyatta net sonuç tam olarak hedefe eşit (LONG ve SHORT).
for (const long of [true, false]) {
  const price = priceForNetProfit(long, 0.8013, 124.7, 1, FEE)
  near(netAtPrice(long, 0.8013, 124.7, price, FEE), 1)
}
// SHORT'ta kâr için fiyat girişin altında, LONG'da üstünde.
assert.ok((priceForNetProfit(false, 0.8013, 124.7, 1, FEE) ?? 1) < 0.8013)
assert.ok((priceForNetProfit(true, 100, 1, 1, FEE) ?? 0) > 100)
// Başa baş (net 0) fiyatı komisyon yüzünden girişten biraz uzakta.
assert.ok((priceForNetProfit(true, 100, 1, 0, FEE) ?? 0) > 100)
assert.ok((priceForNetProfit(false, 100, 1, 0, FEE) ?? 1e9) < 100)
// Eksik veri -> null
assert.equal(netAtPrice(true, null, 1, 2, FEE), null)
assert.equal(priceForNetProfit(true, 100, 0, 1, FEE), null)
// SHORT'ta imkânsız kâr (fiyat sıfırın altı) -> null
assert.equal(priceForNetProfit(false, 1, 1, 5, FEE), null)

assert.deepEqual(activeCloseTarget({mode: 'profit', value: 1}), {mode: 'profit', value: 1, side: null})
assert.deepEqual(activeCloseTarget({mode: 'price', value: '0.79', side: 'BELOW'}), {mode: 'price', value: 0.79, side: 'BELOW'})
assert.equal(activeCloseTarget({mode: 'x', value: 1}), null)
assert.equal(activeCloseTarget(null), null)
assert.equal(parseAmount('1,5'), 1.5)
assert.equal(parseAmount(' 0.79 '), 0.79)
assert.equal(parseAmount('abc'), null)
assert.equal(parseAmount('-1'), null)
console.log('close-target ok')

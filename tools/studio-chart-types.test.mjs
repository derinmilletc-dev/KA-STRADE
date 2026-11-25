import assert from 'node:assert/strict'
import test from 'node:test'
import {convertCandles, heikinAshi, lastAtr, niceBrick, renko, sanitizeChartType} from '../studio-chart-types.ts'

const c = (time, o, h, l, cl, volume = 1) => ({time, open: o, high: h, low: l, close: cl, volume})

test('Heikin-Ashi formülü', () => {
  const out = heikinAshi([c(1, 10, 14, 8, 12), c(2, 12, 16, 11, 15)])
  assert.equal(out[0].close, 11); assert.equal(out[0].open, 11)
  assert.equal(out[1].open, 11); assert.equal(out[1].close, 13.5)
  assert.equal(out[1].high, 16); assert.equal(out[1].low, 11)
})
test('Heikin-Ashi bozuk mumu atlar, uzunluk korunur', () => {
  assert.equal(heikinAshi([c(1, 1, 2, 0, 1), c(2, NaN, 2, 0, 1)]).length, 1)
})
test('niceBrick okunur adımlara yuvarlar', () => {
  assert.equal(niceBrick(0.0123), 0.01); assert.equal(niceBrick(430), 500); assert.equal(niceBrick(23), 25); assert.equal(niceBrick(0), 0)
})
test('ATR sabit aralıkta aralığa eşit', () => {
  const candles = Array.from({length: 20}, (_, i) => c(i, 100, 105, 95, 100))
  assert.ok(Math.abs(lastAtr(candles) - 10) < 1e-9)
  assert.equal(lastAtr(candles.slice(0, 5)), null)
})
test('Renko: yükseliş tuğla üretir, küçük geri çekilme üretmez, tersine dönüş iki tuğla ister', () => {
  const candles = [c(1, 100, 100, 100, 100), c(2, 100, 131, 100, 130), c(3, 130, 130, 118, 118), c(4, 118, 118, 98, 98)]
  const out = renko(candles, 10)
  assert.deepEqual(out.slice(0, 3).map(b => [b.open, b.close]), [[100, 110], [110, 120], [120, 130]])
  // 118: 130'dan 12 geri → iki tuğla (20) gerekir, yok
  assert.equal(out[3].close < out[3].open, true)
  assert.deepEqual([out[3].open, out[3].close], [120, 110])
  for (const b of out) assert.ok(Math.abs(b.close - b.open) === 10)
})
test('Renko çok kısa veride boş döner, dönüştürücü yönlendirir', () => {
  assert.deepEqual(renko([c(1, 1, 1, 1, 1)]), [])
  const src = [c(1, 1, 2, 0, 1)]
  assert.equal(convertCandles(src, 'candles'), src)
  assert.equal(convertCandles(src, 'line'), src)
})
test('sanitizeChartType bilinmeyeni mum yapar', () => {
  assert.equal(sanitizeChartType('area'), 'area'); assert.equal(sanitizeChartType('x'), 'candles'); assert.equal(sanitizeChartType(5), 'candles')
})

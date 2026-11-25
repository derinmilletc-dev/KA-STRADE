import assert from 'node:assert/strict'
import test from 'node:test'
import {detectAt, detectPatterns} from '../studio-patterns.ts'
import './register-ts-resolve.mjs'
import {extentOf, mergeExtents, rebaseCompare, returnCorrelation} from '../studio-compare.ts'

const c = (o, h, l, cl) => ({open: o, high: h, low: l, close: cl})
const falling = [c(110, 111, 108, 109), c(109, 110, 106, 107), c(107, 108, 104, 105), c(105, 106, 102, 103), c(103, 104, 100, 101)]
const rising = [c(100, 102, 99, 101), c(101, 104, 100, 103), c(103, 106, 102, 105), c(105, 108, 104, 107), c(107, 110, 106, 109)]
const ids = (candles, i) => detectAt(candles, i).map(h => h.id)

test('doji', () => assert.deepEqual(ids([c(100, 105, 95, 100.2)], 0), ['doji']))
test('çekiç yalnız düşüş bağlamında', () => {
  const hammer = c(100, 101.2, 95, 101)
  assert.ok(ids([...falling, hammer], 5).includes('hammer'))
  assert.ok(!ids([...rising, hammer], 5).includes('hammer'))
})
test('kayan yıldız yalnız yükseliş bağlamında', () => {
  const star = c(109, 115, 108.8, 110)
  assert.ok(ids([...rising, star], 5).includes('shootingStar'))
  assert.ok(!ids([...falling, star], 5).includes('shootingStar'))
})
test('yutan boğa / ayı', () => {
  assert.ok(ids([c(105, 106, 99, 100), c(99, 108, 98, 107)], 1).includes('bullEngulf'))
  assert.ok(ids([c(100, 106, 99, 105), c(106, 107, 97, 98)], 1).includes('bearEngulf'))
  assert.ok(!ids([c(105, 106, 99, 100), c(101, 103, 100, 102)], 1).includes('bullEngulf'))
})
test('sabah ve akşam yıldızı', () => {
  assert.ok(ids([c(110, 111, 99, 100), c(99, 100, 97, 98.5), c(99, 109, 98, 108)], 2).includes('morningStar'))
  assert.ok(ids([c(100, 111, 99, 110), c(111, 113, 110, 111.5), c(111, 112, 101, 102)], 2).includes('eveningStar'))
})
test('üç asker / karga', () => {
  assert.ok(ids([c(100, 106, 99.5, 105), c(103, 111, 102.5, 110), c(108, 116, 107.5, 115)], 2).includes('soldiers'))
  assert.ok(ids([c(115, 115.5, 109, 110), c(112, 112.5, 104, 105), c(107, 107.5, 99, 100)], 2).includes('crows'))
})
test('bozuk/sıfır aralıklı mumda formasyon yok, mum başına en çok bir sonuç', () => {
  assert.deepEqual(detectAt([c(1, 1, 1, 1)], 0), [])
  assert.deepEqual(detectAt([c(NaN, 1, 1, 1)], 0), [])
  const hits = detectPatterns([c(105, 106, 99, 100), c(99, 108, 98, 107)])
  assert.ok(hits.every((h, i, a) => a.findIndex(x => x.index === h.index) === i))
})
test('karşılaştırma ilk ortak mumda eşitlenir, eksik zaman null', () => {
  const main = [{time: 1, close: 100}, {time: 2, close: 110}, {time: 3, close: 120}]
  const other = [{time: 2, close: 50}, {time: 3, close: 60}]
  assert.deepEqual(rebaseCompare(main, other), [null, 110, 132])
  assert.deepEqual(rebaseCompare(main, []), [null, null, null])
})
test('extent ve korelasyon', () => {
  assert.deepEqual(extentOf([null, 3, 1]), {low: 1, high: 3}); assert.equal(extentOf([null]), null)
  assert.deepEqual(mergeExtents({low: 1, high: 2}, {low: 0, high: 1.5}), {low: 0, high: 2}); assert.equal(mergeExtents(null, null), null)
  const main = Array.from({length: 40}, (_, i) => ({time: i, close: 100 + Math.sin(i) * 5 + i}))
  const same = main.map(m => ({time: m.time, close: m.close * 2}))
  assert.ok(Math.abs(returnCorrelation(main, same) - 1) < 1e-9)
  assert.equal(returnCorrelation(main.slice(0, 10), same), null)
})

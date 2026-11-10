import assert from 'node:assert/strict'
import test from 'node:test'
import {buildChart, changeText} from '../frontend/src/coin-live-chart.ts'

const candles = values => values.map((close, index) => ({time: 1_700_000_000 + index * 60, close}))

test('yükselen seride çizgi yukarı gider ve ton "up"', () => {
  const shape = buildChart(candles([100, 101, 102, 104]), 300, 100, 4)
  assert.ok(shape)
  assert.equal(shape.tone, 'up')
  assert.equal(shape.first, 100)
  assert.equal(shape.last, 104)
  assert.ok(Math.abs(shape.changePct - 4) < 1e-9)
  assert.equal(shape.lastX, 296)
  assert.equal(shape.lastY, 4) // en yüksek fiyat üst kenarda (pad)
  assert.match(shape.line, /^M4 /)
  assert.ok(shape.area.endsWith('Z'))
})

test('düşen seride ton "down", en düşük fiyat alt kenarda', () => {
  const shape = buildChart(candles([10, 9, 8]), 300, 100, 4)
  assert.ok(shape)
  assert.equal(shape.tone, 'down')
  assert.equal(shape.lastY, 96)
})

test('düz seri ortada çizilir ve çökmez', () => {
  const shape = buildChart(candles([5, 5, 5]), 300, 100, 4)
  assert.ok(shape)
  assert.equal(shape.lastY, 50)
  assert.equal(shape.changePct, 0)
  assert.equal(shape.tone, 'up')
})

test('yetersiz ya da bozuk veri null döner (uydurma çizim yok)', () => {
  assert.equal(buildChart(null, 300, 100), null)
  assert.equal(buildChart(candles([1]), 300, 100), null)
  assert.equal(buildChart(candles([NaN, 0, -1]), 300, 100), null)
  assert.equal(buildChart(candles([1, 2]), 0, 100), null)
})

test('geçersiz mumlar atlanır, kalanlarla çizilir', () => {
  const shape = buildChart(candles([1, NaN, 2, 3]), 300, 100)
  assert.ok(shape)
  assert.equal(shape.first, 1)
  assert.equal(shape.last, 3)
})

test('değişim metni Türkçe ondalık ve işaretli', () => {
  assert.equal(changeText(0.424), '+0,42%')
  assert.equal(changeText(-1.1), '−1,10%')
  assert.equal(changeText(Number.NaN), '—')
})

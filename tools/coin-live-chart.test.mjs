import assert from 'node:assert/strict'
import test from 'node:test'
import {buildChart, changeText, clockText, nearestIndex, smoothPath} from '../frontend/src/coin-live-chart.ts'

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

test('yumuşak eğri her noktadan geçer ve fiyatın gitmediği yere taşmaz', () => {
  const points = [{x: 0, y: 50}, {x: 10, y: 10}, {x: 20, y: 10}, {x: 30, y: 60}, {x: 40, y: 55}]
  const d = smoothPath(points)
  assert.ok(d.startsWith('M0 50'))
  assert.equal((d.match(/C/g) ?? []).length, points.length - 1)
  assert.ok(d.endsWith('40 55'))
  const numbers = d.replace(/[MC]/g, ' ').trim().split(/\s+/).map(Number)
  const ys = numbers.filter((_, index) => index % 2 === 1)
  assert.ok(Math.min(...ys) >= 10 - 1e-9 && Math.max(...ys) <= 60 + 1e-9, 'kontrol noktaları tepe/dibin dışına çıkmamalı')
})

test('yumuşak eğri tek nokta ve boş girdide çökmez', () => {
  assert.equal(smoothPath([]), '')
  assert.equal(smoothPath([{x: 1, y: 2}]), 'M1 2')
})

test('grafik noktaları fiyat ve zamanı taşır (parmakla gezinme için)', () => {
  const shape = buildChart(candles([10, 12, 11]), 300, 100)
  assert.deepEqual(shape.points.map(point => point.value), [10, 12, 11])
  assert.equal(shape.points[1].time, 1_700_000_060)
})

test('en yakın nokta seçimi sınırlara sabitlenir', () => {
  assert.equal(nearestIndex(0, 60), 0)
  assert.equal(nearestIndex(1, 60), 59)
  assert.equal(nearestIndex(-3, 60), 0)
  assert.equal(nearestIndex(9, 60), 59)
  assert.equal(nearestIndex(0.5, 3), 1)
  assert.equal(nearestIndex(Number.NaN, 60), 0)
  assert.equal(nearestIndex(0.5, 0), 0)
})

test('saat metni geçersiz zamanda boş döner', () => {
  assert.equal(clockText(0), '')
  assert.equal(clockText(Number.NaN), '')
  assert.match(clockText(1_700_000_000), /^\d{2}:\d{2}$/)
})

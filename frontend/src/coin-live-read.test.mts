import assert from 'node:assert/strict'
import test from 'node:test'
import {intervalSeconds, readCandles, windowText, type ReadCandle} from './coin-live-read.ts'

const candle = (i: number, close: number, spread = 0.2, volume = 100): ReadCandle => ({time: 1000 + i * 60, open: close, high: close + spread, low: close - spread, close, volume})
const series = (closes: number[], volume = 100) => closes.map((close, i) => candle(i, close, 0.2, volume))
const flat = (n: number, base = 100) => Array.from({length: n}, (_, i) => base + (i % 2 ? 0.3 : -0.3))

test('too few candles gives no reading', () => {
  assert.equal(readCandles(series(flat(10))), null)
  assert.equal(readCandles(null), null)
  assert.equal(readCandles([{time: 1, open: NaN, high: 1, low: 1, close: 1, volume: 0}]), null)
})
test('steady climb is a clean uptrend', () => {
  const closes = Array.from({length: 60}, (_, i) => 100 + i * 0.3)
  const read = readCandles(series(closes), 'son 1 saat')
  assert.ok(read)
  assert.equal(read.tone === 'up' || read.tone === 'down', true)
  assert.match(read.chips[0], /^\+%/)
  assert.match(read.chips[0], /son 1 saat/)
})
test('steady fall is a clean downtrend', () => {
  const closes = Array.from({length: 60}, (_, i) => 120 - i * 0.3)
  const read = readCandles(series(closes))
  assert.ok(read)
  assert.equal(read.tone, 'down')
  assert.match(read.chips[0], /^−%/)
})
test('choppy range has no direction', () => {
  const read = readCandles(series(flat(60)))
  assert.ok(read)
  assert.equal(read.tone, 'flat')
  assert.match(read.head, /Yatay|Belirgin/)
})
test('false break above resistance is a bull trap', () => {
  const closes = [...flat(56), 100.1, 101.5, 100.0, 99.9]
  const rows = series(closes)
  rows[58] = {...rows[58], high: 102.2}
  const read = readCandles(rows)
  assert.ok(read)
  assert.equal(read.tone, 'warn')
  assert.match(read.head, /Yukarı kırılım tuzağı/)
})
test('false break below support is a bear trap', () => {
  const closes = [...flat(56), 99.9, 98.5, 100.0, 100.1]
  const rows = series(closes)
  rows[58] = {...rows[58], low: 97.8}
  const read = readCandles(rows)
  assert.ok(read)
  assert.equal(read.tone, 'warn')
  assert.match(read.head, /Aşağı kırılım tuzağı/)
})
test('close above the range with volume is a confirmed breakout', () => {
  const rows = series([...flat(57), 101.5, 102.5, 103])
  for (const i of [57, 58, 59]) rows[i] = {...rows[i], volume: 400}
  const read = readCandles(rows)
  assert.ok(read)
  assert.equal(read.tone, 'up')
  assert.match(read.head, /Yukarı kırılım$/)
  assert.ok(read.chips.includes('Hacim yüksek'))
  assert.match(read.text, /Hacim de destekliyor/)
})
test('close below the range without volume warns it is unconfirmed', () => {
  const read = readCandles(series([...flat(57), 98.5, 97.5, 97]))
  assert.ok(read)
  assert.equal(read.tone, 'down')
  assert.match(read.text, /Hacim güçlü değil/)
})
test('pullback inside an uptrend is labelled as such', () => {
  const closes = [...Array.from({length: 54}, (_, i) => 100 + i * 0.4), 120.5, 120.0, 119.6, 119.2, 118.8, 118.4]
  const read = readCandles(series(closes))
  assert.ok(read)
  assert.equal(read.head, 'Yükselişte geri çekilme')
})
test('window text', () => {
  assert.equal(windowText(60, 60), 'son 1 saat')
  assert.equal(windowText(300, 60), 'son 5 saat')
  assert.equal(windowText(900, 60), 'son 15 saat')
  assert.equal(windowText(60, 45), 'son 45 dk')
  assert.equal(windowText(3600, 60), 'son 60 saat')
  assert.equal(windowText(86400, 60), 'son 60 gün')
})
test('interval seconds', () => {
  assert.equal(intervalSeconds('1m'), 60)
  assert.equal(intervalSeconds('15m'), 900)
  assert.equal(intervalSeconds('4h'), 14400)
  assert.equal(intervalSeconds('1d'), 86400)
  assert.equal(intervalSeconds('x'), 0)
  assert.equal(intervalSeconds(''), 0)
})

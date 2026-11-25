import assert from 'node:assert/strict'
import test from 'node:test'
import {bollinger, computeIndicators, ema, macd, overlayExtent, rsi, sanitizeIndicators, sma, vwap} from '../studio-indicators.ts'

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} ≠ ${b}`)

test('sma: ısınma null, sonra kayan ortalama', () => {
  const out = sma([1, 2, 3, 4, 5], 3)
  assert.deepEqual(out, [null, null, 2, 3, 4])
})

test('ema: ilk değer SMA, sonra üstel', () => {
  const out = ema([1, 2, 3, 4, 5], 3)
  assert.deepEqual(out.slice(0, 2), [null, null])
  close(out[2], 2)
  close(out[3], 3) // 4*0.5 + 2*0.5
  close(out[4], 4)
  assert.deepEqual(ema([1, 2], 5), [null, null])
})

test('bollinger: sabit seride bant daralır, orta=SMA', () => {
  const flat = bollinger(new Array(25).fill(10), 20, 2)
  close(flat.mid[24], 10); close(flat.upper[24], 10); close(flat.lower[24], 10)
  const noisy = bollinger([...Array(19).fill(10), 20], 20, 2)
  assert.ok(noisy.upper[19] > noisy.mid[19] && noisy.lower[19] < noisy.mid[19])
  close(noisy.upper[19] - noisy.mid[19], noisy.mid[19] - noisy.lower[19])
})

test('rsi: sürekli yükselişte 100, sürekli düşüşte 0, düz seride 50', () => {
  const up = rsi(Array.from({length: 30}, (_, i) => i + 1), 14)
  assert.equal(up[14], 100); assert.equal(up[29], 100)
  const down = rsi(Array.from({length: 30}, (_, i) => 30 - i), 14)
  assert.equal(down[29], 0)
  assert.equal(rsi(new Array(30).fill(5), 14)[20], 50)
  assert.equal(rsi([1, 2, 3], 14).every(value => value === null), true)
})

test('rsi: Wilder referans değeri (klasik örnek)', () => {
  const data = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.0, 46.03, 46.41, 46.22, 45.64]
  const out = rsi(data, 14)
  assert.ok(Math.abs(out[14] - 70.46) < 0.2, `rsi14=${out[14]}`) // StockCharts örneği ≈ 70.53
})

test('macd: çizgi=EMA12-EMA26, histogram=macd-signal, hizalı uzunluk', () => {
  const values = Array.from({length: 60}, (_, i) => 100 + Math.sin(i / 5) * 5 + i * 0.1)
  const result = macd(values)
  assert.equal(result.macd.length, 60)
  const a = ema(values, 12), b = ema(values, 26)
  close(result.macd[40], a[40] - b[40])
  assert.equal(result.macd[24], null); assert.notEqual(result.macd[25], null)
  assert.notEqual(result.signal[33], null); assert.equal(result.signal[32], null)
  close(result.histogram[50], result.macd[50] - result.signal[50])
})

test('vwap: gün değişince sıfırlanır, hacimsiz mumda tipik fiyat', () => {
  const day = 86400
  const candles = [
    {time: 0, open: 1, high: 3, low: 1, close: 2, volume: 10},
    {time: 60, open: 2, high: 4, low: 2, close: 3, volume: 10},
    {time: day, open: 9, high: 9, low: 9, close: 9, volume: 0},
  ]
  const out = vwap(candles)
  close(out[0], 2); close(out[1], (2 * 10 + 3 * 10) / 20); close(out[2], 9)
  const ms = vwap([{time: 1_700_000_000_000, open: 1, high: 1, low: 1, close: 1, volume: 1}])
  close(ms[0], 1)
})

test('computeIndicators yalnız açık olanları hesaplar; sanitize bilinmeyeni atar', () => {
  const candles = Array.from({length: 40}, (_, i) => ({time: i * 60, open: i, high: i + 1, low: i - 1, close: i, volume: 5}))
  const set = computeIndicators(candles, ['ema20', 'rsi'])
  assert.ok(set.ema20 && set.rsi && !set.macd && !set.bollinger && !set.vwap)
  assert.deepEqual(sanitizeIndicators(['rsi', 'x', 'rsi', 'ema20', 5]), ['ema20', 'rsi'])
  assert.deepEqual(sanitizeIndicators('nope'), [])
})

test('overlayExtent: pencerede en düşük/yüksek, boşsa null', () => {
  const set = {ema20: [null, 5, 7, 6], vwap: [null, 4, 8, 9]}
  assert.deepEqual(overlayExtent(set, 1, 3), {low: 4, high: 8})
  assert.equal(overlayExtent({}, 0, 10), null)
})

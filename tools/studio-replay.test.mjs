import assert from 'node:assert/strict'
import test from 'node:test'
import {canReplay, clampReplay, replaySlice, replayStart, sanitizeSpeed, speedMs, stepReplay} from '../studio-replay.ts'
import {parseKlines} from '../studio-klines.ts'

test('başlangıç noktası güvenli aralıkta', () => {
  assert.equal(replayStart(160), 96); assert.equal(replayStart(30), 20); assert.equal(replayStart(22), 20)
  assert.equal(canReplay(21), false); assert.equal(canReplay(22), true); assert.equal(replayStart(5), 4)
})
test('clamp ve adım sınırları aşmaz, sonda oynatma durur', () => {
  assert.equal(clampReplay(-5, 100), 20); assert.equal(clampReplay(500, 100), 99); assert.equal(clampReplay(NaN, 100), 20)
  const end = stepReplay({index: 98, playing: true, speed: 1}, 1, 100)
  assert.deepEqual(end, {index: 99, playing: false, speed: 1})
  assert.equal(stepReplay({index: 30, playing: true, speed: 1}, 1, 100).playing, true)
  assert.equal(stepReplay({index: 21, playing: false, speed: 1}, -5, 100).index, 20)
})
test('slice son görünen muma kadar', () => {
  assert.equal(replaySlice(Array.from({length: 100}, (_, i) => i), 49).length, 50)
  assert.deepEqual(replaySlice([], 3), [])
})
test('hız', () => { assert.equal(speedMs(4), 250); assert.equal(speedMs(99), 1000); assert.equal(sanitizeSpeed(10), 10); assert.equal(sanitizeSpeed('x'), 1) })
test('parseKlines geçerli veriyi alır, bozukta null döner', () => {
  const ok = [{time: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 3}, {time: 2, open: 1.5, high: 2, low: 1, close: 2, volume: 1}]
  assert.equal(parseKlines(ok).length, 2)
  assert.equal(parseKlines([ok[0]]), null)
  assert.equal(parseKlines([ok[0], {...ok[1], high: 0.1}]), null)
  assert.equal(parseKlines([ok[0], {...ok[1], close: NaN}]), null)
  assert.equal(parseKlines('x'), null)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import {personalNotes} from './personal-read.ts'

const base = {n: 30, enough: true, insights: [], bad_blocks: [{from: 20, to: 24, win_rate: 20, trades: 10}], good_blocks: [{from: 8, to: 12, win_rate: 80, trades: 10}],
  by_coin: [{coin: 'POPUSDT', n: 6, win_rate: 17, net: -12.5}, {coin: 'BTCUSDT', n: 5, win_rate: 80, net: 8}, {coin: 'ETHUSDT', n: 4, win_rate: 50, net: 0.5}],
  avg_win: 2.98, avg_loss: 10.1}
// TSİ = UTC+3
const at = (tsHour: number) => new Date(Date.UTC(2026, 10, 24, tsHour - 3, 30))

test('no notes without enough data', () => {
  assert.deepEqual(personalNotes(null, 'BTCUSDT', 'LONG', at(10)), [])
  assert.deepEqual(personalNotes({...base, enough: false}, 'BTCUSDT', 'LONG', at(10)), [])
})
test('weak hour warns and goes first', () => {
  const notes = personalNotes(base, 'POPUSDT', 'LONG', at(21))
  assert.equal(notes[0].key, 'hour')
  assert.equal(notes[0].tone, 'warn')
  assert.match(notes[0].text, /zayıf saatlerinden/)
})
test('good hour is positive, outside blocks is silent', () => {
  assert.equal(personalNotes(base, 'ETHUSDT', 'NEUTRAL', at(9))[0].tone, 'up')
  assert.ok(!personalNotes(base, 'ETHUSDT', 'NEUTRAL', at(15)).some(n => n.key === 'hour'))
})
test('coin record matches by root and picks tone', () => {
  const pop = personalNotes(base, 'POPUSDT', 'NEUTRAL', at(15)).find(n => n.key === 'coin')
  assert.equal(pop?.tone, 'warn')
  assert.match(pop?.text ?? '', /POP: son 30 günde 6 işlemin %17/)
  assert.match(pop?.text ?? '', /−12,50 USD/)
  assert.equal(personalNotes(base, 'BTC-USD', 'NEUTRAL', at(15)).find(n => n.key === 'coin')?.tone, 'up')
  assert.equal(personalNotes(base, 'ETHUSDT', 'NEUTRAL', at(15)).find(n => n.key === 'coin')?.tone, 'flat')
  assert.equal(personalNotes(base, 'SOLUSDT', 'NEUTRAL', at(15)).find(n => n.key === 'coin'), undefined)
})
test('loss bigger than win only noted when the read has a direction', () => {
  assert.ok(personalNotes(base, 'SOLUSDT', 'LONG', at(15)).some(n => n.key === 'size'))
  assert.ok(!personalNotes(base, 'SOLUSDT', 'NEUTRAL', at(15)).some(n => n.key === 'size'))
  assert.ok(!personalNotes({...base, avg_loss: 2}, 'SOLUSDT', 'LONG', at(15)).some(n => n.key === 'size'))
  assert.ok(!personalNotes({...base, avg_win: null}, 'SOLUSDT', 'LONG', at(15)).some(n => n.key === 'size'))
})
test('at most three notes, warnings first', () => {
  const notes = personalNotes(base, 'POPUSDT', 'SHORT', at(21))
  assert.ok(notes.length <= 3)
  assert.deepEqual(notes.map(n => n.tone), ['warn', 'warn', 'warn'])
})

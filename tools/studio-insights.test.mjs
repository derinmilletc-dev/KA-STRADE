import assert from 'node:assert/strict'
import {test} from 'node:test'
import './register-ts-resolve.mjs'

const {buildInsights, candleRead, publishEngineNote, readEngineNote, withEngineNote, MIN_INSIGHT_CANDLES} = await import('../studio-insights.ts')

const make = (n, fn) => Array.from({length: n}, (_, i) => { const c = fn(i); return {time: 1_700_000_000 + i * 900, open: c.open ?? c.close, high: c.high ?? c.close + 1, low: c.low ?? c.close - 1, close: c.close, volume: c.volume ?? 100} })

test('az mumda yorum hazır değil', () => {
  assert.equal(buildInsights({candles: make(MIN_INSIGHT_CANDLES - 1, i => ({close: 100 + i}))}).ready, false)
})

test('istikrarlı yükselişte trend yükseliş, genel görünüm yükseliş yönlü', () => {
  const candles = make(120, i => ({close: 100 + i * 0.8, open: 100 + i * 0.8 - 0.3, high: 100 + i * 0.8 + 0.5, low: 100 + i * 0.8 - 0.6}))
  const result = buildInsights({candles, analysis: {direction: 'LONG', support: 150, resistance: 220}, decision: {direction: 'LONG', confidenceScore: 70, mtfConfirmed: 4, mtfTotal: 5, riskReward: 1.5}})
  assert.equal(result.ready, true)
  assert.equal(result.items.find(i => i.id === 'trend').tone, 'up')
  assert.ok(/yükseliş/i.test(result.verdict.text))
  const plan = result.items.find(i => i.id === 'plan')
  assert.ok(plan.text.includes('MTF 4/5') && plan.text.includes('1:1,5'))
  assert.ok(result.summary.startsWith('Motor yorumu') && result.summary.length <= 520)
})

test('düşüşte trend düşüş ve plan LONG ise çelişki uyarısı', () => {
  const candles = make(120, i => ({close: 200 - i * 0.8}))
  const result = buildInsights({candles, decision: {direction: 'LONG', confidenceScore: 60}})
  assert.equal(result.items.find(i => i.id === 'trend').tone, 'down')
  assert.ok(result.items.find(i => i.id === 'plan').text.includes('çelişiyor'))
})

test('mum formları: yutan mum ve uzun fitil', () => {
  const base = make(40, i => ({close: 100}))
  const bull = [...base.slice(0, -2), {time: 1, open: 101, high: 101.2, low: 99.4, close: 99.6, volume: 100}, {time: 2, open: 99.4, high: 102.2, low: 99.3, close: 102, volume: 100}]
  assert.equal(candleRead(bull).tone, 'up')
  const wick = [...base.slice(0, -1), {time: 3, open: 100, high: 100.3, low: 97, close: 100.1, volume: 100}]
  assert.equal(candleRead(wick).tone, 'up')
})

test('motor notu: bayat ve yanlış anahtarda boş; soru sınırı aşılmaz', () => {
  publishEngineNote('BTCUSDT:15m', 'özet metni')
  assert.equal(readEngineNote('BTCUSDT:15m'), 'özet metni')
  assert.equal(readEngineNote('ETHUSDT:15m'), '')
  assert.equal(readEngineNote('BTCUSDT:15m', -1), '')
  const q = 'Bu grafiği yorumla'
  const note = 'x'.repeat(900)
  const out = withEngineNote(q, note)
  assert.ok(out.length <= 800 && out.startsWith(q))
  assert.equal(withEngineNote(q, ''), q)
  assert.equal(withEngineNote('y'.repeat(790), note), 'y'.repeat(790))
})

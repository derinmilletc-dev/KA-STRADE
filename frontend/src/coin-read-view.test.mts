import assert from 'node:assert/strict'
import test from 'node:test'
import {alarmSummary, analysisKey, arrowOf, confidenceTone, fitTitle, fitTone, gaugePos, levelBar, priceText, signedPct} from './coin-read-view.ts'

test('arrow and gauge', () => {
  assert.equal(arrowOf('up', 1.2), '▲')
  assert.equal(arrowOf('down', -1), '▼')
  assert.equal(arrowOf('flat', 0.2), '■')
  assert.equal(arrowOf('warn', 1), '!')
  assert.equal(gaugePos(0), 50)
  assert.equal(gaugePos(2), 100)
  assert.equal(gaugePos(-9), 0)
  assert.equal(gaugePos(NaN), 50)
  assert.equal(gaugePos(1), 75)
})
test('text helpers', () => {
  assert.equal(signedPct(1.234), '+%1,23')
  assert.equal(signedPct(-0.5), '−%0,50')
  assert.equal(signedPct(null), '—')
  assert.equal(priceText(0.452), '0,452')
  assert.equal(priceText(undefined), '—')
  assert.equal(priceText(NaN), '—')
  assert.equal(confidenceTone('Yüksek'), 'high')
  assert.equal(confidenceTone('Orta'), 'mid')
  assert.equal(confidenceTone('Düşük'), 'low')
})
test('level bar places price between support and resistance', () => {
  const bar = levelBar(105, {support: [{price: 100, dist_pct: -4.8, strength: 3, touches: 2}], resistance: [{price: 110, dist_pct: 4.8, strength: 2, touches: 1}]})
  assert.ok(bar)
  assert.ok(bar.pos > 40 && bar.pos < 60)
  const marks = Object.fromEntries(bar.marks.map(m => [m.kind, m.pos]))
  assert.ok(marks.support < bar.pos && bar.pos < marks.resistance)
  assert.ok(marks.support >= 0 && marks.resistance <= 100)
  assert.equal(levelBar(100, {support: [], resistance: []}), null)
  assert.equal(levelBar(0, {support: [{price: 1, dist_pct: 0, strength: 1, touches: 1}], resistance: []}), null)
})
test('position fit labels', () => {
  assert.equal(fitTone('uyumlu'), 'up')
  assert.equal(fitTone('ters'), 'down')
  assert.equal(fitTone('kararsiz'), 'flat')
  assert.match(fitTitle('ters'), /ters/)
})
test('alarm summary and symbol mapping', () => {
  assert.equal(alarmSummary({mode: 'against', min_tf: '15m'}), 'Yalnız ters sinyal · Dengeli')
  assert.equal(alarmSummary({mode: 'all', min_tf: '5m'}), 'Her şey · Hassas')
  assert.equal(analysisKey('BTC'), 'BTCUSDT')
  assert.equal(analysisKey(null), null)
  assert.equal(analysisKey('btc'), null)
})

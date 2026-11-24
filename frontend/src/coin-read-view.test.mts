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

import {accuracyLine, bookPos, minutesText, nextDirection, rrText, topEvent, usdText, type ReadEvents, type ReadStats} from './coin-read-view.ts'
import {weakHourNote} from './coinbase-stats.ts'

test('book position and money text', () => {
  assert.equal(bookPos(0), 50)
  assert.equal(bookPos(1), 95)
  assert.equal(bookPos(-1), 5)
  assert.equal(bookPos(NaN), 50)
  assert.equal(usdText(1_250_000), '1,3 mn $')
  assert.equal(usdText(48_000), '48 bin $')
  assert.equal(usdText(900), '900 $')
  assert.equal(rrText(2.25), '1:2,3')
})
test('minutes text', () => {
  assert.equal(minutesText(45), '45 dk')
  assert.equal(minutesText(150), '2 sa 30 dk')
  assert.equal(minutesText(120), '2 sa')
  assert.equal(minutesText(3000), '2 gün')
  assert.equal(minutesText(-5), '0 dk')
})
test('top event prefers danger, then nearest', () => {
  const events: ReadEvents = {covered: true, next_funding: '', items: [
    {kind: 'cpi', at: '', minutes: 600, level: 'warn', text: 'a'}, {kind: 'fomc', at: '', minutes: 90, level: 'danger', text: 'b'}, {kind: 'funding', at: '', minutes: 10, level: 'warn', text: 'c'}]}
  assert.equal(topEvent(events)?.text, 'b')
  assert.equal(topEvent({...events, items: events.items.slice(0, 1).concat(events.items.slice(2))})?.text, 'c')
  assert.equal(topEvent(null), null)
  assert.equal(topEvent({covered: false, next_funding: '', items: []}), null)
})
test('accuracy line hides small samples and prefers the coin', () => {
  const t = (n: number, rate: number | null) => ({n, hit: 0, miss: 0, flat: 0, rate})
  const base: ReadStats = {days: 7, overall: t(40, 58), by_confidence: {}, symbol: null}
  assert.match(accuracyLine(base) ?? '', /tüm coinlerde 40 yorumun %58/)
  assert.match(accuracyLine({...base, symbol: {symbol: 'BTCUSDT', ...t(12, 67)}}) ?? '', /bu coin için 12 yorumun %67/)
  assert.match(accuracyLine({...base, symbol: {symbol: 'BTCUSDT', ...t(4, null)}}) ?? '', /tüm coinlerde/)
  assert.equal(accuracyLine({...base, overall: t(3, null)}), null)
  assert.equal(accuracyLine(null), null)
})
test('watch direction cycles', () => {
  assert.equal(nextDirection('both'), 'long')
  assert.equal(nextDirection('long'), 'short')
  assert.equal(nextDirection('short'), 'both')
})
test('weak hour note only inside a flagged block and with enough data', () => {
  const insights = {n: 12, enough: true, insights: [], bad_blocks: [{from: 0, to: 4, win_rate: 20, trades: 5}], good_blocks: []}
  const at = (utcHour: number) => new Date(Date.UTC(2026, 10, 24, utcHour, 30))
  assert.match(weakHourNote(insights, at(22)) ?? '', /TSİ 00-04.*%20/)   // 22:30 UTC = 01:30 TSİ
  assert.equal(weakHourNote(insights, at(10)), null)
  assert.equal(weakHourNote({...insights, enough: false}, at(22)), null)
  assert.equal(weakHourNote(null, at(22)), null)
})

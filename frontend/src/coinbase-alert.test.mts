import assert from 'node:assert/strict'
import test from 'node:test'
import {alertPresets, alertsFor, firedText, distancePct, parseAlertLevel, pctText, previewLevel} from './coinbase-alert.ts'

test('parse', () => {
  assert.equal(parseAlertLevel(' 1,25 '), 1.25)
  for (const bad of ['', '0', '-2', 'abc', 'Infinity']) assert.equal(parseAlertLevel(bad), null)
})
test('presets', () => {
  const p = alertPresets(100)
  assert.deepEqual(p.up.map(x => x.level), [101, 103, 105, 110])
  assert.deepEqual(p.down.map(x => x.level), [99, 97, 95, 90])
  assert.ok(p.up.every(x => x.dir === 'above') && p.down.every(x => x.dir === 'below'))
  assert.deepEqual(alertPresets(null), {up: [], down: []})
  assert.equal(alertPresets(0.00001234).up[0].level, 0.0000124634)
})
test('distance and preview', () => {
  assert.equal(distancePct(110, 100), 10)
  assert.equal(distancePct(0, 100), null)
  assert.equal(previewLevel('105', 100).dir, 'above')
  assert.equal(previewLevel('95', 100).dir, 'below')
  assert.equal(previewLevel('100', 100).ok, false)
  assert.equal(previewLevel('x', 100).ok, false)
  assert.equal(previewLevel('5', null).ok, true)
  assert.equal(pctText(5), '+%5.00'); assert.equal(pctText(-12.34), '−%12.3'); assert.equal(pctText(null), '')
})
test('alertsFor', () => {
  const list = [{id: 1, product_id: 'A', level: '1', direction: 'above' as const, ref_price: '0.9'}, {id: 2, product_id: 'B', level: '2', direction: 'below' as const, ref_price: '2.1'}]
  assert.deepEqual(alertsFor(list, 'B').map(x => x.id), [2])
})
test('firedText', () => {
  assert.deepEqual(firedText({id: 1, product_id: 'BTCUSDT', level: '100.00', direction: 'above', trigger_price: '101.5'}), {title: '🔔 BTC alarmı', body: 'BTC 100 seviyesinin üstüne çıktı (şimdi 101.5).'})
  assert.equal(firedText({id: 2, product_id: 'AVP-20DEC30-CDE', level: '5', direction: 'below'}).body, 'AVP 5 seviyesinin altına indi.')
})

import assert from 'node:assert/strict'
import test from 'node:test'
import {makeRepeat, parseRepeat, repeatLevels} from './coinbase-repeat.ts'

const plan = {product_id: 'AVP-20DEC30-CDE', side: 'BUY', margin_usd: '40', contracts: 3, leverage: '5.2', entry_reference: '100', stop_price: '97', take_profit_price: '106'}

test('long record uses percentages', () => {
  const r = makeRepeat(plan)!
  assert.equal(r.stopPct, 3); assert.equal(r.tpPct, 6); assert.equal(r.leverage, '5')
  assert.deepEqual(repeatLevels(r, 200), {stop: '194', takeProfit: '212'})
})
test('short mirrors', () => {
  const r = makeRepeat({...plan, side: 'SELL', stop_price: '103', take_profit_price: '94'})!
  assert.deepEqual(repeatLevels(r, 200), {stop: '206', takeProfit: '188'})
})
test('no target and bad data', () => {
  assert.equal(makeRepeat({...plan, take_profit_price: undefined})!.tpPct, null)
  assert.equal(repeatLevels(makeRepeat({...plan, take_profit_price: undefined})!, 50)!.takeProfit, '')
  assert.equal(makeRepeat({...plan, stop_price: '101'}), null)   // stop yanlış yönde
  assert.equal(makeRepeat({...plan, side: 'X'}), null)
  assert.equal(repeatLevels(makeRepeat(plan)!, 0), null)
})
test('parseRepeat rejects junk', () => {
  const r = makeRepeat(plan)!
  assert.deepEqual(parseRepeat(JSON.parse(JSON.stringify(r))), r)
  for (const bad of [null, 'x', {}, {...r, productId: '<b>'}, {...r, stopPct: -1}, {...r, side: 'Z'}]) assert.equal(parseRepeat(bad), null)
})

import assert from 'node:assert/strict'
import {test} from 'node:test'
import {PAID_PLAN_AMOUNT, PAID_PLAN_NAME, planForAmount, SMART_TRADE_FEATURES} from '../smart-trade-plans.ts'

test('plan comes only from the two exact amounts', () => {
  assert.equal(planForAmount('119.90'), 'MASTER_MODE')
  assert.equal(planForAmount('40.00'), 'SMART_TRADE')
  for (const bad of ['40', '40.0', '39.99', '119.9', '0.00', '', null, undefined, 40]) assert.equal(planForAmount(bad), null)
  assert.deepEqual(PAID_PLAN_AMOUNT, {MASTER_MODE: '119.90', SMART_TRADE: '40.00'})
  assert.equal(PAID_PLAN_NAME.SMART_TRADE, 'SMART TRADE')
})
test('smart trade is manual trading: no automation is promised', () => {
  const text = SMART_TRADE_FEATURES.join(' ').toLowerCase()
  assert.ok(text.includes('manuel') && text.includes('grid'))
  assert.ok(!text.includes('auto trade') && !text.includes('otomatik') && !text.includes('original v2'))
})

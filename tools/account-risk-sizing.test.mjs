import assert from 'node:assert/strict'
import {test} from 'node:test'
import {preferredRiskMargin} from '../account-risk-sizing.ts'

const valid = {wallet: 1000, available: 800, entry: 100, stop: 98, leverage: 10, percent: 1}
test('Risk preference computes estimated margin without creating an order or exceeding budget', () => {
  const result = preferredRiskMargin(valid)
  assert.equal(result.margin, 50)
  assert.match(result.reason, /Tahmini/)
  assert.equal(result.margin * valid.leverage * Math.abs(valid.entry - valid.stop) / valid.entry, 10)
})
test('Risk calculation refuses missing/stale-invalid account or stop data explicitly', () => {
  for (const update of [{wallet: NaN}, {entry: 0}, {stop: 100}, {stop: 201}, {leverage: 126}, {percent: 1.1}, {percent: 0.09}, {available: 1}]) {
    const result = preferredRiskMargin({...valid, ...update})
    assert.equal(result.margin, null)
    assert.ok(result.reason.length > 10)
  }
})
test('Risk calculation rounds down rather than overshooting chosen loss budget', () => {
  const input = {...valid, wallet: 333, stop: 97, leverage: 3, percent: 1}
  const result = preferredRiskMargin(input)
  assert.equal(result.margin, 37)
})

import {marginForLoss} from '../account-risk-sizing.ts'
test('Risk-amount sizing derives margin from the loss the user accepts and fails closed', () => {
  const ok = marginForLoss({lossUsdt: 1, available: 800, entry: 100, stop: 98, leverage: 10})
  assert.equal(ok.margin, 5)
  assert.equal(ok.margin * 10 * 0.02, 1)
  assert.equal(marginForLoss({lossUsdt: 0, available: 800, entry: 100, stop: 98, leverage: 10}).margin, null)
  assert.equal(marginForLoss({lossUsdt: 1, available: 800, entry: 100, stop: 100, leverage: 10}).margin, null)
  assert.equal(marginForLoss({lossUsdt: 100, available: 50, entry: 100, stop: 98, leverage: 1}).margin, null)
  assert.equal(marginForLoss({lossUsdt: 0.1, available: 800, entry: 100, stop: 98, leverage: 10}).margin, null)
})

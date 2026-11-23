import assert from 'node:assert/strict'
import test from 'node:test'
import {parseTarget, targetOf, targetProgress} from './coinbase-profit-target.ts'

test('parseTarget', () => {
  assert.equal(parseTarget('5'), 5)
  assert.equal(parseTarget(' 2,5 '), 2.5)
  for (const bad of ['', '0', '-3', 'abc', '1e9', 'Infinity']) assert.equal(parseTarget(bad), null)
})
test('targetProgress', () => {
  assert.deepEqual(targetProgress(2.5, 10), {pct: 25, left: 7.5})
  assert.deepEqual(targetProgress(-3, 10), {pct: 0, left: 13})
  assert.deepEqual(targetProgress(12, 10), {pct: 100, left: 0})
  assert.equal(targetProgress(null, 10), null)
  assert.equal(targetProgress(1, null), null)
})
test('targetOf', () => {
  assert.equal(targetOf({X: {target_usd: '5.00'}}, 'X'), 5)
  assert.equal(targetOf({}, 'X'), null)
  assert.equal(targetOf(null, 'X'), null)
})

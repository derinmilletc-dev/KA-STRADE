import assert from 'node:assert/strict'
import {test} from 'node:test'
import {buildPlanDraft, parsePlanPicks, PLAN_FEE_RATE, targetExitPrice} from '../kais-plan-calc.ts'

const long = {symbol: 'BTCUSDT', direction: 'LONG', confidence_value: 96, entry: 100, stop_loss: 98, tp1: 103, tp2: 105, tp3: 108}
const short = {...long, symbol: 'ETHUSDT', direction: 'SHORT', entry: 100, stop_loss: 102, tp1: 97, tp2: 95, tp3: 92}

// Net kâr = q·|çıkış−giriş| (yön işaretli) − giriş komisyonu − çıkış komisyonu.
const netAt = (direction, entry, margin, leverage, exit) => {
  const q = margin * leverage / entry
  const gross = q * (direction === 'LONG' ? exit - entry : entry - exit)
  return gross - q * entry * PLAN_FEE_RATE - q * exit * PLAN_FEE_RATE
}

test('Target price gives exactly the requested net profit after both fees (long and short)', () => {
  for (const [direction, entry, margin, leverage, target] of [['LONG', 100, 20, 10, 1], ['SHORT', 100, 20, 10, 1], ['LONG', 0.0123, 50, 5, 2.5], ['SHORT', 65000, 100, 20, 1]]) {
    const price = targetExitPrice(direction, entry, margin, leverage, target)
    assert.ok(Math.abs(netAt(direction, entry, margin, leverage, price) - target) < 1e-9, `${direction} ${entry}`)
    assert.ok(direction === 'LONG' ? price > entry : price < entry)
  }
})

test('Invalid inputs return null instead of an invented price', () => {
  for (const args of [[100, 0, 10, 1], [100, 20, 0, 1], [100, 20, 10, 0], [0, 20, 10, 1], [NaN, 20, 10, 1], [100, 20, 10, -1]]) {
    assert.equal(targetExitPrice('LONG', ...args), null)
  }
})

test('Draft uses scan stop and the one-dollar target for all three TPs', () => {
  const plan = buildPlanDraft(long, {marginUsdt: 20, leverage: 10, targetNet: 1})
  assert.equal(plan.draft.symbol, 'BTCUSDT')
  assert.equal(plan.draft.direction, 'LONG')
  assert.equal(plan.draft.stop, '98')
  assert.equal(plan.draft.margin, '20')
  assert.equal(plan.draft.leverage, '10')
  assert.equal(plan.draft.tp1, plan.draft.tp2)
  assert.equal(plan.draft.tp2, plan.draft.tp3)
  assert.ok(Number(plan.draft.tp1) > 100 && Number(plan.draft.tp1) < 103)
  assert.equal(plan.warning, '')
})

test('Without a target the scan TP levels are used unchanged', () => {
  const plan = buildPlanDraft(short, {marginUsdt: 20, leverage: 5, targetNet: null})
  assert.deepEqual([plan.draft.tp1, plan.draft.tp2, plan.draft.tp3], ['97', '95', '92'])
  assert.equal(plan.targetPrice, null)
})

test('Missing margin or leverage is flagged and margin/leverage are left empty, never guessed', () => {
  const plan = buildPlanDraft(long, {marginUsdt: null, leverage: null, targetNet: 1})
  assert.equal(plan.draft.margin, '')
  assert.equal(plan.draft.leverage, '')
  assert.match(plan.warning, /marjin ve kaldıraç/)
  assert.deepEqual([plan.draft.tp1, plan.draft.tp2, plan.draft.tp3], ['103', '105', '108'])
})

test('A very far target is warned about', () => {
  assert.match(buildPlanDraft(long, {marginUsdt: 5, leverage: 1, targetNet: 5}).warning, /çok uzak/)
})

test('Server rows are validated and broken ones dropped without repair', () => {
  assert.deepEqual(parsePlanPicks(null), [])
  const rows = [long, {...long, symbol: 5}, {...long, direction: 'NEUTRAL'}, {...long, entry: NaN}, {...long, tp3: 0}, null, short]
  assert.deepEqual(parsePlanPicks(rows).map(pick => pick.symbol), ['BTCUSDT', 'ETHUSDT'])
})

test('Strict targets (Live form) separate equal TPs by tiny steps away from entry, long and short', () => {
  const longPlan = buildPlanDraft(long, {marginUsdt: 20, leverage: 10, targetNet: 1, strictTargets: true})
  const [a, b, c] = [longPlan.draft.tp1, longPlan.draft.tp2, longPlan.draft.tp3].map(Number)
  assert.ok(a < b && b < c)
  assert.ok((c - a) / a < 0.001)  // hâlâ yaklaşık aynı fiyat
  assert.equal(a, Number(buildPlanDraft(long, {marginUsdt: 20, leverage: 10, targetNet: 1}).draft.tp1))  // TP1 hedef fiyat, değişmez
  const shortPlan = buildPlanDraft(short, {marginUsdt: 20, leverage: 10, targetNet: 1, strictTargets: true})
  const [x, y, z] = [shortPlan.draft.tp1, shortPlan.draft.tp2, shortPlan.draft.tp3].map(Number)
  assert.ok(x > y && y > z)
})

test('Strict targets leave already ordered scan TPs untouched', () => {
  const plan = buildPlanDraft(long, {marginUsdt: 20, leverage: 10, targetNet: null, strictTargets: true})
  assert.deepEqual([plan.draft.tp1, plan.draft.tp2, plan.draft.tp3], ['103', '105', '108'])
})

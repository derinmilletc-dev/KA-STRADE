import assert from 'node:assert/strict'
import test from 'node:test'
import {recommendSizes} from './coinbase-autosize.ts'

const base = {side: 'BUY' as const, entry: 0.705, stop: 0.648, target: 0.82, contractSize: 100, minMargin: 70.5, spendable: 97.79, maxLossUsd: 10, dailyLeftUsd: null, leverageMax: 20, feeRate: 0.001}

test('POP örneği: 1 sözleşme, kaldıraç 1x altına düşmez, borsa marjini bakiyeye sığar', () => {
  const {options, blocked} = recommendSizes(base)
  assert.equal(blocked, null)
  assert.equal(options.length, 1)
  const o = options[0]
  assert.equal(o.contracts, 1)
  assert.ok(o.leverage >= 1 && o.leverage <= 20)
  assert.ok(o.marginUsd <= 97.79 * 0.98)
  assert.ok(o.lossUsd <= 10)
})
test('büyük bakiyede üç profil farklı sözleşme sayısı verir ve kayıp limitini aşmaz', () => {
  const {options} = recommendSizes({...base, spendable: 2000, minMargin: 20, maxLossUsd: 100})
  assert.ok(options.length >= 2)
  assert.ok(options.every(o => o.lossUsd <= 100 && o.contracts >= 1))
  const counts = options.map(o => o.contracts)
  assert.deepEqual(counts, [...counts].sort((a, b) => a - b))
  assert.equal(new Set(counts).size, counts.length)
})
test('kayıp limiti 1 sözleşmeye bile yetmiyorsa açıklama', () => {
  const r = recommendSizes({...base, maxLossUsd: 1})
  assert.deepEqual(r.options, [])
  assert.match(r.blocked!, /kayıp limitin/)
})
test('bakiye 1 sözleşmeye yetmiyorsa açıklama', () => {
  const r = recommendSizes({...base, minMargin: 140})
  assert.match(r.blocked!, /yetmiyor/)
})
test('stop yanlış tarafta', () => {
  assert.match(recommendSizes({...base, stop: 0.8}).blocked!, /yanlış tarafta/)
})
test('günlük kalan zarar hakkı da tavandır', () => {
  const r = recommendSizes({...base, spendable: 2000, minMargin: 20, maxLossUsd: 100, dailyLeftUsd: 3})
  assert.equal(r.options.length, 0)
  assert.match(r.blocked!, /3,00 USD/)
})
test('oran bilinmiyorsa 5x varsayılır', () => {
  const r = recommendSizes({...base, minMargin: null})
  assert.equal(r.blocked, null)
  assert.ok(r.options[0].leverage >= 1)
})

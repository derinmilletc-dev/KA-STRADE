import assert from 'node:assert/strict'
import test from 'node:test'
import {readNetBalance, todayText} from './coinbase-net-balance.ts'

const ok = {available: true, balance: '100.01', gross: '5.70', fees: '1.07', net_today: '4.63', net_balance: '104.64', exact: true}

test('sunucu cevabı sayıya çevrilir', () => {
  assert.deepEqual(readNetBalance(ok), {balance: 100.01, gross: 5.7, fees: 1.07, netToday: 4.63, netBalance: 104.64, exact: true})
})
test('bozuk ya da eksik cevap null verir', () => {
  assert.equal(readNetBalance(null), null)
  assert.equal(readNetBalance({available: false}), null)
  assert.equal(readNetBalance({...ok, net_balance: ''}), null)
  assert.equal(readNetBalance({...ok, fees: 'x'}), null)
})
test('bugün metni komisyonu gösterir, kazanç yoksa boştur', () => {
  const net = readNetBalance(ok)!
  assert.match(todayText(net)!, /^Bugün net \+4,63 USD \(komisyon 1,07 düşüldü\)$/)
  assert.equal(todayText({...net, gross: 0, fees: 0, netToday: 0}), null)
  assert.match(todayText({...net, exact: false})!, /yaklaşık/)
})

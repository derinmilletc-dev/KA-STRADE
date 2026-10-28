import assert from 'node:assert/strict'
import {test} from 'node:test'
import {checkProtection} from '../frontend/src/order-protection.ts'

const positions = [{symbol: 'BTCUSDT', quantity: 0.01}]
const orders = [
  {symbol: 'BTCUSDT', side: 'SELL', type: 'STOP_MARKET'},
  {symbol: 'BTCUSDT', side: 'SELL', type: 'TAKE_PROFIT_MARKET'},
  {symbol: 'BTCUSDT', side: 'SELL', type: 'LIMIT', reduce_only: true},
  {symbol: 'BTCUSDT', side: 'BUY', type: 'LIMIT'},
]

test('long position: stop and reduce-only targets are found on the closing side', () => {
  assert.deepEqual(checkProtection('BTCUSDT', 'LONG', positions, orders), {position: true, stop: true, targets: 2})
})
test('short position does not count the long position protective orders', () => {
  assert.deepEqual(checkProtection('BTCUSDT', 'SHORT', positions, orders), {position: true, stop: false, targets: 0})
})
test('another coin or no position reports nothing', () => {
  assert.equal(checkProtection('ETHUSDT', 'LONG', positions, orders).position, false)
  assert.deepEqual(checkProtection('BTCUSDT', 'LONG', [], []), {position: false, stop: false, targets: 0})
})

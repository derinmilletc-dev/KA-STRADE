import assert from 'node:assert/strict'
import {test} from 'node:test'
import {accountNetPnl, positionNetPnl, NET_TAKER_FEE_RATE} from '../net-pnl.ts'

const position = {symbol: 'BTCUSDT', quantity: 2, entry_price: 100, mark_price: 110, unrealized_pnl: 20}

test('net = brüt − (giriş + çıkış) × miktar × oran', () => {
  assert.ok(Math.abs(positionNetPnl(position) - (20 - 2 * 210 * NET_TAKER_FEE_RATE)) < 1e-9)
})
test('zarardaki pozisyon daha da düşer', () => {
  const loss = {...position, mark_price: 95, unrealized_pnl: -10}
  assert.ok(positionNetPnl(loss) < -10)
})
test('veri eksikse null (uydurma yok)', () => {
  assert.equal(positionNetPnl({...position, entry_price: undefined}), null)
  assert.equal(positionNetPnl({...position, quantity: 0}), null)
  assert.equal(positionNetPnl(null), null)
})
test('metin sayılar da okunur; short miktar işareti önemsiz', () => {
  const text = {quantity: '-2', entry_price: '100', mark_price: '110', unrealized_pnl: '-20'}
  assert.ok(Math.abs(positionNetPnl(text) - (-20 - 2 * 210 * NET_TAKER_FEE_RATE)) < 1e-9)
})
test('hesap toplamı: hepsi hesaplanıyorsa net, değilse brüt ve net:false', () => {
  const a = accountNetPnl([position, position], 40)
  assert.equal(a.net, true)
  assert.ok(Math.abs(a.value - (40 - 2 * 2 * 210 * NET_TAKER_FEE_RATE)) < 1e-9)
  const b = accountNetPnl([position, {symbol: 'X'}], 33)
  assert.deepEqual(b, {value: 33, net: false})
  assert.deepEqual(accountNetPnl([], 0), {value: 0, net: true})
  assert.deepEqual(accountNetPnl(undefined, 5), {value: 5, net: false})
})

// Çalıştırma: node frontend/src/coinbase-position.test.mts
import assert from 'node:assert/strict'
import {closeParts, closePreview, positionView} from './coinbase-position.ts'

const close = (a: number | null, b: number, eps = 1e-6) => assert.ok(a !== null && Math.abs(a - b) < eps, `${a} ≈ ${b}`)

// LONG kârda: girişe çekilebilir, yarısı hesaplanır, şerit soldan sağa stop < giriş < güncel < hedef
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '5', avg_entry_price: '100', current_price: '103'}, {stop_price: '98', take_profit_price: '106'})
  assert.equal(v.dir, 'LONG')
  close(v.movePct, 3)
  close(v.breakeven, 100.2)
  assert.equal(v.canBreakeven, true)
  assert.equal(v.half, 2)
  assert.ok(v.bar!.stop! < v.bar!.entry! && v.bar!.entry! < v.bar!.current! && v.bar!.current! < v.bar!.target!)
}
// LONG zararda: girişe çekilemez
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '1', avg_entry_price: '100', current_price: '99'}, {stop_price: '98'})
  assert.equal(v.canBreakeven, false)
  assert.equal(v.half, null)
  close(v.movePct, -1)
}
// SHORT kârda: şerit ters çevrilir (hedef yine sağda)
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_SHORT', number_of_contracts: '4', avg_entry_price: '50', current_price: '48'}, {stop_price: '51', take_profit_price: '46'})
  close(v.movePct, 4)
  close(v.breakeven, 49.9)
  assert.equal(v.canBreakeven, true)
  assert.ok(v.bar!.stop! < v.bar!.entry! && v.bar!.current! < v.bar!.target!)
}
// Stop zaten girişin ötesinde: tekrar önerilmez
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '2', avg_entry_price: '100', current_price: '110'}, {stop_price: '101'})
  assert.equal(v.canBreakeven, false)
  assert.match(v.breakevenWhy, /zaten/)
}
// Vade: 30 gün içindeyse süre, perp (2050+) ya da uzaksa yok
{
  const now = Date.parse('2026-11-21T00:00:00Z')
  assert.ok(positionView({side: 'LONG', number_of_contracts: '1', avg_entry_price: '1', current_price: '1', expiration_time: '2026-11-27T16:00:00Z'}, null, now).expiryMs! > 0)
  assert.equal(positionView({side: 'LONG', number_of_contracts: '1', avg_entry_price: '1', current_price: '1', expiration_time: '2030-12-20T16:00:00Z'}, null, now).expiryMs, null)
}
console.log('coinbase-position testleri geçti')

// Kapatma payları
assert.deepEqual(closeParts(1), [1])
assert.deepEqual(closeParts(2), [1, 2])
assert.deepEqual(closeParts(3), [1, 3])
assert.deepEqual(closeParts(4), [1, 2, 4])
assert.deepEqual(closeParts(9), [2, 4, 9])
assert.deepEqual(closeParts(0), [])

// Kapatma önizlemesi: LONG, giriş 100, güncel 102 (+20 USD) -> stop 98 ise -20 USD; yarısı için yarıya iner
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '4', avg_entry_price: '100', current_price: '102'}, {stop_price: '98'})
  const full = closePreview(v, 20, 4)
  close(full.now, 20); close(full.atStop, -20)
  const half = closePreview(v, 20, 2)
  close(half.fraction, 0.5); close(half.now, 10); close(half.atStop, -10)
}
// SHORT zararda: giriş 100, güncel 101 (-10 USD), stop 103 -> -30 USD
{
  const v = positionView({side: 'FUTURES_POSITION_SIDE_SHORT', number_of_contracts: '2', avg_entry_price: '100', current_price: '101'}, {stop_price: '103'})
  close(closePreview(v, -10, 2).atStop, -30)
}
// Fiyat girişe çok yakın / stop yok / K/Z yok: stop tahmini uydurulmaz
{
  const near = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '2', avg_entry_price: '100', current_price: '100.01'}, {stop_price: '98'})
  assert.equal(closePreview(near, 0.1, 2).atStop, null)
  const nostop = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '2', avg_entry_price: '100', current_price: '102'}, null)
  assert.equal(closePreview(nostop, 20, 2).atStop, null)
  const nopnl = positionView({side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '2', avg_entry_price: '100', current_price: '102'}, {stop_price: '98'})
  assert.equal(closePreview(nopnl, null, 2).now, null)
}

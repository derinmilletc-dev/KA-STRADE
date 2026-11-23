// Çalıştırma: node frontend/src/coinbase-position.test.mts
import assert from 'node:assert/strict'
import {closeParts, closePreview, netOpenPnl, overlayLive, positionView} from './coinbase-position.ts'

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

// overlayLive: canlı orta fiyat ve K/Z (long/short), eksik veride pozisyon aynen
{
  const base = {side: 'FUTURES_POSITION_SIDE_LONG', number_of_contracts: '3', avg_entry_price: '6.76', current_price: '6.76', unrealized_pnl: '-0.3', product_id: 'AVP'}
  const up = overlayLive(base, {bid: '6.80', ask: '6.82', mid: null}, 10)
  assert.strictEqual(up.current_price, '6.81')
  assert.strictEqual(up.unrealized_pnl, String(Number(((6.81 - 6.76) * 3 * 10).toFixed(4))))
  // Coinbase'in bayat K/Z'si (-0.90) yok sayılır; Coinbase fiyatı 6.71 ise K/Z = (6.71-6.76)*30 = -1.5
  const fromPrice = overlayLive({...base, current_price: '6.71', unrealized_pnl: '-0.90'}, {mid: '6.71'}, 10)
  assert.strictEqual(fromPrice.unrealized_pnl, '-1.5')
  const short = overlayLive({...base, side: 'FUTURES_POSITION_SIDE_SHORT'}, {mid: '6.70'}, 10)
  assert.ok(Number(short.unrealized_pnl) > 0)
  assert.strictEqual(overlayLive(base, {mid: null, bid: null, ask: null}, 10), base)
  const noSize = overlayLive(base, {mid: '7'}, null)
  assert.strictEqual(noSize.current_price, '7')
  assert.strictEqual(noSize.unrealized_pnl, '-0.3')
}

// netOpenPnl: komisyon dahil toplam; komisyonu bilinmeyen satır brüt eklenir ve withFees=false
{
  assert.deepStrictEqual(netOpenPnl([{product_id: 'A', unrealized_pnl: '-0.90'}], {A: '0.53'}), {value: -1.43, withFees: true})
  assert.deepStrictEqual(netOpenPnl([{product_id: 'A', unrealized_pnl: '-0.90'}, {product_id: 'B', unrealized_pnl: '2'}], {A: '0.53', B: null}), {value: 0.57, withFees: false})
  assert.strictEqual(netOpenPnl([], {}), null)
  assert.strictEqual(netOpenPnl([{product_id: 'A', unrealized_pnl: null}], {}), null)
}

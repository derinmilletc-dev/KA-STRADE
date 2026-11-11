import test from 'node:test'
import assert from 'node:assert/strict'
import {watchDca} from './dcaWatch.ts'

const bot = (over: any = {}) => ({side: 'LONG', status: 'RUNNING', spec: {levels: [{entry: 100}, {entry: 97}, {entry: 94}]},
  stats: {stages_total: 3, stages_filled: 1, avg_entry: 100, tp_price: 101.2, stop_price: 90, cycle: 1}, ...over})

test('LONG: sıradaki ek alım düşüşte beklenir', () => {
  const v = watchDca(bot(), 99)
  assert.equal(v.headline, 'Düşüşü bekliyor')
  assert.equal(v.next, 97)
  assert.ok(v.nextDistPct! > 2 && v.nextDistPct! < 2.1)
  assert.ok(v.track && v.track.mark! > v.track.stop && v.track.mark! < v.track.tp!)
})
test('SHORT: çizgi zarar→kâr yönünde, hedef sağda', () => {
  const v = watchDca(bot({side: 'SHORT', spec: {levels: [{entry: 100}, {entry: 103}]}, stats: {stages_total: 2, stages_filled: 1, avg_entry: 100, tp_price: 98.8, stop_price: 110}}), 101)
  assert.equal(v.headline, 'Yükselişi bekliyor')
  assert.ok(v.track!.tp! > v.track!.mark! && v.track!.mark! > v.track!.stop)
})
test('kâr kovalama, durdu ve müdahale metinleri', () => {
  assert.equal(watchDca(bot({stats: {stages_total: 3, stages_filled: 1, avg_entry: 100, tp_price: 101, stop_price: 100.4, trail_active: true}}), 101.5).headline, 'Kârı kovalıyor')
  assert.equal(watchDca(bot({status: 'PAUSED'}), 99).headline, 'Bot durduruldu')
  assert.equal(watchDca(bot({status: 'BLOCKED', reason: 'Stop kurulamadı'}), 99).detail, 'Stop kurulamadı')
})
test('tüm ek alımlar dolduysa sıradaki alım yok; fiyat yoksa çökmez', () => {
  const v = watchDca(bot({stats: {stages_total: 3, stages_filled: 3, avg_entry: 97, tp_price: 98, stop_price: 90}}), null)
  assert.equal(v.next, null); assert.equal(v.headline, 'Tüm ek alımlar doldu'); assert.equal(v.mark, null)
  assert.doesNotThrow(() => watchDca(null, null))
})
test('seviye fiyatı yoksa bile bekleyen ek alım varken "doldu" demez', () => {
  const v = watchDca({side: 'LONG', status: 'RUNNING', stats: {stages_total: 4, stages_filled: 1, avg_entry: 2.5, tp_price: 2.53, stop_price: 2.35}}, 2.5)
  assert.equal(v.headline, 'Düşüşü bekliyor')
})

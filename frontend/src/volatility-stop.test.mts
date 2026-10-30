// Çalıştırma: node frontend/src/volatility-stop.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {averageTrueRange, suggestVolatilityStop} from './volatility-stop.ts'

// Sabit aralıklı mumlar: her mum 99..101, kapanış 100 → ATR = 2
const flat = Array.from({length: 30}, () => ({high: 101, low: 99, close: 100}))
assert.strictEqual(averageTrueRange(flat), 2)
assert.strictEqual(averageTrueRange(flat.slice(0, 10)), null, 'az mum → hesap yok')

// LONG: dip 99, çeyrek ATR tampon → 98.5; girişe 1.5 = 0.75 ATR < 1.5 ATR → en az 1.5 ATR = 3 → stop 97
let s = suggestVolatilityStop(flat, 100, 'LONG')!
assert.ok(Math.abs(s.stop - 97) < 1e-9, `stop ${s.stop}`)
assert.strictEqual(s.basis, 'atr_min')
// SHORT simetrik
s = suggestVolatilityStop(flat, 100, 'SHORT')!
assert.ok(Math.abs(s.stop - 103) < 1e-9)

// Derin dip: son mumlardan biri 95'e inmiş → tampon 94.5, mesafe 5.5 > 3 ATR(6)? ATR artar; sadece aralıkta kaldığını doğrula
const dip = flat.map((c, i) => i === 25 ? {high: 101, low: 95, close: 100} : c)
s = suggestVolatilityStop(dip, 100, 'LONG')!
const atr = averageTrueRange(dip.slice(0, -1))!
assert.ok(100 - s.stop >= 1.5 * atr - 1e-9 && 100 - s.stop <= 3 * atr + 1e-9, 'mesafe 1.5–3 ATR arasında')
assert.ok(s.stop < 100)

// Geçersiz girişler
assert.strictEqual(suggestVolatilityStop(flat, 0, 'LONG'), null)
assert.strictEqual(suggestVolatilityStop([], 100, 'LONG'), null)
// Son (oluşan) mum hesaba katılmaz
const spike = [...flat.slice(0, -1), {high: 150, low: 50, close: 100}]
assert.deepStrictEqual(suggestVolatilityStop(spike, 100, 'LONG'), suggestVolatilityStop(flat, 100, 'LONG'))
// Çok sakin coin: ATR çok küçük → en az %0,6
const calm = Array.from({length: 30}, () => ({high: 100.01, low: 99.99, close: 100}))
s = suggestVolatilityStop(calm, 100, 'LONG')!
assert.ok(Math.abs(s.distancePct - 0.6) < 1e-9 && s.basis === 'pct_min')
console.log('volatility-stop ok')

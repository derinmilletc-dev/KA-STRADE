// Çalıştırma: node frontend/src/coinbase-sizing.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {leverageBand, marginChips, priceString, riskPreview, sizeFromLeverage, stopFromPct, targetFromRR} from './coinbase-sizing.ts'

// Stop yönü: LONG'da altta, SHORT'ta üstte.
assert.strictEqual(stopFromPct('BUY', 100, 2), 98)
assert.strictEqual(stopFromPct('SELL', 100, 2), 102)
assert.strictEqual(stopFromPct('BUY', 100, 0), null)
assert.strictEqual(stopFromPct('BUY', 0, 2), null)
assert.strictEqual(stopFromPct('BUY', 100, 100), null)

// Hedef: stop mesafesinin katı. Yanlış taraftaki stop hedef üretmez.
assert.strictEqual(targetFromRR('BUY', 100, 98, 2), 104)
assert.strictEqual(targetFromRR('SELL', 100, 102, 3), 94)
assert.strictEqual(targetFromRR('BUY', 100, 101, 2), null)

// Boyutlandırma: 300 USD x 5 = 1500 USD, sözleşme başına 30 USD -> 50 sözleşme.
const sized = sizeFromLeverage({margin: 300, leverage: 5, entry: 3000, contractSize: 0.01})
assert.deepStrictEqual(sized && {contracts: sized.contracts, notional: sized.notional}, {contracts: 50, notional: 1500})
assert.strictEqual(sizeFromLeverage({margin: 10, leverage: 1, entry: 3000, contractSize: 0.01}), null, 'tek sözleşme sığmıyor')
assert.strictEqual(sizeFromLeverage({margin: NaN, leverage: 5, entry: 3000, contractSize: 0.01}), null)

// Risk önizleme: 50 sözleşme x 0,01 = 0,5 birim; stop 60 uzakta -> 30 USD + komisyon 3 (1500 x 0,1% x 2).
const risk = riskPreview({side: 'BUY', entry: 3000, stop: 2940, target: 3120, contracts: 50, contractSize: 0.01})
assert.ok(risk)
assert.strictEqual(risk.wrongSide, false)
assert.ok(Math.abs(risk.lossUsd - 33) < 1e-9, String(risk.lossUsd))
assert.ok(risk.gainUsd !== null && Math.abs(risk.gainUsd - 57) < 1e-9, String(risk.gainUsd))
assert.strictEqual(risk.rr, 2)
assert.ok(Math.abs(risk.stopPct - 2) < 1e-9)
// Yanlış taraf: zarar 0 değil "yanlış taraf" bayrağı.
const wrong = riskPreview({side: 'BUY', entry: 3000, stop: 3100, target: null, contracts: 10, contractSize: 0.01})
assert.strictEqual(wrong?.wrongSide, true)
assert.strictEqual(wrong?.rr, null)
assert.strictEqual(riskPreview({side: 'BUY', entry: 0, stop: 1, target: null, contracts: 1, contractSize: 1}), null)

// Kaldıraç bantları ve teminat kısayolları.
assert.strictEqual(leverageBand(2).tone, 'low')
assert.strictEqual(leverageBand(5).tone, 'mid')
assert.strictEqual(leverageBand(25).tone, 'high')
assert.deepStrictEqual(marginChips(200).map(item => item.value), ['50', '100', '150', '200'])
assert.deepStrictEqual(marginChips(null), [])
assert.strictEqual(marginChips(0.001).length, 0)

// Fiyat metni.
assert.strictEqual(priceString(3003.5), '3003.5')
assert.strictEqual(priceString(0.012345678), '0.012346')
assert.strictEqual(priceString(0), '')
console.log('coinbase-sizing testleri geçti')

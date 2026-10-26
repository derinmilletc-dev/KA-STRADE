// Çalıştırma: node --test frontend/src/coin-ranking.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {AI_DISCLAIMER, buildSignalMap, confidenceTone, signalLabel, sortByConfidence} from './coin-ranking.ts'

test('yön AL / SAT / BEKLE etiketine çevrilir, bilinmeyen değer BEKLE olur', () => {
  assert.equal(signalLabel('LONG'), 'AL')
  assert.equal(signalLabel('short'), 'SAT')
  assert.equal(signalLabel('BEKLE'), 'BEKLE')
  for (const value of [undefined, null, '', 'x', 5]) assert.equal(signalLabel(value), 'BEKLE')
})

test('güven rengi eşikleri', () => {
  assert.equal(confidenceTone(95), 'high')
  assert.equal(confidenceTone(80), 'high')
  assert.equal(confidenceTone(79), 'mid')
  assert.equal(confidenceTone(65), 'mid')
  assert.equal(confidenceTone(64), 'low')
})

test('sonuç haritası: results dizisi ve düz dizi, güven yuvarlanır ve 0-100 ile sınırlanır', () => {
  const rows = [{symbol: 'BTCUSDT', direction: 'LONG', confidence: 82.6, final_decision_score: 71}, {symbol: 'ETHUSDT', direction: 'SHORT', confidence: 140}, {symbol: 'XRPUSDT', direction: 'BEKLE', confidence: -5}]
  for (const payload of [{results: rows}, rows]) {
    const map = buildSignalMap(payload)
    assert.deepEqual(map.BTCUSDT, {label: 'AL', confidence: 83, score: 71})
    assert.deepEqual(map.ETHUSDT, {label: 'SAT', confidence: 100, score: 0})
    assert.deepEqual(map.XRPUSDT, {label: 'BEKLE', confidence: 0, score: 0})
  }
})

test('bozuk satırlar atlanır, bozuk cevap boş harita verir', () => {
  const map = buildSignalMap({results: [null, {}, {symbol: 'A'}, {symbol: 'B', confidence: 'abc'}, {symbol: 7, confidence: 90}, {symbol: 'OKUSDT', direction: 'LONG', confidence: 70}]})
  assert.deepEqual(Object.keys(map), ['OKUSDT'])
  for (const payload of [null, undefined, 'x', 3, {}, {results: 'x'}]) assert.deepEqual(buildSignalMap(payload), {})
})

test('sıralama güvene göre azalan, eşitlikte karar skoruna göre, analizsizler sona ve kendi sırasıyla', () => {
  const coins = [{symbol: 'AAA'}, {symbol: 'BBB'}, {symbol: 'CCC'}, {symbol: 'DDD'}, {symbol: 'EEE'}]
  const map = buildSignalMap([
    {symbol: 'BBB', direction: 'LONG', confidence: 90, final_decision_score: 50},
    {symbol: 'CCC', direction: 'SHORT', confidence: 90, final_decision_score: 60},
    {symbol: 'DDD', direction: 'BEKLE', confidence: 55},
  ])
  assert.deepEqual(sortByConfidence(coins, map).map(c => c.symbol), ['CCC', 'BBB', 'DDD', 'AAA', 'EEE'])
  assert.deepEqual(sortByConfidence(coins, null), coins)
  assert.deepEqual(coins.map(c => c.symbol), ['AAA', 'BBB', 'CCC', 'DDD', 'EEE'])   // girdi değişmez
})

test('uyarı metni sabit ve yatırım tavsiyesi olmadığını söyler', () => {
  assert.equal(AI_DISCLAIMER, 'Yatırım tavsiyesi değildir. Kais AI analiz raporudur.')
})

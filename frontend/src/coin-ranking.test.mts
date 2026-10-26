// Çalıştırma: node --test frontend/src/coin-ranking.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {AI_DISCLAIMER, alignment, strengthLabel, buildSignalMap, confidenceTone, filterBySignal, leanOf, leanText, sortByConfidence, sortCoins} from './coin-ranking.ts'

test('yön LONG / SHORT / NEUTRAL olur, bilinmeyen değer NEUTRAL', () => {
  assert.equal(leanOf('LONG'), 'LONG')
  assert.equal(leanOf('short'), 'SHORT')
  assert.equal(leanOf('BEKLE'), 'NEUTRAL')
  for (const value of [undefined, null, '', 'x', 5]) assert.equal(leanOf(value), 'NEUTRAL')
})

test('ekranda AL / SAT kelimesi çıkaran bir etiket yok', () => {
  const map = buildSignalMap([{symbol: 'A', direction: 'LONG', confidence: 90, long_score: 90, short_score: 25}])
  assert.ok(!JSON.stringify(map).match(/"(AL|SAT)"/))
  assert.equal(leanText(map.A), 'LONG ağır basıyor')
})

test('güven rengi eşikleri', () => {
  assert.equal(confidenceTone(95), 'high')
  assert.equal(confidenceTone(80), 'high')
  assert.equal(confidenceTone(79), 'mid')
  assert.equal(confidenceTone(65), 'mid')
  assert.equal(confidenceTone(64), 'low')
})

test('iki taraf oranı geldiğinde ikisi de aynen gösterilir', () => {
  const map = buildSignalMap({results: [
    {symbol: 'BTCUSDT', direction: 'LONG', confidence: 95, final_decision_score: 71, long_score: 95, short_score: 50},
    {symbol: 'ETHUSDT', direction: 'SHORT', confidence: 76.4, long_score: 25, short_score: 76},
    {symbol: 'XRPUSDT', direction: 'BEKLE', confidence: 55, long_score: 45, short_score: 41},
  ]})
  assert.deepEqual(map.BTCUSDT, {lean: 'LONG', long: 95, short: 50, confidence: 95, score: 71})
  assert.deepEqual(map.ETHUSDT, {lean: 'SHORT', long: 25, short: 76, confidence: 76, score: 0})
  assert.deepEqual(map.XRPUSDT, {lean: 'NEUTRAL', long: 45, short: 41, confidence: 55, score: 0})
})

test('eski sunucu (oran alanı yok): yalnız ana yön gösterilir, diğer taraf uydurulmaz', () => {
  const map = buildSignalMap([{symbol: 'A', direction: 'LONG', confidence: 82.6}, {symbol: 'B', direction: 'SHORT', confidence: 140}, {symbol: 'C', direction: 'BEKLE', confidence: -5}])
  assert.deepEqual(map.A, {lean: 'LONG', long: 83, short: null, confidence: 83, score: 0})
  assert.deepEqual(map.B, {lean: 'SHORT', long: null, short: 100, confidence: 100, score: 0})
  assert.deepEqual(map.C, {lean: 'NEUTRAL', long: null, short: null, confidence: 0, score: 0})
})

test('oranlardan biri bozuksa ikisi birden güvenilmez sayılır', () => {
  const map = buildSignalMap([{symbol: 'A', direction: 'LONG', confidence: 70, long_score: 70, short_score: 'abc'}])
  assert.equal(map.A.short, null)
  assert.equal(map.A.long, 70)
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

test('filtreler: LONG, SHORT ve güçlü sinyal; analizsiz coin filtrede görünmez, "tümü"nde görünür', () => {
  const coins = [{symbol: 'L1'}, {symbol: 'L2'}, {symbol: 'S1'}, {symbol: 'W1'}, {symbol: 'N1'}]
  const map = buildSignalMap([
    {symbol: 'L1', direction: 'LONG', confidence: 90}, {symbol: 'L2', direction: 'LONG', confidence: 70},
    {symbol: 'S1', direction: 'SHORT', confidence: 85}, {symbol: 'W1', direction: 'BEKLE', confidence: 69},
  ])
  const names = (filter: 'all' | 'long' | 'short' | 'strong') => filterBySignal(coins, map, filter).map(c => c.symbol)
  assert.deepEqual(names('all'), ['L1', 'L2', 'S1', 'W1', 'N1'])
  assert.deepEqual(names('long'), ['L1', 'L2'])
  assert.deepEqual(names('short'), ['S1'])
  assert.deepEqual(names('strong'), ['L1', 'S1'])
  assert.deepEqual(filterBySignal(coins, null, 'long'), coins)   // analiz yokken liste boşalmaz
})

test('sıralama kipleri: güven, hacim (mevcut sıra), değişim büyüklüğü; girdi değişmez', () => {
  const coins = [{symbol: 'A', change: 1}, {symbol: 'B', change: -9}, {symbol: 'C', change: 4}]
  const map = buildSignalMap([{symbol: 'C', direction: 'LONG', confidence: 90}, {symbol: 'A', direction: 'LONG', confidence: 60}])
  assert.deepEqual(sortCoins(coins, map, 'confidence').map(c => c.symbol), ['C', 'A', 'B'])
  assert.deepEqual(sortCoins(coins, map, 'volume').map(c => c.symbol), ['A', 'B', 'C'])
  assert.deepEqual(sortCoins(coins, map, 'change').map(c => c.symbol), ['B', 'C', 'A'])
  assert.deepEqual(coins.map(c => c.symbol), ['A', 'B', 'C'])
})

test('uyarı metni sabit ve yatırım tavsiyesi olmadığını söyler', () => {
  assert.equal(AI_DISCLAIMER, 'Yatırım tavsiyesi değildir. Kais AI analiz raporudur.')
})

test('yön uyumu: seçilen yön analizle aynıysa uyumlu, tersiyse ters, net yön yoksa yok', () => {
  const map = buildSignalMap([{symbol: 'A', direction: 'LONG', confidence: 90}, {symbol: 'B', direction: 'SHORT', confidence: 70}, {symbol: 'C', direction: 'BEKLE', confidence: 50}])
  assert.equal(alignment(map.A, 'LONG'), 'aligned')
  assert.equal(alignment(map.A, 'SHORT'), 'opposed')
  assert.equal(alignment(map.B, 'SHORT'), 'aligned')
  assert.equal(alignment(map.C, 'LONG'), 'none')
  assert.equal(alignment(undefined, 'LONG'), 'none')
})

test('sinyal gücü etiketi', () => {
  assert.equal(strengthLabel(95), 'Güçlü')
  assert.equal(strengthLabel(80), 'Güçlü')
  assert.equal(strengthLabel(70), 'Orta')
  assert.equal(strengthLabel(64), 'Zayıf')
})

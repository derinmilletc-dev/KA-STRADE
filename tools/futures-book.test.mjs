import assert from 'node:assert/strict'
import {test} from 'node:test'
import {bookState, closePrice, freshClosePrice, parseBookTicker} from '../futures-book.ts'

const NOW = 1_700_000_000_000

test('bookTicker mesajı (tekli ve birleşik yayın) ve REST cevabı okunur', () => {
  assert.deepEqual(parseBookTicker({s: 'BTCUSDT', b: '80450.1', a: '80450.2'}, 'btcusdt', NOW), {symbol: 'BTCUSDT', bid: 80450.1, ask: 80450.2, at: NOW})
  assert.equal(parseBookTicker({stream: 'btcusdt@bookTicker', data: {s: 'BTCUSDT', b: '1', a: '2'}}, 'BTCUSDT', NOW)?.ask, 2)
  assert.equal(parseBookTicker({symbol: 'ETHUSDT', bidPrice: '3000', askPrice: '3000.1'}, 'ETHUSDT', NOW)?.bid, 3000)
})

test('başka coinin, bozuk veya ters fiyatın mesajı reddedilir', () => {
  assert.equal(parseBookTicker({s: 'ETHUSDT', b: '1', a: '2'}, 'BTCUSDT', NOW), null)
  assert.equal(parseBookTicker({s: 'BTCUSDT', b: '0', a: '2'}, 'BTCUSDT', NOW), null)
  assert.equal(parseBookTicker({s: 'BTCUSDT', b: '3', a: '2'}, 'BTCUSDT', NOW), null)
  assert.equal(parseBookTicker(null, 'BTCUSDT', NOW), null)
})

test('kapanış fiyatı: LONG en iyi alıştan, SHORT en iyi satıştan', () => {
  const book = {symbol: 'BTCUSDT', bid: 100, ask: 101, at: NOW}
  assert.equal(closePrice('LONG', book), 100)
  assert.equal(closePrice('SHORT', book), 101)
})

test('tazelik: 1,5 sn canlı, 4 sn gecikme, sonrası eski; eski fiyat kullanılmaz', () => {
  const book = {symbol: 'BTCUSDT', bid: 100, ask: 101, at: NOW}
  assert.equal(bookState(book, NOW + 1000), 'live')
  assert.equal(bookState(book, NOW + 3000), 'late')
  assert.equal(bookState(book, NOW + 5000), 'stale')
  assert.equal(bookState(null, NOW), 'stale')
  assert.equal(freshClosePrice('LONG', book, 'BTCUSDT', NOW + 3000), 100)
  assert.equal(freshClosePrice('LONG', book, 'BTCUSDT', NOW + 5000), null)
  assert.equal(freshClosePrice('LONG', book, 'ETHUSDT', NOW), null)
})

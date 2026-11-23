// Çalıştırma: node --test frontend/src/coinbase-ticker.test.mts
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {applyL2, parseTicker, subscribeMessage, topOfBook, type Books} from './coinbase-ticker.ts'

test('parseTicker: snapshot ve update fiyatlarını okur, bozuk satırı atlar', () => {
  const message = {channel: 'ticker', events: [{type: 'snapshot', tickers: [
    {product_id: 'AVP-20DEC30-CDE', price: '6.81', best_bid: '6.80', best_ask: '6.82'},
    {product_id: 'BAD', price: ''}, {price: '1'}, null, {product_id: 'ZERO', price: '0'},
  ]}, {type: 'update', tickers: [{product_id: 'HYP-20DEC30-CDE', price: '64.5'}]}]}
  const out = parseTicker(message, 1000)
  assert.deepStrictEqual(Object.keys(out).sort(), ['AVP-20DEC30-CDE', 'HYP-20DEC30-CDE'])
  assert.deepStrictEqual(out['AVP-20DEC30-CDE'], {price: 6.81, bid: 6.8, ask: 6.82, at: 1000})
  assert.strictEqual(out['HYP-20DEC30-CDE'].bid, null)
})

test('parseTicker: başka kanal, boş ve bozuk girdi boş döner', () => {
  assert.deepStrictEqual(parseTicker({channel: 'heartbeats', events: []}), {})
  assert.deepStrictEqual(parseTicker(null), {})
  assert.deepStrictEqual(parseTicker('x'), {})
  assert.deepStrictEqual(parseTicker({channel: 'ticker', events: 'x'}), {})
})

test('subscribeMessage: ticker + heartbeats aboneliği', () => {
  const [ticker, book, beat] = subscribeMessage(['A-1']).map(text => JSON.parse(text))
  assert.deepStrictEqual(ticker, {type: 'subscribe', product_ids: ['A-1'], channel: 'ticker'})
  assert.deepStrictEqual(book, {type: 'subscribe', product_ids: ['A-1'], channel: 'level2'})
  assert.deepStrictEqual(beat, {type: 'subscribe', channel: 'heartbeats'})
})

test('applyL2 + topOfBook: snapshot, güncelleme, silme ve ters defter', () => {
  const books: Books = {}
  const snap = {channel: 'l2_data', events: [{type: 'snapshot', product_id: 'AVP', updates: [
    {side: 'bid', price_level: '6.70', new_quantity: '5'}, {side: 'bid', price_level: '6.69', new_quantity: '2'},
    {side: 'offer', price_level: '6.73', new_quantity: '4'}, {side: 'offer', price_level: '6.75', new_quantity: '1'}, {side: 'x', price_level: '1', new_quantity: '1'}]}]}
  assert.deepStrictEqual(applyL2(books, snap), ['AVP'])
  assert.deepStrictEqual(topOfBook(books.AVP, 5), {price: 6.715, bid: 6.7, ask: 6.73, at: 5})
  applyL2(books, {channel: 'l2_data', events: [{type: 'update', product_id: 'AVP', updates: [{side: 'bid', price_level: '6.70', new_quantity: '0'}, {side: 'offer', price_level: '6.72', new_quantity: '3'}]}]})
  assert.deepStrictEqual(topOfBook(books.AVP, 6), {price: 6.705, bid: 6.69, ask: 6.72, at: 6})
  assert.deepStrictEqual(applyL2(books, {channel: 'ticker', events: []}), [])
  assert.strictEqual(topOfBook(undefined), null)
  assert.strictEqual(topOfBook({bids: new Map([[7, 1]]), asks: new Map([[6, 1]])}), null)
  assert.strictEqual(topOfBook({bids: new Map(), asks: new Map([[6, 1]])}), null)
})

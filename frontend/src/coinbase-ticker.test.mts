// Çalıştırma: node --test frontend/src/coinbase-ticker.test.mts
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {parseTicker, subscribeMessage} from './coinbase-ticker.ts'

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
  const [ticker, beat] = subscribeMessage(['A-1']).map(text => JSON.parse(text))
  assert.deepStrictEqual(ticker, {type: 'subscribe', product_ids: ['A-1'], channel: 'ticker'})
  assert.deepStrictEqual(beat, {type: 'subscribe', channel: 'heartbeats'})
})

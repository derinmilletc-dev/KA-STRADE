// Çalıştırma: node frontend/src/live-quote.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {bookTickerUrl, closeEstimate, parseBookTicker, streamSymbols, QUOTE_FRESH_MS} from './live-quote.ts'

// bookTicker mesajı: hem birleşik akış ({stream, data}) hem tekil biçim okunur.
const parsed = parseBookTicker({stream: 'btcusdt@bookTicker', data: {e: 'bookTicker', s: 'BTCUSDT', b: '100.5', a: '100.6'}}, 1000)
assert.deepStrictEqual(parsed, {symbol: 'BTCUSDT', quote: {bid: 100.5, ask: 100.6, at: 1000}})
assert.strictEqual(parseBookTicker({s: 'ETHUSDT', b: '0', a: '1'}, 1), null, 'sıfır fiyat reddedilir')
assert.strictEqual(parseBookTicker({s: 'ETHUSDT', b: '2', a: '1'}, 1), null, 'ask < bid reddedilir')
assert.strictEqual(parseBookTicker({result: null, id: 1}, 1), null, 'abonelik yanıtı fiyat değildir')

const quote = {bid: 0.49, ask: 0.51, at: 10_000}
// LONG alış (bid) fiyatından kapanır: giriş 0,50 → 0,49 = zarar (mark 0,505 gösterse bile).
const long = closeEstimate({long: true, entry: 0.5, quantity: 1000, quote, nowMs: 10_500})!
assert.ok(Math.abs(long.pnl - (-10)) < 1e-9)
assert.strictEqual(long.exitPrice, 0.49)
assert.ok(Math.abs(long.pnlAfterFee - (-10 - 0.49 * 1000 * 0.0005)) < 1e-9)
// SHORT satış (ask) fiyatından kapanır.
const short = closeEstimate({long: false, entry: 0.5, quantity: 1000, quote, nowMs: 10_500})!
assert.ok(Math.abs(short.pnl - (-10)) < 1e-9)
assert.strictEqual(short.exitPrice, 0.51)
// Eski fiyat: tahmin yok (ekran uyarı gösterir).
assert.strictEqual(closeEstimate({long: true, entry: 0.5, quantity: 1000, quote, nowMs: 10_000 + QUOTE_FRESH_MS + 1}), null)
assert.strictEqual(closeEstimate({long: true, entry: 0.5, quantity: 1000, quote: undefined, nowMs: 0}), null)
assert.strictEqual(closeEstimate({long: true, entry: null, quantity: 1000, quote, nowMs: 10_000}), null)

assert.deepStrictEqual(streamSymbols(['ethusdt', 'BTCUSDT', 'ETHUSDT', '', null, 'bad symbol']), ['BTCUSDT', 'ETHUSDT'])
assert.strictEqual(bookTickerUrl([]), null)
assert.strictEqual(bookTickerUrl(['BTCUSDT', 'ETHUSDT']), 'wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker')
console.log('live-quote: tamam')

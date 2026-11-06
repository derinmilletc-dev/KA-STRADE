// Çalıştırma: node frontend/src/live-quote.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {bookTickerUrl, closeEstimate, liveChangePercent, midPrice, parseBookTicker, streamSymbols, QUOTE_FRESH_MS} from './live-quote.ts'

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

// Canlı fiyat (manuel işlem ekranı): bid/ask ortası; eski veya bozuk fiyat "canlı" sayılmaz.
assert.strictEqual(midPrice({bid: 0.1688, ask: 0.1690, at: 10_000}, 10_500), 0.1689)
assert.strictEqual(midPrice({bid: 1, ask: 2, at: 10_000}, 10_000 + QUOTE_FRESH_MS + 1), null, 'eski fiyat canlı değildir')
assert.strictEqual(midPrice(undefined, 0), null)
assert.strictEqual(midPrice({bid: 0, ask: 0, at: 1}, 1), null, 'sıfır fiyat reddedilir')

// Canlı değişim: listedeki %3,23 değişimden açılış türetilir, canlı fiyat yeniden yüzdeye çevrilir.
const open = 0.16883 / 1.0323
assert.ok(Math.abs(liveChangePercent(0.16883, 3.23, 0.16883)! - 3.23) < 1e-9, 'fiyat değişmediyse yüzde aynı kalır')
assert.ok(Math.abs(liveChangePercent(0.16883, 3.23, open * 1.05)! - 5) < 1e-9, 'fiyat yükselince yüzde canlı güncellenir')
assert.strictEqual(liveChangePercent(0.16883, 3.23, null), null, 'canlı fiyat yoksa hesaplanmaz')
assert.strictEqual(liveChangePercent(0, 3.23, 1), null)
assert.strictEqual(liveChangePercent(1, -100, 1), null, 'açılış fiyatı türetilemez')
assert.strictEqual(liveChangePercent(1, Number.NaN, 1), null)
console.log('live-quote testleri geçti')

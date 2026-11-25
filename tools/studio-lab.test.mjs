import assert from 'node:assert/strict'
import test from 'node:test'
import './register-ts-resolve.mjs'

const {PRESETS, allSignals, ruleSignals, sanitizeRule, sanitizeRules} = await import('../studio-rules.ts')
const {runBacktest, cleanStrategy} = await import('../studio-backtest.ts')
const {volumeProfile} = await import('../studio-volume-profile.ts')

const bar = (i, o, h, l, c, v = 10) => ({time: i, open: o, high: h, low: l, close: c, volume: v})
const flat = (n, price = 100) => Array.from({length: n}, (_, i) => bar(i, price, price + 1, price - 1, price))
const num = value => ({kind: 'num', value}), close = {kind: 'price', field: 'close'}

test('kural temizleme geçersizi eler', () => {
  assert.equal(sanitizeRule({left: close, op: 'x', right: num(1)}), null)
  assert.equal(sanitizeRule({left: {kind: 'ema', period: 1}, op: '>', right: num(1)}), null)
  assert.equal(sanitizeRule({left: {kind: 'ema', period: 20}, op: '>', right: {kind: 'num', value: 'a'}}), null)
  assert.equal(sanitizeRules([...PRESETS[0].entry, null, 5]).length, 1)
})
test('kesişim yalnız kesişme mumunda true', () => {
  const candles = [90, 95, 99, 101, 103].map((c, i) => bar(i, c, c, c, c))
  const out = ruleSignals(candles, {left: close, op: 'crossUp', right: num(100)})
  assert.deepEqual(out, [false, false, false, true, false])
  assert.deepEqual(ruleSignals(candles, {left: close, op: '>', right: num(100)}), [false, false, false, true, true])
})
test('boş kural listesi sinyal vermez', () => assert.deepEqual(allSignals(flat(3), []), [false, false, false]))
test('backtest: giriş sonraki mum açılışından, hedef vurulunca çıkar', () => {
  const candles = flat(60)
  candles[50] = bar(50, 100, 101, 99, 99.5)           // sinyal mumu (kapanış < 100)
  candles[51] = bar(51, 100, 107, 99.5, 106)          // giriş 100, hedef %6 = 106 → vurulur
  const result = runBacktest(candles, {side: 'long', entry: [{left: close, op: 'crossDown', right: num(100)}], exit: [], stopPct: 3, takePct: 6, feePct: 0})
  assert.equal(result.trades.length, 1)
  const trade = result.trades[0]
  assert.equal(trade.entryIndex, 51); assert.equal(trade.entryPrice, 100); assert.equal(trade.reason, 'take')
  assert.ok(Math.abs(trade.returnPct - 6) < 1e-9)
})
test('backtest: aynı mumda stop ve hedef → stop sayılır; komisyon düşer', () => {
  const candles = flat(60)
  candles[50] = bar(50, 100, 101, 99, 99.5)
  candles[51] = bar(51, 100, 110, 90, 100)
  const result = runBacktest(candles, {entry: [{left: close, op: 'crossDown', right: num(100)}], stopPct: 3, takePct: 6, feePct: 0.1})
  assert.equal(result.trades[0].reason, 'stop')
  assert.ok(Math.abs(result.trades[0].returnPct - (-3 - 0.2)) < 1e-9)
  assert.ok(result.netPct < 0 && result.maxDrawdownPct > 0)
})
test('backtest: short yönü, az veri ve kuralsız durum uyarı verir', () => {
  assert.match(runBacktest(flat(10), {}).warning, /mum gerekir/)
  assert.match(runBacktest(flat(60), {}).warning, /Giriş kuralı/)
  const candles = flat(60); candles[50] = bar(50, 100, 101, 99, 99.5); candles[51] = bar(51, 100, 100.5, 93, 94)
  const result = runBacktest(candles, {side: 'short', entry: [{left: close, op: 'crossDown', right: num(100)}], stopPct: 3, takePct: 6, feePct: 0})
  assert.equal(result.trades[0].reason, 'take'); assert.ok(result.trades[0].returnPct > 5.9)
  assert.equal(cleanStrategy({stopPct: -1, feePct: 99}).stopPct, 3)
})
test('backtest: açık işlem son mumda kapanır', () => {
  const candles = flat(60); candles[50] = bar(50, 100, 101, 99, 99.5)
  const result = runBacktest(candles, {entry: [{left: close, op: 'crossDown', right: num(100)}], stopPct: 0, takePct: 0, feePct: 0})
  assert.equal(result.trades[0].reason, 'son'); assert.equal(result.trades[0].exitIndex, 59)
})
test('hacim profili: toplam korunur, POC yoğun kademede', () => {
  const candles = [{high: 10, low: 0, volume: 100}, {high: 6, low: 4, volume: 300}]
  const p = volumeProfile(candles, 10)
  assert.ok(Math.abs(p.bins.reduce((s, b) => s + b.volume, 0) - 400) < 1e-6)
  assert.ok(p.poc > 4 && p.poc < 6)
  assert.ok(p.valueLow <= p.poc && p.valueHigh >= p.poc)
  assert.equal(volumeProfile([], 10), null); assert.equal(volumeProfile([{high: 1, low: 1, volume: 5}], 10), null)
})

const {scanCandles, scanMarkets} = await import('../studio-screener.ts')
test('tarayıcı: sinyal son kapanmış mumda aranır', () => {
  const candles = Array.from({length: 40}, (_, i) => bar(i, 100, 101, 99, i === 38 ? 105 : 98))
  const rule = {left: close, op: 'crossUp', right: num(100)}
  assert.equal(scanCandles('A', candles, [rule]).match, true)
  assert.equal(scanCandles('A', candles.slice(0, 20), [rule]).error, 'Yeterli veri yok')
  const late = Array.from({length: 40}, (_, i) => bar(i, 100, 101, 99, i === 39 ? 105 : 98))
  assert.equal(scanCandles('A', late, [rule]).match, false)
})
test('tarayıcı: hatalı sembol tüm taramayı bozmaz, tekrarlar elenir, iptal durdurur', async () => {
  const candles = Array.from({length: 40}, (_, i) => bar(i, 100, 101, 99, i === 38 ? 105 : 98))
  const rule = {left: close, op: 'crossUp', right: num(100)}
  const fetcher = async symbol => { if (symbol === 'BAD') throw new Error('x'); return candles }
  const rows = await scanMarkets(['A', 'BAD', 'A', 'B'], [rule], fetcher, {signal: new AbortController().signal})
  assert.equal(rows.length, 3); assert.equal(rows.filter(r => r.match).length, 2); assert.equal(rows.find(r => r.symbol === 'BAD').error, 'Veri alınamadı')
  const controller = new AbortController(); controller.abort()
  assert.equal((await scanMarkets(['A'], [rule], fetcher, {signal: controller.signal})).length, 0)
})

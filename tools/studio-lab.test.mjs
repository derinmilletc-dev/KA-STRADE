import assert from 'node:assert/strict'
import test from 'node:test'
import './register-ts-resolve.mjs'

const {PRESETS, allSignals, ruleSignals, sanitizeRule, sanitizeRules, ruleText, ruleTextEn} = await import('../studio-rules.ts')
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

// ---- Strategy Lab v2: onay, ATR stop, iz süren stop, pozisyon boyutu, metrikler, kayıt ----
const {splitCheck, barsPerYear} = await import('../studio-backtest.ts')
const {sanitizeSaved, upsertSaved, removeSaved, sanitizeStrategy} = await import('../studio-strategies.ts')
const {atr} = await import('../studio-indicators.ts')
const crossDown100 = {left: close, op: 'crossDown', right: num(100)}

test('onay kuralı: aynı mumda yoksa, süre içinde sağlanırsa girer; süre dolarsa girmez', () => {
  const candles = flat(70)
  candles[50] = bar(50, 100, 101, 99, 99.5)         // giriş sinyali (kapanış 100'ün altına keser)
  candles[53] = bar(53, 100, 101, 99, 100.5, 500)   // onay: hacim yüksek
  const confirm = [{left: {kind: 'price', field: 'volume'}, op: '>', right: num(100)}]
  const within = runBacktest(candles, {entry: [crossDown100], confirm, confirmBars: 5, stopPct: 0, takePct: 0, feePct: 0})
  assert.equal(within.trades.length, 1); assert.equal(within.trades[0].entryIndex, 54)
  const tooLate = runBacktest(candles, {entry: [crossDown100], confirm, confirmBars: 2, stopPct: 0, takePct: 0, feePct: 0})
  assert.equal(tooLate.trades.length, 0)
  const sameBar = runBacktest(candles, {entry: [crossDown100], confirm, confirmBars: 0, stopPct: 0, takePct: 0, feePct: 0})
  assert.equal(sameBar.trades.length, 0)
})
test('ATR tabanlı stop/hedef girişte sabitlenir ve R hesaplanır', () => {
  const candles = flat(70)           // ATR = 2 (high-low)
  candles[50] = bar(50, 100, 101, 99, 99.5)
  candles[51] = bar(51, 100, 100.5, 95, 96)
  const result = runBacktest(candles, {entry: [crossDown100], stopMode: 'atr', stopAtr: 2, takeMode: 'atr', takeAtr: 4, feePct: 0})
  const trade = result.trades[0]
  assert.equal(trade.reason, 'stop'); assert.ok(Math.abs(trade.exitPrice - 96) < 1e-6)   // 100 - 2*2
  assert.ok(Math.abs(trade.r - -1) < 1e-6)
  assert.ok(atr(candles, 14)[20] !== null)
})
test('iz süren stop kârı korur', () => {
  const candles = flat(70)
  candles[50] = bar(50, 100, 101, 99, 99.5)
  candles[51] = bar(51, 100, 110, 100, 109)     // yeni tepe 110
  candles[52] = bar(52, 109, 109, 104, 105)     // 110'dan %5 geri = 104.5 → iz stop vurur
  const result = runBacktest(candles, {entry: [crossDown100], stopPct: 20, takePct: 0, trailPct: 5, feePct: 0})
  const trade = result.trades[0]
  assert.equal(trade.reason, 'trail'); assert.ok(Math.abs(trade.exitPrice - 104.5) < 1e-6); assert.ok(trade.returnPct > 4)
})
test('en fazla mum süresi dolunca kapanır', () => {
  const candles = flat(70); candles[50] = bar(50, 100, 101, 99, 99.5)
  const result = runBacktest(candles, {entry: [crossDown100], stopPct: 0, takePct: 0, maxBars: 3, feePct: 0})
  assert.equal(result.trades[0].reason, 'süre'); assert.equal(result.trades[0].bars, 3)
})
test('pozisyon boyutu: sabit % ve risk bazlı; hesap etkisi boyuta göre ölçeklenir', () => {
  const candles = flat(70); candles[50] = bar(50, 100, 101, 99, 99.5); candles[51] = bar(51, 100, 100.5, 96, 97)
  const base = {entry: [crossDown100], stopPct: 3, takePct: 0, feePct: 0}
  const all = runBacktest(candles, base).trades[0]
  const half = runBacktest(candles, {...base, sizing: 'fixed', sizingValue: 50}).trades[0]
  assert.ok(Math.abs(all.equityPct - -3) < 1e-9); assert.ok(Math.abs(half.equityPct - -1.5) < 1e-9)
  const risk = runBacktest(candles, {...base, sizing: 'risk', sizingValue: 1, maxLeverage: 1}).trades[0]
  assert.ok(Math.abs(risk.equityPct - -1) < 1e-9); assert.ok(Math.abs(risk.sizePct - 100 / 3) < 1e-9)   // %1 risk / %3 stop
  const capped = runBacktest(candles, {...base, stopPct: 0.5, sizing: 'risk', sizingValue: 1, maxLeverage: 1}).trades[0]
  assert.equal(capped.sizePct, 100)                                                                      // kaldıraç sınırı
  const noStop = runBacktest(candles, {...base, stopPct: 0, sizing: 'risk', sizingValue: 1})
  assert.match(noStop.warning, /stop gerekir/)
})
test('metrikler: beklenti, getiri/risk oranı, üst üste kayıp, maruziyet, sermaye', () => {
  const candles = flat(120)
  for (const start of [20, 50, 80]) { candles[start] = bar(start, 100, 101, 99, 99.5); candles[start + 1] = bar(start + 1, 100, 107, 99.5, 106) }   // 3 kazanç (+6%)
  candles[100] = bar(100, 100, 101, 99, 99.5); candles[101] = bar(101, 100, 100.5, 96, 97)                                                         // 1 kayıp (-3%)
  const result = runBacktest(candles, {entry: [crossDown100], stopPct: 3, takePct: 6, feePct: 0, capital: 1000}, {barsPerYear: 35040})
  assert.equal(result.trades.length, 4)
  assert.equal(result.maxConsecLosses, 1)
  assert.ok(Math.abs(result.payoff - 2) < 1e-6)
  assert.ok(Math.abs(result.expectancyR - (2 + 2 + 2 - 1) / 4) < 1e-6)
  assert.ok(result.exposurePct > 0 && result.exposurePct < 10)
  assert.ok(Math.abs(result.finalCapital - 1000 * 1.06 ** 3 * 0.97) < 1e-6)
  assert.ok(result.sharpe === null || Number.isFinite(result.sharpe))
  assert.ok(result.bestPct > 5.9 && result.worstPct < -2.9)
})
test('eğitim/test ayrımı yeterli veride iki sonuç döner, azında null', () => {
  const candles = flat(400); for (const start of [30, 130, 330]) { candles[start] = bar(start, 100, 101, 99, 99.5); candles[start + 1] = bar(start + 1, 100, 107, 99.5, 106) }
  const check = splitCheck(candles, {entry: [crossDown100], stopPct: 3, takePct: 6, feePct: 0})
  assert.ok(check && check.early.trades.length >= 1 && check.late.trades.length >= 1)
  assert.equal(splitCheck(flat(100), {entry: [crossDown100]}), null)
  assert.equal(barsPerYear('1d'), 365); assert.equal(barsPerYear('x'), undefined)
})
test('yeni kural operandları: en yüksek(N) önceki mumlara bakar, MACD/Bollinger/hacim SMA temizlenir', () => {
  const candles = flat(30); candles[25] = bar(25, 100, 110, 99, 109)
  const out = ruleSignals(candles, {left: close, op: '>', right: {kind: 'hh', period: 10}})
  assert.equal(out[25], true); assert.equal(out[24], false)
  for (const kind of ['macd', 'macdsig', 'bbup', 'bblow']) assert.deepEqual(sanitizeRule({left: {kind}, op: '>', right: num(0)}).left, {kind})
  assert.equal(sanitizeRule({left: {kind: 'hh', period: 1}, op: '>', right: num(0)}), null)
})
test('kayıtlı stratejiler temizlenir, aynı ad güncellenir, üst sınır uygulanır', () => {
  const strategy = {entry: [crossDown100], stopPct: 2}
  let list = upsertSaved([], '  Benim  ', strategy, () => 'a')
  list = upsertSaved(list, 'Benim', {...strategy, stopPct: 4}, () => 'b')
  assert.equal(list.length, 1); assert.equal(list[0].id, 'a'); assert.equal(list[0].strategy.stopPct, 4)
  for (let i = 0; i < 12; i++) list = upsertSaved(list, `S${i}`, strategy, () => `id${i}`)
  assert.equal(list.length, 8)
  assert.deepEqual(sanitizeSaved([{id: 'x', name: 'k', strategy: {entry: [{left: 1}]}}, null, 5]), [])
  assert.equal(sanitizeStrategy({entry: [crossDown100], stopPct: -5}).stopPct, 3)
  assert.equal(removeSaved(list, 'id11').some(item => item.id === 'id11'), false)
})

test('kural cümlesi İngilizce ve Türkçe üretilir', () => {
  const rule = {left: close, op: 'crossUp', right: {kind: 'ema', period: 50}}
  assert.equal(ruleTextEn(rule), 'Close crosses above EMA(50)')
  assert.equal(ruleText(rule), 'Kapanış EMA(50) yukarı keser')
  assert.equal(ruleTextEn({left: {kind: 'hh', period: 20}, op: '<', right: {kind: 'macdsig'}}), 'Highest(20) is below MACD signal')
})

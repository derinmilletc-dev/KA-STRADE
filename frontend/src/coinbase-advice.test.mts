// Çalıştırma: node --test frontend/src/coinbase-advice.test.mts
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {dailyLossMeter, profitHint, tradeAdvice} from './coinbase-advice.ts'

const base = {side: 'BUY' as const, entry: 100, stop: 98, target: 106, contracts: 10, contractSize: 1}

test('tradeAdvice: dengeli plan uygun, komisyon dahil kayıp/kazanç', () => {
  const a = tradeAdvice({...base, availableMargin: 1000})!
  assert.equal(a.verdict, 'ok')
  assert.ok(Math.abs(a.feeUsd - 2) < 1e-9)           // 10 × 100 × 0.001 × 2
  assert.ok(Math.abs(a.lossUsd! - 22) < 1e-9)        // 10×2 + 2
  assert.ok(Math.abs(a.gainUsd! - 58) < 1e-9)        // 10×6 − 2
  assert.equal(a.items[0].tone, 'good')
})
test('tradeAdvice: yanlış taraf, limit aşımı ve dolu kapasite engeller', () => {
  assert.equal(tradeAdvice({...base, stop: 102})!.verdict, 'block')
  const over = tradeAdvice({...base, maxLossUsd: 10})!
  assert.equal(over.verdict, 'block'); assert.equal(over.items[0].id, 'maxLoss')
  const daily = tradeAdvice({...base, dailyLimitUsd: 50, dailyLossUsd: 40})!
  assert.equal(daily.items[0].id, 'daily'); assert.match(daily.items[0].text, /kalan 10,00/)
  assert.equal(tradeAdvice({...base, openOrders: 5, maxOrders: 5})!.items[0].id, 'capacity')
})
test('tradeAdvice: sınırsız limit (1.000.000) engel sayılmaz', () => {
  assert.equal(tradeAdvice({...base, maxLossUsd: 1_000_000, dailyLimitUsd: 1_000_000})!.verdict, 'ok')
})
test('tradeAdvice: uyarılar (düşük R/R, yakın stop, büyük risk, komisyon, kaldıraç)', () => {
  const ids = (input: Parameters<typeof tradeAdvice>[0]) => tradeAdvice(input)!.items.map(item => item.id)
  assert.ok(ids({...base, target: 101}).includes('rr'))
  assert.ok(ids({...base, stop: 99.8, target: 110}).includes('tight'))
  assert.ok(ids({...base, availableMargin: 50}).includes('big'))
  assert.ok(ids({...base, entry: 100, target: 100.3, stop: 99}).includes('fee'))
  assert.ok(ids({...base, leverage: 15}).includes('lev'))
  assert.ok(ids({...base, target: null}).includes('noTarget'))
  assert.equal(tradeAdvice({...base, target: null})!.verdict, 'ok')
})
test('tradeAdvice: SHORT ve eksik veri', () => {
  const s = tradeAdvice({...base, side: 'SELL', stop: 102, target: 94})!
  assert.equal(s.verdict, 'ok'); assert.ok(s.rr! > 2.9)
  assert.equal(tradeAdvice({...base, contracts: 0}), null)
})
test('dailyLossMeter: limitli, limitsiz, eşikler', () => {
  assert.equal(dailyLossMeter('5', '50')!.tone, 'ok')
  assert.equal(dailyLossMeter('-30', '50')!.tone, 'warn')
  const bad = dailyLossMeter('-45', '50')!
  assert.equal(bad.tone, 'bad'); assert.equal(bad.leftUsd, 5)
  assert.match(dailyLossMeter('-60', '50')!.text, /limit doldu/)
  const none = dailyLossMeter('-12', '1000000')!
  assert.equal(none.limitUsd, null); assert.match(none.text, /günlük limit yok/)
  assert.equal(dailyLossMeter(null, '50'), null)
})
test('profitHint: yarıdan sonra öner, tek sözleşmede stopu girişe çek', () => {
  const v = {dir: 'LONG' as const, entry: 100, stop: 98, target: 110}
  assert.equal(profitHint({...v, contracts: 3, current: 104}), null)
  const half = profitHint({...v, contracts: 3, current: 106})!
  assert.equal(half.action, 'half'); assert.equal(half.level, 'watch'); assert.equal(half.progressPct, 60)
  assert.equal(profitHint({...v, contracts: 3, current: 109})!.level, 'strong')
  assert.equal(profitHint({...v, contracts: 1, current: 106})!.action, 'breakeven')
  assert.equal(profitHint({...v, contracts: 1, current: 106, stop: 100.5}), null)   // stop zaten kârda
  assert.equal(profitHint({...v, contracts: 3, current: 110}), null)                // hedef doldu
  const short = profitHint({dir: 'SHORT', contracts: 2, entry: 100, stop: 102, target: 90, current: 94})!
  assert.equal(short.progressPct, 60)
  assert.equal(profitHint({dir: 'LONG', contracts: 2, entry: 100, stop: 98, target: null, current: 106}), null)
})

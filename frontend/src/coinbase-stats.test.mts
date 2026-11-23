// Çalıştırma: node --test frontend/src/coinbase-stats.test.mts
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {exitText, fmtMoney, readStats, sourceVerdict, type StatsData} from './coinbase-stats.ts'

const base: StatsData = {
  trades: 6, wins: 3, losses: 3, unknown: 0, win_rate: 50, net: '4.20', gross: '6.00', fees: '1.80', avg_win: '5.00', avg_loss: '-3.60', profit_factor: 1.39,
  best: {coin: 'AVP', net: '9.20'}, worst: {coin: 'HYP', net: '-7.30'}, by_coin: [],
  by_exit: {target: 2, stop: 3, manual_close: 1},
  by_source: {kais: {trades: 3, wins: 2, net: '6.00'}, manual: {trades: 3, wins: 1, net: '-1.80'}, unknown: {trades: 0, wins: 0, net: '0.00'}},
}

test('readStats: bozuk ve boş veri null, geçerli veri aynen', () => {
  assert.equal(readStats(null), null)
  assert.equal(readStats({trades: 0, net: '0.00', by_source: {}, by_exit: {}}), null)
  assert.equal(readStats({trades: 2, net: '1', by_source: undefined}), null)
  assert.equal(readStats(base), base)
})
test('sourceVerdict: az veride yargı yok, yeterli veride karşılaştırır', () => {
  const few = sourceVerdict({...base.by_source, kais: {trades: 1, wins: 1, net: '3.00'}})
  assert.equal(few.ready, false); assert.match(few.text, /Kais önerisiyle 2 işlem daha/)
  const both = sourceVerdict({kais: {trades: 1, wins: 0, net: '0'}, manual: {trades: 2, wins: 0, net: '0'}, unknown: base.by_source.unknown})
  assert.match(both.text, /Kais önerisiyle 2 işlem daha ve elle 1 işlem daha/)
  const ok = sourceVerdict(base.by_source)
  assert.equal(ok.ready, true); assert.match(ok.text, /^Kais önerisiyle açtıkların/); assert.match(ok.text, /%67.*%33/)
  const worse = sourceVerdict({...base.by_source, kais: {trades: 3, wins: 0, net: '-9.00'}})
  assert.match(worse.text, /^Elle açtıkların/)
})
test('fmtMoney ve exitText', () => {
  assert.equal(fmtMoney(1.5), '+1,50 USD'); assert.equal(fmtMoney(-2), '-2,00 USD'); assert.equal(fmtMoney(null), '—'); assert.equal(fmtMoney(3, false), '3,00 USD')
  assert.equal(exitText(base.by_exit), '2 hedef · 3 stop · 1 elle kapatma'); assert.equal(exitText({target: 0, stop: 0, manual_close: 0}), '')
})

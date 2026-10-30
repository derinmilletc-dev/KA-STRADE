import assert from 'node:assert/strict'
import {test} from 'node:test'
import {buildLadder, formatPct, formatUsdt, positionInsights, positionMetrics, sortPositions, summarize} from '../frontend/src/position-view.ts'

const NOW = Date.parse('2026-10-30T12:00:00Z')
const pos = (extra = {}) => ({symbol: 'SOLUSDT', direction: 'LONG', quantity: 3.2, entry_price: 142.5, mark_price: 144.9, notional: 463.68, liquidation_price: 118, unrealized_pnl: 7.68, leverage: 5, margin_type: 'isolated', ...extra})
const plan = (extra = {}) => ({source: 'ORIGINAL_V2_LIVE', stop_loss: 140, targets: [147, 150.5, 154], initial_risk_usdt: 8, tp1_fraction: 0.6, opened_at: '2026-10-30T10:25:00Z', ...extra})

test('LONG: stop/hedef mesafeleri, risk ve R hesabı', () => {
  const m = positionMetrics(pos(), plan(), NOW)
  assert.equal(m.coin, 'SOL')
  assert.ok(Math.abs(m.riskAtStop - 8) < 1e-9)           // (142.5-140)*3.2
  assert.ok(Math.abs(m.rMultiple - 0.96) < 1e-9)         // 7.68 / 8
  assert.ok(Math.abs(m.toStopPct - (144.9 - 140) / 144.9 * 100) < 1e-9)
  assert.ok(Math.abs(m.toTp1Pct - (147 - 144.9) / 144.9 * 100) < 1e-9)
  assert.ok(Math.abs(m.margin - 463.68 / 5) < 1e-9)
  assert.equal(m.level, 'ok')
  assert.equal(m.openedMs, Date.parse('2026-10-30T10:25:00Z'))
})

test('SHORT: yön tersine çevrilir', () => {
  const m = positionMetrics(pos({direction: 'SHORT', entry_price: 0.1842, mark_price: 0.1851, quantity: 900, unrealized_pnl: -0.81, liquidation_price: 0.2203, notional: 166.6}), plan({stop_loss: 0.1858, targets: [0.178, 0.172, 0.166], initial_risk_usdt: 1.4}), NOW)
  assert.equal(m.long, false)
  assert.ok(m.toStopPct > 0 && m.toStopPct < 0.5)
  assert.ok(Math.abs(m.riskAtStop - (0.1858 - 0.1842) * 900) < 1e-9)
  assert.equal(m.level, 'danger')
})

test('Stop kâr tarafındaysa risk negatif (kilitli kâr) ve toplam riske sayılmaz', () => {
  const m = positionMetrics(pos({mark_price: 150}), plan({stop_loss: 143}), NOW)
  assert.ok(m.riskAtStop < 0)
  const s = summarize([{metrics: m, protectedStop: true}], {wallet: 500, dailyLoss: 0, dailyLimit: 15, exposureLimit: null})
  assert.equal(s.riskAtStop, 0)
})

test('Plan yoksa (dış pozisyon) stop/hedef uydurulmaz', () => {
  const m = positionMetrics(pos(), undefined, NOW)
  assert.equal(m.stop, null)
  assert.equal(m.riskAtStop, null)
  assert.equal(m.toStopPct, null)
  assert.equal(m.ladder, null)
  assert.equal(m.rMultiple, null)
})

test('Eksik/bozuk veri çökertmez', () => {
  const m = positionMetrics({symbol: 'XUSDT', direction: 'LONG', mark_price: 'abc'}, {stop_loss: null, targets: []}, NOW)
  assert.equal(m.mark, null)
  assert.equal(m.pnl, null)
  assert.equal(m.level, 'ok')
})

test('Merdiven: konumlar %6-%94 aralığında, yakın etiketler ikinci satıra ya da elenir', () => {
  const ladder = buildLadder(true, 144.9, {stop: 140, entry: 142.5, tp1: 147, tp2: 150.5, tp3: 154})
  for (const marker of ladder.markers) assert.ok(marker.pos >= 6 && marker.pos <= 94)
  assert.ok(ladder.markPos >= 6 && ladder.markPos <= 94)
  const crowded = buildLadder(true, 100.2, {stop: 100.0, entry: 100.1, tp1: 100.3, tp2: 100.4, tp3: 100.5})
  const row0 = crowded.markers.filter(marker => marker.row === 0).map(marker => marker.pos)
  const row1 = crowded.markers.filter(marker => marker.row === 1).map(marker => marker.pos)
  for (const row of [row0, row1]) for (let i = 1; i < row.length; i++) assert.ok(row[i] - row[i - 1] >= 15)
  assert.equal(buildLadder(true, 100, {stop: 100, entry: 100, tp1: 100, tp2: null, tp3: null}), null)
})

test('Akıllı yorumlar: stopa yakın uyarı, TP1 yakın, başabaş cümlesi yalnız bot planında', () => {
  const near = positionMetrics(pos({mark_price: 140.8}), plan(), NOW)
  assert.ok(positionInsights(near, plan(), true).some(item => item.tone === 'warn' && item.text.includes('stopa')))
  assert.ok(positionInsights(near, plan(), false).some(item => item.text.includes('doğrulanamadı')))
  const close = positionMetrics(pos({mark_price: 146.2}), plan(), NOW)
  assert.ok(positionInsights(close, plan(), true).some(item => item.text.includes('stop girişe çekilir')))
  const manualPlan = plan({source: 'MANUAL'})
  assert.ok(!positionInsights(positionMetrics(pos({mark_price: 146.2}), manualPlan, NOW), manualPlan, true).some(item => item.text.includes('stop girişe çekilir')))
})

test('Sıralama: risk yakınlığı, kâr/zarar, süre', () => {
  const a = positionMetrics(pos({symbol: 'AUSDT', mark_price: 140.4}), plan(), NOW)
  const b = positionMetrics(pos({symbol: 'BUSDT', mark_price: 150, unrealized_pnl: 9}), plan(), NOW)
  const c = positionMetrics(pos({symbol: 'CUSDT', unrealized_pnl: -3}), plan({opened_at: '2026-10-30T11:50:00Z'}), NOW)
  const list = [{metrics: b}, {metrics: c}, {metrics: a}]
  assert.equal(sortPositions(list, 'risk')[0].metrics.symbol, 'AUSDT')
  assert.equal(sortPositions(list, 'pnl')[0].metrics.symbol, 'BUSDT') // en kârlı üstte
  assert.equal(sortPositions(list, 'pnl')[2].metrics.symbol, 'CUSDT')
  assert.equal(sortPositions(list, 'time')[0].metrics.symbol, 'CUSDT') // en yeni üstte
  assert.equal(list[0].metrics.symbol, 'BUSDT') // orijinal dizi değişmez
})

test('Özet: toplam, günlük sınır payı, maruziyet, koruma sayısı', () => {
  const a = positionMetrics(pos(), plan(), NOW)
  const b = positionMetrics(pos({symbol: 'BUSDT'}), plan(), NOW)
  const s = summarize([{metrics: a, protectedStop: true}, {metrics: b, protectedStop: false}], {wallet: 500, dailyLoss: 5, dailyLimit: 15, exposureLimit: 1000})
  assert.equal(s.count, 2)
  assert.ok(Math.abs(s.pnl - 15.36) < 1e-9)
  assert.ok(Math.abs(s.riskAtStop - 16) < 1e-9)
  assert.ok(Math.abs(s.riskOfRemainingDailyPct - 160) < 1e-9)
  assert.ok(Math.abs(s.exposurePct - 92.736) < 1e-9)
  assert.equal(s.protectedCount, 1)
  const empty = summarize([], {wallet: null, dailyLoss: null, dailyLimit: null, exposureLimit: 0})
  assert.equal(empty.pnl, null)
  assert.equal(empty.exposureLimit, null)
})

test('Biçimlendirme', () => {
  assert.equal(formatPct(1.9, 1), '%1,9')
  assert.equal(formatPct(null), '—')
  assert.equal(formatUsdt(7.68, true), '+7,68')
  assert.equal(formatUsdt(-0.77, true), '−0,77')
  assert.equal(formatUsdt(null), '—')
})

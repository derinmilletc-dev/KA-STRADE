// Çalıştırma: node frontend/src/dca.test.mts   (Node 22.18+; ek paket gerekmez)
// backend/tests/test_dca_plan.py ile aynı örnek: iki hesap aynı sayıları vermeli.
import assert from 'node:assert'
import {readFileSync} from 'node:fs'
import {DCA_LIMITS, DCA_PRESETS, computeDca, fitToCap, maxBaseMargin, minBaseMargin, planFit, LOSS_HEADROOM} from './dcaMath.ts'

const F = {tickSize: 0.01, stepSize: 0.001, minNotional: 5}
const base: any = {side: 'LONG', price: 100, baseMargin: 10, safetyOrders: 3, stepPct: 1.5, volumeMultiplier: 1.5, takeProfitPct: 1, stopGapPct: 1.5, leverage: 3, filters: F}
const near = (a: number, b: number, eps = 1e-6) => assert(Math.abs(a - b) < eps, `${a} ≉ ${b}`)

let r = computeDca(base)
assert.ok(r.valid, r.errors.join(' '))
assert.deepStrictEqual(r.stages.map(s => s.entry), [100, 98.5, 97, 95.5])
assert.deepStrictEqual(r.stages.map(s => s.qty), [0.3, 0.45, 0.675, 1.012])
near(r.stages[3].avgEntry, 236.446 / 2.437)
assert.strictEqual(r.stages[0].tpPrice, 101)
assert.strictEqual(r.stopPrice, 94.06)
near(r.totalNotional, 236.446, 1e-4)
near(r.totalMargin, 236.446 / 3, 1e-4)
const raw = 0.3 * 5.94 + 0.45 * 4.44 + 0.675 * 2.94 + 1.012 * 1.44
const slip = 0.3 * 100 * 0.0003 + 2.437 * 94.06 * 0.0003            // taban piyasa girişi + piyasa stop (limit girişlerde kayma 0)
near(r.worstLoss, raw + 236.446 * 0.0005 + 2.437 * 94.06 * 0.0005 + slip + 236.446 * 0.0003, 1e-4)
assert.ok(r.stages.every((s, i, a) => i === 0 || a[i - 1].tpPrice > s.tpPrice), 'ortalama düştükçe hedef düşer')
assert.ok(r.liquidation < r.stopPrice && r.liquidationGapPct > 1)

r = computeDca({...base, side: 'SHORT'})
assert.ok(r.valid, r.errors.join(' '))
assert.deepStrictEqual(r.stages.map(s => s.entry), [100, 101.5, 103, 104.5])
assert.ok(r.stopPrice > 104.5 && r.liquidation > r.stopPrice && r.stages[0].tpPrice < 100)

assert.strictEqual(new Set(computeDca({...base, volumeMultiplier: 1}).stages.map(s => s.qty)).size, 1)
// reddedilenler
for (const [patch, text] of [
  [{safetyOrders: 6}, '1 ile 5'], [{safetyOrders: 2.5}, '1 ile 5'], [{volumeMultiplier: 2.5}, 'çarpan'], [{stepPct: 0.1}, 'aralık'],
  [{takeProfitPct: 0.05}, 'Kâr hedefi'], [{stopGapPct: 0.1}, 'Stop mesafesi'], [{leverage: 6}, 'Kaldıraç'], [{leverage: 2.5}, 'Kaldıraç'],
  [{baseMargin: 1, leverage: 2}, 'en düşük emir değerinin altında'], [{safetyOrders: 5, stepPct: 6, stopGapPct: 3}, 'uzaklık'],
  [{filters: undefined}, 'okunamadı'], [{price: Number.NaN}, 'sıfırdan büyük'],
] as [any, string][]) {
  const x = computeDca({...base, ...patch})
  assert.ok(!x.valid && x.errors.some(e => e.includes(text)), `${JSON.stringify(patch)} → ${x.errors.join(' | ')}`)
}
// hazır ayarlar: her fiyat düzeyinde ve iki yönde geçerli plan vermeli (arayüzde boş ekran çıkmasın)
for (const preset of DCA_PRESETS) {
  for (const price of [0.5, 2.5, 100, 2450]) {
    for (const side of ['LONG', 'SHORT'] as const) {
      const f = {tickSize: price / 10000, stepSize: price > 1000 ? 0.001 : price > 1 ? 0.01 : 1, minNotional: 5}
      const p = preset.form
      const x = computeDca({side, price, baseMargin: Number(p.margin), safetyOrders: p.safety, stepPct: Number(p.step), volumeMultiplier: Number(p.volume),
        takeProfitPct: Number(p.tp), stopGapPct: Number(p.gap), leverage: p.leverage, filters: f})
      assert.ok(x.valid, `${preset.id} ${side} @${price}: ${x.errors.join(' | ')}`)
    }
  }
}
// akıllı marj önerisi: BTC benzeri (min emir 100 USDT) coin 10 USDT limitine sığmaz, ucuz coin sığar
{
  const btc: any = {...base, price: 75806.2, filters: {tickSize: 0.1, stepSize: 0.001, minNotional: 100}}
  const need = minBaseMargin(btc)
  assert.ok(need && need.margin > 10, `BTC taban marj: ${JSON.stringify(need)}`)
  assert.ok(computeDca({...btc, baseMargin: need.margin}).errors.every(e => !e.includes('en düşük emir')), 'önerilen marj minimumu geçmeli')
  assert.ok(computeDca({...btc, baseMargin: need.margin - (need.margin <= 10 ? 0.5 : need.margin <= 50 ? 1 : 5)}).errors.some(e => e.includes('en düşük emir')), 'bir adım azı yetmemeli')
  assert.strictEqual(planFit(btc, 10).state, 'over-cap')
  assert.strictEqual(planFit(btc, 500).state, 'ok')
  assert.strictEqual(fitToCap(btc, 10), null, 'BTC 10 USDT toplam marja hiçbir şekilde sığmaz')
  assert.strictEqual(planFit({...base, filters: undefined}, 10).state, 'invalid')
  // tek işlem zarar sınırı: plan sınıra sığacak kadar küçülür
  const bigLoss = {...base, baseMargin: 40}
  assert.ok(computeDca(bigLoss).worstLoss > 3)
  const limited = planFit(bigLoss, 300, 3)
  assert.strictEqual(limited.state, 'ok')
  assert.ok(limited.margin! < 40)
  assert.ok(computeDca({...bigLoss, baseMargin: limited.margin!}).worstLoss <= 3 + 1e-9)
  // ucuz coin: varsayılan ayar sınıra sığmaz ama otomatik sığdırma çözer ve sonuç gerçekten geçerli + sınır içinde
  assert.strictEqual(planFit(base, 10).state, 'over-cap')
  const fix = fitToCap(base, 10)
  assert.ok(fix && fix.total <= 10 + 1e-9, JSON.stringify(fix))
  const check = computeDca({...base, baseMargin: fix.margin, safetyOrders: fix.safety, volumeMultiplier: fix.volume})
  assert.ok(check.valid && check.totalMargin <= 10 + 1e-9, check.errors.join(' '))
  assert.ok(fix.safety <= base.safetyOrders && fix.volume <= base.volumeMultiplier, 'yalnız azaltır')
  // limit büyükse plan marjini AYNEN kalır (limiti doldurmaz); minimumun altındaysa en küçük geçerli marja çıkar
  const keep = planFit(base, 500)
  assert.ok(keep.state === 'ok' && keep.margin === base.baseMargin, `marj korunmalı: ${JSON.stringify(keep)}`)
  const raise = planFit({...btc, baseMargin: 10}, 500)
  assert.ok(raise.state === 'ok' && raise.margin! > 10 && raise.margin! < 100, `minimuma çıkmalı: ${JSON.stringify(raise)}`)
  const fix500 = fitToCap({...base, baseMargin: 10}, 500)
  assert.ok(fix500 && fix500.margin === 10 && fix500.safety === 3 && fix500.volume === 1.5, `değişiklik gerekmez: ${JSON.stringify(fix500)}`)
  const big = maxBaseMargin(base, 100)
  assert.ok(big && big.total <= 100 && big.margin >= 10, JSON.stringify(big))
}
console.log('dca.test.mts: tamam')

// ---- backend ile eşlik (parity): backend/tests/fixtures/dca_parity.json backend planından üretilir (python tests/make_dca_parity_fixture.py) ----
{
  const fx = JSON.parse(readFileSync(new URL('../../backend/tests/fixtures/dca_parity.json', import.meta.url), 'utf8'))
  const c = fx.costs
  assert.strictEqual(DCA_LIMITS.takerFee, c.taker_fee)
  assert.strictEqual(DCA_LIMITS.slippageMarketPct, c.slippage_market_pct)
  assert.strictEqual(DCA_LIMITS.slippageLimitEntryPct, c.slippage_limit_entry_pct)
  assert.strictEqual(DCA_LIMITS.slippageTpExitPct, c.slippage_tp_exit_pct)
  assert.strictEqual(DCA_LIMITS.fundingAllowancePct, c.funding_allowance_pct)
  assert.strictEqual(DCA_LIMITS.minNetProfitUsdt, c.min_net_profit_usdt)
  assert.strictEqual(DCA_LIMITS.minNetProfitPct, c.min_net_profit_pct)
  assert.strictEqual(DCA_LIMITS.maintenanceRate, c.maintenance_rate_estimate)
  assert.strictEqual(DCA_LIMITS.liqBufferPct, c.liq_buffer_pct)
  for (const k of fx.cases) {
    const a = k.args
    const res = computeDca({side: a.direction, price: a.price, baseMargin: a.base_margin_usdt, safetyOrders: a.safety_orders, stepPct: a.step_pct,
      volumeMultiplier: a.volume_multiplier, takeProfitPct: a.take_profit_pct, stopGapPct: a.stop_gap_pct, leverage: a.leverage,
      filters: {tickSize: fx.rules.tick, stepSize: fx.rules.step, minNotional: fx.rules.min_notional}})
    const tpRejected = res.errors.some(e => e.includes('net kâr bırakmıyor') || e.includes('işlem ücretini'))
    if (k.ok) {
      assert.ok(!tpRejected, `backend kabul etti, frontend TP reddetti: ${JSON.stringify(a)}`)
      assert.deepStrictEqual(res.stages.map(s => s.tpPrice), k.tp_prices)
      res.stages.forEach((s, i) => { near(s.tpNetProfitAfterCosts, k.tp_net_after_costs[i], 1e-6); near(s.tpNetProfit, k.tp_net_fee_only[i], 1e-6) })
      near(res.worstLoss, k.worst_loss, 1e-5)
    } else if (k.tp_rejected) {
      assert.ok(tpRejected, `backend TP reddetti, frontend kabul etti: ${JSON.stringify(a)}`)
    }
  }
  console.log(`parity: ${fx.cases.length} durum backend ile aynı`)
}

// Hazır planlarda kaldıraç en az 3x; sığdırılan planın en kötü zararı sınırın altında pay bırakır (sunucuyla kuruş farkı olmasın).
for (const preset of DCA_PRESETS) assert.ok(preset.form.leverage >= 3, `${preset.id} kaldıracı 3x'ten düşük`)
for (const preset of DCA_PRESETS) {
  const input: any = {side: 'LONG', price: 100, baseMargin: Number(preset.form.margin), safetyOrders: preset.form.safety, stepPct: Number(preset.form.step), volumeMultiplier: Number(preset.form.volume),
    takeProfitPct: Number(preset.form.tp), stopGapPct: Number(preset.form.gap), leverage: preset.form.leverage, filters: F}
  const fit = planFit(input, 300, 5)
  if (fit.state === 'ok') assert.ok(computeDca({...input, baseMargin: fit.margin!}).worstLoss <= 5 * LOSS_HEADROOM + 1e-9, `${preset.id} en kötü zararı sınırın payını aştı`)
}
console.log('ok: kaldıraç ≥3x ve zarar payı')

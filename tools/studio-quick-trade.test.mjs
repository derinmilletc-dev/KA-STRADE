import assert from 'node:assert/strict'
import {test} from 'node:test'
import {activePlan, allowedLeverages, allowedMargins, errorText, levelsFor, nearestChoice, planRights, postJson, riskSummary} from '../studio-quick-trade.ts'

const analysis = {direction: 'LONG', entry: 100, stop_loss: 98, tp1: 103, tp2: 106, tp3: 110}

test('Analiz yönüyle aynı AL: analizin kendi seviyeleri kullanılır', () => {
  const levels = levelsFor('LONG', 100, analysis)
  assert.deepEqual(levels, {stop: 98, tp1: 103, tp2: 106, tp3: 110, source: 'analysis'})
})

test('Analizin tersine SAT: mesafeler ters çevrilir, SHORT için tutarlıdır', () => {
  const levels = levelsFor('SHORT', 100, analysis)
  assert.equal(levels.source, 'mirror')
  assert.equal(levels.stop, 102)
  assert.deepEqual([levels.tp1, levels.tp2, levels.tp3], [97, 94, 90])
  assert.ok(levels.stop > 100 && 100 > levels.tp1 && levels.tp1 > levels.tp2 && levels.tp2 > levels.tp3)
})

test('Fiyat analiz hedefini geçtiyse mutlak seviye kullanılmaz, mesafeler yeni fiyata taşınır', () => {
  const levels = levelsFor('LONG', 104, analysis)
  assert.equal(levels.source, 'mirror')
  assert.ok(levels.stop < 104 && 104 < levels.tp1 && levels.tp1 < levels.tp2 && levels.tp2 < levels.tp3)
})

test('Analiz yoksa sabit yüzdeler; yön için her zaman tutarlı', () => {
  for (const side of ['LONG', 'SHORT']) {
    const levels = levelsFor(side, 0.00001234, null)
    assert.equal(levels.source, 'default')
    if (side === 'LONG') assert.ok(levels.stop < 0.00001234 && levels.tp1 > 0.00001234 && levels.tp3 > levels.tp2)
    else assert.ok(levels.stop > 0.00001234 && levels.tp1 < 0.00001234 && levels.tp3 < levels.tp2)
  }
})

test('Geçersiz fiyat ya da bozuk analiz emir seviyesi üretmez / güvenli düşer', () => {
  assert.equal(levelsFor('LONG', null, analysis), null)
  assert.equal(levelsFor('LONG', 0, analysis), null)
  assert.equal(levelsFor('LONG', NaN, analysis), null)
  const broken = levelsFor('LONG', 100, {direction: 'LONG', entry: 100, stop_loss: 98, tp1: 99, tp2: 98, tp3: 97})
  assert.equal(broken.source, 'default')
  const absurd = levelsFor('SHORT', 100, {direction: 'LONG', entry: 100, stop_loss: 0.0001, tp1: 101, tp2: 102, tp3: 500})
  assert.equal(absurd.source, 'default')
})

test('Risk özeti: kayıp, kazanç, R/Ö ve likidasyon karşılaştırması', () => {
  const levels = levelsFor('LONG', 100, analysis)
  const risk = riskSummary(100, levels, 25, 2)
  assert.equal(risk.notional, 50)
  assert.equal(risk.lossAtStop, 1)
  assert.equal(risk.gainAtTp1, 1.5)
  assert.equal(risk.rewardRisk, 1.5)
  assert.equal(risk.stopBeforeLiquidation, true)
  const tight = riskSummary(100, {stop: 60, tp1: 110, tp2: 120, tp3: 130, source: 'default'}, 25, 3)
  assert.equal(tight.stopBeforeLiquidation, false)
  assert.equal(riskSummary(100, levels, 0, 2), null)
})

test('Teminat ve kaldıraç seçenekleri kullanıcı sınırına uyar', () => {
  assert.deepEqual(allowedMargins(100), [10, 25, 50, 100])
  assert.deepEqual(allowedMargins(30), [10, 25])
  assert.deepEqual(allowedMargins(7), [7])
  assert.deepEqual(allowedMargins(undefined), [10, 25, 50, 100])
  assert.deepEqual(allowedLeverages(2), [1, 2])
  assert.deepEqual(allowedLeverages(undefined), [1, 2, 3])
  assert.deepEqual(allowedLeverages(0), [1])
  assert.equal(nearestChoice(40, [10, 25, 50]), 50)
  assert.equal(nearestChoice(25, [10, 25, 50]), 25)
})

test('Hata metni FastAPI biçimlerinden okunur', () => {
  assert.equal(errorText({detail: 'Kilitli.'}, 'x'), 'Kilitli.')
  assert.equal(errorText({detail: [{msg: 'a'}, {msg: 'b'}]}, 'x'), 'a · b')
  assert.equal(errorText({detail: {message: 'obj'}}, 'x'), 'obj')
  assert.equal(errorText(null, 'yedek'), 'yedek')
})

const reply = (status, body) => async () => ({ok: status < 400, status, json: async () => body})
test('postJson: başarı, reddedilen emir (belirli) ve belirsiz sonuç ayrılır', async () => {
  assert.deepEqual(await postJson('/x', {}, 1000, reply(200, {ok: true})), {ok: true, data: {ok: true}})
  const rejected = await postJson('/x', {}, 1000, reply(422, {detail: 'Stop çok uzak.'}))
  assert.equal(rejected.ok, false); assert.equal(rejected.uncertain, false); assert.equal(rejected.message, 'Stop çok uzak.')
  const locked = await postJson('/x', {}, 1000, reply(423, {detail: 'ARM kapalı.'}))
  assert.match(locked.message, /^Kilitli: ARM kapalı\./); assert.equal(locked.uncertain, false)
  const maintenance = await postJson('/x', {}, 1000, reply(503, {detail: 'Bakım.'}))
  assert.equal(maintenance.uncertain, false)
  const serverFault = await postJson('/x', {}, 1000, reply(502, {detail: 'Belirsiz.'}))
  assert.equal(serverFault.uncertain, true)
  const network = await postJson('/x', {}, 1000, async () => { throw new TypeError('fail') })
  assert.equal(network.uncertain, true); assert.equal(network.status, 0)
})

test('Canlı plan: yalnız sahipliği doğrulanmış plan kapatılır, kısmi kapatma yalnız manuelde', () => {
  const plans = [
    {id: 'old-closed', symbol: 'BTCUSDT', status: 'CLOSED', provenance_state: 'CONFIRMED'},
    {id: 'plan-btc-1', symbol: 'BTCUSDT', status: 'OPEN', provenance_state: 'CONFIRMED', source: 'MANUAL'},
    {id: 'plan-eth-1', symbol: 'ETHUSDT', status: 'OPEN', provenance_state: 'CONFIRMED', source: 'AUTO'},
    {id: 'plan-ext-1', symbol: 'SOLUSDT', status: 'OPEN', provenance_state: 'READ_ONLY_EXTERNAL'},
  ]
  assert.equal(activePlan(plans, 'btcusdt').id, 'plan-btc-1')
  assert.deepEqual(planRights(activePlan(plans, 'BTCUSDT')), {id: 'plan-btc-1', close: true, reduce: true})
  assert.deepEqual(planRights(activePlan(plans, 'ETHUSDT')), {id: 'plan-eth-1', close: true, reduce: false})
  assert.deepEqual(planRights(activePlan(plans, 'SOLUSDT')), {id: 'plan-ext-1', close: false, reduce: false})
  assert.deepEqual(planRights(undefined), {id: '', close: false, reduce: false})
})

import assert from 'node:assert/strict'
import {test} from 'node:test'
import {activePlan, allowedLeverages, allowedMargins, errorText, levelsFor, nearestChoice, netOfFees, planRights, postJson, riskSummary, TAKER_FEE_RATE} from '../studio-quick-trade.ts'

test('Komisyon dahil net: brüt kârdan giriş+çıkış komisyonu düşülür ("+ görünüp eksi kapanma" farkı)', () => {
  // 50 USDT'lik pozisyon (0,001 BTC @ 50.000): iki yön ≈ 0,05 USDT komisyon. Brüt +0,20 → net ≈ +0,15.
  assert.equal(TAKER_FEE_RATE, 0.0005)
  assert.ok(Math.abs(netOfFees(0.2, 50000, 50000, 0.001) - 0.15) < 1e-9)
  // Küçük brüt kâr komisyonla zarara döner.
  assert.ok(netOfFees(0.03, 50000, 50200, 0.001) < 0)
})

test('Komisyon dahil net: brüt zarar ve sıfır geçerlidir, eksik veri null döner', () => {
  assert.ok(netOfFees(-0.1, 100, 99, 1) < -0.1)
  assert.ok(netOfFees(0, 100, 100, 1) < 0)
  assert.equal(netOfFees(null, 100, 100, 1), null)
  assert.equal(netOfFees(1, null, 100, 1), null)
  assert.equal(netOfFees(1, 100, 100, 0), null)
  assert.equal(netOfFees(Number.NaN, 100, 100, 1), null)
})

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

import {addJournal, breakEvenStop, limitIssue, limitPresets, parseJournal, setJournalNote, sizeByRisk, stopIssue, summarizeJournal, tightenStop} from '../studio-quick-trade.ts'

test('Limit önerileri pasif tarafta: AL altta, SAT üstte; analiz girişi uygunsa ilk', () => {
  const buy = limitPresets('LONG', 100, 99)
  assert.equal(buy[0], 99); assert.ok(buy.every(v => v < 100))
  const sell = limitPresets('SHORT', 100, 101)
  assert.equal(sell[0], 101); assert.ok(sell.every(v => v > 100))
  assert.ok(limitPresets('LONG', 100, 101).every(v => v < 100))
  assert.deepEqual(limitPresets('LONG', null), [])
})

test('Limit doğrulama: piyasa tarafındaki, boş ve çok uzak fiyatlar reddedilir', () => {
  assert.equal(limitIssue('LONG', 100, 99), null)
  assert.equal(limitIssue('SHORT', 100, 101), null)
  assert.match(limitIssue('LONG', 100, 100.5), /altında/)
  assert.match(limitIssue('SHORT', 100, 99), /üstünde/)
  assert.match(limitIssue('LONG', 100, null), /gir/)
  assert.match(limitIssue('LONG', 100, 70), /uzak/)
  assert.notEqual(limitIssue('LONG', null, 99), null)
})

test('Riske göre büyüklük: teminat = risk ÷ (stop mesafesi × kaldıraç); sınır ve asgari işaretlenir', () => {
  const r = sizeByRisk({balance: 1000, riskPct: 1, entry: 100, stop: 98, leverage: 5, maxMargin: 300})
  assert.equal(r.riskUsdt, 10); assert.equal(r.margin, 100); assert.ok(Math.abs(r.actualRisk - 10) < 1e-9); assert.equal(r.capped, false); assert.equal(r.tooSmall, false)
  const capped = sizeByRisk({balance: 100000, riskPct: 2, entry: 100, stop: 98, leverage: 5, maxMargin: 300})
  assert.equal(capped.capped, true); assert.equal(capped.margin, 300); assert.ok(capped.actualRisk < capped.riskUsdt)
  const small = sizeByRisk({balance: 100, riskPct: .5, entry: 100, stop: 90, leverage: 10, maxMargin: 300})
  assert.equal(small.tooSmall, true)
  assert.equal(sizeByRisk({balance: 0, riskPct: 1, entry: 100, stop: 98, leverage: 5, maxMargin: 300}), null)
  assert.equal(sizeByRisk({balance: 1000, riskPct: 1, entry: 100, stop: 100, leverage: 5, maxMargin: 300}), null)
  const shortSide = sizeByRisk({balance: 1000, riskPct: 1, entry: 100, stop: 102, leverage: 5, maxMargin: 300})
  assert.equal(shortSide.margin, 100)
})

test('Başa baş ve sıkılaştırma yalnız kâr yönünde ve fiyatın zarar tarafında', () => {
  assert.equal(breakEvenStop('LONG', 100, 100.05), null)       // fiyat henüz başa baş seviyesinin altında
  assert.equal(breakEvenStop('LONG', 100, 101), 100.12)
  assert.equal(breakEvenStop('SHORT', 100, 99), 99.88)
  assert.equal(breakEvenStop('SHORT', 100, 100.1), null)
  assert.equal(tightenStop('LONG', 110, 98, 1), 108.9)
  assert.equal(tightenStop('LONG', 110, 109, 1), null)         // mevcut stoptan geride kalırdı
  assert.equal(tightenStop('SHORT', 90, 102, 1), 90.9)
  assert.equal(tightenStop('SHORT', 90, 90.5, 1), null)
  assert.equal(tightenStop('LONG', null, 98, 1), null)
})

test('Elle stop: tetiklenecek ya da riski artıracak stop reddedilir', () => {
  assert.equal(stopIssue('LONG', 110, 98, 105), null)
  assert.match(stopIssue('LONG', 110, 98, 111), /zarar tarafında/)
  assert.match(stopIssue('LONG', 110, 98, 95), /uzaklaştır/)
  assert.equal(stopIssue('SHORT', 90, 102, 95), null)
  assert.match(stopIssue('SHORT', 90, 102, 89), /zarar tarafında/)
  assert.match(stopIssue('SHORT', 90, 102, 105), /uzaklaştır/)
  assert.match(stopIssue('LONG', 110, 98, null), /gir/)
})

test('Günlük: tekrar eklenmez, sınırlı kalır, not güncellenir, bozuk kayıt temizlenir, özet doğru', () => {
  const make = (id, followed, risk, rr) => ({id, ts: 1, account: 'DEMO', symbol: 'BTCUSDT', side: 'LONG', kind: 'MARKET', entry: 100, stop: 98, tp1: 102, tp2: 104, tp3: 106, margin: 50, leverage: 2, riskUsdt: risk, rewardRisk: rr, analysis: 'LONG', followedAnalysis: followed, note: ''})
  let list = addJournal([], make('a', true, 2, 1.5))
  list = addJournal(list, make('a', true, 2, 1.5))
  assert.equal(list.length, 1)
  list = addJournal(list, make('b', false, 4, null))
  assert.equal(list[0].id, 'b')
  list = setJournalNote(list, 'a', 'x'.repeat(400))
  assert.equal(list.find(i => i.id === 'a').note.length, 280)
  const s = summarizeJournal(list)
  assert.equal(s.count, 2); assert.equal(s.followedPct, 50); assert.equal(s.avgRisk, 3); assert.equal(s.avgRewardRisk, 1.5)
  assert.deepEqual(parseJournal('bozuk'), []); assert.deepEqual(parseJournal('[{"id":1},{"x":2}]'), [])
  let many = []
  for (let i = 0; i < 230; i++) many = addJournal(many, make(`k${i}`, true, 1, 1))
  assert.equal(many.length, 200)
  assert.equal(summarizeJournal([]).followedPct, null)
})

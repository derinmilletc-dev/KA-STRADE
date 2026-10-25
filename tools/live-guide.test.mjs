import assert from 'node:assert/strict'
import {test} from 'node:test'
import {clampCustomPercent, openMarginTotal, marginSimulation, CUSTOM_MARGIN_MIN_PERCENT, marginModePreview, capacityPreview, liveGuide, matchPreset, RISK_PRESETS, riskSummary, whyNoTrade, duration, retryDelay, positionNote, smartAlerts, dailySummary, statusHelp, nextScan, reconnectingGuide, rejectionBreakdown} from '../frontend/src/live-guide.ts'

const ready = {
  statusLoaded: true, statusVerified: true, statusError: null, loading: false, freeTrial: false,
  credentialsConfigured: true, connected: true, balance: 141.49, emergency: false, recoveryRequired: false,
  accountRateLimited: false, marketBanSeconds: 0, fillLocked: false, dailyLocked: false, riskApproved: true,
  authorizationValid: true, policyAcknowledged: true, armed: true, running: false, paused: false,
  entryBlocker: null, sessionRemainingSeconds: 0,
}
const guide = patch => liveGuide({...ready, ...patch})

test('loading and unverified status never claim progress', () => {
  assert.equal(guide({statusLoaded: false, statusVerified: false, loading: true}).phase, 'loading')
  const failed = guide({statusLoaded: false, statusVerified: false, loading: false, statusError: 'Bağlantı zaman aşımı.'})
  assert.equal(failed.phase, 'problem')
  assert.equal(failed.action, 'retry')
  assert.match(failed.message, /Bağlantı zaman aşımı\./)
  // Önceki durum görünse bile doğrulanmadıysa kurulum adımı gösterilmez.
  assert.equal(guide({statusVerified: false, riskApproved: false}).phase, 'problem')
})

test('setup walks the four steps in order and names what was just completed', () => {
  const step1 = guide({credentialsConfigured: false, connected: false, balance: null})
  assert.deepEqual([step1.phase, step1.step, step1.action], ['setup', 1, 'open-connection'])
  const step2 = guide({riskApproved: false, authorizationValid: false, policyAcknowledged: false, armed: false})
  assert.deepEqual([step2.step, step2.action], [2, 'choose-risk'])
  assert.match(step2.done, /141,49 USDT/)
  const step3 = guide({authorizationValid: false, armed: false})
  assert.deepEqual([step3.step, step3.action], [3, 'approve'])
  assert.match(step3.message, /24 saatlik risk izni, canlı emir izni \(ARM\)/)
  assert.doesNotMatch(step3.message, /limitleri onayı/)
  const step4 = guide({})
  assert.deepEqual([step4.step, step4.action], [4, 'start'])
  assert.doesNotMatch(step4.message, /ARM/)
  // Durdurmadan sonra yalnız ARM kapanır: onay adımına geri dönülmez, başlatma yeniden açar.
  const rearm = guide({armed: false})
  assert.deepEqual([rearm.step, rearm.action], [4, 'start'])
  assert.match(rearm.message, /ARM\) başlatırken 24 saatliğine yeniden açılır/)
  assert.ok(step4.why && step2.why && step3.why)
})

test('a saved key that cannot be verified is a problem, never the key form again', () => {
  const result = guide({connected: false})
  assert.equal(result.phase, 'problem')
  assert.equal(result.action, 'retry')
  assert.notEqual(result.action, 'open-connection')
})

test('problems take priority over setup and running, in a fixed order', () => {
  assert.equal(guide({freeTrial: true}).action, 'upgrade')
  assert.equal(guide({emergency: true, emergencyText: 'LIVE_EXCEPTION', running: true}).action, 'clear-emergency')
  assert.equal(guide({recoveryRequired: true, running: true}).action, 'recovery')
  assert.equal(guide({fillLocked: true, fillLockSymbol: 'SOLUSDT', running: true}).action, 'review-fill-lock')
  const limited = guide({accountRateLimited: true, accountRateLimitText: 'Binance bekleme süresi.'})
  assert.deepEqual([limited.phase, limited.action], ['problem', null])
  // Acil durdurma, kurtarmadan önce gelir.
  assert.equal(guide({emergency: true, recoveryRequired: true}).action, 'clear-emergency')
})

test('missing balance blocks the risk step instead of guessing amounts', () => {
  const result = guide({balance: null, riskApproved: false})
  assert.deepEqual([result.phase, result.action, result.step], ['problem', 'retry', 2])
})

test('running state explains blocked entries and offers stop', () => {
  const ok = guide({running: true, sessionRemainingSeconds: 7200})
  assert.deepEqual([ok.phase, ok.tone, ok.action], ['running', 'ok', 'stop'])
  assert.match(ok.message, /her 15 dakikalık mum/)
  const blocked = guide({running: true, entryBlocker: 'Binance genel engeli aktif.'})
  assert.equal(blocked.tone, 'warn')
  assert.equal(blocked.message, 'Binance genel engeli aktif.')
})

test('notices cover market ban, daily lock and an ending session without blocking the guide', () => {
  const result = guide({running: true, marketBanSeconds: 760, dailyLocked: true, sessionRemainingSeconds: 1800})
  assert.equal(result.notices.length, 3)
  assert.match(result.notices[0].text, /12 dk 40 sn/)
  assert.equal(guide({}).notices.length, 0)
  const continuing = guide({running: true, marketBanSeconds: 300, scanningContinues: true}).notices[0]
  assert.equal(continuing.tone, 'ok')
  assert.match(continuing.text, /sabit IP'den devam ediyor/)
})

test('a stopped session reason is carried into the next step', () => {
  assert.match(guide({paused: true, pauseReason: 'Oturum süresi doldu.'}).message, /^Son oturum durdu: Oturum süresi doldu\./)
})

test('presets stay inside server limits and can be recognised', () => {
  for (const preset of RISK_PRESETS) {
    assert.ok(preset.risk_percent >= 0.5 && preset.risk_percent <= 2, preset.id)
    assert.ok(preset.daily_loss_percent >= Math.max(1, preset.risk_percent) && preset.daily_loss_percent <= 25, preset.id)
    assert.ok(preset.symbol_count >= 10 && preset.symbol_count <= 100, preset.id)
    assert.ok([1, 4, 12, 24].includes(preset.session_hours), preset.id)
  }
  assert.equal(matchPreset({risk_percent: 1, daily_loss_percent: 3, symbol_count: 40, session_hours: 12})?.id, 'dengeli')
  assert.equal(matchPreset({risk_percent: 1.5, daily_loss_percent: 3, symbol_count: 40, session_hours: 12}), null)
})

test('risk summary uses the balance and server caps', () => {
  const summary = riskSummary(141.49, 1, 3, {max_loss_per_trade_usdt: 1, daily_loss_limit_usdt: 20})
  assert.equal(summary.perTrade, 1)
  assert.equal(summary.perTradeCapped, true)
  assert.ok(Math.abs(summary.perDay - 4.2447) < 1e-9)
  assert.equal(summary.perDayCapped, false)
  assert.ok(Math.abs(summary.dayShare - 3) < 1e-9)
  assert.equal(riskSummary(0, 1, 3), null)
})

test('why-no-trade uses only real scan results', () => {
  assert.equal(whyNoTrade(null), null)
  assert.equal(whyNoTrade({last_scan_at: '2026-10-22T10:00:00Z', candidate_count: 2}), null)
  assert.match(whyNoTrade({last_scan_at: 'x', candidate_count: 0, scanned_symbol_count: 40, near_misses: [{symbol: 'SOLUSDT', reason_code: 'confidence_low', confidence: 81, confidence_threshold: 85, failed_checks: ['trap']}]}),
    /40 coinin hiçbiri.*SOL: güven skoru düşük \(güven %81, gereken %85\) · geçmeyen: tuzak radarı/)
  assert.match(whyNoTrade({last_scan_at: 'x', candidate_count: 0, scanned_symbol_count: 12, rejection_counts: {market_volume: 3, signal_conditions: 9}}), /en sık sebep: trend\/sinyal koşulu oluşmadı \(9 coin\)/)
  assert.match(whyNoTrade({last_failure: {message: 'Binance istek sınırı bekleniyor.'}}), /tamamlanamadı: Binance istek sınırı/)
  assert.equal(whyNoTrade({candidate_count: 0}), null)
})

test('durations read naturally', () => {
  assert.equal(duration(45), '45 sn')
  assert.equal(duration(760), '12 dk 40 sn')
  assert.equal(duration(7260), '2 sa 1 dk')
})

test('read retries back off and never exceed a minute', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 9].map(retryDelay), [5, 10, 20, 40, 60, 60])
})

test('position notes use real levels only', () => {
  const now = Date.parse('2026-10-22T12:00:00Z')
  const plan = {stop_loss: '95', targets: ['110', '120', '130'], opened_at: '2026-10-22T09:00:00Z'}
  const long = positionNote({direction: 'LONG', mark_price: 100}, plan, now)
  assert.equal(long.text, "TP1'e %10 · stopa %5,0 · 3 sa 0 dk açık")
  const short = positionNote({direction: 'SHORT', mark_price: '100'}, {stop_loss: 101, targets: [90]}, now)
  assert.equal(short.tone, 'warn')
  assert.match(short.text, /^TP1'e %10 · stopa %1,0$/)
  assert.match(positionNote({direction: 'LONG', mark_price: 111}, plan, now).text, /^TP1 seviyesi geçildi/)
  assert.equal(positionNote({direction: 'LONG', mark_price: 0}, plan, now), null)
  assert.equal(positionNote({direction: 'LONG', mark_price: 100}, undefined, now), null)
})

test('smart alerts fire only on verified numbers', () => {
  const base = {dailyLoss: null, dailyLimit: null, dailyLocked: false, availableBalance: null, minMargin: null, marketBanSeconds: 0, openPositions: 0, protectedPositions: 0}
  assert.deepEqual(smartAlerts(base), [])
  assert.match(smartAlerts({...base, dailyLoss: 3.5, dailyLimit: 4.2})[0].text, /yaklaşıldı: %83 kullanıldı/)
  assert.deepEqual(smartAlerts({...base, dailyLoss: 3.5, dailyLimit: 4.2, dailyLocked: true}), [])
  assert.match(smartAlerts({...base, availableBalance: 3.2, minMargin: 5})[0].text, /en düşük teminatın/)
  assert.equal(smartAlerts({...base, marketBanSeconds: 60, openPositions: 2, protectedPositions: 2})[0].tone, 'ok')
  assert.equal(smartAlerts({...base, marketBanSeconds: 60, openPositions: 2, protectedPositions: 1})[0].tone, 'stop')
  assert.deepEqual(smartAlerts({...base, marketBanSeconds: 60}), [])
})

test('daily summary reads like a sentence and needs data', () => {
  assert.equal(dailySummary({}), null)
  assert.equal(dailySummary({entries: 0, realizedPnl: 0, last24h: {scans: 90, candidates: 3}}).text, 'Bugün işlem açılmadı · son 24 saatte 90 tarama, 3 aday.')
  const busy = dailySummary({entries: 2, realizedPnl: -1.5, dailyLoss: 1.5, dailyLimit: 6})
  assert.equal(busy.text, 'Bugün 2 işlem · gerçekleşen -1,50 USDT.')
  assert.equal(busy.lossUsed, 25)
})

test('a stalled strategy loop is reported while running', () => {
  assert.equal(guide({running: true, lastCycleAgeSeconds: 90}).tone, 'ok')
  const stalled = guide({running: true, lastCycleAgeSeconds: 1500})
  assert.deepEqual([stalled.tone, stalled.action], ['stop', 'retry'])
  assert.match(stalled.message, /25 dk 0 sn önce/)
})

test('status help gives a concrete next step', () => {
  assert.match(statusHelp('İstek zaman aşımına uğradı.').text, /Render panelinde/)
  assert.equal(statusHelp('LIVE için site girişinde 2FA korumasını etkinleştirin.').href, '/profile')
  assert.match(statusHelp('LIVE kontrolü başka bir kullanıcı veya oturuma ait.').text, /Oturumu geri bağla/)
})

test('next scan follows the 15 minute close and waits for a ban', () => {
  const now = Date.parse('2026-10-22T10:47:30Z')
  assert.deepEqual(nextScan(now), {at: Date.parse('2026-10-22T11:00:00Z'), inSeconds: 750})
  assert.equal(nextScan(Date.parse('2026-10-22T11:00:00Z')).at, Date.parse('2026-10-22T11:15:00Z'))
  assert.equal(nextScan(now, {waitUntilMs: Date.parse('2026-10-22T12:16:00Z')}).at, Date.parse('2026-10-22T12:30:00Z'))
})

test('a dropped connection keeps the last verified progress', () => {
  const lost = reconnectingGuide({...ready, running: true}, 125, 'İstek zaman aşımına uğradı.')
  assert.deepEqual([lost.phase, lost.tone, lost.action, lost.step], ['problem', 'wait', 'retry', 4])
  assert.match(lost.message, /2 dk 5 sn önce\): otomatik işlem çalışıyordu/)
  assert.match(reconnectingGuide({...ready, riskApproved: false}, 30, '').message, /2\. adımdaydın \(Risk ayarı\)/)
})

test('24h rejection breakdown ranks reasons and gates from real counts only', () => {
  const now = Date.parse('2026-10-23T10:00:00Z')
  const since = now / 1000 - 5 * 3600
  const result = rejectionBreakdown({scans: 20, since_epoch: since, reasons: {signal_conditions: 600, stop_too_wide: 120, confidence_low: 80, junk: 0}, checks: {direction: 600, mtf: 50, confidence: 80, bogus_zero: 0}}, now)
  assert.equal(result.scans, 20)
  assert.deepEqual(result.reasons.map(row => [row.key, row.count, row.percent]), [['signal_conditions', 600, 75], ['stop_too_wide', 120, 15], ['confidence_low', 80, 10]])
  assert.equal(result.reasons[0].bar, 100)
  assert.equal(result.reasons[1].bar, 20)
  assert.equal(result.reasons[0].label, 'Trend/sinyal koşulu oluşmadı')
  assert.deepEqual(result.gates.map(row => row.key), ['direction', 'confidence', 'mtf'])
  assert.equal(result.gates[0].label, 'Net trend yok (yön belirsiz)')
  assert.match(result.headline, /^Son 5 saatte 20 taramada en sık eleme: trend\/sinyal koşulu oluşmadı \(%75\)\.$/)
  assert.equal(result.thin, false)
})

test('24h rejection breakdown never invents data when nothing was recorded', () => {
  assert.equal(rejectionBreakdown(undefined, Date.now()), null)
  assert.equal(rejectionBreakdown(null, Date.now()), null)
  assert.equal(rejectionBreakdown({scans: 0, reasons: {a: 5}}, Date.now()), null)
  assert.equal(rejectionBreakdown({scans: 3, reasons: {}}, Date.now()), null)
  const few = rejectionBreakdown({scans: 2, since_epoch: null, reasons: {signal_conditions: 10}}, Date.now())
  assert.equal(few.thin, true)
  assert.equal(few.hours, null)
  assert.match(few.headline, /^Kayıtlı taramalarda 2 taramada/)
  const recent = rejectionBreakdown({scans: 4, since_epoch: Date.now() / 1000 - 600, reasons: {stop_too_wide: 3}}, Date.now())
  assert.match(recent.headline, /^Son bir saatte/)
  assert.equal(recent.reasons[0].label, "Stop %6 sınırından geniş")
})

test('capacity preview shows the real ceiling of a selected risk and never fakes one', () => {
  const caps = {max_margin_usdt: 43.434, leverage: 3, estimated_cost_pct: 0.16, max_stop_distance_pct: 6}
  const preview = capacityPreview(144.78, caps)
  assert.ok(Math.abs(preview.maxNotional - 130.302) < 1e-9)
  assert.deepEqual(preview.rows.map(row => row.stopPct), [1, 2, 3, 6])
  // %6 stop uzaktaysa en fazla 130.302 x 6.16% = 8.027 USDT = bakiyenin ~%5,5'i: %6 hedef tam uygulanamaz.
  assert.ok(Math.abs(preview.atMaxStop.loss - 130.302 * 6.16 / 100) < 1e-9)
  assert.ok(preview.atMaxStop.percent < 6 && preview.atMaxStop.percent > 5)
  // Stop yakınsa uygulanabilir risk çok daha küçüktür.
  assert.ok(preview.rows[1].percent < 2)
})

test('capacity preview is null when the wallet or limits cannot be verified', () => {
  const caps = {max_margin_usdt: 43, leverage: 3, estimated_cost_pct: 0.16}
  assert.equal(capacityPreview(0, caps), null)
  assert.equal(capacityPreview(144.78, null), null)
  assert.equal(capacityPreview(144.78, undefined), null)
  assert.equal(capacityPreview(144.78, {leverage: 3, estimated_cost_pct: 0.16}), null)
  assert.equal(capacityPreview(144.78, {...caps, max_margin_usdt: NaN}), null)
  assert.equal(capacityPreview(144.78, {...caps, leverage: 0}), null)
  // Üst stop mesafesi gönderilmezse güvenli varsayılan %6 kullanılır.
  assert.equal(capacityPreview(144.78, caps).atMaxStop.stopPct, 6)
})

test('margin mode preview scales with the mode percent and respects the system ceiling', () => {
  const caps = {leverage: 3, estimated_cost_pct: 0.16, max_stop_distance_pct: 6, system_ceilings: {max_margin_per_trade_usdt: 100}}
  const safe = marginModePreview(144.78, caps, 30), mid = marginModePreview(144.78, caps, 50), aggressive = marginModePreview(144.78, caps, 65)
  assert.ok(Math.abs(safe.marginUsdt - 43.434) < 1e-6)
  assert.ok(safe.atMaxStop.loss < mid.atMaxStop.loss && mid.atMaxStop.loss < aggressive.atMaxStop.loss)
  assert.ok(Math.abs(aggressive.atMaxStop.percent - 94.107 * 3 * 6.16 / 100 / 144.78 * 100) < 1e-6)
  assert.equal(safe.capped, false)
  const rich = marginModePreview(1000, caps, 65)
  assert.equal(rich.marginUsdt, 100); assert.equal(rich.capped, true)
  assert.equal(marginModePreview(0, caps, 30), null)
  assert.equal(marginModePreview(100, null, 30), null)
})

// ---- Teminat Kontrol Merkezi (yalnız gösterim)
const marginCaps = {max_margin_usdt: 43, leverage: 3, estimated_cost_pct: 0.2, max_stop_distance_pct: 6, max_total_margin_usdt: 115, system_ceilings: {max_margin_per_trade_usdt: 1000}}

test('özel yüzde: geçersiz değerler reddedilir, sınırlar korunur', () => {
  assert.equal(CUSTOM_MARGIN_MIN_PERCENT, 5)
  assert.equal(clampCustomPercent(40, 65), 40)
  assert.equal(clampCustomPercent('42.46', 65), 42.5)
  for (const bad of [4.9, 65.1, 0, -10, NaN, Infinity, '', 'abc', null, undefined, {}]) assert.equal(clampCustomPercent(bad, 65), null, String(bad))
  assert.equal(clampCustomPercent(5, 65), 5)
  assert.equal(clampCustomPercent(65, 65), 65)
  assert.equal(clampCustomPercent(30, 4), null)
})

test('açık teminat toplamı: kayıtlı marjin, tahmin ve eksik veri', () => {
  assert.deepEqual(openMarginTotal([]), {total: 0, estimated: false})
  assert.deepEqual(openMarginTotal([{margin: 10}, {margin: 5.5}]), {total: 15.5, estimated: false})
  const est = openMarginTotal([{quantity: 1.2, entry_price: 100, leverage: 3}])
  assert.equal(est.estimated, true); assert.ok(Math.abs(est.total - 40) < 1e-9)
  assert.equal(openMarginTotal([{quantity: 1, entry_price: 100}]), null)
})

test('teminat simülasyonu: hazır modlar sunucu formülüyle aynı, teorik değerler', () => {
  const wallet = 144
  const safe = marginSimulation(wallet, marginCaps, 30, {openMargin: {total: 40, estimated: true}, dailyLimit: 7.2, dailyLoss: 2.22})
  assert.ok(Math.abs(safe.marginUsdt - 43.2) < 1e-9)
  assert.ok(Math.abs(safe.notional - 129.6) < 1e-9)
  assert.ok(Math.abs(safe.lossAtThree.loss - 129.6 * 3.2 / 100) < 1e-9)
  assert.ok(Math.abs(safe.lossAtMax.loss - 129.6 * 6.2 / 100) < 1e-9)
  assert.ok(Math.abs(safe.walletShare - 30) < 1e-9)
  assert.ok(Math.abs(safe.room - 75) < 1e-9); assert.equal(safe.tradesFit, 1)
  assert.ok(Math.abs(safe.dailyLeft - 4.98) < 1e-9)
  const aggressive = marginSimulation(wallet, marginCaps, 65)
  assert.ok(aggressive.marginUsdt > safe.marginUsdt && aggressive.lossAtMax.loss > safe.lossAtMax.loss)
  assert.equal(aggressive.room, null); assert.equal(aggressive.dailyLeft, null)
})

test('simülasyon: sistem tavanı kırpar, eksik veri null döner, sınırı aşmaz', () => {
  const capped = marginSimulation(1000, {...marginCaps, system_ceilings: {max_margin_per_trade_usdt: 50}}, 65)
  assert.equal(capped.marginUsdt, 50); assert.equal(capped.capped, true)
  assert.equal(marginSimulation(0, marginCaps, 30), null)
  assert.equal(marginSimulation(144, null, 30), null)
  assert.equal(marginSimulation(144, marginCaps, 0), null)
  const full = marginSimulation(144, marginCaps, 30, {openMargin: {total: 200, estimated: false}})
  assert.equal(full.room, 0); assert.equal(full.tradesFit, 0)
})

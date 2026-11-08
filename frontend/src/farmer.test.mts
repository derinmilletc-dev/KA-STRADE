// Çalıştırma: node frontend/src/farmer.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {COPY, ageText, armRemaining, priceText, badgeOf, emergencyOutcome, friendlyError, money, pickScreen, pnlView, protectionOf, slotView, statusMessageKey, whyKey, tr_} from './farmerView.ts'

const base: any = {ok: true, name: 'KAIS FARMER', available: true, state: 'OFF', configured: true, slots: {capacity: 2, held: 0, open: 0, closing: 0, free: 2}, effective_capacity: 2, max_positions: 5}

// --- ilk kullanım / ekran seçimi ---
assert.strictEqual(pickScreen({status: null, loading: true, error: false, accountConnected: true}), 'loading')
assert.strictEqual(pickScreen({status: null, loading: false, error: true, accountConnected: true}), 'backend_unavailable')
assert.strictEqual(pickScreen({status: null, loading: false, error: false, accountConnected: true}), 'loading')
assert.strictEqual(pickScreen({status: base, loading: false, error: false, accountConnected: false}), 'account_unavailable')
assert.strictEqual(pickScreen({status: {...base, available: false}, loading: false, error: false, accountConnected: true}), 'disabled')
assert.strictEqual(pickScreen({status: {...base, configured: false}, loading: false, error: false, accountConnected: true}), 'onboarding')
assert.strictEqual(pickScreen({status: base, loading: false, error: false, accountConnected: true}), 'dashboard')

// --- durum rozetleri ve mesajlar: renk tek başına anlam taşımaz (metin + ikon) ---
for (const state of ['OFF', 'RUNNING', 'ENTRY_PAUSED_ARM', 'ENTRY_PAUSED_SAFETY', 'STOPPING', 'INTERVENTION_REQUIRED'] as const) {
  const badge = badgeOf(state)
  assert.ok(COPY[badge.key].en.length > 0 && badge.icon, state)
  assert.ok(COPY[statusMessageKey(state, false)].tr && COPY[statusMessageKey(state, true)].en, state)
}
assert.strictEqual(tr_('en', badgeOf('ENTRY_PAUSED_ARM').key), 'ENTRY PAUSED')
assert.strictEqual(badgeOf('INTERVENTION_REQUIRED').tone, 'stop')
assert.strictEqual(tr_('en', statusMessageKey('STOPPING', true)), 'New positions are stopped. Existing positions continue under their protection orders.')
assert.match(tr_('en', statusMessageKey('ENTRY_PAUSED_ARM', true)), /Existing positions remain protected/)
assert.match(tr_('en', statusMessageKey('INTERVENTION_REQUIRED', false)), /No new position will be opened/)

// --- kapasite: teorik 5, etkin 2 ---
let s = slotView({...base, slots: {capacity: 2, held: 0, open: 2, closing: 0, free: 0}})
assert.deepStrictEqual([s.capacity, s.used, s.free, s.limitedByMargin, s.configuredMax], [2, 2, 0, true, 5])
assert.deepStrictEqual(s.dots, ['used', 'used'])
s = slotView({slots: {capacity: 5, held: 1, open: 1, closing: 1, free: 2}, effective_capacity: 5, max_positions: 5})
assert.deepStrictEqual([s.capacity, s.used, s.free, s.limitedByMargin], [5, 3, 2, false])
assert.deepStrictEqual(s.dots, ['used', 'used', 'used', 'free', 'free'])
s = slotView({slots: {capacity: 0, held: 0, open: 0, closing: 0, free: 0}, effective_capacity: 0, max_positions: 5})
assert.deepStrictEqual([s.zero, s.dots.length], [true, 0])
assert.strictEqual(slotView({slots: null, effective_capacity: null, max_positions: undefined}).capacity, 0)

// --- PnL: bilinmeyen sayı asla 0 değil ---
assert.deepStrictEqual(pnlView('VERIFIED', 1.234), {text: '+$1.23', kind: 'verified', sign: 'up'})
assert.deepStrictEqual(pnlView('VERIFIED', -0.5), {text: '-$0.50', kind: 'verified', sign: 'down'})
assert.deepStrictEqual(pnlView('PENDING', null), {text: '—', kind: 'pending', sign: null})
assert.deepStrictEqual(pnlView('UNVERIFIED', null), {text: '—', kind: 'unverified', sign: null})
assert.deepStrictEqual(pnlView('PENDING', 0), {text: '—', kind: 'pending', sign: null})            // doğrulanmamış 0 gösterilmez
assert.deepStrictEqual(pnlView('VERIFIED', null), {text: '—', kind: 'pending', sign: null})
assert.strictEqual(pnlView('VERIFIED', 0).text, '$0.00')                                           // doğrulanmış gerçek sıfır
assert.strictEqual(tr_('en', 'pnl.UNVERIFIED'), 'Final result could not be verified yet')
assert.ok(!JSON.stringify(COPY).includes('PNL ERROR'))
assert.strictEqual(money(null), '—')
assert.strictEqual(money(NaN), '—')

// --- yaş, ARM süresi ---
assert.deepStrictEqual([ageText(12), ageText(18 * 60 + 5), ageText(3725), ageText(null), ageText(-1)], ['12s', '18m', '1h 2m', '—', '—'])
assert.strictEqual(armRemaining(1000 + 3 * 3600 + 25 * 60, 1000), '03:25')
assert.strictEqual(armRemaining(900, 1000), null)
assert.strictEqual(armRemaining(null, 1000), null)

assert.deepStrictEqual([priceText(64231.456), priceText(2.5), priceText(0.1234567), priceText(null), priceText(0)], ['64,231.46', '2.5', '0.12346', '—', '—'])

// --- koruma durumu ---
assert.strictEqual(protectionOf({engine_status: 'RUNNING', slot_state: 'OPEN'}), 'protected')
assert.strictEqual(protectionOf({engine_status: 'RUNNING', slot_state: 'CLOSING'}), 'closing')
assert.strictEqual(protectionOf({engine_status: 'INTERVENTION_REQUIRED', slot_state: 'OPEN'}), 'attention')
assert.strictEqual(protectionOf({engine_status: 'RUNNING', slot_state: 'HELD'}), 'pending')
assert.strictEqual(protectionOf({engine_status: 'RUNNING', slot_state: null}), 'pending')

// --- "neden pozisyon yok" yalnız bilinen kodlar; bilinmeyen uydurulmaz ---
for (const code of ['NO_CANDIDATES', 'SCORE_TOO_LOW', 'MTF_NOT_CONFIRMED', 'MOMENTUM_MISMATCH', 'CAPACITY_FULL', 'COOLDOWN', 'ARM_EXPIRED', 'SAFETY_GATE', 'DAILY_LOSS', 'STOP_BOT', 'INTERVENTION', 'OFF']) {
  assert.notStrictEqual(whyKey(code), 'why.OTHER', code)
}
assert.strictEqual(whyKey('SOMETHING_INTERNAL'), 'why.OTHER')
assert.ok(!COPY['why.OTHER'].en.includes('SOMETHING'))

// --- ham hata gösterilmez ---
const raw = 'Traceback (most recent call last): sqlite3.ProgrammingError'
assert.ok(!friendlyError(new Error(raw), 'en').includes('sqlite'))
assert.ok(!friendlyError(new Error(raw), 'tr').includes('Traceback'))
assert.match(friendlyError(new Error('{"code":"FARMER_GATES_CLOSED"}'), 'en'), /not turned on/)
assert.match(friendlyError(new Error('SETTINGS_LOCKED_WHILE_ACTIVE'), 'en'), /Stop it first/)

// --- acil kapatma sonucu ---
assert.strictEqual(emergencyOutcome({complete: true, closed: 2, failed: 0}), 'success')
assert.strictEqual(emergencyOutcome({complete: false, closed: 1, failed: 1}), 'partial')
assert.strictEqual(emergencyOutcome({complete: false, closed: 0, failed: 1}), 'failure')
assert.strictEqual(emergencyOutcome(null), 'failure')

// --- müşteriye görünen metinde dahili isimler yok ---
const all = JSON.stringify(COPY).toLowerCase()
for (const banned of ['apf', 'farmer v1', 'v1 ', 'c9', 'c10', 'orchestrator', 'engine']) assert.ok(!all.includes(banned), banned)
for (const [key, pair] of Object.entries(COPY)) assert.ok((pair as any).tr && (pair as any).en, key)

console.log('farmer.test.mts OK')

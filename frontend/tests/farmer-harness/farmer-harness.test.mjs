// Etkileşim testleri (gerçek tarayıcı, sahte API). Çıktı: out.js/out.css (git'e eklenmez).
import assert from 'node:assert'
import path from 'node:path'
import {pathToFileURL} from 'node:url'
const {chromium} = await import(process.env.PLAYWRIGHT ? pathToFileURL(process.env.PLAYWRIGHT).href : 'playwright')
const dir = path.resolve('.')
const browser = await chromium.launch()
let n = 0
async function open(s, {w = 390, h = 844, q = '', lang = 'en'} = {}) {
  const ctx = await browser.newContext({viewport: {width: w, height: h}, locale: lang})
  await ctx.addInitScript(l => { try { localStorage.setItem('kt_lang', l) } catch {} }, lang)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(`file://${dir}/index.html?s=${s}${q}`)
  await page.waitForSelector('[data-testid="farmer-panel"]')
  await page.waitForTimeout(150)
  page.errors = errors; page.ctx = ctx
  return page
}
const calls = page => page.evaluate(() => window.__calls)
const fillUser = async (page, t = '1', l = '1.5', c = '100') => { await page.getByRole('textbox', {name: 'Profit Target'}).fill(t); await page.getByRole('textbox', {name: 'Max Loss'}).fill(l); await page.getByRole('textbox', {name: 'Margin Cap'}).fill(c); await page.waitForTimeout(500) }
const text = (page, id) => page.getByTestId(id).innerText()
async function test(name, fn) { try { await fn(); n++; console.log('ok  ', name) } catch (e) { console.log('FAIL', name, '\n', e.message); process.exitCode = 1 } }

await test('loading state is shown, not blank', async () => {
  const p = await open('loading'); assert.ok(await p.getByTestId('farmer-loading').isVisible()); await p.ctx.close()
})
await test('backend unavailable state + retry', async () => {
  const p = await open('down'); assert.ok(await p.getByTestId('farmer-backend-unavailable').isVisible()); assert.match(await p.innerText('body'), /protection orders keep working/)
  await p.getByRole('button', {name: /Try again/}).click(); await p.waitForTimeout(100); assert.ok((await calls(p)).filter(c => c === 'GET /farmer/status').length >= 2); await p.ctx.close()
})
await test('account unavailable asks to connect', async () => {
  const p = await open('account'); assert.ok(await p.getByTestId('farmer-account-unavailable').isVisible()); await p.getByTestId('farmer-account-unavailable').getByRole('button', {name: /Open connection/}).click(); assert.ok((await calls(p)).includes('CONNECT')); await p.ctx.close()
})
await test('disabled state: explains, no activate, no raw flags', async () => {
  const p = await open('disabled'); const body = await p.innerText('body')
  assert.ok(await p.getByTestId('farmer-disabled').isVisible()); assert.strictEqual(await p.getByTestId('farmer-activate').count(), 0)
  assert.ok(!/KAISTRADE_FARMER|LIVE_FLAG|TPM/.test(body)); await p.ctx.close()
})
await test('wizard step 1: nothing pre-filled, save disabled until all three set, then saves settings (no start yet)', async () => {
  const p = await open('onboarding'); assert.ok(await p.getByTestId('farmer-save').isDisabled())
  assert.strictEqual(await p.getByRole('textbox', {name: 'Profit Target'}).inputValue(), '')
  await p.getByRole('textbox', {name: 'Profit Target'}).fill('1'); await p.getByRole('textbox', {name: 'Max Loss'}).fill('1.5')
  await p.getByRole('group', {name: /Margin Cap/}).getByRole('button', {name: '100'}).click(); await p.waitForTimeout(500)
  await p.getByTestId('farmer-save').click(); await p.waitForTimeout(150)
  const c = await calls(p); assert.ok(c.some(x => x.startsWith('POST /farmer/settings')), JSON.stringify(c)); assert.ok(!c.includes('POST /farmer/start'))
  assert.strictEqual(c.some(x => x.includes('NaN')), false); await p.ctx.close()
})
await test('wizard last step: activation starts the bot (settings already saved)', async () => {
  const p = await open('off'); assert.ok(await p.getByTestId('farmer-step-start').isVisible())
  await p.getByTestId('farmer-activate').click(); await p.waitForTimeout(150)
  assert.ok((await calls(p)).includes('POST /farmer/start')); await p.ctx.close()
})
await test('rejected start shows no false success (backend refusal is not "active")', async () => {
  const p = await open('off', {q: '&start=reject'})
  await p.getByTestId('farmer-activate').click(); await p.waitForTimeout(200)
  const body = await p.innerText('body'); assert.ok(!/KAIS FARMER is active/.test(body), body); assert.match(body, /could not be started/); assert.ok(!/FARMER_NOT_STARTED/.test(body)); await p.ctx.close()
})
await test('preview comes from server for custom values', async () => {
  const p = await open('onboarding'); await fillUser(p)
  await p.getByRole('textbox', {name: 'Profit Target'}).fill('0.2'); await p.waitForTimeout(600)
  assert.match(await text(p, 'farmer-preview'), /TIGHT/)
  assert.ok((await calls(p)).some(c => c.startsWith('POST /farmer/preview'))); await p.ctx.close()
})
await test('invalid number blocks saving, no request sent', async () => {
  const p = await open('onboarding'); await p.getByRole('textbox', {name: 'Max Loss'}).fill('abc'); await p.waitForTimeout(500)
  assert.ok(await p.getByTestId('farmer-form-error').isVisible()); assert.ok(await p.getByTestId('farmer-save').isDisabled())
  assert.ok(!(await calls(p)).some(c => c.includes('/farmer/settings'))); await p.ctx.close()
})
await test('running with no position explains why and does not push trading', async () => {
  const p = await open('running_empty'); assert.match(await text(p, 'farmer-message'), /actively scanning/)
  assert.match(await text(p, 'farmer-why-primary'), /No setup reached the required score/)
  const body = await p.innerText('body'); assert.ok(!/start trading|fill.*slots|empty slot.*start/i.test(body))
  assert.match(await text(p, 'farmer-no-positions'), /only when a qualified setup appears/); await p.ctx.close()
})
await test('capacity: 2 effective of 5 theoretical, never shows 2/5', async () => {
  const p = await open('running_two'); const s = await text(p, 'farmer-slots')
  assert.match(s, /2 \/ 2 effective positions/); assert.match(s, /allows only 2 simultaneous/); assert.ok(!/2 \/ 5/.test(s))
  assert.strictEqual(await p.locator('[data-testid="farmer-slots"] i').count(), 2); await p.ctx.close()
})
await test('capacity: zero shows explanation', async () => {
  const p = await open('zero_cap'); assert.match(await text(p, 'farmer-slots'), /no position can be opened/); await p.ctx.close()
})
await test('available slots wording is not pressure', async () => {
  const p = await open('running_one'); const s = await text(p, 'farmer-slots'); assert.match(s, /1 position available\. KAIS FARMER will use them only when a qualified setup appears/); await p.ctx.close()
})
await test('positions: cards, protection, verified/pending/unverified pnl, no fake zero', async () => {
  const p = await open('pnl'); const cards = p.getByTestId('farmer-position'); assert.strictEqual(await cards.count(), 3)
  const t = await cards.allInnerTexts(); assert.match(t[0], /CLOSING/); assert.match(t[0], /being verified/); assert.match(t[1], /could not be verified yet/); assert.match(t[2], /\+\$1\.02/); assert.match(t[2], /Net result verified/)
  assert.ok(!/\$0\.00/.test(t[0] + t[1])); assert.ok(!/PNL ERROR/i.test(await p.innerText('body'))); await p.ctx.close()
})
await test('ARM expired: message, single renew, existing positions stay protected, renew posts /arm', async () => {
  const p = await open('arm_expired'); assert.match(await text(p, 'farmer-message'), /Existing positions remain protected\. Renew ARM/)
  assert.match(await text(p, 'farmer-badge'), /ENTRY PAUSED/); assert.ok(await p.getByText('PROTECTED').first().isVisible())
  await p.getByTestId('farmer-renew-arm').click(); await p.waitForTimeout(100); assert.ok((await calls(p)).some(c => c === 'POST /arm {"understood":true}')); await p.ctx.close()
})
await test('STOP BOT: posts stop-bot only, copy says existing positions remain protected, no emergency', async () => {
  const p = await open('running_one'); assert.match(await p.innerText('body'), /STOP BOT stops new entries only/)
  await p.getByTestId('farmer-stop').click(); await p.waitForTimeout(100); const c = await calls(p)
  assert.ok(c.includes('POST /farmer/stop-bot')); assert.ok(!c.some(x => x.includes('emergency'))); await p.ctx.close()
})
await test('stopping state copy', async () => {
  const p = await open('stopping'); assert.match(await text(p, 'farmer-message'), /New positions are stopped\. Existing positions continue under their protection orders/); assert.strictEqual(await p.getByTestId('farmer-stop').count(), 0); await p.ctx.close()
})
await test('safety pause shows daily loss reason', async () => {
  const p = await open('safety'); assert.match(await text(p, 'farmer-status'), /ENTRY PAUSED/); assert.match(await text(p, 'farmer-status'), /daily loss limit is reached/i); await p.ctx.close()
})
await test('intervention: prominent, no raw errors, no activate, ack posts /farmer/acknowledge', async () => {
  const p = await open('intervention'); const a = await text(p, 'farmer-intervention')
  assert.match(a, /Attention Required/); assert.match(a, /review the issue before restarting/); assert.strictEqual(await p.getByTestId('farmer-activate').count(), 0)
  assert.match(await text(p, 'farmer-badge'), /INTERVENTION REQUIRED/); assert.ok(!/Traceback|sqlite|INTERVENTION_REQUIRED/.test(await p.innerText('body')))
  await p.getByTestId('farmer-ack').click(); await p.waitForTimeout(100); assert.ok((await calls(p)).includes('POST /farmer/acknowledge')); await p.ctx.close()
})
await test('emergency: needs confirmation, cancel sends nothing', async () => {
  const p = await open('running_two'); await p.getByTestId('farmer-emergency').click()
  const m = p.getByTestId('farmer-emergency-modal'); assert.ok(await m.isVisible()); assert.match(await m.innerText(), /Close all KAIS FARMER positions\?[\s\S]*reduce-only[\s\S]*different price/)
  assert.ok(!(await calls(p)).some(c => c.includes('emergency-close'))); await p.getByRole('button', {name: 'Cancel'}).click(); assert.strictEqual(await m.count(), 0)
  assert.ok(!(await calls(p)).some(c => c.includes('emergency-close'))); await p.ctx.close()
})
await test('emergency: Escape closes modal; focus starts on Cancel (safe default)', async () => {
  const p = await open('running_two'); await p.getByTestId('farmer-emergency').click()
  assert.strictEqual(await p.evaluate(() => document.activeElement?.textContent), 'Cancel'); await p.keyboard.press('Escape'); assert.strictEqual(await p.getByTestId('farmer-emergency-modal').count(), 0); await p.ctx.close()
})
for (const [mode, id, re] of [['', 'farmer-emergency-success', /verified flat/], ['partial', 'farmer-emergency-partial', /not all could be verified/], ['fail', 'farmer-emergency-failure', /could not be completed/]]) {
  await test(`emergency result: ${id}`, async () => {
    const p = await open('running_two', {q: mode ? `&em=${mode}` : ''}); await p.getByTestId('farmer-emergency').click(); await p.getByTestId('farmer-emergency-confirm').click(); await p.waitForTimeout(250)
    assert.ok((await calls(p)).includes('POST /farmer/emergency-close')); assert.match(await text(p, id), re); await p.ctx.close()
  })
}
await test('scanner: candidates with statuses; none → no forcing copy', async () => {
  const p = await open('running_two'); const t = await text(p, 'farmer-scanner'); for (const s of ['Qualified', 'Waiting confirmation', 'Rejected', 'Cooling down']) assert.ok(t.includes(s), s)
  assert.match(t, /Higher score = a stronger setup/); const q = await open('running_none'); assert.match(await text(q, 'farmer-no-candidates'), /No trade is being forced/); await p.ctx.close(); await q.ctx.close()
})
await test('guide opens (KAIS REHBER), covers topics, Esc closes, Learn more deep-links', async () => {
  const p = await open('running_two'); await p.getByTestId('farmer-open-guide').click(); const g = p.getByTestId('farmer-guide'); assert.ok(await g.isVisible())
  const t = await g.innerText(); for (const k of ['KAIS LONG', 'DCA', 'Profit Target', 'Max Loss', 'Minimum Score', 'Dead Position Protection', 'ARM', 'STOP BOT', 'Emergency Close', 'verified', 'guaranteed', 'exchange-side protection']) assert.ok(new RegExp(k, 'i').test(t), k)
  await p.keyboard.press('Escape'); assert.strictEqual(await g.count(), 0)
  await p.getByTestId('farmer-scanner').getByRole('button', {name: 'Learn more'}).click(); assert.match(await p.getByTestId('farmer-guide').innerText(), /Minimum score/i); await p.ctx.close()
})
await test('settings are read-only while positions are open', async () => {
  const p = await open('running_one'); await p.getByTestId('farmer-settings').locator('summary').first().click(); assert.ok(await p.getByRole('textbox', {name: 'Profit Target'}).isDisabled()); assert.match(await text(p, 'farmer-settings'), /only while KAIS FARMER is off/); await p.ctx.close()
})
await test('no internal names visible (APF / V1 / Farmer V1 / engine)', async () => {
  for (const s of ['onboarding', 'running_two', 'intervention', 'disabled']) { const p = await open(s); const b = await p.innerText('body'); assert.ok(!/\bAPF\b|Farmer V1|\bV1\b|orchestrator|GridEngine|C9|C10/i.test(b), s); await p.ctx.close() }
})
await test('Turkish locale renders Turkish copy', async () => {
  const p = await open('running_two', {lang: 'tr'}); assert.match(await text(p, 'farmer-message'), /koruma emirleriyle yönetiyor/); assert.match(await text(p, 'farmer-badge'), /ÇALIŞIYOR/); await p.ctx.close()
})
await test('polling is controlled: no request storm', async () => {
  const p = await open('running_two'); await p.waitForTimeout(1500); const gets = (await calls(p)).filter(c => c === 'GET /farmer/status').length; assert.ok(gets <= 2, String(gets)); await p.ctx.close()
})
await test('mobile: no horizontal overflow on every screen', async () => {
  for (const s of ['onboarding', 'running_two', 'intervention', 'arm_expired', 'pnl', 'disabled', 'zero_cap']) {
    const p = await open(s); const o = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth); assert.ok(o <= 0, `${s} overflow ${o}`); await p.ctx.close()
  }
})
await test('mobile touch targets >= 44px for main actions', async () => {
  const p = await open('running_one'); for (const id of ['farmer-stop', 'farmer-emergency']) { const b = await p.getByTestId(id).boundingBox(); assert.ok(b.height >= 44, `${id} ${b.height}`) }; await p.ctx.close()
})
await test('notifications: open, close, critical state (and none for the initial state)', async () => {
  const ctx = await browser.newContext({viewport: {width: 390, height: 844}, locale: 'en'})
  await ctx.addInitScript(() => { try { localStorage.setItem('kt_lang', 'en') } catch {} })
  const page = await ctx.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message))
  await page.clock.install()
  await page.goto(`file://${dir}/index.html?s=notify`)
  await page.waitForSelector('[data-testid="farmer-panel"]'); await page.clock.runFor(200)
  const toasts = () => page.evaluate(() => [...document.querySelectorAll('#kt-toasts .ktT')].map(e => e.innerText))
  assert.deepStrictEqual(await toasts(), [], 'initial state must not notify')
  const step = async patch => { await page.evaluate(p => { const {base, pos} = window.__mk; window.__status = base(p(pos)) }, patch); await page.clock.runFor(10500); await page.waitForTimeout(100) }
  await page.evaluate(() => { const {base, pos} = window.__mk; window.__status = base({positions: [pos({grid_id: 'x1', symbol: 'SOLUSDT'})]}) })
  await page.clock.runFor(10500); await page.waitForTimeout(100)
  let t = (await toasts()).join(' | '); assert.match(t, /SOL LONG opened/, t)
  const rr = {grid_id: 'x1', symbol: 'SOLUSDT', side: 'LONG', opened_at: 1, closed_at: Date.now() / 1000, target_usdt: 0.4, max_loss_usdt: 2, est_cost_usdt: 0.2, close_reason: 'TARGET', pnl_state: 'PENDING', commission: null, verified_net_pnl: null}
  await page.evaluate(r => { const {base} = window.__mk; window.__status = base({positions: [], recent_results: [r], summary: {closed_count: 1, target_count: 1, stop_count: 0, other_count: 0, win_rate: 1, verified_count: 0, verified_net_pnl: null, commission_total: null, best_pnl: null, worst_pnl: null}}) }, rr)
  await page.clock.runFor(10500); await page.waitForTimeout(100)
  t = (await toasts()).join(' | '); assert.match(t, /SOL hit its target/, t)
  await page.evaluate(() => { const {base, pos} = window.__mk; window.__status = base({state: 'INTERVENTION_REQUIRED', positions: [pos({engine_status: 'BLOCKED'})]}) })
  await page.clock.runFor(10500); await page.waitForTimeout(100)
  t = (await toasts()).join(' | '); assert.match(t, /Attention required/, t)
  assert.deepStrictEqual(errors, []); await ctx.close()
})
await test('no page errors in any scenario', async () => {
  for (const s of ['loading', 'down', 'account', 'disabled', 'onboarding', 'off', 'running_empty', 'running_two', 'five', 'zero_cap', 'arm_expired', 'safety', 'stopping', 'intervention', 'pnl', 'results', 'results_empty']) { console.log('  scenario', s); const p = await open(s); assert.deepStrictEqual(p.errors, [], s); await p.ctx.close() }
})
await browser.close()
console.log(`${n} passed`)

import {expect, test, type Page} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from './helpers/master-trade-preview-data'
test.use({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 844, height: 390}, locale: 'tr-TR'})
test.setTimeout(90000)
const root = (p: Page) => p.locator('.masterReferenceAnalysis')
const terminal = (p: Page) => root(p).locator('.refChart[data-fs="on"]')
const layer = (p: Page) => terminal(p).locator('.sqtLayer')
const NOW = Date.now()
async function prep(page: Page, size = {width: 844, height: 390}, opts: {day?: Record<string, unknown>; expired?: boolean} = {}) {
  const posts: unknown[] = []
  const starts: Array<Record<string, unknown>> = []
  let expired = opts.expired ?? false
  await page.addInitScript(() => {
    try { localStorage.setItem('kais-site-tour:v1:studio-member', `never|${Date.now()}`); for (const id of ['market', 'risk']) localStorage.setItem(`kais-page-tips:v1:studio-member:${id}`, 'seen'); localStorage.setItem('kaistrade-studio-grid-prefs', JSON.stringify({margin: 300, leverage: 5, recycle: true, mode: 'NEUTRAL', preset: 'analysis'})) } catch {}
    let cur: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', {get: () => cur, configurable: true})
    Object.defineProperty(document, 'fullscreenEnabled', {value: true, configurable: true})
    Object.defineProperty(Element.prototype, 'requestFullscreen', {value() { cur = this as Element; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(document, 'exitFullscreen', {value() { cur = null; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(Screen.prototype, 'orientation', {value: {lock: () => Promise.resolve(), unlock() {}, type: 'landscape-primary'}, configurable: true})
  })
  await page.setViewportSize({width: size.height, height: size.width})
  await page.route('https://**', r => r.abort())
  await page.routeWebSocket('wss://**', s => s.close())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockAssistant(page)
  const user = {id: 'studio-member', email: 'member@example.test', display_name: 'Studio Member', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', r => r.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  await page.route('**/api/v25/status', r => r.fulfill({json: {
    connected: true, real_trading_locked: false, armed: !expired, execution_state: 'READY', live_auto_trade: false, recovery_ready: true,
    credentials: {configured: true, fingerprint: 'ABCDEF'}, readiness: {ready: true, gates: [{key: 'risk', passed: true}]}, authorization: {valid: !expired}, consent: {active: !expired}, policy_acknowledged: true,
    policy: {max_margin_per_trade: 300, max_leverage: 50, max_loss_per_trade: 200}, account: {wallet_balance: 1000, available_balance: 900, margin_balance: 1000, unrealized_pnl: 0, positions: [], open_orders: []}, plans: [], events: [],
  }}))
  await page.route('**/api/v25/consent', r => { posts.push(r.request().postDataJSON()); expired = false; return r.fulfill({json: {ok: true}}) })
  await page.route('**/api/exchange-connections/status', r => r.fulfill({json: {connections: {LIVE: {configured: true, active: true, fingerprint: 'ABCDEF'}}}}))
  await page.route('**/api/markets**', r => r.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis-universe**', r => r.fulfill({json: {results: previewMarkets.map((m, i) => ({...m, direction: 'LONG', confidence: 94 - i, final_decision_score: 90 - i}))}}))
  await page.route('**/api/analysis/**', r => r.fulfill({json: previewAnalysis(new URL(r.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', r => r.fulfill({json: previewCandles(new URL(r.request().url()).pathname.split('/').pop(), NOW)}))
  await page.route('**/api/v25/grid/live/status', r => r.fulfill({json: {ok: true, enabled: true, max_levels: 20, max_margin_usdt: 300, max_leverage: 50, grids: [], active_count: 0, grid: null, day: opts.day ?? null}}))
  await page.route('**/api/v25/grid/filters**', r => r.fulfill({json: {ok: true, symbol: 'BTCUSDT', tickSize: 0.1, stepSize: 0.001, minQty: 0.001, minNotional: 5}}))
  await page.route('**/api/v25/grid/live/start', r => { const body = r.request().postDataJSON() as Record<string, unknown>; if (!body.preview) starts.push(body); if (body.preview) return r.fulfill({json: {ok: true, preview: true, plan: {worst_loss_usdt: 12.5, profit_per_cycle_usdt: 0.4}, available_balance: 900}})
    const p0 = previewMarkets.find(m => m.symbol === 'BTCUSDT')!.price
    const row = {grid_id: 'g1', symbol: 'BTCUSDT', side: 'NEUTRAL', status: 'RUNNING', reason: '', position_qty: 0, needs_user: false, loop_running: true, recycle: true, alerts: [], orders: [],
      stats: {cycles: 0, realized_usdt: 0, gross_usdt: 0, fees_usdt: 0, open_levels: []}, spec: {levels: [{entry: p0 * .99, target: p0 * .995}, {entry: p0 * 1.01, target: p0 * 1.005}]}}
    return r.fulfill({json: {ok: true, enabled: true, max_levels: 20, max_margin_usdt: 300, max_leverage: 50, grids: [row], active_count: 1, grid: row, day: opts.day ?? null}}) })
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText(/\d+%/, {timeout: 20000})
  await root(page).locator('.kfsStudioBtn').click()
  await page.setViewportSize(size)
  await expect(terminal(page)).toBeVisible({timeout: 15000})
  const drawer = page.locator('#kfsAnalysisDrawer')
  if (await drawer.isVisible().catch(() => false)) { await drawer.locator('header button').click(); await expect(drawer).toBeHidden() }
  return {posts, starts}
}

const DAY = {grids: 2, cycles: 7, realized_usdt: -4.2, fees_usdt: 0.31, best_usdt: 1.1, worst_usdt: -5.3, limit_usdt: 10, used_usdt: 4.2, left_usdt: 5.8, reached: false}

test('Bugün kartı: net, tur, ücret ve günlük limit çubuğu Çalışan sekmesinde görünür', async ({page}) => {
  await prep(page, {width: 844, height: 390}, {day: DAY})
  await layer(page).locator('.sqgChip').click()
  const grid = layer(page).locator('.sqgPanel')
  await grid.getByRole('tab', {name: /Çalışan/}).click()
  const card = grid.locator('.sqgDay')
  await expect(card).toBeVisible()
  await expect(card).toContainText('4,20')
  await expect(card.locator('[role=progressbar]')).toHaveAttribute('aria-valuenow', '4.2')
  await expect(card).toContainText('5,80')
  await page.screenshot({path: 'test-results/day-card.png'})
})

test('Günlük limit dolunca Yeni sekmesi kilitlenir ve nedeni yazar', async ({page}) => {
  await prep(page, {width: 844, height: 390}, {day: {...DAY, realized_usdt: -10.5, used_usdt: 10.5, left_usdt: 0, reached: true}})
  await layer(page).locator('.sqgChip').click()
  const grid = layer(page).locator('.sqgPanel')
  await expect(grid.locator('.sqgChecks')).toContainText('Günlük zarar limitine ulaşıldı', {timeout: 10000})
  await expect(grid.locator('.sqgFoot .sqtHold')).toBeDisabled()
  await grid.getByRole('tab', {name: /Çalışan/}).click()
  await expect(grid.locator('.sqgDayLimit')).toContainText('Limit doldu')
  await page.screenshot({path: 'test-results/day-reached.png'})
})

test('20x üstü kaldıraç: ek onay işaretlenmeden başlat açılmaz; 20x ve altında onay istenmez', async ({page}) => {
  const {starts} = await prep(page)
  await layer(page).locator('.sqgChip').click()
  const grid = layer(page).locator('.sqgPanel')
  const lev = grid.locator('.sqgLevScroll')
  const hold = grid.locator('.sqgFoot .sqtHold')
  const pick = async (v: string) => { const radio = lev.getByRole('radio', {name: v}); await radio.scrollIntoViewIfNeeded(); await radio.click() }
  await expect(hold).toBeEnabled({timeout: 10000})
  await pick('20x')
  await expect(grid.locator('.sqgAck')).toHaveCount(0)
  await expect(hold).toBeEnabled({timeout: 10000})
  await pick('25x')
  await expect(grid.locator('.sqgAck')).toBeVisible()
  await expect(grid.locator('.sqgAck')).toContainText('%4,0')
  await expect(hold).toBeDisabled()
  await grid.locator('.sqgAck').scrollIntoViewIfNeeded()
  await page.screenshot({path: 'test-results/lev-ack.png'})
  await grid.locator('.sqgAck').click()
  await expect(hold).toBeEnabled()
  await pick('30x')
  await expect(hold).toBeDisabled()
  await expect(grid.locator('.sqgAck input')).not.toBeChecked()
  await pick('25x')
  await expect(hold).toBeDisabled()
  await grid.locator('.sqgAck').click()
  await expect(hold).toBeEnabled()
  // scrollIntoView overflow:hidden katmanı kaydırmış olabilir; gerçek dokunuşta olmaz
  await page.evaluate(() => document.querySelectorAll('.masterReferenceAnalysis *').forEach(e => { if (e.scrollTop && !e.classList.contains('sqgBody')) e.scrollTop = 0 }))
  const box = (await hold.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down(); await page.waitForTimeout(2200); await page.mouse.up()
  await expect.poll(() => starts.length, {timeout: 8000}).toBe(1)
  expect(starts[0]).toMatchObject({leverage: 25, high_leverage_ack: true})
})

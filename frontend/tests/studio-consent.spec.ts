import {expect, test, type Page} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from './helpers/master-trade-preview-data'
test.use({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 844, height: 390}, locale: 'tr-TR'})
test.setTimeout(90000)
const root = (p: Page) => p.locator('.masterReferenceAnalysis')
const terminal = (p: Page) => root(p).locator('.refChart[data-fs="on"]')
const layer = (p: Page) => terminal(p).locator('.sqtLayer')
const NOW = Date.now()
async function prep(page: Page, size = {width: 844, height: 390}) {
  const posts: unknown[] = []
  let expired = true
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
  await page.route('**/api/v25/grid/live/status', r => r.fulfill({json: {ok: true, enabled: true, max_levels: 20, max_margin_usdt: 300, max_leverage: 50, grids: [], active_count: 0, grid: null}}))
  await page.route('**/api/v25/grid/filters**', r => r.fulfill({json: {ok: true, symbol: 'BTCUSDT', tickSize: 0.1, stepSize: 0.001, minQty: 0.001, minNotional: 5}}))
  await page.route('**/api/v25/grid/live/start', r => r.fulfill({json: {ok: true, preview: true, plan: {worst_loss_usdt: 12.5, profit_per_cycle_usdt: 0.4}, available_balance: 900}}))
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText(/\d+%/, {timeout: 20000})
  await root(page).locator('.kfsStudioBtn').click()
  await page.setViewportSize(size)
  await expect(terminal(page)).toBeVisible({timeout: 15000})
  const drawer = page.locator('#kfsAnalysisDrawer')
  if (await drawer.isVisible().catch(() => false)) { await drawer.locator('header button').click(); await expect(drawer).toBeHidden() }
  return posts
}
for (const size of [{width: 844, height: 390}, {width: 667, height: 375}, {width: 932, height: 430}, {width: 1180, height: 700}]) {
  test(`İzin yenile + 50x ${size.width}x${size.height}`, async ({page}) => {
    const posts = await prep(page, size)
    await layer(page).locator('.sqgChip').click()
    const grid = layer(page).locator('.sqgPanel')
    await expect(grid).toBeVisible()
    const open = grid.locator('.sqcOpen')
    await expect(open).toBeVisible({timeout: 10000})
    await open.scrollIntoViewIfNeeded()
    await page.screenshot({path: `test-results/consent-warn-${size.width}.png`})
    // 50x kaydırma
    const lev = grid.locator('.sqgLevScroll')
    await expect(lev.getByRole('radio', {name: '50x'})).toHaveCount(1)
    await lev.getByRole('radio', {name: '50x'}).scrollIntoViewIfNeeded()
    await lev.getByRole('radio', {name: '50x'}).click()
    await expect(lev.getByRole('radio', {name: '50x'})).toHaveAttribute('aria-checked', 'true')
    await page.screenshot({path: `test-results/lev50-${size.width}.png`})
    await open.click()
    const panel = layer(page).locator('.sqcPanel')
    await expect(panel).toBeVisible()
    const box = (await panel.boundingBox())!
    expect(box.y).toBeGreaterThanOrEqual(-0.5); expect(box.y + box.height).toBeLessThanOrEqual(size.height + 0.5)
    expect(box.x).toBeGreaterThanOrEqual(-0.5); expect(box.x + box.width).toBeLessThanOrEqual(size.width + 0.5)
    const go = panel.locator('.sqcGo')
    await expect(go).toBeDisabled()
    await panel.getByRole('radio', {name: /^8/}).click()
    await panel.locator('.sqcCheck').click()
    await expect(go).toBeEnabled()
    await page.screenshot({path: `test-results/consent-panel-${size.width}.png`})
    const gb = (await go.boundingBox())!
    expect(gb.y + gb.height).toBeLessThanOrEqual(size.height + 0.5)
    await go.click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({understood: true, hours: 8})
    await expect(panel).toBeHidden()
  })
}

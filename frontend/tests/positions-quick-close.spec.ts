import {expect, test} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'

// Pozisyon kartı: "Kapat" tuşu kart kapalıyken de görünür; dokununca onay penceresi açılır, emir gitmez.
test.use({baseURL: 'http://127.0.0.1:4174', viewport: {width: 390, height: 844}, locale: 'tr-TR'})
test.setTimeout(60000)

test('Pozisyon kartında hızlı Kapat tuşu görünür ve onay penceresini açar', async ({page}) => {
  const mock = await mockAssistant(page)
  const user = {id: 'live-ui-owner', role: 'OWNER', active: true, email_verified: true}
  const position = {symbol: 'TRUMPUSDT', direction: 'SHORT', position_side: 'BOTH', quantity: -77.45, entry_price: 1.291, mark_price: 1.294, unrealized_pnl: -0.23, leverage: 10, margin_type: 'isolated', liquidation_price: 1.41}
  const plan = {id: 'plan-trump-0001', symbol: 'TRUMPUSDT', direction: 'SHORT', status: 'OPEN', source: 'MANUAL', provenance_state: 'CONFIRMED', stop_loss: 1.315, targets: [1.263, 1.237, 1.212],
    exchange_protection: {stop: true, tp1: true, tp2: true, tp3: true}, opened_at: new Date(Date.now() - 154000).toISOString()}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true}}}))
  await page.route('**/api/exchange-connections/status', route => route.fulfill({json: {connections: {LIVE: {configured: true, active: true, fingerprint: 'ABCDEF'}}}}))
  await page.route('**/api/v25/status', route => route.fulfill({json: {
    connected: true, credentials: {configured: true, fingerprint: 'ABCDEF'}, real_trading_locked: false, armed: true, live_auto_trade: false, execution_state: 'ARMED',
    recovery_ready: true, authorization: {valid: true}, policy_acknowledged: true,
    readiness: {ready: true, gates: [{key: 'risk', passed: true}, {key: 'protection', passed: true}]},
    account: {wallet_balance: 100, available_balance: 90, margin_balance: 100, unrealized_pnl: -0.23, positions: [position], open_orders: []}, plans: [plan], events: [], original_v2_live: {available: true, enabled: false, risk_revision: 1},
  }}))
  await page.addInitScript(() => { try { localStorage.setItem('kais-site-tour:v1:live-ui-owner', `never|${Date.now()}`); for (const id of ['market', 'risk', 'live', 'positions']) localStorage.setItem(`kais-page-tips:v1:live-ui-owner:${id}`, 'seen') } catch { /* yok */ } })
  await page.goto('/master-trade?tab=canli', {waitUntil: 'domcontentloaded'})
  const card = page.locator('.pvCard').first()
  await expect(card).toBeVisible({timeout: 20000})
  const quick = card.locator('.pvQuickClose')
  await expect(quick).toBeVisible()
  await expect(card.locator('.pvQuick')).toContainText('Kapanış ≈')
  await card.scrollIntoViewIfNeeded()
  await page.screenshot({path: 'test-results/positions-quick-close.png'})
  await card.screenshot({path: 'test-results/positions-quick-close-card.png'})
  await quick.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.screenshot({path: 'test-results/positions-quick-close-confirm.png'})
  expect(mock.requests.filter(request => /^(POST|PUT|PATCH|DELETE) \/api\/v25\/(order|position|plan)/.test(request))).toEqual([])
})

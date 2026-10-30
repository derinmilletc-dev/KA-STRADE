import {test, type Page} from '@playwright/test'
import {appendFileSync, mkdirSync} from 'node:fs'
import {mockAssistant} from '../helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from '../helpers/master-trade-preview-data'

test.use({baseURL: 'http://127.0.0.1:4174'})
test.setTimeout(120000)
const log = (line: string) => { mkdirSync('shots-out', {recursive: true}); appendFileSync('shots-out/info.txt', `${line}\n`) }

async function prepare(page: Page, width: number, height: number) {
  await page.setViewportSize({width, height})
  await mockAssistant(page)
  const user = {id: 'shots-owner', email: 'owner@example.test', display_name: 'Owner', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  const opened = new Date(Date.now() - 11 * 60000).toISOString()
  const position = {symbol: 'HYPEUSDT', direction: 'LONG', quantity: 2.9, entry_price: 92.837, mark_price: 92.9895, unrealized_pnl: 0.44, leverage: 3, margin_type: 'ISOLATED', liquidation_price: 62.5547, notional: 269.67}
  const plan = {id: 'plan-hype-0001', symbol: 'HYPEUSDT', status: 'OPEN', source: 'ORIGINAL_V2_LIVE', provenance_state: 'CONFIRMED', exchange_protection: {stop: true, tp1: true, tp3: true}, stop_loss: 88.414, targets: [96.208, 101.404, 105.734], opened_at: opened, initial_risk_usdt: 11}
  const position2 = {symbol: 'SOLUSDT', direction: 'SHORT', quantity: 1.2, entry_price: 146.1, mark_price: 145.07, unrealized_pnl: 1.24, leverage: 3, margin_type: 'ISOLATED', liquidation_price: 190.2}
  const plan2 = {id: 'plan-sol-0002', symbol: 'SOLUSDT', status: 'OPEN', source: 'MANUAL', provenance_state: 'CONFIRMED', exchange_protection: {stop: true, tp1: true, tp2: true, tp3: true}, stop_loss: 147.3, targets: [141.2, 138.4, 135.0], opened_at: new Date(Date.now() - 95 * 60000).toISOString(), initial_risk_usdt: 2.6}
  const live = {connected: true, real_trading_locked: false, armed: true, execution_state: 'ARMED', live_auto_trade: true, recovery_ready: true,
    credentials: {configured: true, fingerprint: 'SHA256:ABCDEF0123'}, readiness: {ready: true, gates: [{key: 'risk', passed: true}]}, authorization: {valid: true}, consent: {active: true}, policy_acknowledged: true,
    auto: {}, scanner: {}, account: {wallet_balance: 412.5, available_balance: 260.1, positions: [position, position2], open_orders: []}, plans: [plan, plan2], events: [], original_v2_live: {available: true, enabled: true, paused: false, risk_percent: 1, daily_loss_percent: 5, risk_revision: 1, daily: {locked: false}, session: {remaining_seconds: 3600}, symbols: ['BTCUSDT', 'HYPEUSDT', 'SOLUSDT'], scanner: {stage: 'WAITING_NEXT_CANDLE', last_cycle_at: new Date().toISOString(), last_scan_at: new Date().toISOString(), scanned_symbol_count: 40, candidate_count: 0}}}
  await page.route('**/api/v25/status', route => route.fulfill({json: live}))
  await page.route('**/api/exchange-connections/status', route => route.fulfill({json: {connections: {LIVE: {configured: true, active: true, fingerprint: 'ABCDEF0123'}}}}))
  await page.route('**/api/markets**', route => route.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis/**', route => route.fulfill({json: previewAnalysis(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.addInitScript(() => { localStorage.setItem('protrebot-v25-session', 'cookie-session:shots-owner'); localStorage.setItem('kais-site-tour:v1:shots-owner', `done|${Date.now()}`); localStorage.setItem('kt-app-ready', `done|${Date.now()}`) })
}

test('manual volatility stop', async ({page}) => {
  page.on('pageerror', error => log(`PAGEERROR ${error.message}`))
  await prepare(page, 1440, 900)
  await page.route('**/api/v25/risk/preview', route => route.fulfill({json: {notional_usdt: 50, margin_usdt: 10, estimated_stop_loss_usdt: 1.2, stop_distance_pct: 2.4}}))
  const dismiss = async () => { for (let i = 0; i < 3; i++) { const b = page.getByRole('button', {name: 'Sonra hatırlat', exact: true}); if (await b.isVisible().catch(() => false)) { await b.click(); await page.waitForTimeout(500) } } }
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await page.waitForTimeout(5000)
  await dismiss()
  await page.locator('.refTradeBtn').first().click()
  await page.waitForTimeout(3000)
  await dismiss()
  for (let i = 0; i < 4; i++) {
    if (await page.getByText('Dalgaya göre').first().isVisible().catch(() => false)) break
    const next = page.getByRole('button', {name: 'Devam →'}).first()
    if (await next.isEnabled().catch(() => false)) { await next.click(); await page.waitForTimeout(1200) } else break
  }
  await page.screenshot({path: 'shots-out/stop-1-once.png'})
  const button = page.getByRole('button', {name: /Dalgaya göre/}).first()
  log(`button visible=${await button.isVisible().catch(() => false)}`)
  await button.click().catch(error => log(`CLICK ${String(error).slice(0, 200)}`))
  await page.waitForTimeout(2500)
  await page.screenshot({path: 'shots-out/stop-2-sonra.png'})
  await page.setViewportSize({width: 390, height: 844})
  await page.waitForTimeout(1500)
  await page.screenshot({path: 'shots-out/stop-3-telefon.png', fullPage: true})
})

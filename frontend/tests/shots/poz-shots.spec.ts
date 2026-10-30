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

for (const [name, width, height] of [['phone', 390, 844], ['desktop', 1440, 900]] as const) {
  test(`positions ${name}`, async ({page}) => {
    page.on('pageerror', error => log(`PAGEERROR ${error.message}`))
    page.on('console', message => { if (message.type() === 'error') log(`CONSOLE ${message.text().slice(0, 600)}`) })
    await prepare(page, width, height)
    await page.goto('/master-trade?tab=canli', {waitUntil: 'domcontentloaded'})
    await page.waitForTimeout(4000)
    for (const label of ['Sonra hatırlat', 'Remind me later']) { const b = page.getByRole('button', {name: label, exact: true}); if (await b.isVisible().catch(() => false)) await b.click() }
    const root = page.locator('.pvRoot').first()
    const count = await root.count()
    log(`${name} pvRoot=${count} slim=${await page.locator('.pvSlim').count()}`)
    const details = page.getByText('Teknik ayrıntılar', {exact: false}).first()
    if (await details.isVisible().catch(() => false)) { await details.click(); log(`RECOVERY ${(await page.locator('body').innerText()).slice(0, 1500)}`) }
    if (!count) { await page.screenshot({path: `shots-out/${name}-page.png`, fullPage: true}); return }
    log(`${name} DIAG ${JSON.stringify(await page.evaluate(() => {
      const out: string[] = []; let el = document.querySelector('.pvRoot') as HTMLElement | null
      while (el && out.length < 8) { const cs = getComputedStyle(el); out.push(`${el.tagName}.${String(el.className).slice(0, 40)} W${Math.round(el.getBoundingClientRect().width)} d:${cs.display} gc:${cs.gridColumn} gtc:${cs.gridTemplateColumns.slice(0, 30)}`); el = el.parentElement }
      const card = document.querySelector('.pvCard') as HTMLElement | null
      if (card) { const cs = getComputedStyle(card); out.push(`CARD bg:${cs.backgroundColor} border:${cs.borderTopColor} ${cs.borderTopWidth}`) }
      return out }))}`)
    await root.scrollIntoViewIfNeeded()
    await page.waitForTimeout(800)
    await root.screenshot({path: `shots-out/${name}-1-kapali.png`})
    await page.locator('.pvSlim').first().click()
    await page.waitForTimeout(800)
    await root.screenshot({path: `shots-out/${name}-2-acik.png`})
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    log(`${name} overflow=${overflow}`)
  })
}

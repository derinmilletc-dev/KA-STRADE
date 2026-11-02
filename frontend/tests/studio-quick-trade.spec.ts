import {expect, test, type Page, type Locator} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from './helpers/master-trade-preview-data'

// Analysis Studio hızlı AL/SAT: yatay telefon. Tüm emir uçları sahte; gerçek istek gitmez.
test.use({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 844, height: 390}, locale: 'tr-TR'})
test.setTimeout(90000)

const root = (page: Page) => page.locator('.masterReferenceAnalysis')
const terminal = (page: Page) => root(page).locator('.refChart[data-fs="on"]')
const layer = (page: Page) => terminal(page).locator('.sqtLayer')
const FIXED_NOW = Date.now()

type Pos = {symbol: string; direction: 'LONG' | 'SHORT'; quantity: number; entry_price: number; mark_price: number; unrealized_pnl: number; leverage: number; position_side: string; stop_loss: number; tp1: number; tp2: number; tp3: number}
type Mock = {positions: Pos[]; plans: Array<Record<string, unknown>>; tests: Array<Record<string, unknown>>; orders: Array<Record<string, unknown>>; closes: Array<Record<string, unknown>>; reduces: Array<Record<string, unknown>>; orderStatus: number; orderBody: unknown; armed: boolean}
const price = (symbol: string) => previewMarkets.find(market => market.symbol === symbol)!.price

const statusJson = (mock: Mock) => ({
  connected: true, real_trading_locked: !mock.armed, armed: mock.armed, execution_state: 'READY', live_auto_trade: false, recovery_ready: true,
  credentials: {configured: true, fingerprint: 'ABCDEF'}, readiness: {ready: true, gates: [{key: 'risk', passed: true}]},
  authorization: {valid: true}, consent: {active: true}, policy_acknowledged: true,
  policy: {max_margin_per_trade: 100, max_leverage: 3},
  account: {wallet_balance: 1000, available_balance: 900, margin_balance: 1000, unrealized_pnl: 0, positions: mock.positions, open_orders: []},
  plans: mock.plans, events: [],
})

function position(symbol: string, direction: 'LONG' | 'SHORT', leverage = 2): Pos {
  const entry = price(symbol), sign = direction === 'LONG' ? 1 : -1
  return {symbol, direction, quantity: 0.01 * sign, entry_price: entry, mark_price: entry, unrealized_pnl: 0.5 * sign, leverage, position_side: 'BOTH',
    stop_loss: entry * (1 - sign * .006), tp1: entry * (1 + sign * .005), tp2: entry * (1 + sign * .01), tp3: entry * (1 + sign * .015)}
}
const planFor = (symbol: string, direction: 'LONG' | 'SHORT', source = 'MANUAL') => ({id: `plan-${symbol}-1`, symbol, direction, status: 'OPEN', source, provenance_state: 'CONFIRMED', stop_loss: 1, targets: [1, 2, 3]})

async function prepare(page: Page, seed: Partial<Mock> = {}, size = {width: 844, height: 390}) {
  const mock: Mock = {positions: [], plans: [], tests: [], orders: [], closes: [], reduces: [], orderStatus: 200, orderBody: null, armed: true, ...seed}
  await page.addInitScript(() => {
    try { localStorage.setItem('kais-site-tour:v1:studio-member', `never|${Date.now()}`); for (const id of ['market', 'risk']) localStorage.setItem(`kais-page-tips:v1:studio-member:${id}`, 'seen') } catch { /* yok */ }
    let current: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', {get: () => current, configurable: true})
    Object.defineProperty(document, 'fullscreenEnabled', {value: true, configurable: true})
    Object.defineProperty(Element.prototype, 'requestFullscreen', {value() { current = this as Element; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(document, 'exitFullscreen', {value() { current = null; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(Screen.prototype, 'orientation', {value: {lock: () => Promise.resolve(), unlock() {}, type: 'landscape-primary'}, configurable: true})
  })
  await page.setViewportSize(size.width >= 1024 ? size : {width: size.height, height: size.width}) // telefon önce dik; masaüstü doğrudan
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('wss://**', socket => socket.close())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockAssistant(page)
  const user = {id: 'studio-member', email: 'member@example.test', display_name: 'Studio Member', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  await page.route('**/api/v25/status', route => route.fulfill({json: statusJson(mock)}))
  await page.route('**/api/exchange-connections/status', route => route.fulfill({json: {connections: {LIVE: {configured: true, active: true, fingerprint: 'ABCDEF'}}}}))
  await page.route('**/api/markets**', route => route.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis-universe**', route => route.fulfill({json: {results: previewMarkets.map((market, index) => ({...market, direction: 'LONG', confidence: 94 - index, final_decision_score: 94 - index}))}}))
  await page.route('**/api/analysis/**', route => route.fulfill({json: previewAnalysis(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop(), FIXED_NOW)}))
  await page.route('**/api/v25/order/test', route => { mock.tests.push(route.request().postDataJSON()); return route.fulfill({json: {ok: true, message: 'ok'}}) })
  await page.route('**/api/v25/order', route => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    mock.orders.push(body)
    if (mock.orderStatus !== 200) return route.fulfill({status: mock.orderStatus, json: mock.orderBody ?? {detail: 'Reddedildi.'}})
    const symbol = String(body.symbol), direction = body.direction as 'LONG' | 'SHORT'
    mock.positions.push(position(symbol, direction, Number(body.leverage))); mock.plans.push(planFor(symbol, direction))
    return route.fulfill({json: {ok: true, plan: {id: `plan-${symbol}-1`}}})
  })
  await page.route('**/api/v25/position/close', route => {
    const body = route.request().postDataJSON() as {plan_id: string}
    mock.closes.push(body)
    const plan = mock.plans.find(item => item.id === body.plan_id)
    mock.positions = mock.positions.filter(item => item.symbol !== plan?.symbol); mock.plans = mock.plans.filter(item => item !== plan)
    return route.fulfill({json: {ok: true}})
  })
  await page.route('**/api/v25/position/reduce', route => {
    const body = route.request().postDataJSON() as {plan_id: string}
    mock.reduces.push(body)
    const plan = mock.plans.find(item => item.id === body.plan_id)
    const pos = mock.positions.find(item => item.symbol === plan?.symbol)
    if (pos) pos.quantity = pos.quantity / 2
    return route.fulfill({json: {ok: true}})
  })
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText(/\d+%/, {timeout: 20000})
  await root(page).locator('.kfsStudioBtn').click()
  await page.setViewportSize(size) // telefonu yan çevir
  await expect(terminal(page)).toBeVisible({timeout: 15000})
  const drawer = page.locator('#kfsAnalysisDrawer')
  if (await drawer.isVisible().catch(() => false)) { await drawer.locator('header button').click(); await expect(drawer).toBeHidden() }
  return mock
}

const hold = async (page: Page, button: Locator, ms: number) => {
  const box = (await button.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(ms)
  await page.mouse.up()
}
const overlaps = (a: {x: number; y: number; width: number; height: number}, b: {x: number; y: number; width: number; height: number}) =>
  a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1
const boxOf = async (locator: Locator) => { const box = await locator.boundingBox(); expect(box).not.toBeNull(); return box! }

test('Dock: AL ve SAT görünür, yalnız açık işlemi olmayan coinde; hiçbir şeyin üstüne binmez', async ({page}) => {
  await prepare(page)
  const dock = layer(page).locator('.sqtDock')
  await expect(dock).toBeVisible()
  await expect(dock.getByRole('button', {name: /AL \(LONG\)/})).toBeEnabled()
  await expect(dock.getByRole('button', {name: /SAT \(SHORT\)/})).toBeEnabled()
  const size = page.viewportSize()!
  const d = await boxOf(dock)
  expect(d.x).toBeGreaterThanOrEqual(0); expect(d.x + d.width).toBeLessThanOrEqual(size.width); expect(d.y + d.height).toBeLessThanOrEqual(size.height)
  const rail = await boxOf(terminal(page).locator('.kfsRail'))
  expect(overlaps(d, rail)).toBe(false)
  const toolbar = await boxOf(terminal(page).locator('.refToolbar'))
  expect(overlaps(d, toolbar)).toBe(false)
  await page.screenshot({path: 'test-results/studio-dock.png'})
})

test('AL: onay kartı açılır, kısa basış emir göndermez, basılı tutunca dry-run + emir doğru gövdeyle gider', async ({page}) => {
  const mock = await prepare(page)
  await layer(page).getByRole('button', {name: /AL \(LONG\)/}).click()
  const sheet = layer(page).locator('.sqtSheet')
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('CANLI')
  await expect(sheet).toContainText('Stop olursa')
  const size = page.viewportSize()!
  const s = await boxOf(sheet)
  expect(s.x).toBeGreaterThanOrEqual(0); expect(s.x + s.width).toBeLessThanOrEqual(size.width); expect(s.y).toBeGreaterThanOrEqual(0); expect(s.y + s.height).toBeLessThanOrEqual(size.height)
  await page.screenshot({path: 'test-results/studio-sheet-al.png'})
  const holdButton = sheet.locator('.sqtHold')
  await hold(page, holdButton, 300) // kısa: gitmemeli
  await page.waitForTimeout(300)
  expect(mock.tests).toHaveLength(0); expect(mock.orders).toHaveLength(0)
  await hold(page, holdButton, 1700)
  await expect.poll(() => mock.orders.length, {timeout: 8000}).toBe(1)
  expect(mock.tests).toHaveLength(1)
  const order = mock.orders[0] as Record<string, number | string | null>
  expect(order).toMatchObject({symbol: 'BTCUSDT', direction: 'LONG', order_type: 'MARKET', confirmation: 'CANLI EMİR GÖNDER', tp_split: '60-20-20', margin_usdt: 25, leverage: 2})
  expect(String(order.intent_id).length).toBeGreaterThanOrEqual(8)
  expect(Number(order.stop_loss)).toBeLessThan(price('BTCUSDT')); expect(Number(order.tp1)).toBeGreaterThan(price('BTCUSDT'))
  expect(Number(order.tp1)).toBeLessThan(Number(order.tp2)); expect(Number(order.tp2)).toBeLessThan(Number(order.tp3))
  await expect(sheet).toBeHidden()
  // Pozisyon yenilenince dock gider, çip gelir
  await expect(layer(page).locator('.sqtChip')).toBeVisible({timeout: 15000})
  await expect(layer(page).locator('.sqtDock')).toHaveCount(0)
  await page.screenshot({path: 'test-results/studio-chip.png'})
})

test('SAT (analiz LONG iken): uyarı çıkar, SHORT için stop yukarıda hedefler aşağıda gider', async ({page}) => {
  const mock = await prepare(page)
  await layer(page).getByRole('button', {name: /SAT \(SHORT\)/}).click()
  const sheet = layer(page).locator('.sqtSheet')
  await expect(sheet.locator('.sqtWarn')).toContainText('Analiz LONG diyor')
  await page.screenshot({path: 'test-results/studio-sheet-sat.png'})
  await hold(page, sheet.locator('.sqtHold'), 1700)
  await expect.poll(() => mock.orders.length, {timeout: 8000}).toBe(1)
  const order = mock.orders[0] as Record<string, number | string>
  expect(order.direction).toBe('SHORT')
  expect(Number(order.stop_loss)).toBeGreaterThan(price('BTCUSDT'))
  expect(Number(order.tp1)).toBeLessThan(price('BTCUSDT'))
  expect(Number(order.tp1)).toBeGreaterThan(Number(order.tp2)); expect(Number(order.tp2)).toBeGreaterThan(Number(order.tp3))
})

test('Reddedilen emir: hata kartta gösterilir, kart açık kalır, pozisyon oluşmaz', async ({page}) => {
  const mock = await prepare(page, {orderStatus: 422, orderBody: {detail: 'Stop mesafesi risk sınırını aşıyor.'}})
  await layer(page).getByRole('button', {name: /AL \(LONG\)/}).click()
  const sheet = layer(page).locator('.sqtSheet')
  await hold(page, sheet.locator('.sqtHold'), 1700)
  await expect(sheet.locator('.sqtError')).toContainText('Stop mesafesi risk sınırını aşıyor.')
  await expect(sheet).toBeVisible()
  expect(mock.positions).toHaveLength(0)
  await sheet.getByRole('button', {name: 'Vazgeç', exact: true}).last().click()
  await expect(sheet).toBeHidden()
})

test('Belirsiz sonuç (sunucu 502): emir tekrar gönderilmez, uyarı çıkar, dock kısa süre kilitli', async ({page}) => {
  const mock = await prepare(page, {orderStatus: 502, orderBody: {detail: 'Belirsiz yürütme.'}})
  await layer(page).getByRole('button', {name: /AL \(LONG\)/}).click()
  await hold(page, layer(page).locator('.sqtSheet .sqtHold'), 1700)
  await expect(layer(page).locator('.sqtToast')).toContainText('doğrulanamadı')
  await expect(layer(page).locator('.sqtDock .sqtSide').first()).toBeDisabled()
  expect(mock.orders).toHaveLength(1)
})

test('Canlı kilitliyken (ARM kapalı) kart nedenini söyler ve basılı tut kapalı kalır', async ({page}) => {
  const mock = await prepare(page, {armed: false})
  await layer(page).getByRole('button', {name: /AL \(LONG\)/}).click()
  const sheet = layer(page).locator('.sqtSheet')
  await expect(sheet.locator('.sqtChecks [data-ok="false"]')).toBeVisible()
  await expect(sheet.locator('.sqtHold')).toBeDisabled()
  expect(mock.orders).toHaveLength(0)
})

test('Esc önce kartı kapatır, tam ekranı kapatmaz', async ({page}) => {
  await prepare(page)
  await layer(page).getByRole('button', {name: /AL \(LONG\)/}).click()
  await expect(layer(page).locator('.sqtSheet')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(layer(page).locator('.sqtSheet')).toBeHidden()
  await expect(terminal(page)).toBeVisible()
})

test('Açık işlemler: sekmeler yalnız açık işlem varken görünür, dokununca coin değişir; çip/liste/kapatma çalışır', async ({page}) => {
  const mock = await prepare(page, {positions: [position('BTCUSDT', 'LONG'), position('ETHUSDT', 'SHORT', 3)], plans: [planFor('BTCUSDT', 'LONG'), planFor('ETHUSDT', 'SHORT')]})
  const tabs = terminal(page).locator('.sqtTabs')
  await expect(tabs).toBeVisible({timeout: 15000})
  await expect(tabs.getByRole('tab')).toHaveCount(2)
  await expect(tabs.getByRole('tab', {name: /BTC/})).toHaveAttribute('aria-selected', 'true')
  // OHLC satırı ve sekmeler üst üste binmez
  const ohlc = await boxOf(terminal(page).locator('.refOhlc'))
  const t = await boxOf(tabs)
  expect(t.x + t.width).toBeLessThanOrEqual(ohlc.x + ohlc.width + 1)
  await page.screenshot({path: 'test-results/studio-tabs.png'})
  // Dock yok (BTC'de açık işlem var), çip var
  await expect(layer(page).locator('.sqtDock')).toHaveCount(0)
  const chip = layer(page).locator('.sqtChip')
  await expect(chip).toBeVisible()
  // Kart: yarısını kapat → onay → reduce
  await chip.click()
  const card = layer(page).locator('.sqtCard')
  await expect(card).toBeVisible()
  await page.screenshot({path: 'test-results/studio-card.png'})
  await card.getByRole('button', {name: 'Yarısını kapat'}).click()
  await card.getByRole('button', {name: 'Evet, kapat'}).click()
  await expect.poll(() => mock.reduces.length).toBe(1)
  expect(mock.reduces[0]).toMatchObject({plan_id: 'plan-BTCUSDT-1', percent: 50, confirmation: 'KISMİ KAPAT'})
  // Liste (Tümü) → ETH'e geç
  await card.getByRole('button', {name: 'Tümü'}).click()
  const tray = layer(page).locator('.sqtTray')
  await expect(tray).toBeVisible()
  await page.screenshot({path: 'test-results/studio-tray.png'})
  await tray.getByRole('button', {name: /ETHUSDT/}).click()
  await expect(terminal(page).locator('.kfsSymbolBtn')).toContainText('ETHUSDT')
  await expect(tabs.getByRole('tab', {name: /ETH/})).toHaveAttribute('aria-selected', 'true')
  // ETH'de kapat (basılı tut)
  if (!(await layer(page).locator('.sqtCard').isVisible())) await layer(page).locator('.sqtChip').click()
  await hold(page, layer(page).locator('.sqtCard .sqtHold'), 1700)
  await expect.poll(() => mock.closes.length).toBe(1)
  expect(mock.closes[0]).toMatchObject({plan_id: 'plan-ETHUSDT-1', confirmation: 'CANLI POZİSYONU KAPAT'})
})

test('Başka coine bakarken: açık işlem varsa sekmeler görünür ve AL/SAT çubuğu da vardır', async ({page}) => {
  await prepare(page, {positions: [position('BTCUSDT', 'LONG')], plans: [planFor('BTCUSDT', 'LONG')]})
  await terminal(page).locator('.kfsSymbolBtn').click()
  await page.getByRole('dialog').getByText('ETH/USDT', {exact: true}).first().click()
  await expect(terminal(page).locator('.kfsSymbolBtn')).toContainText('ETHUSDT')
  await expect(terminal(page).locator('.sqtTabs').getByRole('tab', {name: /BTC/})).toBeVisible()
  await expect(layer(page).locator('.sqtDock')).toBeVisible()
  await expect(layer(page).locator('.sqtChip')).toHaveCount(0)
})

for (const size of [{width: 667, height: 375}, {width: 740, height: 360}, {width: 932, height: 430}, {width: 1280, height: 720}]) {
  test.describe(`${size.width}`, () => {
    if (size.width >= 1024) test.use({isMobile: false, hasTouch: false})
  test(`Yerleşim ${size.width}×${size.height}: kart, dock, sekmeler ekran içinde ve çakışmadan`, async ({page}) => {
    await prepare(page, {positions: [position('BTCUSDT', 'LONG'), position('ETHUSDT', 'SHORT'), position('SOLUSDT', 'LONG')], plans: [planFor('BTCUSDT', 'LONG'), planFor('ETHUSDT', 'SHORT'), planFor('SOLUSDT', 'LONG')]}, size)
    const tabs = terminal(page).locator('.sqtTabs')
    await expect(tabs).toBeVisible({timeout: 15000})
    const ohlc = await boxOf(terminal(page).locator('.refOhlc'))
    const t = await boxOf(tabs)
    expect(t.x + t.width).toBeLessThanOrEqual(ohlc.x + ohlc.width + 1)
    const chip = layer(page).locator('.sqtChip')
    await chip.click()
    const card = layer(page).locator('.sqtCard')
    const c = await boxOf(card)
    const rail = await boxOf(terminal(page).locator('.kfsRail'))
    expect(c.x).toBeGreaterThanOrEqual(0); expect(c.y).toBeGreaterThanOrEqual(0)
    expect(c.x + c.width).toBeLessThanOrEqual(size.width); expect(c.y + c.height).toBeLessThanOrEqual(size.height)
    expect(overlaps(c, rail)).toBe(false)
    await card.getByRole('button', {name: 'Tümü'}).click()
    const tray = await boxOf(layer(page).locator('.sqtTray'))
    expect(tray.x).toBeGreaterThanOrEqual(0); expect(tray.y).toBeGreaterThanOrEqual(0); expect(tray.x + tray.width).toBeLessThanOrEqual(size.width)
    await page.screenshot({path: `test-results/studio-layout-${size.width}x${size.height}.png`})
  })
  })
}

test('İngilizce: AL/SAT yerine BUY/SELL, onay kartı İngilizce', async ({browser}) => {
  const context = await browser.newContext({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 844, height: 390}, locale: 'en-US'})
  const page = await context.newPage()
  await prepare(page)
  await expect(layer(page).getByRole('button', {name: /BUY \(LONG\)/})).toBeVisible()
  await layer(page).getByRole('button', {name: /SELL \(SHORT\)/}).click()
  await expect(layer(page).locator('.sqtSheet')).toContainText('If stopped out')
  await expect(layer(page).locator('.sqtSheet')).toContainText('Hold · SELL')
  await context.close()
})

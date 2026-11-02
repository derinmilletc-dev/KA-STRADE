import {expect, test, type Page, type Locator} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from './helpers/master-trade-preview-data'

// Analysis Studio grid paneli: yatay telefon. Tüm grid uçları sahte; gerçek istek gitmez.
test.use({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 844, height: 390}, locale: 'tr-TR'})
test.setTimeout(90000)

const root = (page: Page) => page.locator('.masterReferenceAnalysis')
const terminal = (page: Page) => root(page).locator('.refChart[data-fs="on"]')
const layer = (page: Page) => terminal(page).locator('.sqtLayer')
const panel = (page: Page) => layer(page).locator('.sqgPanel')
const FIXED_NOW = Date.now()
const price = (symbol: string) => previewMarkets.find(market => market.symbol === symbol)!.price

type Row = Record<string, unknown>
type Mock = {positions: Array<Record<string, unknown>>; starts: Array<Record<string, unknown>>; stops: string[]; emergencies: string[]; grids: Row[]; startStatus: number; startBody: unknown; enabled: boolean; previews: Array<Record<string, unknown>>; previewStatus: number; previewBody: unknown}

const gridRow = (id: string, symbol: string, side: string, extra: Row = {}): Row => ({
  grid_id: id, symbol, side, status: 'RUNNING', reason: '', position_qty: 0, needs_user: false, loop_running: true, recycle: true, alerts: [], orders: [],
  stats: {cycles: 3, realized_usdt: 0.42, gross_usdt: 0.5, fees_usdt: 0.08, open_levels: [{level: 2, cycle: 1, entry: price(symbol) * .995, qty: 0.001}]},
  spec: {levels: [{entry: price(symbol) * .99, target: price(symbol) * .995}, {entry: price(symbol) * 1.01, target: price(symbol) * 1.005}]}, ...extra,
})

async function prepare(page: Page, seed: Partial<Mock> = {}, size = {width: 844, height: 390}) {
  const mock: Mock = {positions: [], starts: [], stops: [], emergencies: [], grids: [], startStatus: 200, startBody: null, enabled: true, previews: [], previewStatus: 200, previewBody: null, ...seed}
  await page.addInitScript(() => {
    try { localStorage.setItem('kais-site-tour:v1:studio-member', `never|${Date.now()}`); for (const id of ['market', 'risk']) localStorage.setItem(`kais-page-tips:v1:studio-member:${id}`, 'seen'); localStorage.setItem('kaistrade-studio-grid-prefs', JSON.stringify({margin: 300, leverage: 5, recycle: true, mode: 'NEUTRAL', preset: 'analysis'})) } catch { /* yok */ }
    let current: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', {get: () => current, configurable: true})
    Object.defineProperty(document, 'fullscreenEnabled', {value: true, configurable: true})
    Object.defineProperty(Element.prototype, 'requestFullscreen', {value() { current = this as Element; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(document, 'exitFullscreen', {value() { current = null; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(Screen.prototype, 'orientation', {value: {lock: () => Promise.resolve(), unlock() {}, type: 'landscape-primary'}, configurable: true})
  })
  await page.setViewportSize(size.width >= 1024 ? size : {width: size.height, height: size.width})
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('wss://**', socket => socket.close())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockAssistant(page)
  const user = {id: 'studio-member', email: 'member@example.test', display_name: 'Studio Member', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  await page.route('**/api/v25/status', route => route.fulfill({json: {
    connected: true, real_trading_locked: false, armed: true, execution_state: 'READY', live_auto_trade: false, recovery_ready: true,
    credentials: {configured: true, fingerprint: 'ABCDEF'}, readiness: {ready: true, gates: [{key: 'risk', passed: true}]}, authorization: {valid: true}, consent: {active: true}, policy_acknowledged: true,
    policy: {max_margin_per_trade: 300, max_leverage: 10, max_loss_per_trade: 200}, account: {wallet_balance: 1000, available_balance: 900, margin_balance: 1000, unrealized_pnl: 0, positions: mock.positions, open_orders: []}, plans: [], events: [],
  }}))
  await page.route('**/api/exchange-connections/status', route => route.fulfill({json: {connections: {LIVE: {configured: true, active: true, fingerprint: 'ABCDEF'}}}}))
  await page.route('**/api/markets**', route => route.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis-universe**', route => route.fulfill({json: {results: previewMarkets.map((market, index) => ({...market, direction: 'LONG', confidence: 94 - index, final_decision_score: 90 - index}))}}))
  await page.route('**/api/analysis/**', route => route.fulfill({json: previewAnalysis(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop(), FIXED_NOW)}))
  const statusBody = () => ({ok: true, enabled: mock.enabled, max_levels: 20, max_margin_usdt: 300, max_leverage: 20, grids: mock.grids, active_count: mock.grids.filter(g => g.status === 'RUNNING').length, grid: mock.grids[0] ?? null})
  await page.route('**/api/v25/grid/live/status', route => route.fulfill({json: statusBody()}))
  await page.route('**/api/v25/grid/filters**', route => route.fulfill({json: {ok: true, symbol: 'BTCUSDT', tickSize: 0.1, stepSize: 0.001, minQty: 0.001, minNotional: 5}}))
  await page.route('**/api/v25/grid/live/start', route => {
    const body = route.request().postDataJSON() as Row
    if (body.preview) {
      mock.previews.push(body)
      if (mock.previewStatus !== 200) return route.fulfill({status: mock.previewStatus, json: mock.previewBody ?? {detail: 'Plan reddedildi.'}})
      return route.fulfill({json: {ok: true, preview: true, plan: {worst_loss_usdt: 12.5, profit_per_cycle_usdt: 0.4}, available_balance: 900}})
    }
    mock.starts.push(body)
    if (mock.startStatus !== 200) return route.fulfill({status: mock.startStatus, json: mock.startBody ?? {detail: 'Grid reddedildi.'}})
    mock.grids.unshift(gridRow(`g${mock.starts.length}`, String(body.symbol), String(body.direction), {stats: {cycles: 0, realized_usdt: 0, gross_usdt: 0, fees_usdt: 0, open_levels: []}}))
    return route.fulfill({json: {ok: true, ...statusBody()}})
  })
  await page.route('**/api/v25/grid/live/stop**', route => { const id = new URL(route.request().url()).searchParams.get('grid_id') ?? ''; mock.stops.push(id); const g = mock.grids.find(x => x.grid_id === id); if (g) g.status = 'STOPPED'; return route.fulfill({json: {ok: true, ...statusBody()}}) })
  await page.route('**/api/v25/grid/live/emergency**', route => { const id = new URL(route.request().url()).searchParams.get('grid_id') ?? ''; mock.emergencies.push(id); const g = mock.grids.find(x => x.grid_id === id); if (g) g.status = 'EMERGENCY_CLOSED'; return route.fulfill({json: {ok: true, ...statusBody()}}) })
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText(/\d+%/, {timeout: 20000})
  await root(page).locator('.kfsStudioBtn').click()
  await page.setViewportSize(size)
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
const inside = (box: {x: number; y: number; width: number; height: number}, size: {width: number; height: number}) => {
  expect(box.x).toBeGreaterThanOrEqual(-0.5); expect(box.y).toBeGreaterThanOrEqual(-0.5)
  expect(box.x + box.width).toBeLessThanOrEqual(size.width + 0.5); expect(box.y + box.height).toBeLessThanOrEqual(size.height + 0.5)
}
const num = async (locator: Locator) => Number((await locator.inputValue()).replace(',', '.'))
const ready = async (page: Page) => {
  await layer(page).locator('.sqgChip').click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('#sqg-upper')).toBeVisible()
  await expect(panel(page).locator('.sqgFoot .sqtHold')).toBeEnabled({timeout: 10000})
}

test('Çip: sol altta, AL çubuğu ve araç çubuklarıyla çakışmaz; panel ekran içinde açılır ve Esc kapatır', async ({page}) => {
  await prepare(page)
  const chip = layer(page).locator('.sqgChip')
  await expect(chip).toBeVisible()
  const size = page.viewportSize()!
  const c = await boxOf(chip)
  inside(c, size)
  expect(overlaps(c, await boxOf(layer(page).locator('.sqtDock')))).toBe(false)
  expect(overlaps(c, await boxOf(terminal(page).locator('.kfsRail')))).toBe(false)
  await ready(page)
  inside(await boxOf(panel(page)), size)
  await expect(layer(page).locator('.sqtDock')).toBeHidden()
  await page.screenshot({path: 'test-results/grid-panel.png'})
  await page.keyboard.press('Escape')
  await expect(panel(page)).toBeHidden()
  await expect(layer(page).locator('.sqtDock')).toBeVisible()
})

test('Nötr öneri: fiyat aralığın içinde, stoplar dışında; kısa basış başlatmaz, basılı tutmak doğru gövdeyi gönderir', async ({page}) => {
  const mock = await prepare(page)
  await ready(page)
  await expect(panel(page).locator('#sqg-upper')).toHaveValue(/^\d+([.,]\d)?$/)   // fiyat adımına (0,1) oturmuş değer; kurallar gelmeden okunmasın
  const p = price('BTCUSDT')
  const upper = await num(panel(page).locator('#sqg-upper')), lower = await num(panel(page).locator('#sqg-lower'))
  const stopUp = await num(panel(page).locator('#sqg-stop-up')), stop = await num(panel(page).locator('#sqg-stop'))
  expect(lower).toBeLessThan(p); expect(upper).toBeGreaterThan(p); expect(stop).toBeLessThan(lower); expect(stopUp).toBeGreaterThan(upper)
  await expect(panel(page).locator('.sqgStats').first()).toContainText('USDT')
  const start = panel(page).locator('.sqgFoot .sqtHold')
  await hold(page, start, 300)
  await page.waitForTimeout(300)
  expect(mock.starts).toHaveLength(0)
  await hold(page, start, 2000)
  await expect.poll(() => mock.starts.length, {timeout: 8000}).toBe(1)
  expect(mock.starts[0]).toMatchObject({symbol: 'BTCUSDT', direction: 'NEUTRAL', confirmation: 'CANLI GRID BAŞLAT', recycle: true, stop_upper: stopUp, stop, lower, upper})
  await expect(panel(page).locator('.sqgCard')).toHaveCount(1)
  await expect(panel(page).locator('.sqgCard')).toContainText('BTCUSDT')
  await page.screenshot({path: 'test-results/grid-running.png'})
})

test('Haritadaki tutamak sürüklenir ve klavyeyle oynar; değer alanı güncellenir, genişlik "Özel" olur', async ({page}) => {
  await prepare(page)
  await ready(page)
  const input = panel(page).locator('#sqg-upper')
  const before = await num(input)
  const handle = panel(page).locator('.sqgHandle', {hasText: 'Üst'}).first()
  await handle.scrollIntoViewIfNeeded()
  const box = await boxOf(handle)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 14, {steps: 6})
  await page.mouse.up()
  const after = await num(input)
  expect(after).toBeGreaterThan(before)
  await expect(panel(page).locator('.sqgCustom')).toBeVisible()
  await handle.focus()
  await page.keyboard.press('ArrowDown')
  await expect.poll(() => num(input)).toBeLessThan(after)
  await page.screenshot({path: 'test-results/grid-drag.png'})
})

test('Geçersiz giriş engellenir: alt > üst, fiyat dışı aralık ve çok küçük kademe; başlat kapalı kalır', async ({page}) => {
  const mock = await prepare(page)
  await ready(page)
  const start = panel(page).locator('.sqgFoot .sqtHold')
  await panel(page).locator('#sqg-lower').fill(String(price('BTCUSDT') * 2)); await panel(page).locator('#sqg-lower').blur()
  await expect(panel(page).locator('.sqgChecks li[data-ok="false"]').first()).toBeVisible()
  await expect(start).toBeDisabled()
  await panel(page).locator('.sqgPresets button').first().click() // öneri geri gelir
  await expect(start).toBeEnabled()
  await panel(page).locator('.sqgSeg button[data-mode="LONG"]').click()
  const stop = await num(panel(page).locator('#sqg-stop')), lower = await num(panel(page).locator('#sqg-lower')), upper = await num(panel(page).locator('#sqg-upper'))
  expect(stop).toBeLessThan(lower); expect(upper).toBeLessThan(price('BTCUSDT'))
  await expect(panel(page).locator('#sqg-stop-up')).toHaveCount(0)
  await panel(page).locator('#sqg-stop').fill(String(lower + 5)); await panel(page).locator('#sqg-stop').blur()
  await expect(start).toBeDisabled()
  expect(mock.starts).toHaveLength(0)
})

test('Sunucu reddederse hata görünür, grid oluşmaz; sonra düzelince başlar', async ({page}) => {
  const mock = await prepare(page, {startStatus: 409, startBody: {detail: 'En kötü zarar 9.00 USDT, tek işlem sınırı 3 USDT.'}})
  await ready(page)
  await hold(page, panel(page).locator('.sqgFoot .sqtHold'), 2000)
  await expect(panel(page).locator('.sqgError')).toContainText('tek işlem sınırı')
  await expect(panel(page).locator('.sqgTabs')).toContainText('Çalışan')
  expect(mock.grids).toHaveLength(0)
})

test('Çalışan grid: kart değerleri, Durdur iki adımlı, Acil kapat basılı tutulur ve grid_id gider', async ({page}) => {
  const mock = await prepare(page, {grids: [gridRow('ga', 'BTCUSDT', 'NEUTRAL'), gridRow('gb', 'ETHUSDT', 'LONG')]})
  await expect(layer(page).locator('.sqgChip')).toContainText('2')
  await layer(page).locator('.sqgChip').click()
  await panel(page).locator('.sqgTabs button').nth(1).click()
  const cards = panel(page).locator('.sqgCard')
  await expect(cards).toHaveCount(2)
  await expect(cards.first()).toContainText('Tur'); await expect(cards.first()).toContainText('3')
  await page.screenshot({path: 'test-results/grid-cards.png'})
  await cards.first().getByRole('button', {name: /^Durdur$/}).click()
  expect(mock.stops).toHaveLength(0)
  await cards.first().locator('.sqgAsk .sqtBtn').first().click()
  await expect.poll(() => mock.stops).toEqual(['ga'])
  const second = panel(page).locator('.sqgCard[data-tone="ok"]').first()
  await hold(page, second.locator('.sqtHold'), 2000)
  await expect.poll(() => mock.emergencies).toEqual(['gb'])
})

test('Açık pozisyon varsa o coinde grid başlatılamaz ve neden yazar', async ({page}) => {
  const entry = price('BTCUSDT')
  await prepare(page, {positions: [{symbol: 'BTCUSDT', direction: 'LONG', quantity: 0.01, entry_price: entry, mark_price: entry, unrealized_pnl: 0, leverage: 2, position_side: 'BOTH'}]})
  await layer(page).locator('.sqgChip').click()
  await expect(panel(page).locator('.sqgChecks')).toContainText('açık pozisyon')
  await expect(panel(page).locator('.sqgFoot .sqtHold')).toBeDisabled()
})

test('Canlı grid kapalıysa (bayrak) panel nedenini yazar ve başlatmaz', async ({page}) => {
  const mock = await prepare(page, {enabled: false})
  await layer(page).locator('.sqgChip').click()
  await expect(panel(page).locator('.sqgChecks')).toContainText('henüz açılmadı')
  await expect(panel(page).locator('.sqgFoot .sqtHold')).toBeDisabled()
  expect(mock.starts).toHaveLength(0)
})

test('Geçmişe göre deneme: kart görünür, tur ve net sonuç yazar; Oynaklık önayarı aralığı değiştirir', async ({page}) => {
  await prepare(page)
  await ready(page)
  const back = panel(page).locator('.sqgBack')
  await back.scrollIntoViewIfNeeded()
  await expect(back).toContainText('Geçmişte nasıl çalışırdı')
  await expect(back).toContainText('Tamamlanan tur')
  await expect(back).toContainText('En büyük düşüş')
  const before = await num(panel(page).locator('#sqg-upper'))
  await panel(page).locator('.sqgPresets button', {hasText: 'Oynaklık'}).click()
  await expect(panel(page).locator('.sqgPresets button', {hasText: 'Oynaklık'})).toHaveAttribute('aria-checked', 'true')
  await expect(panel(page).locator('.sqgNote')).toContainText(/ATR|yeterli mum/)
  await expect.poll(() => num(panel(page).locator('#sqg-upper'))).not.toBe(before)
})

test('Grafikte grid çizgileri: panel açıkken önizleme çizgileri, kapanınca kalkar', async ({page}) => {
  await prepare(page)
  const overlay = terminal(page).locator('.refGridOverlay')
  await expect(overlay).toHaveCount(0)
  await ready(page)
  await expect.poll(() => terminal(page).locator('.refGridOverlay line').count()).toBeGreaterThan(5)
  await expect(terminal(page).locator('.refGridOverlay [data-kind="stop"]').first()).toBeAttached()
  await page.screenshot({path: 'test-results/grid-chart-lines.png'})
  await page.keyboard.press('Escape')
  await expect(overlay).toHaveCount(0)
})

test('Çalışan gridin kademeleri panel kapalıyken de grafikte görünür', async ({page}) => {
  await prepare(page, {grids: [gridRow('ga', 'BTCUSDT', 'NEUTRAL')]})
  await expect.poll(() => terminal(page).locator('.refGridOverlay line').count()).toBeGreaterThan(0)
})

test('Fiyat aralığın dışındaysa uyarı çıkar; açık kademe yoksa "Durdur ve yeniden kur" gridi durdurup yeni öneriyi açar', async ({page}) => {
  const p = price('BTCUSDT')
  const far = {spec: {levels: [{entry: p * .9, target: p * .91}, {entry: p * .92, target: p * .93}]}, stats: {cycles: 1, realized_usdt: 0.1, gross_usdt: 0.12, fees_usdt: 0.02, open_levels: []}}
  const mock = await prepare(page, {grids: [gridRow('gx', 'BTCUSDT', 'NEUTRAL', far)]})
  await layer(page).locator('.sqgChip').click()
  await panel(page).locator('.sqgTabs button').nth(1).click()
  const card = panel(page).locator('.sqgCard')
  await expect(card.locator('.sqgRange')).toContainText('aralığının')
  await card.getByRole('button', {name: /Durdur ve yeniden kur/}).click()
  expect(mock.stops).toHaveLength(0)
  await card.locator('.sqgRange .sqtBtn').first().click()
  await expect.poll(() => mock.stops).toEqual(['gx'])
  await expect(panel(page).locator('#sqg-upper')).toBeVisible({timeout: 10000})
})

test('Fiyat aralık dışındayken açık kademe varsa yeniden kur kapalıdır ve nedeni yazar', async ({page}) => {
  const p = price('BTCUSDT')
  const far = {spec: {levels: [{entry: p * .9, target: p * .91}]}, stats: {cycles: 0, realized_usdt: 0, gross_usdt: 0, fees_usdt: 0, open_levels: [{level: 1, cycle: 0, entry: p * .9, qty: 0.001}]}}
  await prepare(page, {grids: [gridRow('gy', 'BTCUSDT', 'LONG', far)]})
  await layer(page).locator('.sqgChip').click()
  await panel(page).locator('.sqgTabs button').nth(1).click()
  await expect(panel(page).locator('.sqgRange')).toContainText('Açık kademe var')
  await expect(panel(page).getByRole('button', {name: /Durdur ve yeniden kur/})).toHaveCount(0)
})

test('Geçmiş sekmesi: biten gridler özetlenir, toplam net ve tur görünür', async ({page}) => {
  const done = (id: string, net: number, status: string) => gridRow(id, 'BTCUSDT', 'LONG', {status, reason: 'Kullanıcı durdurdu', created_at: 1_700_000_000, updated_at: 1_700_000_000 + 7200, stats: {cycles: 4, realized_usdt: net, gross_usdt: net + 0.1, fees_usdt: 0.1, open_levels: []}})
  await prepare(page, {grids: [done('h1', 1.25, 'STOPPED'), done('h2', -0.4, 'EMERGENCY_CLOSED')]})
  await layer(page).locator('.sqgChip').click()
  await panel(page).locator('.sqgTabs button').nth(2).click()
  await expect(panel(page).locator('.sqgHist')).toHaveCount(2)
  await expect(panel(page).locator('.sqgTotals')).toContainText('Toplam net')
  await expect(panel(page).locator('.sqgTotals')).toContainText('+0,850')
  await expect(panel(page).locator('.sqgHist').first()).toContainText('2 saat')
  await page.screenshot({path: 'test-results/grid-history.png'})
})

for (const [width, height] of [[667, 375], [740, 360], [844, 390], [1180, 700]] as const) {
  test(`Yerleşim ${width}×${height}: panel ekran içinde, gövde kayar, başlat düğmesi hep görünür, harita taşmaz`, async ({page}) => {
    await prepare(page, {}, {width, height})
    await ready(page)
    const size = {width, height}
    inside(await boxOf(panel(page)), size)
    inside(await boxOf(panel(page).locator('.sqgFoot .sqtHold')), size)
    const body = await boxOf(panel(page).locator('.sqgBody'))
    const map = await boxOf(panel(page).locator('.sqgMap'))
    expect(map.x).toBeGreaterThanOrEqual(body.x - 1); expect(map.x + map.width).toBeLessThanOrEqual(body.x + body.width + 1)
    const overflow = await panel(page).locator('.sqgBody').evaluate(el => ({x: el.scrollWidth - el.clientWidth, canScroll: el.scrollHeight >= el.clientHeight}))
    expect(overflow.x).toBeLessThanOrEqual(1)
    expect(overflow.canScroll).toBe(true)
    for (const button of await panel(page).locator('.sqgPanel button, button').all()) { if (await button.isVisible()) { const b = await button.boundingBox(); if (b) expect(b.width).toBeGreaterThan(0) } }
    await page.screenshot({path: `test-results/grid-layout-${width}.png`})
  })
}


test('Sunucu planı doğrular; kesin ret başlatmayı kapatır, koruma alanları isteğe eklenir', async ({page}) => {
  const mock = await prepare(page, {previewStatus: 409, previewBody: {detail: 'Kullanılabilir bakiye grid marjininden düşük.'}})
  await layer(page).locator('.sqgChip').click()
  await expect(panel(page)).toBeVisible()
  await expect(panel(page).locator('.sqgChecks')).toContainText('Kullanılabilir bakiye grid marjininden düşük.', {timeout: 10000})
  await expect(panel(page).locator('.sqgFoot .sqtHold')).toBeDisabled()
  expect(mock.starts).toHaveLength(0)
  expect(mock.previews.length).toBeGreaterThan(0)
  mock.previewStatus = 200
  await panel(page).locator('#sqg-tp').fill('5')
  await panel(page).locator('#sqg-sl').fill('8')
  await panel(page).locator('#sqg-sl').blur()
  await expect(panel(page).locator('.sqgChecks')).toContainText('Sunucu planı doğruladı', {timeout: 10000})
  await expect(panel(page).locator('.sqgFoot .sqtHold')).toBeEnabled()
  const last = mock.previews[mock.previews.length - 1]
  expect(last).toMatchObject({preview: true, take_profit_usdt: 5, loss_limit_usdt: 8})
  expect(mock.starts).toHaveLength(0)
  await hold(page, panel(page).locator('.sqgFoot .sqtHold'), 1800)
  await expect.poll(() => mock.starts.length, {timeout: 8000}).toBe(1)
  expect(mock.starts[0]).toMatchObject({take_profit_usdt: 5, loss_limit_usdt: 8})
  expect(mock.starts[0]).not.toHaveProperty('preview')
  await page.screenshot({path: 'test-results/grid-protect.png'})
})

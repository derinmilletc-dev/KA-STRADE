import {expect, test, type Page} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
import {previewAnalysis, previewCandles, previewMarkets} from './helpers/master-trade-preview-data'

// Telefon: dokunmatik, dikey. Testler görünümü yatay yapmak için pencere boyutunu değiştirir (cihazı çevirmek gibi).
test.use({baseURL: 'http://127.0.0.1:4174', hasTouch: true, isMobile: true, viewport: {width: 390, height: 844}})
test.setTimeout(60000)
const root = (page: Page) => page.locator('.masterReferenceAnalysis')
const entry = (page: Page) => root(page).getByRole('button', {name: 'Landscape fullscreen'})
const terminal = (page: Page) => root(page).locator('.refChart[data-fs="on"]')
const overlay = (page: Page) => page.getByRole('dialog', {name: 'Turn your phone sideways'})
const drawer = (page: Page) => page.locator('#kfsAnalysisDrawer')

type Support = 'full' | 'none'

async function prepare(page: Page, options: {support?: Support; landscape?: boolean} = {}) {
  const support = options.support ?? 'full'
  // Fullscreen ve ekran yönü kilidi sahte API ile gözlenir; 'none' tarayıcıda hiç desteklenmediği durumu taklit eder.
  await page.addInitScript(mode => {
    const calls = {request: 0, exit: 0, lock: [] as string[], unlock: 0}
    ;(window as unknown as {__fs: typeof calls}).__fs = calls
    if (mode === 'none') {
      Object.defineProperty(document, 'fullscreenEnabled', {value: false, configurable: true})
      Object.defineProperty(Element.prototype, 'requestFullscreen', {value: undefined, configurable: true})
      Object.defineProperty(Screen.prototype, 'orientation', {value: {}, configurable: true})
      return
    }
    let current: Element | null = null
    Object.defineProperty(document, 'fullscreenElement', {get: () => current, configurable: true})
    Object.defineProperty(document, 'fullscreenEnabled', {value: true, configurable: true})
    Object.defineProperty(Element.prototype, 'requestFullscreen', {value() { calls.request += 1; current = this as Element; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(document, 'exitFullscreen', {value() { calls.exit += 1; current = null; document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve() }, configurable: true})
    Object.defineProperty(Screen.prototype, 'orientation', {value: {lock(orientation: string) { calls.lock.push(orientation); return Promise.resolve() }, unlock() { calls.unlock += 1 }}, configurable: true})
  }, support)
  await page.setViewportSize(options.landscape ? {width: 844, height: 390} : {width: 390, height: 844})
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('wss://**', socket => socket.close())
  await page.route('**/api/**', route => route.fulfill({status: 501, json: {detail: 'Offline API fixture missing'}}))
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockAssistant(page)
  const user = {id: 'landscape-member', email: 'member@example.test', display_name: 'Landscape Member', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  const live = {
    connected: false, real_trading_locked: true, armed: false, execution_state: 'LOCKED', live_auto_trade: false, recovery_ready: true,
    credentials: {configured: false}, readiness: {ready: false, gates: [{key: 'risk', passed: false}]}, authorization: {valid: false}, consent: {active: false},
    policy_acknowledged: false, account: {positions: [], open_orders: []}, plans: [], events: [],
  }
  await page.route('**/api/v25/status', route => route.fulfill({json: live}))
  await page.route('**/api/exchange-connections/status', route => route.fulfill({json: {connections: {LIVE: {configured: false, active: false}}}}))
  await page.route('**/api/markets**', route => route.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis-universe**', route => route.fulfill({json: {results: previewMarkets.map((market, index) => ({...market, direction: 'LONG', confidence: 94 - index, final_decision_score: 94 - index, smart_score: 94 - index}))}}))
  await page.route('**/api/analysis/**', route => route.fulfill({json: previewAnalysis(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop(), Date.now())}))
  const requests: string[] = []
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(`${request.method()} ${new URL(request.url()).pathname}`) })
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText('89%')
  await expect(root(page).locator('svg[data-chart-ai-capture="reference"]')).toBeVisible()
  return {requests}
}

const fsCalls = (page: Page) => page.evaluate(() => (window as unknown as {__fs: {request: number; exit: number; lock: string[]; unlock: number}}).__fs)
const pageIsRestored = async (page: Page) => {
  await expect(terminal(page)).toHaveCount(0)
  await expect(overlay(page)).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.classList.contains('kaisChartFullscreen') || document.body.classList.contains('kaisChartFullscreen'))).toBe(false)
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).not.toBe('hidden')
}
// Sipariş/pozisyon değiştirebilecek hiçbir istek (GET dışı) gitmemeli.
// POST /v25/risk/preview yalnızca salt okunur bir tahmin hesabıdır (özellikten bağımsız, sayfa yüklenirken de çağrılır).
const mutations = (requests: string[], from: number) => requests.slice(from).filter(request => !request.startsWith('GET ') && !request.endsWith('/v25/risk/preview'))
// Analiz ve Çıkış düğmeleri her ekran boyutunda ekranın içinde ve erişilebilir olmalı.
const controlsReachable = async (page: Page) => {
  const size = page.viewportSize()!
  for (const name of ['Analysis', 'Exit fullscreen']) {
    const box = await terminal(page).getByRole('button', {name, exact: true}).boundingBox()
    expect(box, name).not.toBeNull()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(size.width)
    expect(box!.y + box!.height).toBeLessThanOrEqual(size.height)
  }
}
const chartMatchesCanvas = async (page: Page) => {
  const canvas = root(page).locator('.refChartCanvas')
  const box = await canvas.boundingBox()
  const viewBox = await root(page).locator('svg[data-chart-ai-capture="reference"]').getAttribute('viewBox')
  expect(box).not.toBeNull()
  const [, , width, height] = (viewBox ?? '').split(' ').map(Number)
  expect(Math.abs(width - box!.width)).toBeLessThan(2)
  expect(Math.abs(height - box!.height)).toBeLessThan(2)
  return box!
}

test('The fullscreen button is labelled, compact and only an entry point (no trading request)', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  await expect(entry(page)).toBeVisible()
  await expect(entry(page)).toHaveAttribute('type', 'button')
  const box = await entry(page).boundingBox()
  expect(box!.width).toBeLessThanOrEqual(48)
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  expect(mutations(requests, before)).toEqual([])
})

test('Portrait users see the rotation overlay and Cancel restores the page completely', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  const url = page.url()
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect(overlay(page)).toContainText('Switch to landscape for a wider trading chart.')
  await expect(terminal(page)).toHaveCount(0)
  await expect(overlay(page).getByRole('button', {name: 'Cancel'})).toBeFocused()
  expect(await page.evaluate(() => document.documentElement.classList.contains('kaisChartFullscreen'))).toBe(true)
  await overlay(page).getByRole('button', {name: 'Cancel'}).click()
  await pageIsRestored(page)
  await expect(entry(page)).toBeFocused()
  expect(page.url()).toBe(url)
  const calls = await fsCalls(page)
  expect(calls.request).toBe(1)
  expect(calls.exit).toBe(1)
  expect(calls.unlock).toBe(calls.lock.length)
  expect(mutations(requests, before)).toEqual([])
})

test('Supported browsers request landscape fullscreen from the tap and release it on exit', async ({page}) => {
  await prepare(page)
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect.poll(async () => (await fsCalls(page)).lock).toEqual(['landscape'])
  expect((await fsCalls(page)).request).toBe(1)
  await page.setViewportSize({width: 844, height: 390}) // telefon yan çevrildi
  await expect(terminal(page)).toBeVisible()
  await expect(overlay(page)).toHaveCount(0)
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  const calls = await fsCalls(page)
  expect(calls.exit).toBe(1)
  expect(calls.unlock).toBe(1)
})

test('Unsupported fullscreen and orientation lock fall back to manual rotation without trapping the user', async ({page}) => {
  await prepare(page, {support: 'none'})
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect(overlay(page)).toContainText('turn your phone by hand')
  await page.setViewportSize({width: 844, height: 390})
  await expect(terminal(page)).toBeVisible()
  await page.setViewportSize({width: 390, height: 844}) // geri döndü: uyarı yine çıkış sunar
  await expect(overlay(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
})

test('The landscape terminal is chart-first with a compact toolbar and a closed analysis panel', async ({page}) => {
  const {requests} = await prepare(page, {landscape: true})
  const before = requests.length
  // Etiket yazımı grafik genişliğine göre değişir (dar ekranda "$" ve ok yok); sayısal değerler aynı kalmalı.
  const pillValues = async () => (await root(page).locator('.masterTradeChartPills span').allInnerTexts()).map(text => text.replace(/[↑↓$,]/g, '').replace(/\s+/g, ' ').trim()).sort()
  const pillsBefore = await pillValues()
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await expect(overlay(page)).toHaveCount(0)
  const frame = await terminal(page).boundingBox()
  expect(frame).toEqual({x: 0, y: 0, width: 844, height: 390})
  const toolbar = terminal(page).locator('.refToolbar')
  await expect(toolbar.locator('.kfsIdent b')).toHaveText('BTCUSDT')
  await expect(toolbar.locator('.kfsIdent strong')).toContainText('$')
  await expect(toolbar.locator('select')).toBeVisible()
  await expect(toolbar.locator('.refIndicators summary')).toBeVisible()
  await expect(toolbar.getByRole('button', {name: 'Analysis', exact: true})).toBeVisible()
  await expect(toolbar.getByRole('button', {name: 'Exit fullscreen'})).toBeFocused()
  // Emirle ilgili kısayollar tam ekranda gösterilmez.
  await expect(toolbar.locator('.refOrderTools')).toBeHidden()
  await controlsReachable(page)
  const canvas = await chartMatchesCanvas(page)
  expect(canvas.height).toBeGreaterThan(390 * 0.6)
  expect(canvas.width).toBe(844)
  await expect(drawer(page)).toBeHidden()
  // Sayfa kaydırması kilitli.
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).overflow)).toBe('hidden')
  // Mevcut seviye etiketleri (Entry/TP/SL...) olduğu gibi duruyor; yeniden hesaplanmaz.
  expect(await pillValues()).toEqual(pillsBefore)
  expect(mutations(requests, before)).toEqual([])
})

test('The analysis drawer opens beside the chart, reuses the existing values and closes without leaving the chart', async ({page}) => {
  await prepare(page, {landscape: true})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  const url = page.url()
  const closedCanvas = await chartMatchesCanvas(page)
  await terminal(page).getByRole('button', {name: 'Analysis', exact: true}).click()
  await expect(drawer(page)).toBeVisible()
  await expect(terminal(page).getByRole('button', {name: 'Analysis', exact: true})).toHaveAttribute('aria-expanded', 'true')
  const gridLabels = await root(page).locator('.refIndicatorGrid .masterTradeMetricTile > small:first-child').allInnerTexts()
  const labels = await drawer(page).locator('.masterTradeMetricTile > small:first-child').allInnerTexts()
  expect(labels).toHaveLength(6) // Trend, Momentum, RSI, MACD, Volume, Confidence
  expect(labels).toEqual(gridLabels.slice(0, 6)) // aynı bileşen, aynı sıra (etiketler çalışma anında çevrilir)
  // Değerler sayfadaki göstergelerle aynı kaynaktan gelir.
  for (let index = 0; index < 6; index += 1) {
    const fromGrid = await root(page).locator('.refIndicatorGrid .masterTradeMetricTile').nth(index).locator('> strong').innerText()
    const fromDrawer = await drawer(page).locator('.masterTradeMetricTile').nth(index).locator('> strong').innerText()
    expect(fromDrawer).toBe(fromGrid)
  }
  const openCanvas = await chartMatchesCanvas(page) // grafik yeni genişliği algıladı
  expect(openCanvas.width).toBeLessThan(closedCanvas.width)
  await expect(root(page).locator('svg[data-chart-ai-capture="reference"]')).toBeVisible()
  await expect(terminal(page)).toBeVisible()
  await drawer(page).getByRole('button', {name: 'Close analysis'}).click()
  await expect(drawer(page)).toBeHidden()
  const restored = await chartMatchesCanvas(page)
  expect(Math.abs(restored.width - closedCanvas.width)).toBeLessThan(2)
  expect(page.url()).toBe(url)
  // Esc önce paneli kapatır, sonra tam ekranı.
  await terminal(page).getByRole('button', {name: 'Analysis', exact: true}).click()
  await expect(drawer(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(drawer(page)).toBeHidden()
  await expect(terminal(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
})

test('The chart recalculates its size when the device rotates or the viewport changes while open', async ({page}) => {
  await prepare(page, {landscape: true})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await chartMatchesCanvas(page)
  await page.setViewportSize({width: 667, height: 375})
  await controlsReachable(page)
  await page.setViewportSize({width: 740, height: 360})
  await expect.poll(async () => Math.round((await root(page).locator('.refChartCanvas').boundingBox())!.width)).toBe(740)
  await chartMatchesCanvas(page)
  await terminal(page).getByRole('button', {name: 'Analysis', exact: true}).click()
  await expect(drawer(page)).toBeVisible()
  await chartMatchesCanvas(page)
  await page.setViewportSize({width: 932, height: 430})
  await expect.poll(async () => Math.round((await root(page).locator('.refChartCanvas').boundingBox())!.height)).toBeGreaterThan(300)
  await chartMatchesCanvas(page)
})

test('On very small landscape screens the analysis panel is an overlay drawer and the chart keeps its width', async ({page}) => {
  await prepare(page, {landscape: true})
  await page.setViewportSize({width: 568, height: 320})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await controlsReachable(page)
  const before = await chartMatchesCanvas(page)
  await terminal(page).getByRole('button', {name: 'Analysis', exact: true}).click()
  await expect(drawer(page)).toBeVisible()
  const after = await chartMatchesCanvas(page)
  expect(after.width).toBe(before.width)
  await expect.poll(async () => { const box = await drawer(page).boundingBox(); return box ? Math.round(box.x + box.width) : -1 }).toBeLessThanOrEqual(568)
  const drawerBox = await drawer(page).boundingBox()
  expect(drawerBox!.x + drawerBox!.width).toBeLessThanOrEqual(568)
  expect(drawerBox!.width).toBeLessThan(568 * 0.8)
})

test('Live chart keeps working and exit restores normal scrolling and layout', async ({page}) => {
  await prepare(page, {landscape: true})
  const before = await root(page).locator('.refChart').boundingBox()
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await expect(root(page).locator('.refCandle').first()).toBeVisible()
  await expect(root(page).locator('.refZoomTools')).toBeVisible()
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  const after = await root(page).locator('.refChart').boundingBox()
  expect(after!.width).toBe(before!.width)
  expect(after!.height).toBe(before!.height)
  await expect(entry(page)).toBeVisible()
})

test('Browser back closes fullscreen instead of leaving Master Trade', async ({page}) => {
  await prepare(page, {landscape: true})
  const url = page.url()
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await page.goBack()
  await pageIsRestored(page)
  expect(page.url()).toBe(url)
  await expect(root(page)).toBeVisible()
})

test('Desktop layout keeps its toolbar unchanged (no fullscreen button for mouse users)', async ({browser}) => {
  const context = await browser.newContext({baseURL: 'http://127.0.0.1:4174', viewport: {width: 1440, height: 900}, hasTouch: false, isMobile: false})
  const page = await context.newPage()
  await prepare(page)
  await page.setViewportSize({width: 1440, height: 900})
  await expect(entry(page)).toBeHidden()
  await expect(root(page).locator('.refToolbar')).toBeVisible()
  await context.close()
})

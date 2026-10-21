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
// Sabit zaman: canlı mum mesajı, fixture'daki son mumla aynı zamana yazılabilsin.
const FIXED_NOW = Date.now()
const LAST_CANDLE_TIME = Math.floor(FIXED_NOW / 1000)

type Support = 'full' | 'none'
const liveClose = 85999.99

async function prepare(page: Page, options: {support?: Support; landscape?: boolean; liveFeed?: boolean} = {}) {
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
  // routeWebSocket kendi WebSocket sarmalayıcısını kurar ve sahte yayını ezer; canlı yayın testinde kullanılmaz.
  if (!options.liveFeed) await page.routeWebSocket('wss://**', socket => socket.close())
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockAssistant(page)
  // mockAssistant da routeWebSocket kurar; sahte yayın onun sarmalayıcısından SONRA yüklenmeli.
  if (options.liveFeed) {
    // Sahte canlı mum yayını (yalnız okunur veri): son mumun kapanışını periyodik olarak günceller.
    await page.addInitScript(({time, close}) => {
      class FakeSocket {
        readyState = 0; onopen: (() => void) | null = null; onmessage: ((event: {data: string}) => void) | null = null; onclose: (() => void) | null = null; onerror: (() => void) | null = null
        private timer: ReturnType<typeof setInterval> | undefined
        constructor() {
          setTimeout(() => {
            this.readyState = 1; this.onopen?.()
            const send = () => this.onmessage?.({data: JSON.stringify({k: {s: 'BTCUSDT', i: '15m', t: time * 1000, o: '85300', h: '86000', l: '85200', c: String(close), v: '999'}})})
            send(); this.timer = setInterval(send, 300)
          }, 50)
        }
        close() { this.readyState = 3; clearInterval(this.timer); this.onclose?.() }
        send() {}
        addEventListener() {}
        removeEventListener() {}
      }
      ;(window as unknown as {WebSocket: unknown}).WebSocket = FakeSocket
    }, {time: LAST_CANDLE_TIME, close: liveClose})
  }
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
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop(), FIXED_NOW)}))
  const requests: string[] = []
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(`${request.method()} ${new URL(request.url()).pathname}`) })
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await expect(root(page).locator('.refReportSymbol')).toContainText('89%')
  await expect(root(page).locator('svg[data-chart-ai-capture="reference"]')).toBeVisible()
  return {requests}
}

const fsCalls = (page: Page) => page.evaluate(() => (window as unknown as {__fs: {request: number; exit: number; lock: string[]; unlock: number}}).__fs)
const pageIsRestored = async (page: Page) => {
  await expect(page.locator('[inert]')).toHaveCount(0)
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

test('Zoom, drawings and timeframe stay on the same chart instance through enter, use and exit, with no orders', async ({page}) => {
  const {requests} = await prepare(page, {landscape: true})
  const before = requests.length
  const svg = root(page).locator('svg[data-chart-ai-capture="reference"]')
  await root(page).locator('.refZoomTools button').nth(1).click() // yakınlaştır
  const zoomedCandles = await root(page).locator('.refCandle').count()
  await root(page).locator('.refDrawingTools button').nth(1).click() // yatay çizgi
  await svg.click({position: {x: 200, y: 120}})
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(1)
  await svg.evaluate(element => { (window as unknown as {__chartNode: Element}).__chartNode = element })
  const sameNode = () => page.evaluate(() => document.querySelector('.masterReferenceAnalysis svg[data-chart-ai-capture="reference"]') === (window as unknown as {__chartNode: Element}).__chartNode)
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  expect(await sameNode()).toBe(true) // aynı DOM düğümü: yeniden kurulmadı
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(1)
  expect(await root(page).locator('.refCandle').count()).toBeLessThanOrEqual(zoomedCandles + 20) // yakınlaştırma korunur (genişlik artınca biraz fazla mum sığabilir)
  // Zaman dilimi seçici yatay çubukta çalışır ve çıkıştan sonra da seçili kalır.
  const klines = page.waitForRequest(request => request.url().includes('/api/klines/BTCUSDT') && request.url().includes('5m'))
  await terminal(page).locator('.refToolbar select').selectOption('5m')
  await klines
  await expect(svg).toBeVisible()
  await svg.evaluate(element => { (window as unknown as {__chartNode: Element}).__chartNode = element }) // yeniden etiketle: zaman dilimi değişince veri yüklenirken çizim alanı zaten yenilenir (mevcut davranış)
  await terminal(page).getByRole('button', {name: 'Analysis', exact: true}).click()
  await expect(drawer(page)).toBeVisible()
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  await expect(root(page).locator('.refToolbar select')).toHaveValue('5m')
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(1)
  expect(await sameNode()).toBe(true)
  expect(mutations(requests, before)).toEqual([])
})

test('Live candle updates keep flowing into the fullscreen price and chart', async ({page}) => {
  await prepare(page, {landscape: true, liveFeed: true})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await expect(terminal(page).locator('.kfsIdent strong')).toHaveText('$85,999.99', {timeout: 8000})
  await expect(terminal(page).locator('.refOhlc')).toContainText('$85,999.99')
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  await expect(root(page).locator('.refOhlc')).toContainText('$85,999.99')
})

test('Keyboard focus stays inside the fullscreen layer and the overlay', async ({page}) => {
  await prepare(page, {landscape: true})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  for (let step = 0; step < 14; step += 1) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('.refChart[data-fs="on"]')) || document.activeElement === document.body)).toBe(true)
  }
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  await page.setViewportSize({width: 390, height: 844})
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  for (let step = 0; step < 5; step += 1) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('.kfsOverlay')) || document.activeElement === document.body)).toBe(true)
  }
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
})

test('Escape closes an open chart menu first and only then leaves fullscreen', async ({page}) => {
  await prepare(page, {landscape: true})
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await terminal(page).locator('.refIndicators summary').click()
  await expect(terminal(page).locator('.refIndicators')).toHaveAttribute('open', '')
  await page.keyboard.press('Escape')
  await expect(terminal(page).locator('.refIndicators')).not.toHaveAttribute('open', '')
  await expect(terminal(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
})

test('Back from the rotation overlay, and exiting, always leave the page scrollable again', async ({page}) => {
  await prepare(page)
  const url = page.url()
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.goBack()
  await pageIsRestored(page)
  expect(page.url()).toBe(url)
  await page.evaluate(() => window.scrollTo(0, 120))
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0) // gerçekten kaydırılabiliyor
  await page.evaluate(() => window.scrollTo(0, 0))
  await entry(page).click()
  await page.setViewportSize({width: 844, height: 390})
  await expect(terminal(page)).toBeVisible()
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  await page.setViewportSize({width: 390, height: 844})
  await page.evaluate(() => window.scrollTo(0, 120))
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
})

test('In normal portrait the added button keeps the drawing-tools row on one line (page does not shift)', async ({page}) => {
  await prepare(page)
  const row = page.locator('.masterReferenceAnalysis .refToolbar > .refDrawingTools')
  const rowBox = (await row.boundingBox())!
  const buttons = await row.locator(':scope > button, :scope > details').evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return {y: Math.round(r.y), h: Math.round(r.height), w: Math.round(r.width)} }))
  expect(buttons.length).toBe(9)
  expect(rowBox.height).toBeLessThanOrEqual(40)
  expect(new Set(buttons.map(button => button.y)).size).toBe(1)
  expect(Math.min(...buttons.map(button => button.w))).toBeGreaterThanOrEqual(34)
})

// Telefon animasyonu: zaman çizelgesini Web Animations API ile durdurup belirli anlara götürür (uyku/bekleme yok).
const phoneAngleAt = (page: Page, ms: number) => page.evaluate(time => {
  const phone = document.querySelector('.kfsPhone') as HTMLElement
  const animation = phone.getAnimations()[0]
  animation.pause(); animation.currentTime = time
  const m = new DOMMatrixReadOnly(getComputedStyle(phone).transform)
  return Math.round(Math.atan2(m.b, m.a) * 180 / Math.PI)
}, ms)

test('Rotation illustration starts upright, turns 90 degrees clockwise, holds, returns and repeats', async ({page}) => {
  await prepare(page)
  await page.emulateMedia({reducedMotion: 'no-preference'}) // prepare() sabit görüntü için hareketi kapatır
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  const info = await page.evaluate(() => {
    const phone = document.querySelector('.kfsPhone') as HTMLElement
    const [animation] = phone.getAnimations()
    const box = phone.getBoundingClientRect(), stage = phone.parentElement!.getBoundingClientRect()
    const timing = animation.effect!.getComputedTiming()
    return {upright: box.height > box.width, inStage: box.left >= stage.left && box.right <= stage.right, duration: timing.duration, iterations: timing.iterations}
  })
  expect(info.upright).toBe(true)
  expect(info.iterations).toBe(Infinity)
  expect(info.duration).toBe(4800)
  expect(await phoneAngleAt(page, 0)).toBe(0)
  expect(await phoneAngleAt(page, 480)).toBe(0) // ~500 ms bekleme
  const mid = await phoneAngleAt(page, 1100)
  expect(mid).toBeGreaterThan(0) // saat yönü = pozitif açı
  expect(mid).toBeLessThan(90)
  expect(await phoneAngleAt(page, 1800)).toBe(90)
  expect(await phoneAngleAt(page, 2900)).toBe(90) // yatay bekleme
  const back = await phoneAngleAt(page, 3600)
  expect(back).toBeGreaterThan(0); expect(back).toBeLessThan(90)
  expect(await phoneAngleAt(page, 4500)).toBe(0)
  // Taşma yok: dönerken de sahne içinde kalır.
  for (const time of [600, 1000, 1400]) {
    await phoneAngleAt(page, time)
    const fits = await page.evaluate(() => { const box = document.querySelector('.kfsPhone')!.getBoundingClientRect(), stage = document.querySelector('.kfsPhoneStage')!.getBoundingClientRect(); return box.left >= stage.left && box.right <= stage.right && box.top >= stage.top && box.bottom <= stage.bottom })
    expect(fits).toBe(true)
  }
})

test('Cancel stops the illustration and reopening restarts it upright', async ({page}) => {
  const {requests} = await prepare(page)
  await page.emulateMedia({reducedMotion: 'no-preference'})
  const before = requests.length
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.evaluate(() => { const a = document.querySelector('.kfsPhone')!.getAnimations()[0]; a.pause(); a.currentTime = 1800 })
  expect(await phoneAngleAt(page, 1800)).toBe(90)
  await page.getByRole('button', {name: 'Cancel'}).click()
  await expect(overlay(page)).toHaveCount(0)
  expect(await page.locator('.kfsPhone').count()).toBe(0)
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  const restart = await page.evaluate(() => { const phone = document.querySelector('.kfsPhone') as HTMLElement, a = phone.getAnimations()[0]; return {time: Number(a.currentTime), upright: phone.getBoundingClientRect().height > phone.getBoundingClientRect().width} })
  expect(restart.time).toBeLessThan(500)
  expect(restart.upright).toBe(true)
  expect(mutations(requests, before)).toEqual([])
})

test('Reduced motion shows a static upright phone with a landscape hint', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await prepare(page)
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  const state = await page.evaluate(() => {
    const phone = document.querySelector('.kfsPhone') as HTMLElement
    const box = phone.getBoundingClientRect(), hint = getComputedStyle(phone.parentElement!, '::after')
    return {animations: phone.getAnimations().length, upright: box.height > box.width, hintWidth: hint.width, hintStyle: hint.borderTopStyle}
  })
  expect(state.animations).toBe(0)
  expect(state.upright).toBe(true)
  expect(state.hintStyle).toBe('dashed')
  expect(parseFloat(state.hintWidth)).toBeGreaterThan(parseFloat('44px'))
})

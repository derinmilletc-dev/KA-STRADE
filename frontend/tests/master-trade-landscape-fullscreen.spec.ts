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

type Support = 'full' | 'none' | 'reject-lock' | 'hang-lock'
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
    Object.defineProperty(Screen.prototype, 'orientation', {value: {lock(orientation: string) { calls.lock.push(orientation); return mode === 'hang-lock' ? new Promise(() => {}) : mode === 'reject-lock' ? Promise.reject(new DOMException('Orientation lock is not allowed here', 'NotSupportedError')) : Promise.resolve() }, unlock() { calls.unlock += 1 }}, configurable: true})
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
  // Rapor yüklendi mi: güven yüzdesi görünmeli. Sabit değer (89%) beklenmez; FIXED_NOW dosya yüklenirken alındığından uzun
  // koşularda sonraki testlerde mumlar "eskir" ve güven değişebilir (85%), bu da sıraya bağlı kararsızlık yaratıyordu.
  await expect(root(page).locator('.refReportSymbol')).toContainText(/\d+%/, {timeout: 20000})
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
// Sol çizim araç çubuğunun açık hâldeki sütun genişliği (CSS: .kfsRail width).
const RAIL_WIDTH = 44
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
  expect(canvas.width).toBe(844 - RAIL_WIDTH) // grafik, solundaki 44 px'lik çizim çubuğu dışındaki tüm genişliği kullanır
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
  await expect.poll(async () => Math.round((await root(page).locator('.refChartCanvas').boundingBox())!.width)).toBe(740 - RAIL_WIDTH)
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
  await expect(page.locator('.kfsPhone')).toHaveCount(0) // kapanış kısa bir geçişle olur
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

test('The phone illustration draws its screen detail even under the global background-image reset', async ({page}) => {
  await prepare(page)
  await entry(page).click()
  await expect(overlay(page)).toBeVisible()
  const detail = await page.evaluate(() => {
    const line = getComputedStyle(document.querySelector('.kfsPhone i')!, '::before')
    return {color: line.backgroundColor, height: line.height, transform: line.transform}
  })
  expect(detail.color).toBe('rgb(169, 212, 255)')
  expect(parseFloat(detail.height)).toBeGreaterThan(0)
  expect(detail.transform).not.toBe('none')
})

// ---------- Sol çizim araç çubuğu (yalnız simge) ----------
const rail = (page: Page) => terminal(page).getByRole('toolbar', {name: 'Drawing tools'})
const railTool = (page: Page, name: string) => rail(page).getByRole('button', {name, exact: true})
const chartSvg = (page: Page) => root(page).locator('svg[data-chart-ai-capture="reference"]')
async function enterTerminal(page: Page, options: Parameters<typeof prepare>[1] = {landscape: true}) {
  const context = await prepare(page, options)
  await entry(page).click()
  await expect(terminal(page)).toBeVisible()
  await expect(rail(page)).toBeVisible()
  return context
}
const openMore = async (page: Page) => { await railTool(page, 'More tools').click(); await expect(terminal(page).getByRole('menu', {name: 'More tools'})).toBeVisible() }
const drawHorizontal = async (page: Page, y = 120) => { await railTool(page, 'Horizontal line').click(); await chartSvg(page).click({position: {x: 200, y}}) }

test('The drawing rail is a compact icon-only vertical toolbar beside the chart', async ({page}) => {
  await enterTerminal(page)
  const info = await page.evaluate(() => {
    const bar = document.querySelector('.kfsRail')!.getBoundingClientRect()
    const canvas = document.querySelector('.kfsStage > .refChartCanvas')!.getBoundingClientRect()
    const buttons = Array.from(document.querySelectorAll('.kfsRail button')).map(node => {
      const r = node.getBoundingClientRect(), svg = node.querySelector('svg')
      return {name: node.getAttribute('aria-label'), text: (node.textContent ?? '').trim(), w: r.width, h: r.height, top: r.top, bottom: r.bottom, left: r.left, right: r.right, hasIcon: !!svg}
    })
    return {bar: {left: bar.left, right: bar.right, width: bar.width}, canvasLeft: canvas.left, buttons}
  })
  expect(info.buttons.map(button => button.name)).toEqual(['Collapse drawing tools', 'Crosshair', 'Trend line', 'Horizontal line', 'Eraser', 'Undo', 'Redo', 'More tools'])
  for (const button of info.buttons) { expect(button.text, button.name!).toBe(''); expect(button.hasIcon, button.name!).toBe(true) }
  for (const button of info.buttons.slice(1)) { expect(button.w, button.name!).toBeGreaterThanOrEqual(36); expect(button.h, button.name!).toBeGreaterThanOrEqual(36) }
  // Çubuk grafiğin solunda ayrı bir sütun: grafiği ve fiyat etiketlerini örtmez; düğmeler birbirine binmez.
  expect(info.bar.right).toBeLessThanOrEqual(info.canvasLeft + 1)
  expect(info.bar.width).toBeLessThanOrEqual(48)
  const sorted = [...info.buttons].sort((a, b) => a.top - b.top)
  for (let index = 1; index < sorted.length; index += 1) expect(sorted[index].top, sorted[index].name!).toBeGreaterThanOrEqual(sorted[index - 1].bottom - 0.5)
  const size = page.viewportSize()!
  for (const button of info.buttons) { expect(button.bottom).toBeLessThanOrEqual(size.height); expect(button.right).toBeLessThanOrEqual(size.width) }
  // Eski üst çubuktaki çizim düğmeleri terminalde gizli (yinelenen kontrol yok).
  await expect(terminal(page).locator('.refToolbar .refDrawTool:visible')).toHaveCount(0)
})

test('Selecting a tool shows a clear active state, single selection and creates the drawing without orders', async ({page}) => {
  const {requests} = await enterTerminal(page)
  const before = requests.length
  const trend = railTool(page, 'Trend line'), horizontal = railTool(page, 'Horizontal line')
  await expect(trend).toHaveAttribute('aria-pressed', 'false')
  await trend.click()
  await expect(trend).toHaveAttribute('aria-pressed', 'true')
  const styles = await trend.evaluate(node => { const c = getComputedStyle(node); return {border: c.borderTopColor, background: c.backgroundColor} })
  const idle = await horizontal.evaluate(node => { const c = getComputedStyle(node); return {border: c.borderTopColor, background: c.backgroundColor} })
  expect(styles).not.toEqual(idle)
  await horizontal.click()
  await expect(horizontal).toHaveAttribute('aria-pressed', 'true')
  await expect(trend).toHaveAttribute('aria-pressed', 'false') // tek seçili araç
  await chartSvg(page).click({position: {x: 200, y: 120}})
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(1)
  await expect(horizontal).toHaveAttribute('aria-pressed', 'false') // çizim bitince araç bırakılır (mevcut davranış)
  await trend.click()
  await chartSvg(page).click({position: {x: 160, y: 140}})
  await chartSvg(page).click({position: {x: 300, y: 90}})
  await expect(root(page).locator('.refDrawing--trend:not(.refDrawing--draft)')).toHaveCount(1)
  // Artı imleç düğmesi açılıp kapanır ve durumunu gösterir.
  const crosshair = railTool(page, 'Crosshair')
  await crosshair.click(); await expect(crosshair).toHaveAttribute('aria-pressed', 'true')
  await crosshair.click(); await expect(crosshair).toHaveAttribute('aria-pressed', 'false')
  expect(mutations(requests, before)).toEqual([])
})

test('Undo and redo step through drawings; eraser removes a line; undo also restores an erased line', async ({page}) => {
  await enterTerminal(page)
  const lines = root(page).locator('.refDrawing--hline')
  await expect(railTool(page, 'Undo')).toBeDisabled()
  await expect(railTool(page, 'Redo')).toBeDisabled()
  await drawHorizontal(page, 100)
  await drawHorizontal(page, 180)
  await expect(lines).toHaveCount(2)
  await railTool(page, 'Undo').click()
  await expect(lines).toHaveCount(1)
  await expect(railTool(page, 'Redo')).toBeEnabled()
  await railTool(page, 'Redo').click()
  await expect(lines).toHaveCount(2)
  await expect(railTool(page, 'Redo')).toBeDisabled()
  // Silgi: seçili araç aktif görünür; çizgiye tıklayınca siler.
  await railTool(page, 'Eraser').click()
  await expect(railTool(page, 'Eraser')).toHaveAttribute('aria-pressed', 'true')
  await root(page).locator('.refDrawing--hline .refDrawingHit').first().dispatchEvent('click')
  await expect(lines).toHaveCount(1)
  await railTool(page, 'Undo').click()
  await expect(lines).toHaveCount(2)
  // Kalıcılık: geri alma sonucu tarayıcı kaydına da yazılır.
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('kaistrade-chart-drawings') ?? '{}').BTCUSDT?.length)).toBe(2)
})

test('Hide and show drawings keep them stored; clear all asks for confirmation first', async ({page}) => {
  await enterTerminal(page)
  const lines = root(page).locator('.refDrawing--hline')
  await drawHorizontal(page)
  await expect(lines).toHaveCount(1)
  await openMore(page)
  await terminal(page).getByRole('menuitem', {name: 'Hide drawings'}).or(terminal(page).getByRole('button', {name: 'Hide drawings'})).click()
  await expect(lines).toHaveCount(0)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('kaistrade-chart-drawings') ?? '{}').BTCUSDT?.length)).toBe(1) // yalnız gizlendi, silinmedi
  await openMore(page)
  await terminal(page).getByRole('button', {name: 'Show drawings'}).click()
  await expect(lines).toHaveCount(1)
  // Hepsini sil: önce vazgeç → çizim kalır; sonra onayla → silinir.
  const dialogs: string[] = []
  page.once('dialog', dialog => { dialogs.push(dialog.message()); void dialog.dismiss() })
  await openMore(page)
  await terminal(page).getByRole('button', {name: 'Clear all drawings'}).click()
  await expect(lines).toHaveCount(1)
  expect(dialogs.length).toBe(1)
  page.once('dialog', dialog => { dialogs.push(dialog.message()); void dialog.accept() })
  await openMore(page)
  await terminal(page).getByRole('button', {name: 'Clear all drawings'}).click()
  await expect(lines).toHaveCount(0)
  expect(dialogs.length).toBe(2)
  await expect(railTool(page, 'Undo')).toBeEnabled() // silme de geri alınabilir
  await railTool(page, 'Undo').click()
  await expect(lines).toHaveCount(1)
})

test('The rail collapses to a thin strip and the chart reclaims the width, then expands again', async ({page}) => {
  await enterTerminal(page)
  const canvasWidth = () => page.evaluate(() => document.querySelector('.kfsStage > .refChartCanvas')!.getBoundingClientRect().width)
  const open = await canvasWidth()
  const toggle = rail(page).getByRole('button', {name: 'Collapse drawing tools'})
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await toggle.click()
  const collapsed = rail(page).getByRole('button', {name: 'Expand drawing tools'})
  await expect(collapsed).toHaveAttribute('aria-expanded', 'false')
  await expect(railTool(page, 'Trend line')).toBeHidden()
  await expect.poll(canvasWidth).toBeGreaterThan(open + 12)
  const strip = await rail(page).boundingBox()
  expect(strip!.width).toBeLessThanOrEqual(26)
  await collapsed.click()
  await expect(railTool(page, 'Trend line')).toBeVisible()
  await expect.poll(canvasWidth).toBeLessThanOrEqual(open + 1)
})

test('Tools can be identified without hover: mouse hover, keyboard focus and a touch tap all show the name', async ({page}) => {
  await enterTerminal(page)
  const tip = terminal(page).locator('.kfsTip')
  const box = (await railTool(page, 'Trend line').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await expect(tip).toHaveText('Trend line')
  await page.mouse.move(600, 200)
  await expect(tip).toHaveCount(0)
  await railTool(page, 'Horizontal line').tap()
  await expect(tip).toHaveText('Horizontal line') // dokunmatik: hover olmadan görünür
  await expect(tip).toHaveCount(0, {timeout: 4000}) // kısa süre sonra kendiliğinden kapanır
  await railTool(page, 'Horizontal line').tap() // aracı bırak
  await railTool(page, 'Crosshair').focus()
  await page.keyboard.press('Tab')
  await expect(tip).toHaveText('Trend line')
  const named = await rail(page).getByRole('button').evaluateAll(nodes => nodes.every(node => (node.getAttribute('aria-label') ?? '').length > 0))
  expect(named).toBe(true)
})

test('Escape closes the More menu first, then fullscreen; leaving restores everything and shows drawings again', async ({page}) => {
  await enterTerminal(page)
  await drawHorizontal(page)
  await openMore(page)
  await page.keyboard.press('Escape')
  await expect(terminal(page).getByRole('menu', {name: 'More tools'})).toHaveCount(0)
  await expect(terminal(page)).toBeVisible()
  await openMore(page)
  await terminal(page).getByRole('button', {name: 'Hide drawings'}).click()
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
  await expect(root(page).locator('.refDrawing--hline')).toHaveCount(1) // normal düzende çizimler gizli kalmaz
})

test('Drawings stay at the same price position when the viewport changes while the rail is open', async ({page}) => {
  await enterTerminal(page)
  await drawHorizontal(page, 140)
  // Çizgi, saklanan fiyatına denk gelen yükseklikte kalmalı (görünür aralık genişliğe göre değişebilir; fiyat değişmez).
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('kaistrade-chart-drawings') ?? '{}').BTCUSDT[0].price as number)
  const drawnPrice = () => page.evaluate(() => {
    const line = document.querySelector('.refDrawing--hline > line:not(.refDrawingHit)') as SVGLineElement, svg = document.querySelector('svg[data-chart-ai-capture="reference"]') as SVGSVGElement
    const low = Number(svg.getAttribute('data-axis-low')), high = Number(svg.getAttribute('data-axis-high')), height = Number(svg.getAttribute('data-price-height'))
    return high - Number(line.getAttribute('y1')) / height * (high - low)
  })
  expect(Math.abs((await drawnPrice()) - stored) / stored).toBeLessThan(0.0005)
  const sizeBefore = (await chartSvg(page).boundingBox())!.width
  await page.setViewportSize({width: 667, height: 375})
  await expect(rail(page)).toBeVisible()
  await expect.poll(async () => (await chartSvg(page).boundingBox())!.width).not.toBe(sizeBefore)
  await expect.poll(async () => Math.abs((await drawnPrice()) - stored) / stored).toBeLessThan(0.0005)
  const fit = await page.evaluate(() => { const size = {w: innerWidth, h: innerHeight}; return Array.from(document.querySelectorAll('.kfsRail button')).every(node => { const r = node.getBoundingClientRect(); return r.bottom <= size.h && r.right <= size.w }) })
  expect(fit).toBe(true) // 667x375'te de tüm düğmeler ekranda
})

test('Portrait and the normal toolbar keep their drawing buttons; the rail only exists in the landscape terminal', async ({page}) => {
  await prepare(page)
  await expect(root(page).locator('.kfsRail')).toHaveCount(0)
  await expect(root(page).locator('.refDrawingTools .refDrawTool')).toHaveCount(6)
  await expect(root(page).locator('.refDrawingTools .refDrawTool:visible')).toHaveCount(6)
})

// ---------- Analysis Studio: dikeyde döndürme uyarısı, gerçek yatay algılanınca otomatik açılış ----------
const studioButton = (page: Page) => root(page).getByRole('button', {name: 'Analysis Studio', exact: true})
const studioText = 'Rotate your device to open Analysis Studio.'
const portrait = {width: 390, height: 844}, landscape = {width: 844, height: 390}
// Pencere/belge üzerindeki etkin dinleyicileri sayar (aynı işleyici tekrar eklenirse bir kez sayılır); sızıntı ve çoğalma denetimi.
const countListeners = () => {
  const registry = new Map<string, Set<unknown>>()
  const wrap = (proto: {addEventListener: Function; removeEventListener: Function}) => {
    const add = proto.addEventListener, remove = proto.removeEventListener
    const keyOf = (target: unknown, type: string, options: unknown) => `${target === window ? 'window' : target === document ? 'document' : 'other'}:${type}:${typeof options === 'boolean' ? options : !!(options as {capture?: boolean} | undefined)?.capture}`
    proto.addEventListener = function (this: unknown, type: string, listener: unknown, options?: unknown) {
      if (this === window || this === document) { const key = keyOf(this, type, options); if (!registry.has(key)) registry.set(key, new Set()); registry.get(key)!.add(listener) }
      return add.call(this, type, listener, options)
    }
    proto.removeEventListener = function (this: unknown, type: string, listener: unknown, options?: unknown) {
      if (this === window || this === document) registry.get(keyOf(this, type, options))?.delete(listener)
      return remove.call(this, type, listener, options)
    }
  }
  wrap(EventTarget.prototype as never)
  ;(window as unknown as {__listeners: () => Record<string, number>}).__listeners = () => {
    const out: Record<string, number> = {}
    for (const [key, set] of registry) if (/(resize|orientationchange|popstate|fullscreenchange|keydown)/.test(key)) out[key] = set.size
    return out
  }
}
const listenerCounts = (page: Page) => page.evaluate(() => (window as unknown as {__listeners: () => Record<string, number>}).__listeners())
// Sayfanın kendi gecikmeli kurulumları bitene kadar bekler: art arda iki ölçüm aynı olunca sayılar durulmuştur.
const settledListeners = async (page: Page) => {
  let last = JSON.stringify(await listenerCounts(page))
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await page.waitForTimeout(250)
    const next = JSON.stringify(await listenerCounts(page))
    if (next === last) return JSON.parse(next) as Record<string, number>
    last = next
  }
  return JSON.parse(last) as Record<string, number>
}
const rotateTo = async (page: Page, size: {width: number; height: number}) => { await page.setViewportSize(size); await page.evaluate(() => window.dispatchEvent(new Event('orientationchange'))) }

test('Analysis Studio sits under the indicator table and tapping it in portrait shows the rotation prompt', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  const button = studioButton(page)
  await expect(button).toBeVisible()
  const placement = await page.evaluate(() => { const grid = document.querySelector('.refIndicatorGrid')!.getBoundingClientRect(), btn = document.querySelector('.kfsStudioBtn')!.getBoundingClientRect(); return {gridBottom: grid.bottom, top: btn.top, height: btn.height, width: btn.width, grid: grid.width} })
  expect(placement.top).toBeGreaterThanOrEqual(placement.gridBottom - 1)
  expect(placement.height).toBeGreaterThanOrEqual(44)
  expect(Math.abs(placement.width - placement.grid)).toBeLessThan(2)
  await button.tap()
  await expect(overlay(page)).toBeVisible()
  await expect(overlay(page).getByText(studioText)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  await expect(drawer(page)).toHaveCount(0)
  await expect(overlay(page).getByRole('button', {name: 'Cancel'})).toBeFocused()
  expect(mutations(requests, before)).toEqual([])
})

test('The prompt stays up while the viewport is portrait and a single overlay exists', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await rotateTo(page, {width: 360, height: 740}) // hâlâ dikey
  await page.waitForTimeout(500)
  await expect(overlay(page)).toBeVisible()
  await expect(page.locator('.kfsOverlay')).toHaveCount(1)
  await expect(terminal(page)).toHaveCount(0)
})

test('Real landscape closes the prompt, opens the terminal and the Analysis panel, and keeps the same chart', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  const svg = chartSvg(page)
  await svg.evaluate(element => { (window as unknown as {__chartNode: Element}).__chartNode = element })
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await rotateTo(page, landscape)
  await expect(overlay(page)).toHaveCount(0)
  await expect(terminal(page)).toBeVisible()
  await expect(drawer(page)).toBeVisible() // Analysis Studio = terminal + açık Analysis paneli
  await expect(terminal(page).getByRole('button', {name: 'Analysis', exact: true})).toHaveAttribute('aria-expanded', 'true')
  await expect(terminal(page).getByRole('button', {name: 'Exit fullscreen'})).toBeFocused()
  expect(await page.evaluate(() => document.querySelector('.masterReferenceAnalysis svg[data-chart-ai-capture="reference"]') === (window as unknown as {__chartNode: Element}).__chartNode)).toBe(true)
  await controlsReachable(page)
  expect(mutations(requests, before)).toEqual([])
})

test('Manual rotation works when orientation lock and fullscreen are unavailable', async ({page}) => {
  const {requests} = await prepare(page, {support: 'none'})
  const before = requests.length
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect(overlay(page).getByText(/turn on auto-rotate/i)).toBeVisible() // hata değil, sakin bir ipucu
  await expect(page.getByRole('alert')).toHaveCount(0)
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
  await expect(drawer(page)).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as {__fs?: {request: number}}).__fs?.request ?? 0)).toBe(0)
  expect(mutations(requests, before)).toEqual([])
})

test('Cancel returns to Master Trade without touching trading state and restores focus to the Studio button', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await overlay(page).getByRole('button', {name: 'Cancel'}).click()
  await pageIsRestored(page)
  await expect(studioButton(page)).toBeFocused()
  await expect(root(page).locator('.refMetrics')).toBeVisible()
  expect(mutations(requests, before)).toEqual([])
  // Yeniden açınca yine aynı akış: uyarı → yatay → terminal.
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect(page.locator('.kfsOverlay')).toHaveCount(1)
})

test('Repeated rotations never duplicate listeners, overlays or leave the UI stuck; cancel removes everything', async ({page}) => {
  await page.addInitScript(countListeners)
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  const cycle = async () => {
    await rotateTo(page, landscape)
    await expect(terminal(page)).toBeVisible()
    await expect(page.locator('.kfsOverlay')).toHaveCount(0)
    await rotateTo(page, portrait)
    await expect(page.locator('.kfsOverlay')).toHaveCount(1)
    await expect(terminal(page)).toHaveCount(0)
  }
  await cycle() // ilk geçişlerde sayfa bileşenleri (tembel yüklenenler dahil) bir kez kurulur; kararlı durum bundan sonra ölçülür
  await cycle()
  const steady = await settledListeners(page)
  for (let index = 0; index < 3; index += 1) await cycle()
  const later = await settledListeners(page)
  // Sızıntı = artış: sayfanın geçici (ör. analiz verisi yükleyen) dinleyicileri düşebilir ama dönüşlerle hiçbiri artmaz.
  for (const key of new Set([...Object.keys(steady), ...Object.keys(later)])) expect(later[key] ?? 0, key).toBeLessThanOrEqual(steady[key] ?? 0)
  await overlay(page).getByRole('button', {name: 'Cancel'}).click()
  await pageIsRestored(page)
  const after = await settledListeners(page)
  // Denetleyicinin kendi dinleyicileri (her biri bir tane) iptalde tam olarak kalkar; sayfanın kendi dinleyicileri etkilenmez.
  for (const key of ['window:resize:false', 'window:orientationchange:false', 'window:popstate:false', 'document:keydown:true', 'document:fullscreenchange:false', 'document:webkitfullscreenchange:false']) expect(after[key] ?? 0, key).toBe((later[key] ?? 0) - 1)
})

test('Returning to portrait while Analysis Studio is open shows the prompt again and Escape still exits', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await rotateTo(page, landscape)
  await expect(drawer(page)).toBeVisible()
  await rotateTo(page, portrait)
  await expect(overlay(page)).toBeVisible()
  await expect(overlay(page).getByText(studioText)).toBeVisible()
  await page.keyboard.press('Escape') // panel açık kalmış olsa bile Esc kilitlenmez
  await pageIsRestored(page)
  // Panel yalnız ilk yatay geçişte istenir: kullanıcı kapatırsa döndürünce kendiliğinden geri açılmaz.
  await studioButton(page).click()
  await rotateTo(page, landscape)
  await expect(drawer(page)).toBeVisible()
  await terminal(page).getByRole('button', {name: 'Close analysis'}).click()
  await expect(drawer(page)).toBeHidden()
  await rotateTo(page, portrait)
  await expect(overlay(page)).toBeVisible()
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
  await expect(drawer(page)).toBeHidden()
})

test('Reduced motion: static upright phone and an immediate hand-off; normal motion fades the prompt out', async ({page}) => {
  await prepare(page) // prepare() hareketi azaltır
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  const still = await page.evaluate(() => { const phone = document.querySelector('.kfsPhone') as HTMLElement, box = phone.getBoundingClientRect(); return {animations: phone.getAnimations().length, upright: box.height > box.width, hint: getComputedStyle(phone.parentElement!, '::after').borderTopStyle} })
  expect(still).toEqual({animations: 0, upright: true, hint: 'dashed'})
  await rotateTo(page, landscape)
  await expect(overlay(page)).toHaveCount(0, {timeout: 8000}) // en az uyarı süresi + oturma
  await expect(terminal(page)).toBeVisible()
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
  // Normal hareket: telefon canlı döner ve kapanış kısa bir geçişle olur.
  await rotateTo(page, portrait)
  await page.emulateMedia({reducedMotion: 'no-preference'})
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  expect(await page.evaluate(() => document.querySelector('.kfsPhone')!.getAnimations().length)).toBe(1)
  // Kapanış geçişi kısa sürer (~220 ms); yoklama yerine DOM gözlemcisiyle yakalanır.
  await page.evaluate(() => { const flag = window as unknown as {__sawLeaving?: boolean}; flag.__sawLeaving = false; new MutationObserver(() => { if (document.querySelector('.kfsOverlay[data-leaving="true"]')) flag.__sawLeaving = true }).observe(document.body, {subtree: true, childList: true, attributes: true}) })
  await rotateTo(page, landscape)
  await expect.poll(() => page.evaluate(() => (window as unknown as {__sawLeaving?: boolean}).__sawLeaving), {timeout: 10000}).toBe(true)
  await expect(page.locator('.kfsOverlay')).toHaveCount(0)
  await expect(terminal(page)).toBeVisible()
})

test('The Studio button is hidden for desktop mouse users', async ({browser}) => {
  const context = await browser.newContext({baseURL: 'http://127.0.0.1:4174', viewport: {width: 1440, height: 900}, hasTouch: false, isMobile: false})
  const page = await context.newPage()
  await prepare(page)
  await page.setViewportSize({width: 1440, height: 900})
  await expect(studioButton(page)).toBeHidden()
  await context.close()
})

test('Reopening the prompt right after Cancel (during the fade) restarts the phone upright', async ({page}) => {
  await prepare(page)
  await page.emulateMedia({reducedMotion: 'no-preference'})
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.evaluate(() => { const a = document.querySelector('.kfsPhone')!.getAnimations()[0]; a.pause(); a.currentTime = 1800 }) // yatay evrede
  await overlay(page).getByRole('button', {name: 'Cancel'}).click()
  await studioButton(page).click() // geçiş bitmeden yeniden aç
  await expect(page.locator('.kfsOverlay')).toHaveCount(1)
  const state = await page.evaluate(() => { const phone = document.querySelector('.kfsPhone') as HTMLElement, a = phone.getAnimations()[0], box = phone.getBoundingClientRect(); return {time: Number(a.currentTime), upright: box.height > box.width, leaving: document.querySelector('.kfsOverlay')!.getAttribute('data-leaving')} })
  expect(state.time).toBeLessThan(600)
  expect(state.upright).toBe(true)
  expect(state.leaving).toBeNull()
})

// ---------- Regresyon: uyarı erken kapanmamalı; grafik, kullanıcı gerçekten döndürmeden açılmamalı ----------
test('The prompt stays after one second and several animation cycles; finishing the animation never opens the chart', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  await page.emulateMedia({reducedMotion: 'no-preference'})
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.waitForTimeout(1200) // yaklaşık bir saniye sonra
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  // Animasyon en az iki tam döngü tamamlayana kadar bekle (4,8 sn/döngü); uyarı hâlâ yerinde olmalı.
  await expect.poll(() => page.evaluate(() => Number(document.querySelector('.kfsPhone')!.getAnimations()[0].effect!.getComputedTiming().currentIteration ?? 0)), {timeout: 15000, intervals: [500]}).toBeGreaterThanOrEqual(2)
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  await expect(drawer(page)).toHaveCount(0)
  expect(await page.evaluate(() => [innerWidth, innerHeight])).toEqual([390, 844]) // hâlâ dikey
  expect(mutations(requests, before)).toEqual([])
})

test('A transient landscape reading that bounces straight back (own fullscreen/lock side effect) does not open the chart', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.setViewportSize(landscape)
  await page.evaluate(() => window.dispatchEvent(new Event('resize')))
  await page.waitForTimeout(80)
  await page.setViewportSize(portrait)
  await page.waitForTimeout(900) // oturma süresinden uzun
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  // Gerçek döndürme ise oturunca açılır.
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
})

test('The terminal waits for the layout to settle after a real rotation', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await rotateTo(page, landscape)
  await page.waitForTimeout(120) // oturma süresinden kısa
  await expect(terminal(page)).toHaveCount(0)
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toBeVisible() // sonra açılır
  await expect(drawer(page)).toBeVisible()
})

test('Synthetic popstate events dispatched by the app do not dismiss the prompt; a real back does', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  for (let index = 0; index < 3; index += 1) await page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate')))
  await page.waitForTimeout(300)
  await expect(overlay(page)).toBeVisible()
  await page.goBack()
  await pageIsRestored(page)
  await expect(root(page).locator('.refMetrics')).toBeVisible() // Master Trade'e geri dönüldü
})

test('An involuntary fullscreen exit during the prompt keeps it; after landscape a system exit closes the terminal', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.evaluate(() => document.exitFullscreen())
  await page.waitForTimeout(500)
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
  await page.evaluate(() => (document as unknown as {documentElement: Element}).documentElement.requestFullscreen()) // tarayıcı tam ekranı yeniden verdi
  await page.evaluate(() => document.exitFullscreen()) // sistem hareketiyle çıkış
  await expect(terminal(page)).toBeVisible() // bu çıkışı biz istemedik ve tam ekranda değildik: terminal kalır, kullanıcı Exit ile çıkar
  await terminal(page).getByRole('button', {name: 'Exit fullscreen'}).click()
  await pageIsRestored(page)
})

test('A rejected orientation lock keeps the prompt and waits for manual rotation, without an error', async ({page}) => {
  await prepare(page, {support: 'reject-lock'})
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as unknown as {__fs: {lock: string[]}}).__fs.lock)).toEqual(['landscape']) // doğrudan dokunuştan istendi
  await page.waitForTimeout(1500)
  await expect(overlay(page)).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(terminal(page)).toHaveCount(0)
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
  await expect(drawer(page)).toBeVisible()
})

test('Escape closes the prompt and the page is not stuck; unmounting (leaving the Analysis tab) cleans up', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await pageIsRestored(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  // Başka sekmeye geçmek bileşeni söker: uyarı, kilit sınıfı ve inert hiçbiri kalmaz.
  await page.evaluate(() => { const url = new URL(location.href); url.searchParams.set('tab', 'canli'); history.pushState(history.state, '', url); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.locator('.kfsOverlay')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.classList.contains('kaisChartFullscreen') || document.body.classList.contains('kaisChartFullscreen'))).toBe(false)
  await expect(page.locator('[inert]')).toHaveCount(0)
})

test('A lock request that never settles still lets a real rotation open Analysis Studio', async ({page}) => {
  await prepare(page, {support: 'hang-lock'})
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  await page.waitForTimeout(800)
  await expect(overlay(page)).toBeVisible()
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible()
  await expect(drawer(page)).toBeVisible()
  await expect(page.locator('.kfsOverlay')).toHaveCount(0)
})

test('A missed resize/orientation event is recovered by re-reading the real viewport', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await expect(overlay(page)).toBeVisible()
  // Olay göndermeden yalnız görünür alanı değiştir (kaçan olay): yoklama gerçek durumu okuyup geçişi tamamlar.
  await page.evaluate(() => { const real = window.dispatchEvent.bind(window); window.dispatchEvent = (event: Event) => (event.type === 'resize' || event.type === 'orientationchange' ? true : real(event)) })
  await page.setViewportSize(landscape)
  await expect(terminal(page)).toBeVisible({timeout: 8000}) // en az uyarı süresi (3,5 sn) + yoklama + oturma
  await expect(drawer(page)).toBeVisible()
})

// ---------- Dönme uyarısının en az süresi (3,5 sn) ----------
const MIN_PROMPT = 3500
const promptOpenedAt = (page: Page) => page.evaluate(() => new Promise<number>(resolve => {
  if (document.querySelector('.kfsOverlay')) return resolve(performance.now())
  const observer = new MutationObserver(() => { if (document.querySelector('.kfsOverlay')) { observer.disconnect(); resolve(performance.now()) } })
  observer.observe(document.body, {subtree: true, childList: true})
}))
const terminalOpenedAt = (page: Page) => page.evaluate(() => new Promise<number>(resolve => {
  const check = () => document.querySelector('.masterReferenceAnalysis .refChart[data-fs="on"]') && !document.querySelector('.kfsOverlay')
  if (check()) return resolve(performance.now())
  const observer = new MutationObserver(() => { if (check()) { observer.disconnect(); resolve(performance.now()) } })
  observer.observe(document.body, {subtree: true, childList: true, attributes: true})
}))

test('Early landscape: the prompt stays at least 3.5 s, then Analysis Studio opens automatically', async ({page}) => {
  await prepare(page)
  const opened = promptOpenedAt(page)
  await studioButton(page).click()
  const start = await opened
  await expect(overlay(page)).toBeVisible()
  await page.waitForTimeout(400)
  const done = terminalOpenedAt(page)
  await rotateTo(page, landscape) // ~0,4 sn'de gerçek yatay
  await page.waitForTimeout(1500)
  await expect(overlay(page)).toBeVisible() // yatay ama süre dolmadı
  await expect(terminal(page)).toHaveCount(0)
  const end = await done
  expect(end - start).toBeGreaterThanOrEqual(MIN_PROMPT)
  expect(end - start).toBeLessThan(MIN_PROMPT + 2500) // ardından kendiliğinden açılır
  await expect(drawer(page)).toBeVisible()
})

test('Still portrait after 3.5 s: the prompt stays until landscape is detected, then opens', async ({page}) => {
  const {requests} = await prepare(page)
  const before = requests.length
  await studioButton(page).click()
  await page.waitForTimeout(MIN_PROMPT + 1500)
  await expect(overlay(page)).toBeVisible()
  await expect(terminal(page)).toHaveCount(0)
  await rotateTo(page, landscape)
  await expect(terminal(page)).toBeVisible({timeout: 3000}) // süre çoktan doldu: yalnız oturma beklenir
  await expect(page.locator('.kfsOverlay')).toHaveCount(0)
  expect(mutations(requests, before)).toEqual([])
})

test('Cancel during the minimum wait (already landscape) never opens the terminal later', async ({page}) => {
  await prepare(page)
  await studioButton(page).click()
  await rotateTo(page, landscape)
  await page.waitForTimeout(800)
  await overlay(page).getByRole('button', {name: 'Cancel'}).click()
  await pageIsRestored(page)
  await page.waitForTimeout(MIN_PROMPT)
  await expect(terminal(page)).toHaveCount(0)
  await expect(page.locator('.kfsOverlay')).toHaveCount(0)
})

// ---------- Göstergeler menüsü ----------
for (const width of [320, 360, 390]) {
  test(`Indicators menu at ${width}px: labels stay on one line, menu fits the viewport and toggles still work`, async ({page}) => {
    await prepare(page)
    await page.setViewportSize({width, height: 780})
    const menu = root(page).locator('.refIndicators').first()
    await menu.locator('summary').click()
    const buttons = menu.locator(':scope > div > button')
    await expect(buttons).toHaveCount(2)
    const boxes = await page.evaluate(() => [...document.querySelectorAll('.masterReferenceAnalysis .refIndicators[open] > div, .masterReferenceAnalysis .refIndicators[open] > div > button')].map(element => {
      const rect = element.getBoundingClientRect(), style = getComputedStyle(element)
      return {left: rect.left, right: rect.right, height: rect.height, width: rect.width, lineHeight: parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2, fontSize: parseFloat(style.fontSize), scroll: element.scrollWidth - element.clientWidth, whiteSpace: style.whiteSpace}
    }))
    const [panel, ...items] = boxes
    expect(panel.left).toBeGreaterThanOrEqual(0)
    expect(panel.right).toBeLessThanOrEqual(width)
    expect(panel.width).toBeGreaterThanOrEqual(150)
    for (const item of items) {
      expect(item.whiteSpace).toBe('nowrap')
      expect(item.scroll).toBeLessThanOrEqual(1) // metin taşmıyor / kırpılmıyor
      expect(item.height).toBeGreaterThanOrEqual(40) // dokunma hedefi
      expect(item.height).toBeLessThan(56) // tek satır (hece hece kırılmadı)
      expect(item.width).toBeCloseTo(items[0].width, 0) // tutarlı satır genişliği
    }
    // Menü üstte görünür (grafik/kart tarafından kırpılmıyor): her satırın ortasındaki öğe kendisi.
    for (let index = 0; index < 2; index += 1) {
      const hit = await buttons.nth(index).evaluate(button => { const rect = button.getBoundingClientRect(); return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) === button })
      expect(hit).toBe(true)
    }
    const levels = buttons.nth(0), volume = buttons.nth(1)
    const levelsBefore = await levels.getAttribute('aria-pressed'), volumeBefore = await volume.getAttribute('aria-pressed')
    await levels.click()
    await expect(levels).toHaveAttribute('aria-pressed', levelsBefore === 'true' ? 'false' : 'true')
    await volume.click()
    await expect(volume).toHaveAttribute('aria-pressed', volumeBefore === 'true' ? 'false' : 'true')
  })
}

test.describe('Turkish UI', () => {
  test.use({locale: 'tr-TR'})
  test('Indicators menu in Turkish at 320px: SEVİYELER and HACİM stay whole words on one line', async ({page}) => {
    await prepare(page)
    await page.setViewportSize({width: 320, height: 780})
    const menu = root(page).locator('.refIndicators').first()
    await menu.locator('summary').click()
    const buttons = menu.locator(':scope > div > button')
    await expect(buttons.nth(0)).toHaveText('SEVİYELER')
    await expect(buttons.nth(1)).toHaveText('HACİM')
    for (let index = 0; index < 2; index += 1) {
      const metrics = await buttons.nth(index).evaluate(button => { const range = document.createRange(); range.selectNodeContents(button); return {lines: range.getClientRects().length, overflow: button.scrollWidth - button.clientWidth, right: button.getBoundingClientRect().right} })
      expect(metrics.lines).toBe(1) // tek satır kutusu: kelime hece/harf olarak bölünmedi
      expect(metrics.overflow).toBeLessThanOrEqual(1)
      expect(metrics.right).toBeLessThanOrEqual(320)
    }
  })
})

test('Indicators menu is aligned to the trigger’s right edge when it would overflow the viewport', async ({page}) => {
  await prepare(page)
  const menu = root(page).locator('.refIndicators').first()
  // Tetikleyiciyi sağ kenara yakın konumlandır (dar yatay terminaldeki gibi) ve menüyü aç.
  await menu.evaluate(element => { (element as HTMLElement).style.position = 'fixed'; (element as HTMLElement).style.right = '6px'; (element as HTMLElement).style.top = '120px' })
  await menu.locator('summary').click()
  await expect(menu).toHaveAttribute('data-align', 'end')
  const rect = await menu.locator(':scope > div').evaluate(element => { const box = element.getBoundingClientRect(); return {left: box.left, right: box.right} })
  expect(rect.left).toBeGreaterThanOrEqual(0)
  expect(rect.right).toBeLessThanOrEqual(390)
})

// ---------- Kais AI paneli ----------
const aiUsage = {analysis_enabled: true, access_type: 'trial', quota: {limit: 7, used: 0, reserved: 0, remaining: 7, unlimited: false},
  acknowledgement: {required: true, policy_version: 'chart-ai-v1', text: 'Bu analiz yatırım tavsiyesi değildir.'}, availability: {message: 'ok'}}
for (const viewport of [{width: 390, height: 844}, {width: 375, height: 667}, {width: 360, height: 560}]) {
  test(`Kais AI panel at ${viewport.width}x${viewport.height}: content scrolls independently and never passes under the footer`, async ({page}) => {
    await prepare(page)
    await page.route('**/api/chart-ai/usage', route => route.fulfill({json: aiUsage}))
    await page.reload()
    await expect(root(page).locator('svg[data-chart-ai-capture="reference"]')).toBeVisible()
    await page.setViewportSize(viewport)
    await root(page).locator('.chartAiButton').first().click()
    const dialog = page.locator('.chartAiDialog')
    await expect(dialog.locator('.chartAiModes')).toBeVisible()
    const box = (selector: string) => dialog.locator(selector).first().evaluate(element => { const rect = element.getBoundingClientRect(); return {top: rect.top, bottom: rect.bottom, height: rect.height} })
    const [panel, header, body, modes, footer, send] = await Promise.all(['.chartAiDialog', '.chartAiHeader', '.chartAiBody', '.chartAiModes', '.chartAiFooter', '.chartAiSend'].map(selector => selector === '.chartAiDialog' ? dialog.evaluate(element => { const rect = element.getBoundingClientRect(); return {top: rect.top, bottom: rect.bottom, height: rect.height} }) : box(selector)))
    // Panel görünür alanda; başlık, kaydırılan içerik ve alt eylem bölümü üst üste binmeden sıralanır.
    expect(panel.top).toBeGreaterThanOrEqual(0)
    expect(panel.bottom).toBeLessThanOrEqual(viewport.height + 0.5)
    expect(header.bottom).toBeLessThanOrEqual(body.top + 0.5)
    expect(body.bottom).toBeLessThanOrEqual(footer.top + 0.5)
    expect(footer.bottom).toBeLessThanOrEqual(panel.bottom + 0.5)
    // Analiz türü seçicisi ve ana düğme çakışmaz; seçici tam görünür ve tam boyutludur.
    expect(modes.bottom).toBeLessThanOrEqual(footer.top + 0.5)
    expect(modes.height).toBeGreaterThanOrEqual(40)
    expect(send.height).toBeGreaterThanOrEqual(44)
    expect(send.top).toBeGreaterThanOrEqual(footer.top)
    await expect(dialog.getByRole('button', {name: /close the kais ai panel|Kais AI panelini kapat/i})).toBeVisible()
    // Yakala / yükle denetimleri aynı yükseklikte ve kart içine sığar.
    const controls = await dialog.locator('.chartAiImageEmpty > *').evaluateAll(elements => elements.map(element => { const rect = element.getBoundingClientRect(); return {height: rect.height, right: rect.right} }))
    expect(controls).toHaveLength(2)
    expect(Math.abs(controls[0].height - controls[1].height)).toBeLessThanOrEqual(1)
    for (const control of controls) expect(control.right).toBeLessThanOrEqual(viewport.width)
    // Yasal metin ve Ayrıntılar bağlantısı kaydırılan içerikte: alt bölüm yalnız onay + ana düğmeyi taşır.
    await expect(dialog.locator('.chartAiBody .chartAiLegal')).toHaveCount(1)
    await expect(dialog.locator('.chartAiFooter .chartAiLegal')).toHaveCount(0)
    const scroll = await dialog.locator('.chartAiBody').evaluate(element => ({overflow: getComputedStyle(element).overflowY, scrollable: element.scrollHeight > element.clientHeight}))
    expect(scroll.overflow).toBe('auto')
    if (scroll.scrollable) {
      const footerTop = footer.top
      await dialog.locator('.chartAiBody').evaluate(element => { element.scrollTop = element.scrollHeight })
      const legal = await dialog.locator('.chartAiLegal').evaluate(element => element.getBoundingClientRect().bottom)
      expect(legal).toBeLessThanOrEqual(footerTop + 0.5) // sona kaydırınca yasal metin alt bölümün üstünde kalır
      expect((await box('.chartAiFooter')).top).toBeCloseTo(footerTop, 0) // alt bölüm kaymadı
    }
    // Analiz türü seçimi çalışmaya devam eder.
    const radios = dialog.getByRole('radio')
    await radios.nth(1).click()
    await expect(radios.nth(1)).toHaveAttribute('aria-checked', 'true')
  })
}

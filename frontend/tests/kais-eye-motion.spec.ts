import {expect, test, type Page} from '@playwright/test'

// Konuşan asistan gözünün prosedürel bakış/kırpma sistemi. Zaman Playwright saatiyle ilerletilir; rastgelelik tohumlu bir
// üreteçle enjekte edilir (kesin bir dizi beklenmez, yalnız sınırlar ve davranış özellikleri denetlenir).
test.use({baseURL: 'http://127.0.0.1:4174'})

const GAZE_LIMIT = 0.55

type Probe = {
  rafPending: number; rafMaxPending: number; rafCalls: number
  blinks: Array<{start: number; end: number | null}>; overlaps: number
}
declare global { interface Window { __eye: Probe } }

async function setup(page: Page, {seed = 12345, reduced = false} = {}) {
  await page.clock.install({time: new Date('2026-01-01T00:00:00Z')})
  await page.emulateMedia({reducedMotion: reduced ? 'reduce' : 'no-preference'})
  await page.addInitScript(seed => {
    let state = seed >>> 0
    Math.random = () => ((state = (state * 1664525 + 1013904223) >>> 0) / 2 ** 32)
    // requestAnimationFrame sayacı: aynı anda bekleyen kare isteği sayısı, tek döngü olduğunu doğrular.
    const probe = {rafPending: 0, rafMaxPending: 0, rafCalls: 0, blinks: [] as Array<{start: number; end: number | null}>, overlaps: 0}
    ;(window as unknown as {__eye: typeof probe}).__eye = probe
    const raf = window.requestAnimationFrame.bind(window), caf = window.cancelAnimationFrame.bind(window)
    const live = new Set<number>()
    window.requestAnimationFrame = callback => {
      probe.rafCalls += 1
      const id = raf(time => { live.delete(id); probe.rafPending = live.size; callback(time) })
      live.add(id); probe.rafPending = live.size; probe.rafMaxPending = Math.max(probe.rafMaxPending, live.size)
      return id
    }
    window.cancelAnimationFrame = id => { live.delete(id); probe.rafPending = live.size; caf(id) }
    // Kırpma kaydı: sınıfın eklenip kaldırılması; açıkken yeniden eklenmesi çakışma sayılır.
    new MutationObserver(() => {
      const eye = document.querySelector('.kaisEye')
      const on = eye?.classList.contains('kaisEyeBlink') ?? false
      const last = probe.blinks[probe.blinks.length - 1]
      if (on && (!last || last.end !== null)) probe.blinks.push({start: performance.now(), end: null})
      else if (on && last && last.end === null) { /* aynı kırpma sürüyor */ }
      else if (!on && last && last.end === null) last.end = performance.now()
    }).observe(document, {subtree: true, attributes: true, attributeFilter: ['class'], childList: true})
  }, seed)
  await page.goto('/frontend/tests/fixtures/kais-eye-motion.html')
  await expect(page.locator('.kaisEye')).toHaveCount(1)
}

const eye = (page: Page) => page.locator('.kaisEye')
const look = (page: Page) => page.evaluate(() => { const element = document.querySelector('.kaisEye') as SVGSVGElement | null; return element ? {x: Number(element.dataset.lookX), y: Number(element.dataset.lookY)} : null })

// Bakışı düzenli aralıklarla örnekler (saat ilerletilerek).
async function sample(page: Page, totalMs: number, stepMs = 100) {
  const points: Array<{x: number; y: number}> = []
  for (let elapsed = 0; elapsed < totalMs; elapsed += stepMs) {
    await page.clock.runFor(stepMs)
    const point = await look(page)
    if (point) points.push(point)
  }
  return points
}

for (const presence of ['idle', 'listening'] as const) {
  test(`${presence}: gaze stays bounded, mostly forward, with occasional small shifts (no constant scanning)`, async ({page}) => {
    await setup(page, {seed: presence === 'idle' ? 7 : 99})
    await page.getByRole('button', {name: presence}).click()
    const points = await sample(page, 60000)
    const radii = points.map(point => Math.hypot(point.x, point.y))
    expect(Math.max(...radii)).toBeLessThanOrEqual(GAZE_LIMIT + 0.01) // sınırlı
    const forward = radii.filter(radius => radius <= 0.1).length / radii.length
    expect(forward).toBeGreaterThan(presence === 'listening' ? 0.8 : 0.7) // uzun süreli öne bakış
    expect(radii.some(radius => radius > 0.1)).toBe(true) // yine de ara sıra kayar
    // Sürekli sağ-sol taraması yok: yön değişimleri seyrek.
    let flips = 0
    for (let index = 1; index < points.length; index += 1) if (Math.abs(points[index].x) > 0.1 && Math.abs(points[index - 1].x) > 0.1 && Math.sign(points[index].x) !== Math.sign(points[index - 1].x)) flips += 1
    expect(flips).toBeLessThan(6)
  })
}

test('Gaze shifts are smooth (eased over several frames, not jumps) and intervals vary without a fixed period', async ({page}) => {
  await setup(page, {seed: 4242})
  await page.getByRole('button', {name: 'thinking'}).click()
  const points = await sample(page, 45000, 16)
  // Hareket başlangıçları: önceki örnekten belirgin değişim.
  const deltas = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y))
  expect(Math.max(...deltas)).toBeLessThan(0.2) // tek karede büyük sıçrama yok
  const starts: number[] = []
  for (let index = 1; index < deltas.length; index += 1) if (deltas[index] > 0.004 && deltas[index - 1] <= 0.004 && (!starts.length || index * 16 - starts[starts.length - 1] > 600)) starts.push(index * 16)
  expect(starts.length).toBeGreaterThan(4)
  const gaps = starts.slice(1).map((start, index) => start - starts[index])
  const mean = gaps.reduce((a, b) => a + b, 0) / gaps.length
  const sd = Math.sqrt(gaps.reduce((a, b) => a + (b - mean) ** 2, 0) / gaps.length)
  expect(sd / mean).toBeGreaterThan(0.3) // aralıklar belirgin biçimde değişken: sabit bir periyot yok
})

test('Blinks are brief, irregular, never overlap, and both lids blink together', async ({page}) => {
  await setup(page, {seed: 2026})
  // Sınıf durumu 25 ms adımlarla örneklenir (React işleme zamanlaması ölçümü bozmasın): süre ±25 ms hassasiyetle ölçülür.
  const blinks: Array<{start: number; end: number}> = []
  let open: number | null = null
  for (let time = 0; time < 90000; time += 25) {
    await page.clock.runFor(25)
    const on = await eye(page).evaluate(element => element.classList.contains('kaisEyeBlink'))
    if (on && open === null) open = time
    if (!on && open !== null) { blinks.push({start: open, end: time}); open = null }
  }
  const probe = await page.evaluate(() => window.__eye)
  expect(probe.overlaps).toBe(0)
  expect(blinks.length).toBeGreaterThan(10)
  expect(blinks.length).toBeLessThan(60)
  // Gerçek süre 150 ms'dir (zamanlayıcı ve CSS animation-duration); örnekleme adımı ve React işleme gecikmesi için pay bırakılır.
  // Tek tek ölçümler ±1 adım oynar; ortanca tipik süreyi verir. Hiçbir kırpma uzun süre kapalı kalmaz.
  for (const blink of blinks) { expect(blink.end - blink.start).toBeGreaterThanOrEqual(50); expect(blink.end - blink.start).toBeLessThanOrEqual(300) }
  const durations = blinks.map(blink => blink.end - blink.start).sort((a, b) => a - b)
  const median = durations[Math.floor(durations.length / 2)]
  expect(median).toBeGreaterThanOrEqual(100)
  expect(median).toBeLessThanOrEqual(200)
  for (let index = 1; index < blinks.length; index += 1) expect(blinks[index].start).toBeGreaterThanOrEqual(blinks[index - 1].end) // çakışma yok
  const intervals = blinks.slice(1).map((blink, index) => blink.start - blinks[index].start)
  expect(new Set(intervals.map(value => Math.round(value / 100))).size).toBeGreaterThan(intervals.length * 0.6)
  expect(intervals.some(value => value < 400)).toBe(true) // ara sıra çift kırpma
  // Tek sınıf her iki göz kapağını ve iç kısmı birlikte canlandırır.
  await page.evaluate(() => document.querySelector('.kaisEye')!.classList.add('kaisEyeBlink'))
  for (const layer of ['.kaisEyeUpperLid', '.kaisEyeLowerLid', '.kaisEyeInterior']) await expect(eye(page).locator(layer)).toHaveCSS('animation-name', 'kais-eye-blink')
  await expect(eye(page).locator('.kaisEyeInterior')).toHaveCSS('animation-duration', '0.15s')
})

test('State changes never start a second loop, keep the current gaze, and unmount cancels every frame and timer', async ({page}) => {
  await setup(page, {seed: 31})
  for (let round = 0; round < 6; round += 1) {
    for (const presence of ['listening', 'thinking', 'speaking', 'idle']) {
      await page.getByRole('button', {name: presence}).click()
      await page.clock.runFor(700 + round * 113)
    }
  }
  const probe = await page.evaluate(() => window.__eye)
  expect(probe.rafMaxPending).toBeLessThanOrEqual(1) // aynı anda en fazla tek kare isteği: yinelenen döngü yok
  expect(probe.blinks.filter(blink => blink.end === null).length).toBeLessThanOrEqual(1)
  // Durum değişince bakış her seferinde merkeze sıfırlanmaz.
  await page.getByRole('button', {name: 'idle'}).click()
  await page.clock.runFor(20000)
  let offCenter: {x: number; y: number} | null = null
  for (let index = 0; index < 400 && !offCenter; index += 1) { await page.clock.runFor(50); const point = await look(page); if (point && Math.hypot(point.x, point.y) > 0.15) offCenter = point }
  expect(offCenter).not.toBeNull()
  await page.getByRole('button', {name: 'speaking'}).click()
  const after = await look(page)
  expect(Math.hypot(after!.x, after!.y)).toBeGreaterThan(0.05)
  // Sökme: hiçbir kare isteği ve zamanlayıcı kalmaz.
  await page.getByRole('button', {name: 'Toggle eye'}).click()
  await expect(eye(page)).toHaveCount(0)
  const calls = await page.evaluate(() => window.__eye.rafCalls)
  await page.clock.runFor(60000)
  expect(await page.evaluate(() => ({calls: window.__eye.rafCalls, pending: window.__eye.rafPending}))).toEqual({calls, pending: 0})
})

test('Reduced motion keeps the eye still: no gaze movement, no blinking, no animation frames', async ({page}) => {
  await setup(page, {seed: 5, reduced: true})
  await expect(eye(page)).not.toHaveClass(/kaisEyeMotion/)
  await page.clock.runFor(60000)
  expect(await look(page)).toEqual({x: 0, y: 0})
  const probe = await page.evaluate(() => window.__eye)
  expect(probe.blinks).toHaveLength(0)
  expect(probe.rafCalls).toBe(0)
})

test('A hidden page pauses gaze and blinking; becoming visible resumes with a single loop', async ({page}) => {
  await setup(page, {seed: 77})
  await page.clock.runFor(10000)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(eye(page)).not.toHaveClass(/kaisEyeMotion/)
  const before = await page.evaluate(() => ({calls: window.__eye.rafCalls, blinks: window.__eye.blinks.length}))
  await page.clock.runFor(60000)
  expect(await page.evaluate(() => ({calls: window.__eye.rafCalls, blinks: window.__eye.blinks.length}))).toEqual(before)
  expect(await look(page)).toEqual({x: 0, y: 0})
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, get: () => false})
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(eye(page)).toHaveClass(/kaisEyeMotion/)
  await page.clock.runFor(30000)
  const resumed = await page.evaluate(() => window.__eye)
  expect(resumed.blinks.length).toBeGreaterThan(before.blinks)
  expect(resumed.rafMaxPending).toBeLessThanOrEqual(1)
})

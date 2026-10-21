import {expect, test, type Page} from '@playwright/test'

// Konuşan asistan gözünün prosedürel bakış/kırpma sistemi. Zaman Playwright saatiyle ilerletilir; rastgelelik tohumlu bir
// üreteçle enjekte edilir (kesin bir dizi beklenmez, yalnız sınırlar ve davranış özellikleri denetlenir).
test.use({baseURL: 'http://127.0.0.1:4174'})

const GLANCE_MAX = 0.18 // en büyük bakış yarıçapı (normalize): küçük ve sakin

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
// İşaretçi olayı üretmeden düğmeye bas: işaretçi takibi gözü meşru olarak imlece çevirir, bu testler otonom hareketi ölçer.
const press = (page: Page, name: string) => page.evaluate(label => [...document.querySelectorAll('button')].find(button => button.textContent === label)!.click(), name)
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

// Gözün merkezden ayrıldığı (bakış) anlar: dakikada kaç kez?
const glancesIn = (points: Array<{x: number; y: number}>) => {
  let count = 0
  for (let index = 1; index < points.length; index += 1) if (Math.hypot(points[index].x, points[index].y) > 0.05 && Math.hypot(points[index - 1].x, points[index - 1].y) <= 0.05) count += 1
  return count
}

for (const [presence, maxPerMinute] of [['idle', 5], ['listening', 4], ['speaking', 2]] as const) {
  test(`${presence}: calm — mostly forward, at most ${maxPerMinute} small glances per minute, no jitter, no scanning`, async ({page}) => {
    await setup(page, {seed: presence.length * 31})
    await press(page, presence)
    test.setTimeout(180000)
    const points = await sample(page, 120000)
    const radii = points.map(point => Math.hypot(point.x, point.y))
    expect(Math.max(...radii)).toBeLessThanOrEqual(GLANCE_MAX + 0.01) // küçük ve sınırlı
    const forward = radii.filter(radius => radius <= 0.05).length / radii.length
    expect(forward).toBeGreaterThan(0.85) // neredeyse hep öne
    expect(glancesIn(points) / 2).toBeLessThanOrEqual(maxPerMinute) // 2 dakikada
    // Öne bakarken mikro kıpırdama yok: merkezdeki örnekler tam olarak sabit.
    // Öne bakarken mikro kıpırdama yok: bakış zamanın büyük kısmında tam merkezde; merkeze yakın ara değerler yalnız kısa geçişlerde.
    const exact = points.filter(point => point.x === 0 && point.y === 0).length / points.length
    const nearButNotZero = points.filter(point => { const radius = Math.hypot(point.x, point.y); return radius > 0 && radius <= 0.05 }).length / points.length
    expect(exact).toBeGreaterThan(0.75)
    expect(nearButNotZero).toBeLessThan(0.12)
  })
}

test('Gaze shifts are slow and smooth (eased over many frames, no jumps) and do not repeat at a fixed period', async ({page}) => {
  test.setTimeout(300000)
  await setup(page, {seed: 4242})
  await press(page, 'idle')
  const points = await sample(page, 150000, 32)
  const deltas = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y))
  expect(Math.max(...deltas)).toBeLessThan(0.06) // 32 ms'de sıçrama yok (sakin geçiş)
  // Her hareket en az ~350 ms sürer: hareketli ardışık kare dizileri uzun.
  const runs: number[] = []
  let run = 0
  for (const delta of deltas) { if (delta > 0) run += 1; else if (run) { runs.push(run * 32); run = 0 } }
  expect(runs.length).toBeGreaterThan(3)
  const sorted = runs.slice().sort((a, b) => a - b)
  expect(sorted[Math.floor(sorted.length / 2)]).toBeGreaterThanOrEqual(150) // tipik geçiş çok karelik (tek karelik sıçrama değil)
  const starts: number[] = []
  let moving = false
  deltas.forEach((delta, index) => { if (delta > 0 && !moving) starts.push(index * 32); moving = delta > 0 })
  const gaps = starts.slice(1).map((start, index) => start - starts[index])
  const mean = gaps.reduce((a, b) => a + b, 0) / gaps.length
  const sd = Math.sqrt(gaps.reduce((a, b) => a + (b - mean) ** 2, 0) / gaps.length)
  expect(sd / mean).toBeGreaterThan(0.3) // sabit bir periyot yok
})

test('Entering thinking produces one short, timely glance and then a calm return', async ({page}) => {
  await setup(page, {seed: 808})
  await press(page, 'listening')
  await page.clock.runFor(1000)
  await press(page, 'thinking')
  const points = await sample(page, 4000, 50)
  const firstAway = points.findIndex(point => Math.hypot(point.x, point.y) > 0.05)
  expect(firstAway).toBeGreaterThanOrEqual(0)
  expect(firstAway * 50).toBeLessThanOrEqual(1700) // düşünmeye geçince ~0,5–0,9 sn içinde
  const back = points.slice(firstAway).findIndex(point => Math.hypot(point.x, point.y) <= 0.05)
  expect(back).toBeGreaterThan(0) // ardından merkeze döner
})

test('Blinks are brief, irregular, never overlap, and both lids blink together', async ({page}) => {
  test.setTimeout(120000)
  await setup(page, {seed: 2026})
  await press(page, 'listening') // konuşan asistan bağlamı (boştaki uyku moduna girmez)
  await page.clock.runFor(180000)
  // Sınıf değişimleri sayfa içinde (sahte saatle) kaydedilir.
  const probe = await page.evaluate(() => window.__eye)
  const blinks = probe.blinks.filter(blink => blink.end !== null) as Array<{start: number; end: number}>
  // 6–11 sn aralık: 3 dakikada ~16–30 kırpma (nadir çiftler dahil).
  expect(blinks.length).toBeGreaterThanOrEqual(15)
  expect(blinks.length).toBeLessThanOrEqual(34)
  // Kırpma kısa (170 ms); React işleme zamanlaması için küçük pay. Hiçbir kırpma uzun süre kapalı kalmaz.
  const durations = blinks.map(blink => blink.end - blink.start).sort((a, b) => a - b)
  expect(durations[Math.floor(durations.length / 2)]).toBeGreaterThanOrEqual(120)
  expect(durations[Math.floor(durations.length / 2)]).toBeLessThanOrEqual(220)
  expect(durations[durations.length - 1]).toBeLessThanOrEqual(400)
  for (let index = 1; index < blinks.length; index += 1) expect(blinks[index].start).toBeGreaterThanOrEqual(blinks[index - 1].end) // çakışma yok
  // Çift kırpmanın ikincisi bir gruba aittir: düzenli aralık her grubun ilk kırpmasından ölçülür.
  const groups = blinks.filter((blink, index) => index === 0 || blink.start - blinks[index - 1].start > 1000)
  const regular = groups.slice(1).map((blink, index) => blink.start - groups[index].start)
  for (const value of regular) { expect(value).toBeGreaterThanOrEqual(6000 - 50); expect(value).toBeLessThanOrEqual(11000 + 400) }
  expect(new Set(regular.map(value => Math.round(value / 250))).size).toBeGreaterThan(regular.length * 0.5) // düzensiz
  expect(blinks.length - groups.length).toBeLessThanOrEqual(4) // çift kırpma nadir
  // Tek sınıf her iki göz kapağını ve iç kısmı birlikte canlandırır.
  await page.evaluate(() => document.querySelector('.kaisEye')!.classList.add('kaisEyeBlink'))
  for (const layer of ['.kaisEyeUpperLid', '.kaisEyeLowerLid', '.kaisEyeInterior']) await expect(eye(page).locator(layer)).toHaveCSS('animation-name', 'kais-eye-blink')
  await expect(eye(page).locator('.kaisEyeInterior')).toHaveCSS('animation-duration', '0.17s')
})

test('State changes never start a second loop, keep the current gaze, and unmount cancels every frame and timer', async ({page}) => {
  await setup(page, {seed: 31})
  for (let round = 0; round < 6; round += 1) {
    for (const presence of ['listening', 'thinking', 'speaking', 'idle']) {
      await press(page, presence)
      await page.clock.runFor(700 + round * 113)
    }
  }
  const probe = await page.evaluate(() => window.__eye)
  expect(probe.rafMaxPending).toBeLessThanOrEqual(1) // aynı anda en fazla tek kare isteği: yinelenen döngü yok
  expect(probe.blinks.filter(blink => blink.end === null).length).toBeLessThanOrEqual(1)
  // Durum değişince bakış her seferinde merkeze sıfırlanmaz.
  await press(page, 'idle')
  await page.clock.runFor(1000)
  let offCenter: {x: number; y: number} | null = null
  for (let index = 0; index < 2400 && !offCenter; index += 1) { await page.clock.runFor(50); const point = await look(page); if (point && Math.hypot(point.x, point.y) > 0.07) offCenter = point }
  expect(offCenter).not.toBeNull()
  await press(page, 'speaking')
  const after = await look(page)
  expect(Math.hypot(after!.x, after!.y)).toBeGreaterThan(0.03) // bakış sıfırlanmaz; ardından sakin dönüş
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

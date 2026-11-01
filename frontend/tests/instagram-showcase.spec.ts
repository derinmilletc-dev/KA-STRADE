import {expect, test, type Page} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'

// Yalnız test verisi: gerçek üretim içeriği değildir.
const AUTH_URL = 'http://127.0.0.1:4175/'
const CDN = 'https://scontent-test.cdninstagram.com/v/'
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')
const media = Array.from({length: 12}, (_, index) => ({
  id: String(1000 + index), media_type: index === 1 ? 'VIDEO' : index === 2 ? 'CAROUSEL_ALBUM' : 'IMAGE',
  media_url: index === 1 ? `${CDN}${index}.mp4` : `${CDN}${index}.png`, thumbnail_url: index === 1 ? `${CDN}${index}-thumb.png` : null,
  permalink: `https://www.instagram.com/p/${1000 + index}/`, caption: `Test gönderisi ${index + 1}`, timestamp: '2026-10-30T12:00:00+0000',
  ...(index === 2 ? {children: [{id: 'c1', media_type: 'IMAGE', media_url: `${CDN}c1.png`, thumbnail_url: null}, {id: 'c2', media_type: 'IMAGE', media_url: `${CDN}c2.png`, thumbnail_url: null}]} : {}),
}))
const profile = {username: 'kaistrade_', name: 'KAISTRADE', profile_picture_url: `${CDN}avatar.png`, biography: 'Test biyografisi',
  media_count: 42, profile_url: 'https://www.instagram.com/kaistrade_/'}
const ready = {configured: true, available: true, stale: false, username: 'kaistrade_', profile_url: profile.profile_url, profile, media, next: 'CURSOR2'}
const second = {...ready, profile: null, media: [{...media[0], id: '2000'}], next: null}

async function setup(page: Page, feed: object | null) {
  await page.route('https://**', route => {
    const url = new URL(route.request().url())
    if (!url.hostname.endsWith('cdninstagram.com')) return route.abort()
    if (url.pathname.endsWith('.mp4')) return  // video yanıtı bekletilir; oynatıcı kontrolleri ölçülür
    return route.fulfill({body: PIXEL, contentType: 'image/png'})
  })
  await page.routeWebSocket('wss://**', socket => socket.close())
  const state = await mockAssistant(page)
  state.sessionStatus = 401; state.profileStatus = 401
  const requests: string[] = []
  await page.route('**/api/public/instagram/feed**', route => {
    const url = new URL(route.request().url())
    requests.push(url.search)
    if (!feed) return route.fulfill({status: 503, json: {detail: 'unavailable'}})
    return route.fulfill({json: url.searchParams.get('after') ? second : feed})
  })
  return requests
}

test('desktop login shows the Instagram phone next to the untouched login card', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900})
  const requests = await setup(page, ready)
  await page.goto(AUTH_URL)
  await page.emulateMedia({reducedMotion: 'reduce'})
  const phone = page.locator('.igsPhone')
  await expect(phone).toBeVisible()
  await expect(page.locator('.igsTile').first()).toBeVisible()
  const geometry = await page.evaluate(() => {
    const card = document.querySelector('.authCardLogin')!.getBoundingClientRect()
    const device = document.querySelector('.igsPhone')!.getBoundingClientRect()
    return {overflow: document.documentElement.scrollWidth - window.innerWidth, gap: device.left - card.right, right: device.right, width: window.innerWidth, bottom: device.bottom, height: window.innerHeight}
  })
  expect(geometry.overflow).toBeLessThanOrEqual(0)
  expect(geometry.gap).toBeGreaterThan(16)
  expect(geometry.right).toBeLessThanOrEqual(geometry.width)
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.height)
  const viewLink = page.getByRole('link', {name: /Instagram'da Görüntüle/}).first()
  await expect(viewLink).toHaveAttribute('href', 'https://www.instagram.com/kaistrade_/')
  await expect(viewLink).toHaveAttribute('target', '_blank')
  await expect(viewLink).toHaveAttribute('rel', 'noopener noreferrer')

  // Telefon içinde kaydırma sayfayı kaydırmaz ve sonraki sayfa sunucu imleciyle gelir.
  const scroller = page.locator('.igsScroll')
  await scroller.hover()
  for (let step = 0; step < 6; step++) await page.mouse.wheel(0, 500)
  await expect.poll(() => scroller.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  await expect.poll(() => requests.some(search => search.includes('after=CURSOR2'))).toBe(true)
  await expect(page.locator('.igsTile')).toHaveCount(13)

  // Gönderi ayrıntısı ve çoklu gönderi gezintisi.
  await scroller.evaluate(element => { element.scrollTop = 0 })
  await page.locator('.igsTile').nth(2).click()
  const detail = page.getByRole('dialog', {name: 'Gönderi ayrıntısı'})
  await expect(detail).toBeVisible()
  await expect(detail.getByText('Test gönderisi 3')).toBeVisible()
  await detail.getByRole('button', {name: 'Sonraki'}).click()
  await expect(detail.locator('.igsDots')).toHaveAttribute('aria-label', '2 / 2')
  await expect(detail.getByRole('link', {name: /Gönderiyi Instagram'da aç/})).toHaveAttribute('href', 'https://www.instagram.com/p/1002/')
  await detail.getByRole('button', {name: 'Gönderilere dön'}).click()

  // Video: kontrol çubuklu, sessiz otomatik oynatma yok, satır içi.
  await page.locator('.igsTile').nth(1).click()
  const video = page.locator('.igsDetail video')
  await expect(video).toHaveAttribute('controls', '')
  expect(await video.evaluate(element => (element as HTMLVideoElement).autoplay)).toBe(false)
  expect(await video.evaluate(element => (element as HTMLVideoElement).playsInline)).toBe(true)
})

test('unavailable feed shows the profile link fallback and never fabricated posts', async ({page}) => {
  await page.setViewportSize({width: 1366, height: 768})
  await setup(page, null)
  await page.goto(AUTH_URL)
  await expect(page.locator('.igsFallback')).toBeVisible()
  await expect(page.locator('.igsTile')).toHaveCount(0)
  await expect(page.locator('.igsFallback a')).toHaveAttribute('href', 'https://www.instagram.com/kaistrade_/')
  await expect(page.locator('.authCardLogin')).toBeVisible()
})

for (const viewport of [{width: 1024, height: 768}, {width: 768, height: 1024}, {width: 390, height: 844}]) {
  test(`Instagram phone is hidden and not requested at ${viewport.width}px`, async ({page}) => {
    await page.setViewportSize(viewport)
    const requests = await setup(page, ready)
    await page.goto(AUTH_URL)
    await expect(page.locator('.authCardLogin')).toBeVisible()
    await expect(page.locator('.igsShowcase')).toHaveCount(0)
    expect(requests).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  })
}

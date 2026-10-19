import {expect, test, type Page} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'

/** Her tarayıcıda: ana çalışma alanları açılıyor mu, sayfa çöküyor mu (JS hatası / kurtarma ekranı), telefonda yatay taşma var mı,
 *  Kais sohbeti açılıp cevap veriyor mu, hukuk sayfaları açılıyor mu. Sunucu tamamen taklit; yalnızca tarayıcı uyumluluğu ölçülür. */
const VIEWS = ['dashboard', 'trading', 'master-trade', 'analyst', 'scanner', 'performance', 'risk', 'support', 'ops', 'pricing', 'billing', 'setup'] as const

// Taklit sunucunun bilerek kestiği dış bağlantılardan (borsa akışları vb.) gelen gürültü; tarayıcı uyumsuzluğu değildir.
const NOISE = /Failed to load resource|net::ERR|NS_ERROR|WebSocket|ERR_FAILED|Load failed|aborted|cancelled|NetworkError|access control checks|status of 4\d\d/i

function watch(page: Page) {
  const crashes: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', error => crashes.push(error.message.slice(0, 220)))
  page.on('console', message => {
    if (message.type() === 'error' && !NOISE.test(message.text())) consoleErrors.push(message.text().slice(0, 220))
  })
  return {crashes, consoleErrors}
}

async function settle(page: Page, ms = 1200) {
  await page.waitForLoadState('domcontentloaded')
  await page.waitForTimeout(ms)
}

function note(type: string, description: string) {
  test.info().annotations.push({type, description: description.slice(0, 600)})
}

async function overflow(page: Page) {
  return page.evaluate(() => Math.max(0, document.documentElement.scrollWidth - window.innerWidth))
}

test('Uygulama açılıyor ve çalışma alanları hatasız yükleniyor', async ({page}) => {
  const seen = watch(page)
  await mockAssistant(page)
  await page.goto('/')
  await settle(page, 2000)
  await expect(page.locator('.appRecoveryShell'), 'kurtarma ekranı (sayfa yüklenemedi) görünmemeli').toHaveCount(0)
  const broken: string[] = []
  const wide: string[] = []
  for (const view of VIEWS) {
    const before = seen.crashes.length
    await page.evaluate(view => window.dispatchEvent(new CustomEvent('protrebot-navigate', {detail: view})), view)
    await settle(page)
    const recovery = await page.locator('.appRecoveryShell').count()
    const extra = await overflow(page)
    if (recovery || seen.crashes.length > before) broken.push(`${view}${recovery ? ' [kurtarma ekranı]' : ''}${seen.crashes.length > before ? ` [JS: ${seen.crashes.slice(before).join(' ; ')}]` : ''}`)
    if (extra > 2) wide.push(`${view} (+${extra}px)`)
    if (recovery) { await page.goto('/'); await settle(page, 2000) }
  }
  if (wide.length) note('yatay-tasma', wide.join(', '))
  if (seen.consoleErrors.length) note('konsol-hatasi', [...new Set(seen.consoleErrors)].slice(0, 6).join(' | '))
  expect(broken, `Hatalı çalışma alanları: ${broken.join(' || ')}`).toEqual([])
})

test('Kais sohbeti açılıyor ve cevap veriyor', async ({page}) => {
  const seen = watch(page)
  const state = await mockAssistant(page)
  await page.goto('/')
  await settle(page, 1500)
  await page.getByRole('button', {name: 'Kais AI', exact: true}).first().click()
  await page.getByRole('button', {name: 'Sohbeti aç →', exact: true}).click()
  const dialog = page.getByRole('dialog', {name: 'Kais AI'})
  const box = dialog.getByRole('textbox', {name: 'Kais AI mesajın'})
  await expect(box).toBeEnabled()
  const voice = await dialog.locator('.assistantVoiceTalk').count()
  note('sesli-sohbet-dugmesi', voice ? 'görünüyor' : 'görünmüyor (tarayıcı konuşma tanımayı desteklemiyor)')
  await box.fill('Planlar neler?')
  await box.press('Enter')
  await expect(dialog.locator('.assistantMessage.assistant').last()).toContainText('Yanıt düz metindir')
  expect(state.chats).toHaveLength(1)
  expect(seen.crashes, `JS hataları: ${seen.crashes.join(' | ')}`).toEqual([])
})

for (const path of ['/privacy', '/terms', '/risk']) {
  test(`Hukuk sayfası ${path} açılıyor`, async ({page}) => {
    const seen = watch(page)
    await page.route('https://**', route => route.abort())
    await page.goto(path)
    await expect(page.locator('article h1')).toBeVisible()
    const extra = await overflow(page)
    if (extra > 2) note('yatay-tasma', `${path} (+${extra}px)`)
    expect(seen.crashes, `JS hataları: ${seen.crashes.join(' | ')}`).toEqual([])
  })
}

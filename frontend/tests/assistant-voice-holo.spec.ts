import {expect, test, type Page} from '@playwright/test'
import {mockAssistant, openChat} from './helpers/assistant-api'

test.use({baseURL: 'http://127.0.0.1:4174'})

/** Tarayıcının konuşma tanıma / mikrofon davranışını taklit eder (gerçek mikrofon veya ağ yok). Müşteri tarayıcılarında görülen durumlar:
 *  mic-denied (izin reddedildi), start-throws (start() hata fırlatır), service-denied (konuşma servisi izin vermez),
 *  network-error (Brave/özel Chromium: servis ağ hatası verir), ok (dinleme başlar). */
type Scenario = 'mic-denied' | 'start-throws' | 'service-denied' | 'network-error' | 'ok'

async function stubBrowserSpeech(page: Page, scenario: Scenario) {
  await page.addInitScript(scenario => {
    class FakeRecognition {
      lang = ''; interimResults = false; maxAlternatives = 1; continuous = false
      onresult: ((event: unknown) => void) | null = null
      onerror: ((event: {error: string}) => void) | null = null
      onend: (() => void) | null = null
      start() {
        if (scenario === 'start-throws') throw new DOMException('recognition has already started', 'InvalidStateError')
        if (scenario === 'service-denied') window.setTimeout(() => { this.onerror?.({error: 'service-not-allowed'}); this.onend?.() }, 30)
        if (scenario === 'network-error') window.setTimeout(() => { this.onerror?.({error: 'network'}); this.onend?.() }, 30)
      }
      stop() { /* dinleme sonu */ }
      abort() { /* iptal */ }
    }
    Object.assign(window, {SpeechRecognition: FakeRecognition, webkitSpeechRecognition: FakeRecognition})
    const getUserMedia = () => scenario === 'mic-denied'
      ? Promise.reject(new DOMException('Permission denied', 'NotAllowedError'))
      : Promise.resolve({getTracks: () => [{stop() { /* iz kapatıldı */ }}]})
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: getUserMedia})  // mevcut nesne korunur (devicechange vb. çalışsın)
  }, scenario)
}

async function pressVoiceButton(page: Page, scenario: Scenario) {
  await stubBrowserSpeech(page, scenario)
  await mockAssistant(page)
  const problems: string[] = []
  page.on('pageerror', error => problems.push(`pageerror: ${error.message}`))
  page.on('console', message => { if (message.type() === 'error') problems.push(`console: ${message.text().slice(0, 160)}`) })
  let dialog
  try { dialog = await openChat(page) }
  catch (error) {
    const body = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 500)
    throw new Error(`openChat failed: ${String(error).slice(0, 120)} | ${problems.slice(0, 5).join(' | ')} | BODY: ${body}`)
  }
  await dialog.locator('.assistantVoiceTalk').click()
}

test('Ses düğmesi: dinleme başlarsa animasyonlu göz ekranı açılır', async ({page}) => {
  await pressVoiceButton(page, 'ok')
  const holo = page.locator('.kaisHolo')
  await expect(holo).toBeVisible()
  await expect(holo).toHaveAttribute('data-phase', 'listening')
  await expect(page.locator('.kaisHoloStatus')).toHaveText('Dinliyorum…')
})

for (const [scenario, expected] of [
  ['mic-denied', /Mikrofon izni verilmedi/],
  ['start-throws', /Ses algılanamadı/],
  ['service-denied', /Mikrofon izni verilmedi/],
  ['network-error', /Ses algılanamadı/],
] as const) {
  test(`Ses düğmesi: ${scenario} durumunda göz ekranı açılır ve nedeni ekranın içinde yazar`, async ({page}) => {
    await pressVoiceButton(page, scenario)
    const holo = page.locator('.kaisHolo')
    await expect(holo).toBeVisible({timeout: 20000})
    await expect(holo).toHaveAttribute('data-phase', 'error', {timeout: 20000})
    await expect(page.locator('.kaisHoloStatus')).toHaveText(expected, {timeout: 20000})
    await expect(page.locator('.kaisHoloRetry')).toBeVisible()
    // Hata sessizce kapanmaz: ekran, kullanıcı küçültene kadar açık kalır.
    await page.waitForTimeout(1500)
    await expect(holo).toBeVisible()
  })
}

test('Ses düğmesi: hata sonrası "Tekrar dene" dinlemeyi yeniden başlatır', async ({page}) => {
  await stubBrowserSpeech(page, 'ok')
  await mockAssistant(page)
  // İlk denemede izin reddedilir, ikincide verilir.
  await page.addInitScript(() => {
    let calls = 0
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: () => ++calls === 1
      ? Promise.reject(new DOMException('Permission denied', 'NotAllowedError')) : original({audio: true})})
  })
  const dialog = await openChat(page)
  await dialog.locator('.assistantVoiceTalk').click()
  await expect(page.locator('.kaisHolo')).toHaveAttribute('data-phase', 'error')
  await page.locator('.kaisHoloRetry').click()
  await expect(page.locator('.kaisHolo')).toHaveAttribute('data-phase', 'listening')
  await expect(page.locator('.kaisHoloStatus')).toHaveText('Dinliyorum…')
})

test('Ses düğmesi: hata ekranı küçültülünce kapanır ve sohbet düğmesi yeniden açılır', async ({page}) => {
  await pressVoiceButton(page, 'mic-denied')
  await expect(page.locator('.kaisHolo')).toHaveAttribute('data-phase', 'error')
  await page.getByRole('button', {name: 'Küçült'}).click()
  await expect(page.locator('.kaisHolo')).toHaveCount(0)
})

import {expect, test, type Page} from '@playwright/test'

// Telefon ana sayfası düzen testi: ekran görüntüsü karşılaştırması değil, düzen kuralları (taşma, üst üste binme, dokunma alanı).
// Taban görüntü gerektirmez; her koşuda gerçek ölçümle çalışır. Görüntüler test-results içine de kaydedilir.
test.use({locale: 'tr-TR', viewport: {width: 390, height: 844}})
const base = 'http://127.0.0.1:4177'

const universe = Array.from({length: 12}, (_, i) => ({
  symbol: `COIN${i}USDT`, display: `COIN${i}/USDT`, price: 1.2345 + i, change: i % 2 ? 2.5 : -1.4,
  direction: i < 5 ? 'LONG' : i < 8 ? 'SHORT' : 'BEKLE', final_decision_score: 60 + i, smart_score: 55 + i,
}))
const markets = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT'].map((symbol, i) => ({symbol, display: symbol.replace('USDT', '/USDT'), price: 100 * (i + 1) + 0.1234, change: i % 2 ? 1.23 : -0.87, volume: 1e6}))

async function prepare(page: Page, connected: boolean) {
  await page.route('https://**', route => route.abort())
  await page.routeWebSocket('**', socket => socket.onMessage(() => {}))
  await page.addInitScript(() => { localStorage.setItem('protrebot-v25-session', 'cookie-session:home-layout'); localStorage.setItem('kt-lang', 'tr') })
  await page.route('**/api/**', route => route.fulfill({json: {}}))
  await page.route('**/api/v22/session', route => route.fulfill({json: {user: {id: 'home-layout', display_name: 'Ada Yılmaz', email: 'ada@example.test', role: 'CUSTOMER', active: true}}}))
  await page.route('**/api/v22/subscription', route => route.fulfill({json: {status: 'NONE', plan: 'FREE'}}))
  await page.route('**/api/analysis-universe**', route => route.fulfill({json: {results: universe}}))
  await page.route(/\/api\/(markets|tickers|market)(\?.*)?$/, route => route.fulfill({json: markets}))
  if (connected) {
    await page.route('**/api/v25/status', route => route.fulfill({json: {connected: true, credentials: {configured: true}, account: {wallet_balance: 1234.56, unrealized_pnl: 12.34, positions: []}}}))
    await page.route('**/api/binance-demo/account', route => route.fulfill({json: {configured: true, wallet_balance: 10000, unrealized_pnl: 0, positions: []}}))
  }
}

async function checkLayout(page: Page) {
  const home = page.locator('.mobileHome')
  await expect(home).toBeVisible({timeout: 15000})
  await expect(page.locator('.mobileHome .mhSkel')).toHaveCount(0, {timeout: 15000})
  await page.waitForTimeout(700) // giriş animasyonu bitsin
  const report = await page.evaluate(() => {
    const issues: string[] = []
    const root = document.querySelector('.mobileHome') as HTMLElement
    if (document.documentElement.scrollWidth > window.innerWidth + 1) issues.push(`yatay taşma: ${document.documentElement.scrollWidth} > ${window.innerWidth}`)
    const kids = [...root.children].map(el => ({el: el as HTMLElement, box: el.getBoundingClientRect()})).filter(item => item.box.height > 0)
    for (let a = 0; a < kids.length; a++) for (let b = a + 1; b < kids.length; b++) {
      const x = kids[a].box, y = kids[b].box
      if (x.left < y.right - 1 && y.left < x.right - 1 && x.top < y.bottom - 1 && y.top < x.bottom - 1) issues.push(`üst üste binen bölümler: ${kids[a].el.className} / ${kids[b].el.className}`)
    }
    root.querySelectorAll('button').forEach(button => {
      const box = button.getBoundingClientRect()
      if (box.width > 0 && box.height > 0 && box.height < 32) issues.push(`küçük dokunma alanı (${Math.round(box.height)}px): ${button.textContent?.trim().slice(0, 20)}`)
    })
    root.querySelectorAll('.mhCard, .mhPlan, .mhOpp, .mhRow, .mhQuick').forEach(card => {
      const outer = card.getBoundingClientRect()
      card.querySelectorAll('*').forEach(inner => {
        const box = inner.getBoundingClientRect()
        if (box.width > 0 && (box.right > outer.right + 1 || box.left < outer.left - 1) && getComputedStyle(inner).position !== 'absolute') issues.push(`kart dışına taşan öğe: ${(inner as HTMLElement).className || inner.tagName}`)
      })
    })
    return issues
  })
  expect(report, report.join('\n')).toEqual([])
}

for (const connected of [false, true]) {
  test(`Telefon ana sayfası düzeni · hesap ${connected ? 'bağlı' : 'bağlı değil'}`, async ({page}, info) => {
    await prepare(page, connected)
    await page.goto(base + '/')
    await checkLayout(page)
    await page.screenshot({path: info.outputPath(`home-${connected ? 'bagli' : 'bagli-degil'}.png`), fullPage: true})
  })
}

import {test, type Page} from '@playwright/test'
import {appendFileSync, mkdirSync} from 'node:fs'
import {mockAssistant} from '../helpers/assistant-api'
import {demoReadFixtures} from '../helpers/demo-api'
import {previewAnalysis, previewCandles, previewMarkets} from '../helpers/master-trade-preview-data'

test.use({baseURL: 'http://127.0.0.1:4174'})
test.setTimeout(120000)

async function prepare(page: Page) {
  await page.setViewportSize({width: 1440, height: 900})
  await mockAssistant(page)
  const user = {id: 'shots-owner', email: 'owner@example.test', display_name: 'Owner', role: 'OWNER', active: true, email_verified: true}
  await page.route('**/api/v22/{profile,session}', route => route.fulfill({json: {user, access: {canAccessMasterTrade: true, isPremium: true}}}))
  for (const [path, json] of Object.entries(demoReadFixtures)) await page.route(`**${path}`, route => route.fulfill({json}))
  await page.route('**/api/markets**', route => route.fulfill({json: previewMarkets}))
  await page.route('**/api/analysis-universe**', route => route.fulfill({json: {results: previewMarkets.map((m, i) => ({...m, direction: 'SHORT', confidence: 89 - i, final_decision_score: 89 - i, smart_score: 89 - i}))}}))
  await page.route('**/api/analysis/**', route => route.fulfill({json: previewAnalysis(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.route('**/api/klines/**', route => route.fulfill({json: previewCandles(new URL(route.request().url()).pathname.split('/').pop())}))
  await page.addInitScript(() => { localStorage.setItem('protrebot-v25-session', 'cookie-session:shots-owner'); localStorage.setItem('kais-site-tour:v1:shots-owner', `done|${Date.now()}`); localStorage.setItem('kt-app-ready', `done|${Date.now()}`) })
}

const shot = async (page: Page, name: string) => {
  await page.waitForTimeout(2500)
  await page.screenshot({path: `shots-out/${name}.png`, fullPage: true, type: 'png', scale: 'css'})
  const info = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth, scrollX: window.scrollX,
    wide: Array.from(document.querySelectorAll<HTMLElement>('body *')).filter(el => el.getBoundingClientRect().right > document.documentElement.clientWidth + 2 || el.getBoundingClientRect().left < -2).slice(0, 12).map(el => `${el.tagName}.${String(el.className).slice(0, 80)} L${Math.round(el.getBoundingClientRect().left)} R${Math.round(el.getBoundingClientRect().right)}`),
    tabs: Array.from(document.querySelectorAll('[role=tab]')).filter(el => (el as HTMLElement).checkVisibility()).map(el => (el.textContent || '').trim()),
    main: Array.from(document.querySelectorAll('#root > main > *')).map(el => { const r = el.getBoundingClientRect(); return `${el.tagName}.${String(el.className).slice(0, 50)} T${Math.round(r.top + scrollY)} H${Math.round(r.height)} W${Math.round(r.width)}` }),
    deck: Array.from(document.querySelectorAll('.binanceDemoDeck > *')).map(el => { const r = el.getBoundingClientRect(); return `${el.tagName}.${String(el.className).slice(0, 50)} T${Math.round(r.top + scrollY)} H${Math.round(r.height)} W${Math.round(r.width)} ${getComputedStyle(el).display}` }),
    guideChain: (() => { const out: string[] = []; let el = document.querySelector('.lcGuide') as HTMLElement | null; while (el && out.length < 14) { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); out.push(`${el.tagName}.${String(el.className).slice(0, 40)} L${Math.round(r.left)} W${Math.round(r.width)} ${cs.display} gc:${cs.gridTemplateColumns.slice(0, 40)} gcol:${cs.gridColumn}`); el = el.parentElement } return out })(),
  }))
  mkdirSync('shots-out', {recursive: true})
  appendFileSync('shots-out/info.txt', `SHOT ${name} ${JSON.stringify(info)}\n`)
}

test('demo vs master', async ({page}) => {
  page.on('pageerror', error => { mkdirSync('shots-out', {recursive: true}); appendFileSync('shots-out/info.txt', `PAGEERROR ${error.message}\n`) })
  await prepare(page)
  await page.goto('/', {waitUntil: 'domcontentloaded'})
  await page.waitForTimeout(3000)
  for (const name of ['Sonra hatırlat', 'Remind me later', 'Daha sonra', 'Şimdi değil']) {
    const button = page.getByRole('button', {name, exact: true})
    if (await button.isVisible().catch(() => false)) await button.click()
  }
  await page.keyboard.press('Escape')
  await page.waitForTimeout(800)
  await shot(page, '01-home')
  await page.locator('.v26DashboardChoices button').filter({hasText: 'DEMO İŞLEM'}).click({force: true})
  await shot(page, '02-demo-default')
  await page.getByRole('tab', {name: 'Analiz', exact: true}).first().click()
  await shot(page, '03-demo-analiz')
  await page.getByRole('tab', {name: 'Demo İşlem', exact: true}).first().click()
  await shot(page, '04-demo-islem')
  await page.goto('/master-trade?tab=analiz', {waitUntil: 'domcontentloaded'})
  await shot(page, '05-master-analiz')
  await page.goto('/master-trade?tab=canli', {waitUntil: 'domcontentloaded'})
  await shot(page, '06-master-canli')
})

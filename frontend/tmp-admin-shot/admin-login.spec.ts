import {expect, test} from '@playwright/test'
test('admin login screen', async ({page}, info) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('https://**', r => r.abort())
  await page.route('**/api/**', r => new URL(r.request().url()).pathname === '/api/web/access/check'
    ? r.fulfill({json: {authorized: true}}) : r.fulfill({status: 401, json: {detail: 'Not authenticated'}}))
  await page.goto('/admin.html')
  await expect(page.getByRole('heading', {name: 'KaisTrade Yönetim'})).toBeVisible()
  await expect(page).toHaveTitle('KaisTrade Yönetim')
  await expect(page.getByText('Yeni hesap oluştur')).toHaveCount(0)
  await expect(page.getByText('Google ile devam et')).toHaveCount(0)
  await expect(page.locator('.authIntro, .tickerTape, .authMobileMarkets')).toHaveCount(0)
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  await page.screenshot({path: `../../shots/admin-login-${info.project.name}.png`})
  // main site login unchanged
  await page.goto('/')
  await page.waitForTimeout(1500)
  await page.screenshot({path: `../../shots/main-login-${info.project.name}.png`})
  await expect(page.getByText('Yeni hesap oluştur')).toBeVisible()
  expect(errors).toEqual([])
})

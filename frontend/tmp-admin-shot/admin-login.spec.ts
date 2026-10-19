import {expect, test} from '@playwright/test'

test('staff login screen', async ({page}, info) => {
  const errors: string[] = []
  const logins: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('https://**', r => r.abort())
  await page.route('**/api/**', async r => {
    const path = new URL(r.request().url()).pathname
    if (path === '/api/web/access/check') return r.fulfill({json: {authorized: true}})
    if (path === '/api/v22/auth/login') { logins.push(r.request().postData() ?? ''); return r.fulfill({status: 401, json: {detail: 'Invalid credentials'}}) }
    return r.fulfill({status: 401, json: {detail: 'Not authenticated'}})
  })
  await page.goto('/admin.html')
  const phone = info.project.name === 'phone'
  await expect(page.getByRole('heading', {name: 'Welcome Back'})).toBeVisible()
  await expect(page).toHaveTitle('KaisTrade Yönetim')
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  if (phone) await expect(page.locator('.staffBrief')).toBeHidden()
  else {
    await expect(page.getByRole('heading', {name: 'Keep everything running smoothly'})).toBeVisible()
    await expect(page.locator('.staffTasks li')).toHaveCount(6)
  }
  // Heading font must be sans (the old staff card inherited Georgia).
  const font = await page.locator('.staffForm h2').evaluate(e => getComputedStyle(e).fontFamily)
  expect(font).toContain('Plus Jakarta Sans')
  await expect(page.getByText('Yeni hesap oluştur')).toHaveCount(0)
  await expect(page.getByText('Google ile devam et')).toHaveCount(0)
  await expect(page.getByText('Production')).toHaveCount(0)
  await expect(page.getByRole('link', {name: 'Forgot password?'})).toHaveAttribute('href', 'https://kaistrade.com/forgot-password')
  // No horizontal overflow
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
  await page.screenshot({path: `../../shots/admin-login-${info.project.name}.png`})
  // Password reveal + submit wiring still use the existing handlers.
  await page.getByLabel('Email Address').fill('staff@example.test')
  await page.getByLabel('Password', {exact: true}).fill('not-a-real-password-1')
  await page.getByRole('button', {name: 'Show password'}).click()
  await expect(page.getByLabel('Password', {exact: true})).toHaveAttribute('type', 'text')
  await page.getByRole('button', {name: /Sign In/}).click()
  await expect.poll(() => logins.length).toBe(1)
  expect(JSON.parse(logins[0])).toMatchObject({email: 'staff@example.test', remember: true})
  await expect(page.locator('.staffMessage')).toBeVisible()
  await page.screenshot({path: `../../shots/admin-login-${info.project.name}-error.png`})
  // Main site login unchanged.
  await page.goto('/')
  await expect(page.getByText('Yeni hesap oluştur')).toBeVisible()
  expect(errors).toEqual([])
})

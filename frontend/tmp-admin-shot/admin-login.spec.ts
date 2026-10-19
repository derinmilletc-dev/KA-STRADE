import {expect, test} from '@playwright/test'

test('staff login screen', async ({page}, info) => {
  const errors: string[] = []
  const logins: string[] = []
  const notes: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('https://**', r => r.abort())
  await page.route('**/api/**', async r => {
    const path = new URL(r.request().url()).pathname
    if (path === '/api/web/access/check') return r.fulfill({json: {authorized: true}})
    if (path === '/api/v22/auth/login') { logins.push(r.request().postData() ?? ''); return r.fulfill({status: 401, json: {detail: 'Invalid credentials'}}) }
    return r.fulfill({status: 401, json: {detail: 'Not authenticated'}})
  })
  await page.goto('/admin.html')
  const width = page.viewportSize()!.width
  const mobile = width < 768
  await expect(page.getByRole('heading', {name: 'Welcome Back'})).toBeVisible()
  await expect(page).toHaveTitle('KaisTrade Yönetim')
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/)
  expect(await page.locator('.staffForm h2').evaluate(e => getComputedStyle(e).fontFamily)).toContain('Plus Jakarta Sans')
  await expect(page.getByText('Yeni hesap oluştur')).toHaveCount(0)
  await expect(page.getByText('Google ile devam et')).toHaveCount(0)
  await expect(page.getByRole('link', {name: 'Forgot password?'})).toHaveAttribute('href', 'https://kaistrade.com/forgot-password')
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 'horizontal overflow').toBeLessThanOrEqual(0)
  const toggle = page.getByRole('button', {name: 'Daily Management Checklist'})
  const tasks = page.locator('#staff-checklist li')
  if (mobile) {
    await expect(page.getByRole('heading', {name: 'Keep everything running smoothly'})).toBeHidden()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(tasks.first()).toBeHidden()
    for (const [name, loc] of [['email', page.getByLabel('Email Address')], ['password', page.getByLabel('Password', {exact: true})], ['submit', page.getByRole('button', {name: /Sign In/})]] as const) {
      const box = await loc.boundingBox()
      expect(box!.height, `${name} height`).toBeGreaterThanOrEqual(48)
    }
    const submit = await page.getByRole('button', {name: /Sign In/}).boundingBox()
    notes.push(`Sign In bottom=${Math.round(submit!.y + submit!.height)} viewport=${page.viewportSize()!.height}`)
    expect(submit!.y + submit!.height, 'Sign In visible without scrolling').toBeLessThanOrEqual(page.viewportSize()!.height)
    // Form comes before the checklist in keyboard order.
    const order = await page.evaluate(() => {
      const f = document.querySelector('.staffSubmit')!, t = document.querySelector('.staffChecklistToggle')!
      return Boolean(f.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING)
    })
    expect(order).toBe(true)
    await page.screenshot({path: `../../shots/${info.project.name}-collapsed.png`, fullPage: true})
    await toggle.focus(); await page.keyboard.press('Enter')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(tasks).toHaveCount(6); await expect(tasks.first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 'overflow expanded').toBeLessThanOrEqual(0)
    await page.screenshot({path: `../../shots/${info.project.name}-expanded.png`, fullPage: true})
  } else {
    await expect(page.getByRole('heading', {name: 'Keep everything running smoothly'})).toBeVisible()
    await expect(toggle).toBeHidden()
    await expect(tasks).toHaveCount(6); await expect(tasks.first()).toBeVisible()
    await page.screenshot({path: `../../shots/${info.project.name}.png`})
  }
  // Existing handlers: password reveal and submit still drive the real login request.
  await page.getByLabel('Email Address').fill('staff@example.test')
  await page.getByLabel('Password', {exact: true}).fill('not-a-real-password-1')
  await page.getByRole('button', {name: 'Show password'}).click()
  await expect(page.getByLabel('Password', {exact: true})).toHaveAttribute('type', 'text')
  await page.getByRole('button', {name: /Sign In/}).click()
  await expect.poll(() => logins.length).toBe(1)
  expect(JSON.parse(logins[0])).toMatchObject({email: 'staff@example.test', remember: true})
  await expect(page.locator('.staffMessage')).toBeVisible()
  await page.goto('/')
  await expect(page.getByText('Yeni hesap oluştur')).toBeVisible()
  expect(errors).toEqual([])
  test.info().annotations.push({type: 'notes', description: notes.join('; ')})
  console.log(`[${info.project.name}] ${notes.join('; ')}`)
})

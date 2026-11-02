import {expect, test} from '@playwright/test'
import {mockAssistant} from './helpers/assistant-api'
test.use({baseURL: 'http://127.0.0.1:4174', locale: 'tr-TR'})
const cors = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'}
const mk = (id: string, type: string, severity: string, title: string, message: string, ts: string, read = false) => ({id, type, severity, title, message, timestamp: ts, read, target: 'system-health'})
const items = [
  mk('a1', 'ORIGINAL_LIVE_PAUSED', 'info', 'Original V2 LIVE güvenlik kilidi', 'Original V2 LIVE durduruldu: OWNER acil durdurma. Açık pozisyon otomatik kapatılmadı.', '2026-10-31T09:58:00+00:00'),
  mk('a2', 'ORIGINAL_LIVE_PAUSED', 'info', 'Original V2 LIVE güvenlik kilidi', 'Original V2 LIVE durduruldu: OWNER acil durdurma.', '2026-10-31T09:50:00+00:00'),
  mk('b1', 'LIVE_ORDER_BLOCKED', 'critical', 'LIVE işlem güvenlik uyarısı', 'Canlı emir risk veya hesap kapısında durduruldu; Binance emri gönderilmedi.', '2026-10-30T22:44:00+00:00'),
  mk('b2', 'LIVE_ORDER_BLOCKED', 'critical', 'LIVE işlem güvenlik uyarısı', 'Canlı emir risk veya hesap kapısında durduruldu; Binance emri gönderilmedi.', '2026-10-30T22:40:00+00:00', true),
  mk('c1', 'SMART_PROFIT', 'warning', 'Akıllı Kâr Koruma', 'HYPEUSDT sayaç doldu ve sonraki kapanmış mumda da puan eşiğin altında. Çıkışa hazır: net kâra geçince kapatılacak; zarardaysa beklenir.', '2026-10-30T19:15:00+00:00'),
  mk('d1', 'SCAN_DONE', 'success', 'Piyasa taraması tamamlandı', '12 coin tarandı, 3 sinyal bulundu.', '2026-10-30T18:00:00+00:00', true),
]
for (const size of [{width: 1440, height: 900}, {width: 1180, height: 700}]) {
  test(`Bildirim paneli ${size.width}`, async ({page}) => {
    await page.setViewportSize(size)
    await mockAssistant(page)
    await page.route('**/api/v21/notifications**', r => r.request().method() === 'OPTIONS' ? r.fulfill({status: 204, headers: cors}) : r.fulfill({status: 200, headers: cors, json: {items, unread: items.filter(i => !i.read).length}}))
    await page.goto('/')
    await page.getByRole('button', {name: /^Bildirimler(?:,|$)/}).click()
    const panel = page.getByRole('dialog', {name: 'Bildirimler', exact: true})
    await expect(panel).toBeVisible()
    await expect(panel).toHaveAttribute('data-state', 'data')
    const box = (await panel.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(size.width + 0.5)
    expect(box.y + box.height).toBeLessThanOrEqual(size.height + 0.5)
    await page.waitForTimeout(400)
    await page.screenshot({path: `test-results/notif-${size.width}.png`})
  })
}

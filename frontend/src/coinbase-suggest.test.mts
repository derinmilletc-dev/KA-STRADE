// Çalıştırma: node --test frontend/src/coinbase-suggest.test.mts
import assert from 'node:assert'
import {test} from 'node:test'
import {blockedAdvice, itemReward, itemSelected, itemSub, limitFormValue, limitLabel, limitsPayload, orderCapacity, reasonShort, suggestPayload, type SuggestItem, type SuggestResult} from './coinbase-suggest.ts'

const ticket = {productId: 'ETP-20DEC30-CDE', side: 'BUY' as const, stopPrice: '', marginUsd: '60', contracts: '', limitPrice: '', takeProfit: '', rest: false, leverage: '5'}
const item = (over: Partial<SuggestItem> = {}): SuggestItem => ({stop_pct: 2, reward_r: 2, ok: true, reasons: [], stop_price: '2940', take_profit_price: '3120', contracts: 4, worst_case_loss_usd: '2.35', leverage: '2', notional_usd: '120', reward_usd: '4.8', ...over})
const result = (items: SuggestItem[], shared: string[] = []): SuggestResult => ({items, recommended_stop_pct: null, shared_reasons: shared, state: {bid: '3000', ask: '3000.5', open_orders: 5, open_positions: 5, max_open_orders: 5}})

test('suggestPayload: hazır değilken null, hazırken ticketPayload ile aynı kaldıraç/sözleşme kuralı', () => {
  assert.strictEqual(suggestPayload({...ticket, productId: ''}), null)
  assert.strictEqual(suggestPayload({...ticket, marginUsd: ''}), null)
  assert.deepStrictEqual(suggestPayload(ticket), {product_id: 'ETP-20DEC30-CDE', side: 'BUY', margin_usd: '60', leverage: 5})
  assert.deepStrictEqual(suggestPayload({...ticket, contracts: '3'}), {product_id: 'ETP-20DEC30-CDE', side: 'BUY', margin_usd: '60', contracts: 3})
  assert.strictEqual(suggestPayload({...ticket, contracts: '0'}), null)
  assert.strictEqual((suggestPayload({...ticket, limitPrice: '2990'}) as any).entry_limit_price, '2990')
  assert.strictEqual(suggestPayload({...ticket, limitPrice: 'abc'}), null)
})

test('itemSub / itemReward: geçen kartta sözleşme + en kötü kayıp, geçmeyende kısa sebep', () => {
  assert.strictEqual(itemSub(item()), '4 sözleşme · en çok −2,35 USD')
  assert.strictEqual(itemReward(item()), 'Hedefte +4,8 USD · 2R')
  assert.strictEqual(itemSub(item({ok: false, reasons: ['En kötü kayıp 9 USD; sınır 5 USD.']})), 'Kayıp limitine sığmıyor')
  assert.strictEqual(itemReward(item({ok: false})), null)
})

test('reasonShort sunucu sebeplerini tanır, bilinmeyeni genel etikete çevirir', () => {
  assert.strictEqual(reasonShort('Alım gücü yetersiz: istenen teminat 60'), 'Alım gücü yetersiz')
  assert.strictEqual(reasonShort('Açık emir sınırı (5) dolu; yeni koruma emri için yer yok.'), 'Açık emir sınırı dolu')
  assert.strictEqual(reasonShort('Stop girişe çok yakın; hemen tetiklenir.'), 'Stop çok yakın')
  assert.strictEqual(reasonShort('bambaşka'), 'Ön kontrolden geçmez')
})

test('itemSelected: yalnız stop ve hedef birebir aynıysa seçili', () => {
  assert.ok(itemSelected(item(), {stopPrice: '2940', takeProfit: '3120.0'}))
  assert.ok(!itemSelected(item(), {stopPrice: '2940', takeProfit: ''}))
  assert.ok(!itemSelected(item(), {stopPrice: '2941', takeProfit: '3120'}))
})

test('blockedAdvice: biri geçiyorsa null; hiçbiri geçmiyorsa nedenine göre yönlendirir', () => {
  assert.strictEqual(blockedAdvice(result([item(), item({ok: false, reasons: ['x']})])), null)
  assert.match(blockedAdvice(result([item({ok: false, reasons: ['Açık emir sınırı (5) dolu; yeni koruma emri için yer yok.']})], ['Açık emir sınırı (5) dolu; yeni koruma emri için yer yok.'])) ?? '', /5\/5/)
  assert.match(blockedAdvice(result([item({ok: false, reasons: ['Alım gücü yetersiz: x']})])) ?? '', /Alım gücü/)
})

test('orderCapacity: bracket tek emir; kalan işlem sayısı ve ton', () => {
  assert.deepStrictEqual(orderCapacity(2, 5), {used: 2, max: 5, free: 3, text: '3 işlem daha açabilirsin.', tone: 'ok'})
  assert.strictEqual(orderCapacity(4, 5).tone, 'warn')
  assert.strictEqual(orderCapacity(5, 5).tone, 'bad')
  assert.strictEqual(orderCapacity(null, 5).free, 5)
  assert.strictEqual(orderCapacity(9, 5).free, 0)
})

test('limitLabel: sınırsız, kaldıraç ve stop payı gösterimi', () => {
  assert.strictEqual(limitLabel('max_loss_usd', '1000000'), 'Sınırsız')
  assert.strictEqual(limitLabel('max_loss_usd', '25'), '25 USD')
  assert.strictEqual(limitLabel('leverage_max', '100'), '100x')
  assert.strictEqual(limitLabel('stop_limit_gap_pct', '0.003'), '%0,3')
  assert.strictEqual(limitLabel('max_loss_usd', undefined), '—')
})

test('limitsPayload: boş=özel limit yok, gap yüzdeden orana çevrilir, sınır dışı reddedilir', () => {
  const bounds = {max_loss_usd: ['0.1', '1000000'], daily_loss_limit_usd: ['1', '1000000'], leverage_max: ['1', '100'], stop_limit_gap_pct: ['0.0005', '0.02']} as any
  const ok = limitsPayload({max_loss_usd: '5', daily_loss_limit_usd: '', leverage_max: '10', stop_limit_gap_pct: '0,5'}, {bounds})
  assert.deepStrictEqual(ok, {ok: true, body: {max_loss_usd: '5', daily_loss_limit_usd: null, leverage_max: '10', stop_limit_gap_pct: '0.005'}})
  const bad = limitsPayload({max_loss_usd: '0', daily_loss_limit_usd: '', leverage_max: '', stop_limit_gap_pct: ''}, {bounds})
  assert.strictEqual(bad.ok, false)
  assert.strictEqual((limitsPayload({max_loss_usd: '', daily_loss_limit_usd: '', leverage_max: '', stop_limit_gap_pct: '5'}, {bounds}) as any).field, 'stop_limit_gap_pct')
  assert.strictEqual(limitsPayload({max_loss_usd: 'abc', daily_loss_limit_usd: '', leverage_max: '', stop_limit_gap_pct: ''}, {bounds}).ok, false)
})

test('limitFormValue: gap yüzde olarak, boş özel değer boş form', () => {
  assert.strictEqual(limitFormValue('stop_limit_gap_pct', {stop_limit_gap_pct: '0.005'}), '0.5')
  assert.strictEqual(limitFormValue('max_loss_usd', {}), '')
  assert.strictEqual(limitFormValue('max_loss_usd', {max_loss_usd: '25'}), '25')
})

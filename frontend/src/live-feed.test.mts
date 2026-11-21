// Çalıştırma: node frontend/src/live-feed.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert'
import {friendlyFeed} from './live-feed.ts'

const t = (s: number) => new Date(Date.UTC(2026, 10, 21, 3, 42, s)).toISOString()
const raw = [
  {kind: 'LIVE_ORDER_TIMING', message: 'HUSDT emri 9 sn sürdü', created_at: t(10)},
  {kind: 'LIVE_ALGO_UPDATE', symbol: 'HUSDT', status: 'NEW', message: 'HUSDT Stop/TP koruması · NEW', created_at: t(9)},
  {kind: 'LIVE_ALGO_UPDATE', symbol: 'HUSDT', status: 'NEW', message: 'HUSDT Stop/TP koruması · NEW', created_at: t(9)},
  {kind: 'LIVE_ALGO_UPDATE', symbol: 'HUSDT', status: 'NEW', message: 'x', created_at: t(8)},
  {kind: 'LIVE_FILL', symbol: 'HUSDT', status: 'FILLED', side: 'SELL', price: 0.0107, reduce_only: false, created_at: t(7)},
  {kind: 'LIVE_FILL', symbol: 'HUSDT', status: 'PARTIALLY_FILLED', side: 'SELL', created_at: t(6)},
  {kind: 'LIVE_FILL', symbol: 'HUSDT', status: 'PARTIALLY_FILLED', side: 'SELL', created_at: t(5)},
  {kind: 'LIVE_ORDER_UPDATE', symbol: 'HUSDT', status: 'NEW', message: 'HUSDT NEW · NEW', created_at: t(4)},
  {kind: 'READ_ONLY_CONNECTED', message: 'Canlı hesap salt-okunur bağlantısı doğrulandı; emir gönderilmedi.', created_at: t(3)},
  {kind: 'LIVE_STREAM_CONNECTED', message: 'Binance canlı emir ve pozisyon kullanıcı akışı bağlandı.', created_at: t(2)},
  {kind: 'LIVE_ARM', message: 'Canlı yeni giriş izni 24 saat için açıldı.', created_at: t(1)},
]
const feed = friendlyFeed(raw)
assert.deepStrictEqual(feed.map(item => item.title), ['H koruması kuruldu', 'H SHORT girişi doldu', 'Canlı yeni giriş izni 24 saat için açıldı.'])
assert.strictEqual(feed[1].detail?.startsWith('giriş fiyatı'), true)

// Kapanış dolumu: kâr/zarar işaretli yazılır.
const closed = friendlyFeed([{kind: 'LIVE_FILL', symbol: 'ETHUSDT', status: 'FILLED', side: 'BUY', price: 3000, reduce_only: true, realized_pnl_fill: -1.5, created_at: t(1)}])
assert.strictEqual(closed[0].title, 'ETH pozisyonu kapandı')
assert.strictEqual(closed[0].tone, 'warn')
assert.ok(closed[0].detail?.includes('−1,5 USDT'))

// Koruma reddi uyarı grubundadır; teknik kod görünmez.
const rejected = friendlyFeed([{kind: 'LIVE_ALGO_UPDATE', symbol: 'ABCUSDT', status: 'REJECTED', created_at: t(1)}])
assert.strictEqual(rejected[0].group, 'warn')
assert.ok(!/REJECTED|ALGO/.test(JSON.stringify(rejected)))

// Boş ve bozuk veri güvenli.
assert.deepStrictEqual(friendlyFeed(undefined), [])
assert.deepStrictEqual(friendlyFeed([{}, {kind: 'LIVE_FILL'}]), [])
// Sınır.
assert.strictEqual(friendlyFeed(Array.from({length: 40}, (_, i) => ({kind: 'LIVE_ARM', message: `m${i}`, created_at: t(i)})), 5).length, 5)
console.log('live-feed testleri geçti')

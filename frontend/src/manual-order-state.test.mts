// Çalıştırma: node --test frontend/src/manual-order-state.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {analysisFillBlock, checkRows, normalizeSymbol, priceMap, stopDistance, switchSymbol, targetDistancePct} from './manual-order-state.ts'

const order = {symbol: 'BTCUSDT', direction: 'SHORT', order_type: 'LIMIT', margin_usdt: '12', leverage: '3', tp_split: '50-30-20', limit_expiry_minutes: 60, limit_price: '80000', stop_loss: '80500', tp1: '79500', tp2: '79000', tp3: '78500'}

test('coin değişince fiyata bağlı alanlar silinir, diğer ayarlar korunur', () => {
  const next = switchSymbol(order, 'ARIAUSDT')
  assert.equal(next.symbol, 'ARIAUSDT')
  for (const key of ['limit_price', 'stop_loss', 'tp1', 'tp2', 'tp3'] as const) assert.equal(next[key], '')
  assert.equal(next.direction, 'SHORT')
  assert.equal(next.order_type, 'LIMIT')
  assert.equal(next.margin_usdt, '12')
  assert.equal(next.leverage, '3')
  assert.equal(next.tp_split, '50-30-20')
  assert.equal(next.limit_expiry_minutes, 60)
})

test('girdi nesnesi değişmez', () => {
  const copy = JSON.parse(JSON.stringify(order))
  switchSymbol(order, 'ETHUSDT')
  assert.deepEqual(order, copy)
})

test('aynı coin yeniden seçilirse form aynen kalır (biçim farkı sayılmaz)', () => {
  assert.equal(switchSymbol(order, 'BTCUSDT'), order)
  assert.equal(switchSymbol(order, 'btc/usdt'), order)
})

test('sembol normalleştirme', () => {
  assert.equal(normalizeSymbol(' btc/usdt '), 'BTCUSDT')
  assert.equal(normalizeSymbol(undefined), '')
})

test('analizden doldurma yalnız aynı coinde izinlidir', () => {
  assert.equal(analysisFillBlock('BTCUSDT', 'BTCUSDT'), null)
  assert.equal(analysisFillBlock('btc/usdt', 'BTCUSDT'), null)
  const reason = analysisFillBlock('ARIAUSDT', 'BTCUSDT')
  assert.ok(reason && reason.includes('BTCUSDT') && reason.includes('ARIAUSDT'))
  assert.ok(analysisFillBlock('ARIAUSDT', ''))
  assert.ok(analysisFillBlock('', 'BTCUSDT'))
})

test('stop mesafesi: LONG, SHORT, farklı fiyat seviyeleri ve küçük fiyatlar', () => {
  assert.deepEqual(stopDistance('LONG', 100, 99), {pct: 1, sideOk: true})
  assert.deepEqual(stopDistance('SHORT', 100, 101.5), {pct: 1.5, sideOk: true})
  assert.equal(stopDistance('LONG', 100, 101)?.sideOk, false)
  assert.equal(stopDistance('SHORT', 100, 99)?.sideOk, false)
  const tiny = stopDistance('LONG', 0.0883, 0.0874)
  assert.ok(tiny && Math.abs(tiny.pct - 1.0192) < 0.001)
  const mixed = stopDistance('LONG', 0.0883, 80020.8)   // başka coinin stopu: saçma yüzde ve yanlış taraf, ama sonlu
  assert.ok(mixed && mixed.sideOk === false && mixed.pct > 1e6)
})

test('stop mesafesi geçersiz fiyatta null', () => {
  for (const [entry, stop] of [[0, 1], [1, 0], [NaN, 1], [1, Infinity], [-1, 1]]) assert.equal(stopDistance('LONG', entry, stop), null)
  assert.equal(targetDistancePct(0, 5), null)
  assert.equal(targetDistancePct(100, 103), 3)
})

test('fiyat haritası LONG: soldan sağa stop, giriş, TP1-3; konumlar 0-100 içinde ve artan', () => {
  const map = priceMap('LONG', 100, 98, [102, 104, 106])
  assert.equal(map.valid, true)
  assert.deepEqual(map.points.map(p => p.key), ['stop', 'entry', 'tp1', 'tp2', 'tp3'])
  for (let i = 1; i < map.points.length; i++) assert.ok(map.points[i].pos > map.points[i - 1].pos)
  assert.ok(map.points.every(p => p.pos >= 0 && p.pos <= 100))
})

test('fiyat haritası SHORT: fiyat artan sırada stop sağda, hedefler solda', () => {
  const map = priceMap('SHORT', 100, 102, [98, 96, 94])
  assert.equal(map.valid, true)
  assert.deepEqual(map.points.map(p => p.key), ['tp3', 'tp2', 'tp1', 'entry', 'stop'])
})

test('fiyat haritası yönle uyumsuz veya eksik değerde çizilmez', () => {
  assert.equal(priceMap('LONG', 100, 102, [98, 96, 94]).valid, false)
  assert.equal(priceMap('SHORT', 100, 98, [102, 104, 106]).valid, false)
  assert.equal(priceMap('LONG', 100, 98, [102, 101, 106]).valid, false)
  assert.equal(priceMap('LONG', 100, NaN, [102, 104, 106]).valid, false)
  assert.equal(priceMap('LONG', 100, 98, [102, 104, 106]).reason, '')
  assert.ok(priceMap('LONG', 100, 102, [98, 96, 94]).reason.includes('LONG'))
})

const base = {symbolKnown: true, balanceOk: true, marginOk: true, leverageOk: true, quantityOk: true, stopOk: true, targetsOk: true, dryRunPassed: false, dryRunStale: false}
const states = (input: typeof base) => Object.fromEntries(checkRows(input).map(r => [r.key, r.state]))

test('kontrol listesi: dry-run yokken borsa maddeleri bekliyor, başarılı gösterilmez', () => {
  const s = states(base)
  assert.equal(s.exchange, 'pending')
  assert.equal(s.fresh, 'pending')
  assert.equal(s.symbol, 'ok')
})

test('kontrol listesi: dry-run geçince borsa maddeleri tamam', () => {
  const s = states({...base, dryRunPassed: true})
  assert.equal(s.exchange, 'ok')
  assert.equal(s.fresh, 'ok')
})

test('kontrol listesi: emir dry-run sonrası değiştiyse güncel değil', () => {
  const s = states({...base, dryRunPassed: false, dryRunStale: true})
  assert.equal(s.exchange, 'bad')
  assert.equal(s.fresh, 'bad')
})

test('kontrol listesi: bilinmeyen değerler bekliyor, hatalılar kötü', () => {
  const s = states({...base, symbolKnown: null, balanceOk: false, quantityOk: null, stopOk: false, targetsOk: null, marginOk: false})
  assert.equal(s.symbol, 'pending')
  assert.equal(s.balance, 'bad')
  assert.equal(s.quantity, 'pending')
  assert.equal(s.stop, 'bad')
  assert.equal(s.targets, 'pending')
  assert.equal(s.margin, 'bad')
  assert.equal(checkRows(base).length, 9)
})

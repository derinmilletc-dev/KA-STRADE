// Çalıştırma: node --test frontend/src/manual-order-state.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {analysisFillBlock, normalizeSymbol, switchSymbol} from './manual-order-state.ts'

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

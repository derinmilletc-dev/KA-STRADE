import assert from 'node:assert/strict'
import test from 'node:test'

const store = new Map()
globalThis.window = {
  sessionStorage: {getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)), removeItem: key => store.delete(key)},
  setTimeout: (fn, ms) => setTimeout(fn, ms), dispatchEvent: () => true,
}
globalThis.CustomEvent = class { constructor(type) { this.type = type } }

const {markOrderPending, pendingOrderSymbol, clearOrderPending} = await import('../trade-intent.ts')

test('yeni emir sonrası coin "bekleniyor" sayılır, temizlenince kalkar', () => {
  clearOrderPending()
  assert.equal(pendingOrderSymbol(), null)
  markOrderPending('init/usdt')
  assert.equal(pendingOrderSymbol(), 'INITUSDT')
  clearOrderPending()
  assert.equal(pendingOrderSymbol(), null)
})

test('60 sn geçince bekleme kendiliğinden biter', () => {
  const realNow = Date.now
  try {
    markOrderPending('BTCUSDT')
    assert.equal(pendingOrderSymbol(), 'BTCUSDT')
    Date.now = () => realNow() + 61_000
    assert.equal(pendingOrderSymbol(), null)
  } finally { Date.now = realNow; clearOrderPending() }
})

test('boş coin işaretlenmez', () => {
  clearOrderPending()
  markOrderPending('')
  assert.equal(pendingOrderSymbol(), null)
})

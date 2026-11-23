// Çalıştırma: node frontend/src/connection-health.test.mts
import assert from 'node:assert'
import {createConnectionHealth, isConnectionFailure} from '../../connection-health.ts'

let clock = 1_000
const health = createConnectionHealth(() => clock)
assert.equal(health.get().state, 'ok')

// tek hata bandı açmaz, iki art arda hata açar
health.reportFailure()
assert.equal(health.get().state, 'ok')
clock += 2_000
health.reportFailure()
assert.equal(health.get().state, 'degraded')

// toparlanma: bildirim bir kez gelir
let recovered = 0
health.onRecovered(() => { recovered++ })
health.reportSuccess()
assert.equal(health.get().state, 'ok')
assert.equal(recovered, 1)
health.reportSuccess()
assert.equal(recovered, 1)

// eski hatalar pencere dışında kalır
health.reportFailure()
clock += 31_000
health.reportFailure()
assert.equal(health.get().state, 'ok')

// çevrimdışı → internet döndü → sunucu doğrulanana kadar degraded → başarılı yanıtla ok
health.setBrowserOnline(false)
assert.equal(health.get().state, 'offline')
health.setBrowserOnline(true)
assert.equal(health.get().state, 'degraded')
health.reportSuccess()
assert.equal(health.get().state, 'ok')
assert.equal(recovered, 2)

// sınıflandırma
assert.equal(isConnectionFailure({status: 200}), false)
assert.equal(isConnectionFailure({status: 401}), false)
assert.equal(isConnectionFailure({status: 422}), false)
assert.equal(isConnectionFailure({status: 500}), false)
assert.equal(isConnectionFailure({status: 502}), true)
assert.equal(isConnectionFailure({status: 504}), true)
assert.equal(isConnectionFailure({error: new TypeError('Failed to fetch')}), true)
assert.equal(isConnectionFailure({error: new DOMException('x', 'AbortError')}), false)

// aboneler
const listener = (() => { let n = 0; return {fn: () => { n++ }, count: () => n} })()
const unsubscribe = health.subscribe(listener.fn)
health.reportFailure(); health.reportFailure()
assert.equal(listener.count(), 1)
unsubscribe()
health.reportSuccess()
assert.equal(listener.count(), 1)
console.log('connection-health: ok')

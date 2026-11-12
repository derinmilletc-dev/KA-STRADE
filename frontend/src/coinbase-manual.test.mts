// Çalıştırma: node --test frontend/src/coinbase-manual.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {
  CONFIRM, confirmMatches, emptyTicket, isPositiveDecimal, isProductId, mutatingPath, networkFailure, newRequestId,
  parseFailure, protectionTone, redact, tabVisible, ticketPayload, ticketProblems,
} from './coinbase-manual.ts'

test('sekme: kapalı/izinsiz görünmez, açık ve yükleme hatası görünür', () => {
  assert.equal(tabVisible(404, {detail: 'Not Found'}), false)
  assert.equal(tabVisible(401, {}), false)
  assert.equal(tabVisible(403, {}), false)
  assert.equal(tabVisible(500, {}), false)
  assert.equal(tabVisible(200, {enabled: true}), true)
  assert.equal(tabVisible(424, {code: 'COINBASE_LOAD_FAILED', detail: 'x'}), true)
  assert.equal(tabVisible(424, {code: 'OTHER'}), false)
  assert.equal(tabVisible(200, null), true)
})

test('hata çevirisi: mesaj, kod, acil, sebepler', () => {
  const failure = parseFailure(409, {detail: 'ACİL: stop yok', code: 'CRITICAL_UNPROTECTED', critical: true})
  assert.equal(failure.critical, true)
  assert.equal(failure.code, 'CRITICAL_UNPROTECTED')
  assert.match(failure.message, /ACİL/)
  const pre = parseFailure(422, {detail: 'Ön kontrol geçmedi', code: 'PREFLIGHT_FAILED', reasons: ['a', 3, 'b']})
  assert.deepEqual(pre.reasons, ['a', 'b'])
  assert.equal(pre.critical, false)
})

test('hata çevirisi: boş cevap bile açık mesaj verir', () => {
  for (const status of [400, 401, 403, 429, 500, 502]) {
    const failure = parseFailure(status, null)
    assert.ok(failure.message.length > 10, `status ${status}`)
  }
  assert.match(parseFailure(500, undefined).message, /kontrol et/)
})

test('hata çevirisi: pydantic liste hatası okunur, Retry-After alınır', () => {
  const failure = parseFailure(422, {detail: [{loc: ['body', 'stop_price'], msg: 'alan zorunlu'}]})
  assert.match(failure.message, /stop_price: alan zorunlu/)
  assert.equal(parseFailure(429, {detail: 'x', retry_after_seconds: 4.2}).retryAfterSeconds, 5)
})

test('gizli anahtar mesajda görünmez', () => {
  const pem = '-----BEGIN EC PRIVATE KEY-----\nABCDEF\n-----END EC PRIVATE KEY-----'
  assert.equal(redact(`hata ${pem} bitti`), 'hata [GİZLİ ANAHTAR] bitti')
  assert.equal(redact('anahtar secret-value-123 geçti', ['secret-value-123']), 'anahtar [GİZLİ] geçti')
  assert.equal(redact('kısa abc', ['abc']), 'kısa abc')
  assert.ok(!parseFailure(422, {detail: `kötü ${pem}`}).message.includes('ABCDEF'))
})

test('ağ kopması: emir işleminde "belirsiz" ve acil', () => {
  const timeout = Object.assign(new Error('t'), {name: 'TimeoutError'})
  const order = networkFailure(timeout, true)
  assert.equal(order.critical, true)
  assert.match(order.message, /BİLİNMİYOR/)
  const read = networkFailure(new TypeError('x'), false)
  assert.equal(read.critical, false)
  assert.doesNotMatch(read.message, /BİLİNMİYOR/)
})

test('istek kimlikleri benzersiz ve sunucu sınırına uyar', () => {
  const ids = new Set(Array.from({length: 50}, () => newRequestId()))
  assert.equal(ids.size, 50)
  for (const id of ids) assert.ok(id.length >= 8 && id.length <= 64)
})

test('emir formu: stop zorunlu, sayılar doğrulanır', () => {
  assert.ok(ticketProblems(emptyTicket()).length >= 3)
  const ok = {...emptyTicket(), productId: 'ETP-20DEC30-CDE', stopPrice: '2940.5', marginUsd: '60'}
  assert.deepEqual(ticketProblems(ok), [])
  assert.ok(ticketProblems({...ok, stopPrice: ''}).some(text => text.includes('Stop')))
  assert.ok(ticketProblems({...ok, stopPrice: '0'}).length > 0)
  assert.ok(ticketProblems({...ok, stopPrice: '-5'}).length > 0)
  assert.ok(ticketProblems({...ok, stopPrice: '1e3'}).length > 0)
  assert.ok(ticketProblems({...ok, marginUsd: 'abc'}).length > 0)
  assert.ok(ticketProblems({...ok, contracts: '2.5'}).length > 0)
  assert.ok(ticketProblems({...ok, contracts: '0'}).length > 0)
  assert.deepEqual(ticketProblems({...ok, contracts: '3'}), [])
  assert.ok(ticketProblems({...ok, productId: '../x'}).length > 0)
})

test('emir gövdesi: metin sayılar korunur, sözleşme yoksa gönderilmez', () => {
  const base = {...emptyTicket(), productId: ' ETP-20DEC30-CDE ', stopPrice: ' 2940.50 ', marginUsd: '60'}
  assert.equal(ticketPayload({...base, limitPrice: ' 3001.5 '}).entry_limit_price, '3001.5')
  assert.ok(ticketProblems({...base, limitPrice: 'abc'}).length >= 1)
  assert.deepEqual(ticketPayload(base), {product_id: 'ETP-20DEC30-CDE', side: 'BUY', stop_price: '2940.50', margin_usd: '60'})
  assert.equal(ticketPayload({...base, contracts: '4'}).contracts, 4)
})

test('yardımcılar', () => {
  assert.equal(isProductId('AB'), false)
  assert.equal(isProductId('BIP-20DEC30-CDE'), true)
  assert.equal(isPositiveDecimal('0.0'), false)
  assert.equal(isPositiveDecimal('.5'), false)
  assert.equal(protectionTone('protected'), 'ok')
  assert.equal(protectionTone('UNPROTECTED'), 'bad')
  assert.equal(protectionTone('MISMATCH'), 'warn')
  assert.equal(protectionTone('???'), 'warn')
  assert.equal(confirmMatches('  COINBASE EMİR AÇ ', CONFIRM.open), true)
  assert.equal(confirmMatches('coinbase emir aç', CONFIRM.open), false)
  assert.equal(mutatingPath('/orders/open'), true)
  assert.equal(mutatingPath('/orders/protection'), false)
  assert.equal(mutatingPath('/status'), false)
})

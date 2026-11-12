// Çalıştırma: node --test frontend/src/coinbase-market.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {analysisSymbol, changeText, expiryText, matchRows, priceText, signalsForRows, toRows, type CbProduct} from './coinbase-market.ts'

const product = (over: Partial<CbProduct>): CbProduct => ({
  product_id: 'BIP-20DEC30-CDE', display: 'BTC PERP', root: 'BTC', price: '100000', change_pct: '0.5', volume_usd: '90000000',
  expiry: null, contract_size: '0.01', tradable: true, ...over,
})
const signal = {lean: 'LONG' as const, long: 70, short: 30, confidence: 70, score: 1}

test('liste: yalnız işlem görenler, sunucu sırası korunur, bozuk sayı NaN', () => {
  const rows = toRows([product({}), product({product_id: 'X-1', tradable: false}), product({product_id: 'Y-2', price: null, change_pct: null})])
  assert.deepEqual(rows.map(row => row.symbol), ['BIP-20DEC30-CDE', 'Y-2'])
  assert.ok(Number.isNaN(rows[1].price))
  assert.equal(priceText(rows[1].price), '—')
  assert.equal(changeText(rows[1].change), '—')
})

test('analiz eşlemesi: dayanak varlık → BTCUSDT; olmayan için sinyal uydurulmaz', () => {
  assert.equal(analysisSymbol('BTC'), 'BTCUSDT')
  assert.equal(analysisSymbol(null), null)
  assert.equal(analysisSymbol('b t c'), null)
  const rows = toRows([product({}), product({product_id: 'ZZ-1', root: 'ZZZ'}), product({product_id: 'NR-1', root: null})])
  const map = signalsForRows(rows, {BTCUSDT: signal})
  assert.deepEqual(Object.keys(map ?? {}), ['BIP-20DEC30-CDE'])
  assert.equal(map?.['BIP-20DEC30-CDE'].lean, 'LONG')
})

test('analiz henüz yüklenmediyse null (yükleniyor), boş harita değil', () => {
  assert.equal(signalsForRows(toRows([product({})]), null), null)
  assert.deepEqual(signalsForRows(toRows([product({})]), {}), {})
})

test('arama: kod, ad ve dayanak üzerinden; boşluk ve tire önemsiz', () => {
  const rows = toRows([product({}), product({product_id: 'ETP-20DEC30-CDE', display: 'ETH PERP', root: 'ETH'})])
  assert.equal(matchRows(rows, 'eth').length, 1)
  assert.equal(matchRows(rows, 'btc perp').length, 1)
  assert.equal(matchRows(rows, 'bip-20').length, 1)
  assert.equal(matchRows(rows, '').length, 2)
  assert.equal(matchRows(rows, 'doge').length, 0)
})

test('biçimleyiciler', () => {
  assert.equal(changeText(-1.256), '-1.26%')
  assert.equal(changeText(0.5), '+0.50%')
  assert.equal(expiryText('2030-12-20T16:00:00Z'), '20.12.2030')
  assert.equal(expiryText('abc'), null)
  assert.equal(expiryText(null), null)
})

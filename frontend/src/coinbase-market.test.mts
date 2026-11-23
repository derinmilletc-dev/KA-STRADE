// Çalıştırma: node --test frontend/src/coinbase-market.test.mts   (Node 22.18+; ek paket gerekmez)
import assert from 'node:assert/strict'
import {test} from 'node:test'
import {capacityOf, minMarginNote, minMarginOf, analysisSymbol, changeText, expiryText, matchRows, priceText, signalsForRows, toRows, type CbProduct} from './coinbase-market.ts'

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
  assert.equal(expiryText('2089-12-30T00:00:00Z'), null)
  assert.equal(expiryText(null), null)
})

{
  const {groupRows, variantLabel, isPerp} = await import('./coinbase-market.ts')
  const mk = (symbol: string, display: string, root: string | null, expiry: string | null) => ({symbol, display, price: 1, change: 0, volume: 1, root, expiry})
  const rows = [
    mk('SOL-27NOV26', 'SOL 27-Nov', 'SOL', '2026-11-27T16:00:00Z'),
    mk('SOL-PERP', 'SOL Perp', 'SOL', '2030-12-20T16:00:00Z'),
    mk('BCH-26FEB27', 'BCH 26-Feb 27', 'BCH', '2027-02-26T16:00:00Z'),
    mk('BCH-29JAN27', 'BCH 29-Jan 27', 'BCH', '2027-01-29T16:00:00Z'),
    mk('X-1', 'Mystery', null, null),
  ]
  const g = groupRows(rows)
  assert.deepEqual(g.rows.map(r => r.symbol), ['SOL-PERP', 'BCH-29JAN27', 'X-1'])
  assert.deepEqual(g.variants.SOL.map(r => r.symbol), ['SOL-PERP', 'SOL-27NOV26'])
  assert.deepEqual(g.variants.BCH.map(r => r.symbol), ['BCH-29JAN27', 'BCH-26FEB27'])
  assert.equal(isPerp(rows[1]), true)
  assert.equal(variantLabel(rows[1]), 'Perp')
  assert.equal(variantLabel(rows[0], Date.parse('2026-11-21T00:00:00Z')), '27 Kas')
  assert.equal(variantLabel(rows[2], Date.parse('2026-11-21T00:00:00Z')), '26 Şub 27')
  console.log('coinbase-market gruplama testleri geçti')
}

test('en az teminat: fiyat × sözleşme boyutu × oran; oran yoksa null', () => {
  assert.equal(minMarginOf(30, 10, 0.34), 102)
  assert.equal(minMarginOf(30, 10, NaN), null)
  assert.equal(toRows([product({price: '30', contract_size: '10', margin_rate: '0.34'})])[0].minMargin, 102)
  assert.equal(toRows([product({})])[0].minMargin, null)
})

test('liste notu bakiyeye yetmeyeni işaretler', () => {
  assert.deepEqual(minMarginNote(104.23, 98.94), {text: 'en az ~105 USD', short: true})
  assert.deepEqual(minMarginNote(23.5, 98.94), {text: 'en az ~23.5 USD', short: false})
  assert.equal(minMarginNote(null, 98.94), null)
  assert.equal(minMarginNote(50, null)?.short, false)
})

test('kapasite: bakiyenle en fazla sözleşme, teminat ve kaldıraç', () => {
  // 1 sözleşme 300 USD değerinde, oran %34 → teminat 102; bakiye 98.94 → yetmez
  assert.deepEqual(capacityOf({minMargin: 102, marginRate: 0.34, spendable: 98.94}), {contracts: 0, margin: 0, notional: 0, maxLeverage: 2.9, perContractMargin: 102})
  // bakiye 250 → 2 sözleşme, teminat 204, pozisyon 600; kendi limitin 2x ise kaldıraç 2
  assert.deepEqual(capacityOf({minMargin: 102, marginRate: 0.34, spendable: 250, leverageLimit: 2}), {contracts: 2, margin: 204, notional: 600, maxLeverage: 2, perContractMargin: 102})
  assert.equal(capacityOf({minMargin: null, marginRate: 0.34, spendable: 250}), null)
  assert.equal(capacityOf({minMargin: 102, marginRate: 0.34, spendable: null}), null)
})

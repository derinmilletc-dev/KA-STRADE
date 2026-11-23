import assert from 'node:assert/strict'
import test from 'node:test'
import {autoFix} from './coinbase-autofix.ts'

const base = {marginUsd: 25, contracts: 3, spendable: 98.94}

test('Coinbase gereken marjin bakiyeyi aşıyor: sözleşme küçülür', () => {
  const fix = autoFix({...base, reasons: ['Coinbase vadeli hesapta bu emrin marjini için yeterli bakiye görmüyor (bu emir için gereken marjin ≈ 104.23 USD, kaldıraç 12.22x). Teminatı artır']})
  assert.deepEqual(fix, {label: 'Otomatik düzelt: 2 sözleşme', patch: {contracts: '2'}})   // 104.23/3=34.74; 98.94*.97/34.74=2.76 → 2
})
test('1 sözleşme bile yetmiyorsa düğme yerine açıklama', () => {
  const fix = autoFix({...base, contracts: 1, reasons: ['Coinbase vadeli hesapta bu emrin marjini için yeterli bakiye görmüyor (bu emir için gereken marjin ≈ 140 USD, kaldıraç 3x).']})
  assert.equal(fix?.label, null)
})
test('alım gücü yetersiz: teminat sığdırılır, sözleşme yeniden hesaplanır', () => {
  const fix = autoFix({marginUsd: 176.83, contracts: 5, spendable: 98.94, reasons: ['Alım gücü yetersiz: istenen teminat 176.83 USD, kullanılabilir 98.94 USD']})
  assert.deepEqual(fix, {label: 'Otomatik düzelt: teminat 96.96 USD', patch: {marginUsd: '96.96', contracts: ''}})
})
test('kaldıraç ve kayıp sınırında en küçük sözleşme sayısı seçilir', () => {
  const fix = autoFix({...base, reasons: ['Bu miktarda en kötü kayıp 10 USD sınırını aşar (bu stop ile en fazla 4 sözleşme).', 'Bu miktar 5x kaldıraç sınırını aşar (bu teminatla en fazla 2 sözleşme).']})
  assert.deepEqual(fix, {label: 'Otomatik düzelt: 2 sözleşme', patch: {contracts: '2'}})
})
test('en kötü kayıp sınırı oranla küçültür', () => {
  const fix = autoFix({...base, contracts: 10, reasons: ['En kötü kayıp 20.00 USD; sınır 8 USD.']})
  assert.deepEqual(fix?.label, 'Otomatik düzelt: 4 sözleşme')
})
test('gereken teminat bakiyeye sığıyorsa teminat artırılır, sığmıyorsa açıklama', () => {
  const reason = 'Bu teminatla (25 USD) 1 sözleşme 5x kaldıracı aşar. 1 sözleşme ≈ 300 USD büyüklüğünde: en az 60.00 USD teminat (5x) gerekir.'
  assert.deepEqual(autoFix({...base, reasons: [reason]}), {label: 'Otomatik düzelt: teminat 61.8 USD', patch: {marginUsd: '61.8', contracts: ''}})
  assert.equal(autoFix({...base, spendable: 40, reasons: [reason]})?.label, null)
})
test('düzeltilemeyen neden (günlük limit) için öneri yok', () => {
  assert.equal(autoFix({...base, reasons: ['Günlük zarar sınırı (50 USD) aşılır: bugün 40 USD zarar var.']}), null)
})

test('kaldıraç 1x altında: teminat pozisyonun yarısına iner, sözleşme yeniden hesaplanır', () => {
  const fix = autoFix({marginUsd: 97, contracts: 1, spendable: 98.94, notional: 69.84, reasons: ['Kaldıraç 0.72x; en az 1x olmalı. Teminatı düşür veya miktarı artır (en az 2 sözleşme gerekir).']})
  assert.deepEqual(fix, {label: 'Otomatik düzelt: teminat 34.92 USD', patch: {marginUsd: '34.92', contracts: ''}})
})
test('kaldıraç 1x altında: borsanın istediği teminat yarıdan büyükse ona çıkar', () => {
  const fix = autoFix({marginUsd: 97, contracts: 1, spendable: 98.94, notional: 70, exchangeMargin: 40, reasons: ['Kaldıraç 0.72x; en az 1x olmalı.']})
  assert.deepEqual(fix, {label: 'Otomatik düzelt: teminat 40.8 USD', patch: {marginUsd: '40.8', contracts: ''}})
})

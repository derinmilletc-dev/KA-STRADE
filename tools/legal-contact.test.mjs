import assert from 'node:assert/strict'
import {test} from 'node:test'
import {fillLegalPlaceholders, unfilledLegalPlaceholders} from '../legal-contact.ts'

const filled = {privacyEmail: 'gizlilik@example.test', controller: 'Örnek Kişi', retention: 'Mali kayıtlar 10 yıl saklanır.'}
const empty = {privacyEmail: '', controller: '  ', retention: ''}

test('placeholders are replaced only by real values and stay when a value is empty', () => {
  const text = 'Mail: [GİZLİLİK E-POSTASI]. Sorumlu: [VERİ SORUMLUSU / HUKUKİ UNVAN]. [SAKLAMA SÜRELERİ] [GİZLİLİK E-POSTASI]'
  assert.equal(fillLegalPlaceholders(text, filled), 'Mail: gizlilik@example.test. Sorumlu: Örnek Kişi. Mali kayıtlar 10 yıl saklanır. gizlilik@example.test')
  assert.equal(fillLegalPlaceholders(text, empty), text)
  assert.equal(fillLegalPlaceholders(text, {...filled, retention: ''}).includes('[SAKLAMA SÜRELERİ]'), true)
  assert.deepEqual(unfilledLegalPlaceholders(fillLegalPlaceholders(text, filled)), [])
  assert.equal(unfilledLegalPlaceholders(text).length, 3)
})

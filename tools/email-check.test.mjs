import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import {checkEmail, emailAccepted} from '../email-check.ts'

// Sunucu (backend/tests/test_email_check.py) ile aynı örnekler: iki taraf aynı sonucu vermeli.
const cases = JSON.parse(readFileSync(new URL('../backend/tests/email_check_cases.json', import.meta.url), 'utf8'))

test('ortak örnekler: durum ve öneri sunucuyla aynı', () => {
  const failures = cases.filter(([raw, status, suggestion]) => {
    const got = checkEmail(raw)
    return got.status !== status || got.suggestion !== suggestion
  }).map(([raw, status, suggestion]) => ({raw, want: [status, suggestion], got: checkEmail(raw)}))
  assert.deepEqual(failures, [])
})

test('olası yazım hatası yalnız kullanıcı onaylarsa kabul edilir', () => {
  assert.equal(emailAccepted(checkEmail('ali@gmial.com'), false), false)
  assert.equal(emailAccepted(checkEmail('ali@gmial.com'), true), true)
  assert.equal(emailAccepted(checkEmail('ali@gmail.con'), true), false)
  assert.equal(emailAccepted(checkEmail('ali@gmail.com'), false), true)
})

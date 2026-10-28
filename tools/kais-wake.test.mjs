import assert from 'node:assert/strict'
import {test} from 'node:test'
import {detectWake, takeVoiceLaunch} from '../kais-wake-text.ts'

test('wake word at the start wakes Kais and keeps the question', () => {
  assert.deepEqual(detectWake('Hey Kais bakiyem ne'), {question: 'bakiyem ne'})
  assert.deepEqual(detectWake('hey kayış, bu hafta nasıl gitti?'), {question: 'bu hafta nasıl gitti?'})
  assert.deepEqual(detectWake('Kais'), {question: ''})
  assert.deepEqual(detectWake('selam kaiz'), {question: ''})
})

test('the name in the middle of a sentence or similar words do not wake', () => {
  assert.equal(detectWake('bugün kais ile konuştum'), null)
  assert.equal(detectWake('kaiser roll'), null)
  assert.equal(detectWake('hey nasılsın'), null)
  assert.equal(detectWake(''), null)
})

test('voice launch parameter is consumed once', () => {
  let replaced = ''
  assert.equal(takeVoiceLaunch({search: '?kais=voice&tab=canli', pathname: '/master-trade', hash: ''}, url => { replaced = url }), true)
  assert.equal(replaced, '/master-trade?tab=canli')
  assert.equal(takeVoiceLaunch({search: '?tab=canli', pathname: '/', hash: ''}, () => { throw new Error('no') }), false)
})

import assert from 'node:assert/strict'
import {test} from 'node:test'
import {fixVoiceText, pickAlternative, vocabularyHits} from '../assistant-voice-text.ts'

test('Misheard trading terms and the assistant name are corrected', () => {
  assert.equal(fixVoiceText('bit koyun ne durumda'), 'Bitcoin ne durumda')
  assert.equal(fixVoiceText('ar es ay kaç'), 'RSI kaç')
  assert.equal(fixVoiceText('bi en bi grafiği'), 'BNB grafiği')
  assert.equal(fixVoiceText('stop los koy'), 'stop loss koy')
  assert.equal(fixVoiceText('Kayış bana yardım et'), 'KAİS bana yardım et')
  assert.equal(fixVoiceText('bugün hava nasıl'), 'bugün hava nasıl')
})

test('pickAlternative keeps the top alternative unless only another one contains a known term', () => {
  assert.equal(pickAlternative(undefined), '')
  assert.equal(pickAlternative([]), '')
  assert.equal(pickAlternative([{transcript: 'bit koyun nasıl', confidence: 0.8}, {transcript: 'bitcoin nasıl', confidence: 0.6}]), 'bit koyun nasıl')
  assert.equal(pickAlternative([{transcript: 'bir kuyu nasıl', confidence: 0.8}, {transcript: 'bit koyun nasıl', confidence: 0.6}]), 'bit koyun nasıl')
  // Çok düşük güvenli alternatif tercih edilmez.
  assert.equal(pickAlternative([{transcript: 'bir kuyu nasıl', confidence: 0.9}, {transcript: 'bit koyun nasıl', confidence: 0.2}]), 'bir kuyu nasıl')
  // Boş alternatif seçilmez; güven yoksa sözlük puanı belirler.
  assert.equal(pickAlternative([{transcript: 'merhaba'}, {transcript: '  '}]), 'merhaba')
  assert.equal(pickAlternative([{transcript: 'merhaba'}, {transcript: 'ar es ay'}]), 'ar es ay')
  assert.ok(vocabularyHits('bit koyun ve ar es ay') >= 2)
})

import assert from 'node:assert/strict'
import {test} from 'node:test'
import {parseNavigation} from '../kais-agent-intents.ts'

const view = text => parseNavigation(text, 'tr')?.view ?? null

test('Page commands are understood with fillers, politeness and speech-recognition variants', () => {
  for (const [text, expected] of [
    ['analist aç', 'analyst'], ['Analist sayfasını açar mısın', 'analyst'], ['bana analist sayfasını bir açar mısın lütfen', 'analyst'],
    ['kais master trade e git', 'master-trade'], ['taramayı açalım', 'scanner'], ['tarama sayfasını açalım', 'scanner'],
    ['performans sayfasına gidelim', 'performance'], ['risk sayfasını göster', 'risk'], ['kontrol panelini aç', 'dashboard'],
    ['ana sayfaya git', 'dashboard'], ['destek aç', 'support'], ['yardım sayfasını aç', 'support'],
  ]) assert.equal(view(text), expected, text)
})

test('Joined, suffixed and synonym forms from speech recognition open the right page', () => {
  for (const [text, expected] of [
    ['Analist sayfasını açarmısın', 'analyst'], ['analiste götürür müsün', 'analyst'], ['analist sayfasına geçiş yap', 'analyst'],
    ['analyst sayfasını açmanı istiyorum', 'analyst'], ['destek sayfasına gir', 'support'], ['ana sayfaya dön', 'dashboard'],
    ['grafik sayfasını aç', 'master-trade'], ['profilimi aç', 'setup'], ['abonelik sayfasını aç', 'pricing'],
    ['premium sayfasını aç', 'pricing'], ['ödeme sayfasını aç', 'billing'],
  ]) assert.equal(view(text), expected, text)
})

test('Costly or unclear requests are not treated as page commands', () => {
  assert.equal(view('acil durdurmayı aç'), null)
  assert.equal(view('premium fiyatı ne kadar'), null)
  assert.equal(view('işlem geçmişim nerede'), null)
  assert.equal(view('BTC analizi aç'), null)
  assert.equal(view('bugün hava nasıl'), null)
  assert.equal(view('riskim neden bu kadar yüksek bana uzun uzun açıklar mısın'), null)
})

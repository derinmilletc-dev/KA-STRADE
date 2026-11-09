import assert from 'node:assert/strict'
import {test} from 'node:test'
import {describePlan, parseCommandPlan} from '../kais-plan-intents.ts'

const kinds = plan => plan?.steps.map(step => step.kind)

test('Spec command becomes a four-step plan with the right numbers', () => {
  const plan = parseCommandPlan('Hey Kais, analize gir, %95 üstü üç işlem bul, hazırla, kârı 1 dolar olsun.')
  assert.deepEqual(kinds(plan), ['analysis', 'scan', 'prepare', 'target_profit'])
  assert.deepEqual(plan.steps[1].params, {min_confidence: 95, count: 3})
  assert.deepEqual(plan.steps[3].params, {usdt: 1})
  assert.deepEqual(plan.missing, ['account'])  // Demo mu Canlı mı denmedi: Kais sorar, varsayılan uydurmaz
  assert.deepEqual(plan.invalid, [])
})

test('Account is read from the sentence and live is never assumed', () => {
  assert.equal(parseCommandPlan('demo hesapta %90 üstü iki işlem bul ve hazırla').account, 'demo')
  assert.equal(parseCommandPlan('canlı hesapta %90 üstü iki işlem bul ve hazırla').account, 'live')
  assert.equal(parseCommandPlan('%90 üstü iki işlem bul ve hazırla').account, '')
  assert.deepEqual(parseCommandPlan('demo hesapta %90 üstü iki işlem bul ve hazırla').missing, [])
})

test('Plan never executes and always needs on-screen confirmation', () => {
  const plan = parseCommandPlan('canlı hesapta %95 üstü 3 işlem bul, hazırla, kârı 2 dolar olsun, takip et')
  assert.equal(plan.executes, false)
  assert.equal(plan.requiresScreenConfirmation, true)
  assert.deepEqual(kinds(plan), ['scan', 'prepare', 'target_profit', 'follow'])
  const text = describePlan(plan, 'tr')
  assert.match(text, /Henüz hiçbir şey yapmadım/)
  assert.match(text, /sesle onay yok/)
  assert.match(describePlan(plan, 'en'), /voice cannot confirm/)
})

test('Single commands are not plans, so existing local commands keep working', () => {
  for (const text of ['analist sayfasını aç', 'btc long demo emir hazırla stop 60000 tp1 63000', 'analize gir', 'bana bir şaka yap', 'fiyat alarmı kur btc 70000', '']) {
    assert.equal(parseCommandPlan(text), null, text)
  }
})

test('Unclear or incomplete requests ask instead of guessing', () => {
  const noCount = parseCommandPlan('demo hesapta işlem bul ve hazırla')
  assert.equal(noCount, null)  // bul fiili var ama sayı/oran yok: tarama adımı sayılmaz, tek adım kalır
  const onlyPrepare = parseCommandPlan('demo analize gir ve işlemleri hazırla')
  assert.deepEqual(kinds(onlyPrepare), ['analysis', 'prepare'])
  assert.ok(onlyPrepare.missing.includes('count'))
  const noTarget = parseCommandPlan('demo %90 üstü iki işlem bul, hazırla, kârı ayarla')
  assert.ok(noTarget.missing.includes('target_profit'))
})

test('Out-of-range values are reported invalid and no default is invented', () => {
  const high = parseCommandPlan('demo %250 üstü üç işlem bul ve hazırla')
  assert.deepEqual(high.invalid, ['confidence'])
  assert.deepEqual(high.steps[0].params, {count: 3})
  assert.match(describePlan(high), /%50–%100/)
  const many = parseCommandPlan('demo %95 üstü 40 işlem bul ve hazırla')
  assert.deepEqual(many.invalid, ['count'])
  const zero = parseCommandPlan('demo %95 üstü iki işlem bul, hazırla, kârı 0 dolar olsun')
  assert.deepEqual(zero.invalid, ['target_profit'])
  const huge = parseCommandPlan('demo %95 üstü iki işlem bul, hazırla, kârı 5000 dolar olsun')
  assert.deepEqual(huge.invalid, ['target_profit'])
})

test('Number words, decimals and speech variants', () => {
  assert.deepEqual(parseCommandPlan('demo yüzde 96 üstü beş fırsat bul hazırla').steps[0].params, {min_confidence: 96, count: 5})
  assert.deepEqual(parseCommandPlan('demo %95 üstü bir işlem bul hazırla kârı 1,5 dolar olsun').steps[2].params, {usdt: 1.5})
  assert.deepEqual(parseCommandPlan('demo %95 üstü 2 işlem bul hazırla hedef 3 usdt kâr').steps.map(s => s.kind), ['scan', 'prepare', 'target_profit'])
})

test('Very long input is not parsed as a plan', () => {
  assert.equal(parseCommandPlan('analize gir '.repeat(40)), null)
})

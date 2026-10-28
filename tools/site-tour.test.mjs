import assert from 'node:assert/strict'
import {test} from 'node:test'
import {buildTourSteps, firstName, parseTourMemory, shouldOfferTour, TOUR_WAIT_DAYS} from '../site-tour.ts'

const DAY = 86_400_000
const NOW = Date.UTC(2026, 9, 28, 12)

test('a member with no memory is offered the tour', () => {
  assert.equal(shouldOfferTour(null, NOW), true)
  assert.equal(shouldOfferTour('', NOW), true)
  assert.equal(shouldOfferTour('garbage', NOW), true)
})

test('closing or finishing the tour never asks again', () => {
  assert.equal(shouldOfferTour(`never|${NOW - 400 * DAY}`, NOW), false)
  assert.equal(shouldOfferTour('never|0', NOW), false)
})

test('"later" waits two days and then asks again', () => {
  assert.equal(TOUR_WAIT_DAYS, 2)
  assert.equal(shouldOfferTour(`later|${NOW - DAY}`, NOW), false)
  assert.equal(shouldOfferTour(`later|${NOW - 2 * DAY - 1}`, NOW), true)
  // Saat geriye alınmışsa (kayıt gelecekte) sonsuza kadar susmaz.
  assert.equal(shouldOfferTour(`later|${NOW + 10 * DAY}`, NOW), true)
})

test('memory parsing ignores unknown values', () => {
  assert.deepEqual(parseTourMemory('never|5'), {kind: 'never', at: 5})
  assert.equal(parseTourMemory('done|5'), null)
})

test('free and premium members get different Master Trade steps but the same ending', () => {
  const free = buildTourSteps(false)
  const premium = buildTourSteps(true)
  assert.equal(premium.length, free.length + 1)
  assert.ok(premium.some(step => step.press === 'canli'))
  assert.ok(!free.some(step => step.press || step.need), 'free members are never sent to Master Trade')
  assert.ok(!free.some(step => step.view === 'master-trade'))
  for (const steps of [free, premium]) {
    assert.equal(steps.at(-1).extra, 'voice')
    assert.equal(new Set(steps.map(step => step.id)).size, steps.length)
    assert.ok(steps.some(step => step.extra === 'plans'))
  }
})

test('tour copy makes no profit promises', () => {
  const text = JSON.stringify([...buildTourSteps(true), ...buildTourSteps(false)]).toLowerCase()
  for (const word of ['kazanırsın', 'kesin kâr', 'risksiz', 'para kazan']) assert.ok(!text.includes(word), word)
  assert.ok(!text.replaceAll('garanti değildir', '').replaceAll('garantisi değildir', '').includes('garanti'))
  assert.ok(text.includes('değildir'))
})

test('phone and desktop tours have the same steps but describe their own layout', () => {
  for (const premium of [false, true]) {
    const phone = buildTourSteps(premium, true)
    const desktop = buildTourSteps(premium, false)
    assert.deepEqual(phone.map(step => step.id), desktop.map(step => step.id))
    const phoneText = JSON.stringify(phone).toLowerCase()
    const desktopText = JSON.stringify(desktop).toLowerCase()
    assert.ok(!desktopText.includes('alttaki menü') && !desktopText.includes('menü tuş'), 'desktop must not mention the phone bottom menu')
    assert.ok(!phoneText.includes('ana sayfadaki kart') && !phoneText.includes('sağ üst'), 'phone must not mention desktop-only layout')
    assert.ok(!phone.some(step => (step.targets ?? []).some(target => target.includes('v26DashboardChoices'))))
    assert.ok(!desktop.some(step => (step.targets ?? []).some(target => target.includes('v26MobileBottomNav') || target.includes('v26NavAssistantHost'))))
  }
})

test('first name comes from the display name', () => {
  assert.equal(firstName('Ahmet Yılmaz'), 'Ahmet')
  assert.equal(firstName('  '), '')
  assert.equal(firstName(undefined), '')
})

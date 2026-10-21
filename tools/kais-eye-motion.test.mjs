import assert from 'node:assert/strict'
import {test} from 'node:test'
import {BLINK_DELAY_MAX_MS, BLINK_DELAY_MIN_MS, BLINK_MS, GAZE_LIMIT, GAZE_MAX_MS, GAZE_MIN_MS, PROFILES, boundGaze, easeInOut, lerpGaze, planBlink, planGaze} from '../kais-eye-motion.ts'

// Testte rastgelelik enjekte edilir (tohumlu LCG): kesin bir dizi beklenmez, yalnız sınırlar ve istatistiksel özellikler denetlenir.
const seeded = seed => () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32)
const presences = ['idle', 'listening', 'thinking', 'speaking']

// Bakış zaman çizelgesi benzetimi: hareket sayısı (dakikada) ve merkezde geçen süre payı.
function simulate(presence, rng, totalMs = 30 * 60 * 1000) {
  let current = {x: 0, y: 0}, time = 0, forwardTime = 0, moves = 0
  const plans = []
  while (time < totalMs) {
    const plan = planGaze(presence, current, rng)
    plans.push(plan)
    if (plan.duration > 0) moves += 1
    time += plan.duration + plan.hold
    if (Math.hypot(plan.target.x, plan.target.y) <= 0.05) forwardTime += plan.hold + plan.duration
    current = plan.target
  }
  return {plans, forwardShare: forwardTime / time, glancesPerMinute: plans.filter(plan => plan.kind === 'glance').length / (time / 60000), movesPerMinute: moves / (time / 60000)}
}

test('Gaze targets are small and bounded; transitions are calm (350–600 ms)', () => {
  for (const presence of presences) {
    const rng = seeded(presence.length * 97)
    let current = {x: 0, y: 0}
    for (let index = 0; index < 5000; index += 1) {
      const plan = planGaze(presence, current, rng)
      assert.ok(Math.hypot(plan.target.x, plan.target.y) <= 0.18 + 1e-9, `${presence} radius`)
      if (plan.duration > 0) assert.ok(plan.duration >= GAZE_MIN_MS && plan.duration <= GAZE_MAX_MS, `${presence} duration ${plan.duration}`)
      assert.ok(plan.hold > 0)
      current = plan.target
    }
  }
  assert.deepEqual(boundGaze({x: 5, y: 0}), {x: GAZE_LIMIT, y: 0})
  assert.deepEqual(boundGaze({x: Number.NaN, y: Infinity}), {x: 0, y: 0})
})

test('No micro jitter: a turn without a glance does not move at all', () => {
  const rng = seeded(5)
  for (let index = 0; index < 3000; index += 1) {
    const plan = planGaze('speaking', {x: 0, y: 0}, rng)
    if (plan.kind === 'stay') { assert.equal(plan.duration, 0); assert.deepEqual(plan.target, {x: 0, y: 0}) }
  }
})

test('After a glance the eye returns exactly to center and then holds forward for a long time', () => {
  const rng = seeded(7)
  for (let index = 0; index < 2000; index += 1) {
    const away = {x: 0.06 + rng() * 0.12, y: (rng() - 0.5) * 0.1}
    const plan = planGaze('idle', away, rng)
    assert.equal(plan.kind, 'return')
    assert.deepEqual(plan.target, {x: 0, y: 0})
    assert.ok(plan.hold >= PROFILES.idle.forward[0])
  }
})

test('Calm and meaningful: few glances per minute, speaking is nearly still, the eye is forward almost all the time', () => {
  const rate = {}, share = {}
  for (const presence of presences) {
    const result = simulate(presence, seeded(11 + presence.length))
    rate[presence] = result.glancesPerMinute
    share[presence] = result.forwardShare
  }
  assert.ok(rate.idle >= 1.5 && rate.idle <= 4.5, `idle ${rate.idle}`)
  assert.ok(rate.listening >= 1 && rate.listening <= 3.5, `listening ${rate.listening}`)
  assert.ok(rate.thinking >= 1.5 && rate.thinking <= 4.5, `thinking ${rate.thinking}`)
  assert.ok(rate.speaking <= 1.6, `speaking ${rate.speaking}`)
  for (const presence of presences) assert.ok(share[presence] > 0.85, `${presence} forward ${share[presence]}`)
})

test('A meaningful moment (state change) can force one short glance', () => {
  const plan = planGaze('thinking', {x: 0, y: 0}, seeded(3), {glance: true})
  assert.equal(plan.kind, 'glance')
  assert.ok(plan.duration >= GAZE_MIN_MS)
})

test('Blinks happen every 6–11 s irregularly, doubles are rare, and blinks are short', () => {
  const rng = seeded(23)
  const plans = Array.from({length: 4000}, () => planBlink(rng))
  for (const plan of plans) {
    assert.ok(plan.delay >= BLINK_DELAY_MIN_MS && plan.delay <= BLINK_DELAY_MAX_MS, `delay ${plan.delay}`)
    assert.ok(plan.gap >= 200 && plan.gap <= 320)
  }
  assert.equal(BLINK_DELAY_MIN_MS, 6000)
  assert.equal(BLINK_DELAY_MAX_MS, 11000)
  const doubles = plans.filter(plan => plan.double).length / plans.length
  assert.ok(doubles > 0.02 && doubles < 0.1, `double share ${doubles}`)
  const values = new Set(plans.map(plan => Math.round(plan.delay / 250)))
  assert.ok(values.size >= 18, 'irregular, spread over the range')
  assert.ok(BLINK_MS >= 100 && BLINK_MS <= 180)
})

test('Easing accelerates and decelerates smoothly and interpolation hits both endpoints', () => {
  assert.equal(easeInOut(0), 0)
  assert.equal(easeInOut(1), 1)
  assert.ok(easeInOut(0.1) < 0.1 && easeInOut(0.9) > 0.9)
  for (let t = 0; t < 1; t += 0.05) assert.ok(easeInOut(t + 0.05) >= easeInOut(t))
  assert.deepEqual(lerpGaze({x: 0, y: 0}, {x: 0.1, y: -0.05}, 1), {x: 0.1, y: -0.05})
  assert.equal(Object.keys(PROFILES).sort().join(), presences.slice().sort().join())
})

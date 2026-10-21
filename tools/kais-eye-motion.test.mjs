import assert from 'node:assert/strict'
import {test} from 'node:test'
import {BLINK_DELAY_MAX_MS, BLINK_DELAY_MIN_MS, GAZE_LIMIT, GAZE_MAX_MS, GAZE_MIN_MS, PROFILES, boundGaze, easeInOut, lerpGaze, planBlink, planGaze} from '../kais-eye-motion.ts'

// Testte rastgelelik enjekte edilir (tohumlu LCG): kesin bir dizi beklenmez, yalnız sınırlar ve istatistiksel özellikler denetlenir.
const seeded = seed => () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32)
const presences = ['idle', 'listening', 'thinking', 'speaking']

// Bakış zaman çizelgesi benzetimi: her planın süresi + bekleme toplamı içinde ne kadar zaman merkeze yakın geçti?
function simulate(presence, rng, totalMs = 10 * 60 * 1000) {
  let current = {x: 0, y: 0}, time = 0, forwardTime = 0
  const plans = []
  while (time < totalMs) {
    const plan = planGaze(presence, current, rng)
    plans.push(plan)
    time += plan.duration + plan.hold
    if (Math.hypot(plan.target.x, plan.target.y) <= 0.1) forwardTime += plan.hold
    current = plan.target
  }
  return {plans, forwardShare: forwardTime / time}
}

test('Gaze targets are always bounded (radius ≤ limit, vertical ≤ half) in every presence', () => {
  for (const presence of presences) {
    const rng = seeded(presence.length * 97)
    let current = {x: 0, y: 0}
    for (let index = 0; index < 5000; index += 1) {
      const plan = planGaze(presence, current, rng)
      assert.ok(Math.hypot(plan.target.x, plan.target.y) <= GAZE_LIMIT + 1e-9, `${presence} radius`)
      assert.ok(Math.abs(plan.target.y) <= GAZE_LIMIT / 2 + 1e-9, `${presence} vertical`)
      assert.ok(plan.duration >= GAZE_MIN_MS && plan.duration <= GAZE_MAX_MS, `${presence} duration ${plan.duration}`)
      assert.ok(plan.hold > 0)
      current = plan.target
    }
  }
  assert.deepEqual(boundGaze({x: 5, y: 0}), {x: GAZE_LIMIT, y: 0})
  assert.deepEqual(boundGaze({x: Number.NaN, y: Infinity}), {x: 0, y: 0})
})

test('After any gaze shift the next move returns toward the center (never chains far-off positions)', () => {
  const rng = seeded(7)
  for (let index = 0; index < 2000; index += 1) {
    const away = {x: (rng() - 0.5) * 1.1, y: (rng() - 0.5) * 0.5}
    if (Math.hypot(away.x, away.y) <= 0.1) continue
    const plan = planGaze('idle', away, rng)
    assert.equal(plan.kind, 'return')
    assert.ok(Math.hypot(plan.target.x, plan.target.y) < 0.06)
  }
})

test('Idle and listening are dominated by stable forward attention; listening is calmer than thinking', () => {
  const share = {}
  for (const presence of presences) share[presence] = simulate(presence, seeded(11 + presence.length)).forwardShare
  assert.ok(share.idle > 0.75, `idle ${share.idle}`)
  assert.ok(share.listening > 0.85, `listening ${share.listening}`)
  assert.ok(share.speaking > 0.8, `speaking ${share.speaking}`)
  assert.ok(share.listening > share.thinking)
  // Glances are mostly small; large shifts are rare.
  const {plans} = simulate('idle', seeded(3))
  const glances = plans.filter(plan => plan.kind === 'glance').map(plan => Math.hypot(plan.target.x, plan.target.y))
  const large = glances.filter(radius => radius > 0.32).length / glances.length
  assert.ok(large < 0.25, `large ${large}`)
})

test('No obvious period: intervals between gaze shifts vary widely and directions are not a fixed left/right alternation', () => {
  const {plans} = simulate('idle', seeded(19))
  const intervals = plans.map(plan => plan.duration + plan.hold)
  const mean = intervals.reduce((a, b) => a + b, 0) / intervals.length
  const sd = Math.sqrt(intervals.reduce((a, b) => a + (b - mean) ** 2, 0) / intervals.length)
  assert.ok(sd / mean > 0.35, `coefficient of variation ${sd / mean}`)
  assert.ok(new Set(intervals.map(value => Math.round(value / 50))).size > intervals.length * 0.5)
  const sides = plans.filter(plan => plan.kind === 'glance').map(plan => Math.sign(plan.target.x))
  let alternations = 0
  for (let index = 1; index < sides.length; index += 1) if (sides[index] !== sides[index - 1]) alternations += 1
  assert.ok(alternations / (sides.length - 1) < 0.8, 'not a strict left/right alternation')
  // Vertical component exists but stays small (no stereotyped repeated upward glance).
  const ups = plans.filter(plan => plan.kind === 'glance' && plan.target.y < -0.1).length / Math.max(1, plans.filter(plan => plan.kind === 'glance').length)
  assert.ok(ups < 0.45)
})

test('Blinks are irregular, bounded, occasionally doubled, and the double gap is brief', () => {
  const rng = seeded(23)
  const plans = Array.from({length: 4000}, () => planBlink(rng))
  for (const plan of plans) {
    assert.ok(plan.delay >= BLINK_DELAY_MIN_MS && plan.delay <= BLINK_DELAY_MAX_MS, `delay ${plan.delay}`)
    assert.ok(plan.gap >= 180 && plan.gap <= 320)
  }
  const doubles = plans.filter(plan => plan.double).length / plans.length
  assert.ok(doubles > 0.08 && doubles < 0.25, `double share ${doubles}`)
  const mean = plans.reduce((a, b) => a + b.delay, 0) / plans.length
  const sd = Math.sqrt(plans.reduce((a, b) => a + (b.delay - mean) ** 2, 0) / plans.length)
  assert.ok(sd / mean > 0.35, 'irregular intervals')
  assert.ok(mean > 3000 && mean < 7000, `mean ${mean}`)
})

test('Easing accelerates and decelerates smoothly and interpolation hits both endpoints', () => {
  assert.equal(easeInOut(0), 0)
  assert.equal(easeInOut(1), 1)
  assert.ok(easeInOut(0.1) < 0.1 && easeInOut(0.9) > 0.9)
  assert.ok(Math.abs(easeInOut(0.5) - 0.5) < 1e-9)
  for (let t = 0; t < 1; t += 0.05) assert.ok(easeInOut(t + 0.05) >= easeInOut(t))
  assert.deepEqual(lerpGaze({x: 0, y: 0}, {x: 0.3, y: -0.1}, 1), {x: 0.3, y: -0.1})
  assert.deepEqual(lerpGaze({x: 0.2, y: 0.1}, {x: 0, y: 0}, 0), {x: 0.2, y: 0.1})
  assert.equal(Object.keys(PROFILES).sort().join(), presences.slice().sort().join())
})

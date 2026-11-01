import assert from 'node:assert/strict'
import {test} from 'node:test'
import {countdownPhase, formatCountdown, nextPollDelay, parsePublicMaintenance, remainingMs} from '../maintenance-countdown.ts'

test('parses the public maintenance payload and rejects bad shapes', () => {
  assert.deepEqual(parsePublicMaintenance({mode: 'NORMAL', scheduled_at: 1800000600.5, now: 1800000000}), {mode: 'NORMAL', scheduledAt: 1800000601000 - 500, serverNow: 1800000000000})
  assert.deepEqual(parsePublicMaintenance({mode: 'MAINTENANCE', scheduled_at: null, now: 5}), {mode: 'MAINTENANCE', scheduledAt: null, serverNow: 5000})
  for (const bad of [null, 'x', 5, {}, {mode: 'HACK'}, {mode: 5}]) assert.equal(parsePublicMaintenance(bad), null)
  const odd = parsePublicMaintenance({mode: 'NORMAL', scheduled_at: 'yarin', now: NaN})
  assert.deepEqual(odd, {mode: 'NORMAL', scheduledAt: null, serverNow: null})
})

test('remaining time uses the server clock offset, not the device clock', () => {
  const scheduled = 1_000_000
  // Cihaz saati sunucudan 1 saat ileri: sayaç yine de doğru kalmalı.
  const offset = -3_600_000
  assert.equal(remainingMs(scheduled, offset, scheduled + 3_600_000 - 90_000), 90_000)
})

test('formats the countdown in both languages', () => {
  assert.equal(formatCountdown(0), '00:00:00')
  assert.equal(formatCountdown(-5000), '00:00:00')
  assert.equal(formatCountdown(61_000), '00:01:01')
  assert.equal(formatCountdown(3_723_000), '01:02:03')
  assert.equal(formatCountdown(90_061_000, 'tr'), '1g 01:01:01')
  assert.equal(formatCountdown(90_061_000, 'en'), '1d 01:01:01')
  assert.equal(formatCountdown(59_100), '00:01:00')
})

test('phases tighten as the start approaches', () => {
  assert.equal(countdownPhase(3_600_000), 'far')
  assert.equal(countdownPhase(600_000), 'soon')
  assert.equal(countdownPhase(60_000), 'imminent')
  assert.equal(countdownPhase(1), 'imminent')
  assert.equal(countdownPhase(0), 'due')
  assert.equal(countdownPhase(-10), 'due')
})

test('polling is rare when idle, brisk near zero and calm after failures', () => {
  assert.equal(nextPollDelay(null, false), 60_000)
  assert.equal(nextPollDelay(3_600_000, false), 20_000)
  assert.equal(nextPollDelay(5_000, false), 5_500)
  assert.equal(nextPollDelay(100, false), 1_500)
  assert.equal(nextPollDelay(0, false), 2_000)
  assert.equal(nextPollDelay(-4000, false), 2_000)
  assert.equal(nextPollDelay(5000, true), 30_000)
})

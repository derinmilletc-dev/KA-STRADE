import assert from 'node:assert/strict'
import {test} from 'node:test'

function listeners() {
  const map = new Map()
  return {
    addEventListener: (name, fn) => map.set(name, fn),
    removeEventListener: (name) => map.delete(name),
    fire: name => map.get(name)?.(),
  }
}

test('Gizli sekmede sorgu atılmaz, görününce hemen yenilenir', async () => {
  const doc = {hidden: false, ...listeners()}
  const win = {...listeners(), setInterval, clearInterval, setTimeout, clearTimeout}
  globalThis.document = doc
  globalThis.window = win
  const {visibleInterval, clearVisibleInterval} = await import(`../visible-interval.ts?t=${Date.now()}`)
  let calls = 0
  const id = visibleInterval(() => { calls++ }, 20)
  try {
  await new Promise(r => setTimeout(r, 70))
  const visibleCalls = calls
  assert.ok(visibleCalls >= 2, 'görünürken sorgu çalışmalı')
  doc.hidden = true
  await new Promise(r => setTimeout(r, 90))
  assert.equal(calls, visibleCalls, 'gizliyken sorgu atılmamalı')
  doc.hidden = false
  win.fire('online')
  await new Promise(r => setTimeout(r, 10))
  assert.ok(calls - visibleCalls <= 1, 'son sorgu yeni olduğundan ek sorgu patlaması olmaz')
  clearVisibleInterval(id)
  const after = calls
  await new Promise(r => setTimeout(r, 60))
  assert.equal(calls, after, 'temizlenince durmalı')
  } finally { clearVisibleInterval(id) }
})

test('Uzun süre gizli kaldıktan sonra görününce sorgu hemen yenilenir', async () => {
  const doc = {hidden: false, ...listeners()}
  const win = {...listeners(), setInterval, clearInterval, setTimeout, clearTimeout}
  globalThis.document = doc
  globalThis.window = win
  const realNow = Date.now
  const {visibleInterval, clearVisibleInterval} = await import(`../visible-interval.ts?t=${Date.now()}b`)
  let calls = 0
  const id = visibleInterval(() => { calls++ }, 60000)
  Date.now = () => realNow() + 5 * 60 * 1000
  try {
    doc.fire('visibilitychange')
    await new Promise(r => setTimeout(r, 40))
    assert.equal(calls, 1)
  } finally { Date.now = realNow; clearVisibleInterval(id) }
})

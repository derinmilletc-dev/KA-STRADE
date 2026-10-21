import assert from 'node:assert/strict'
import {test} from 'node:test'
import {FULLSCREEN_CLASS, HISTORY_MARKER, canLockOrientation, canRequestFullscreen, createLandscapeController, isLandscape} from '../master-trade-fullscreen.ts'

// Sahte tarayıcı ortamı: dinleyici sayaçları, tam ekran, ekran yönü kilidi ve geçmiş.
function makeEnv({width = 390, height = 844, fullscreen = 'ok', lock = 'ok'} = {}) {
  const classes = () => { const set = new Set(); return {add: name => set.add(name), remove: name => set.delete(name), has: name => set.has(name)} }
  const target = () => {
    const handlers = new Map(), adds = {}, removes = {}
    return {
      handlers, adds, removes,
      addEventListener(type, handler) { adds[type] = (adds[type] ?? 0) + 1; if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(handler) },
      removeEventListener(type, handler) { removes[type] = (removes[type] ?? 0) + 1; handlers.get(type)?.delete(handler) },
      fire(type, event = {}) { [...(handlers.get(type) ?? [])].forEach(handler => handler(event)) },
      active(type) { return handlers.get(type)?.size ?? 0 },
    }
  }
  const win = Object.assign(target(), {innerWidth: width, innerHeight: height})
  win.history = {state: null, pushes: 0, backs: 0, pushState(state) { this.state = state; this.pushes += 1 }, back() { this.backs += 1; this.state = null }}
  const doc = Object.assign(target(), {fullscreenElement: null, fullscreenEnabled: fullscreen !== 'missing', exits: 0})
  doc.body = {classList: classes()}
  doc.documentElement = {classList: classes()}
  if (fullscreen !== 'missing') {
    doc.documentElement.requestFullscreen = async () => {
      if (fullscreen === 'reject') throw new Error('denied')
      doc.fullscreenElement = doc.documentElement; doc.fire('fullscreenchange')
    }
    doc.exitFullscreen = async () => { doc.exits += 1; doc.fullscreenElement = null; doc.fire('fullscreenchange') }
  }
  const scr = {orientation: undefined, locks: [], unlocks: 0}
  if (lock !== 'missing') {
    scr.orientation = {
      lock: async orientation => { if (lock === 'reject') throw new Error('NotSupportedError'); scr.locks.push(orientation) },
      unlock() { scr.unlocks += 1 },
    }
  }
  return {env: {window: win, document: doc, screen: scr}, win, doc, scr}
}
const listenerBalance = target => Object.keys({...target.adds, ...target.removes}).every(type => (target.adds[type] ?? 0) === (target.removes[type] ?? 0))
const tick = () => new Promise(resolve => setTimeout(resolve, 0))

test('Feature detection reports what the browser can do without throwing', () => {
  assert.equal(isLandscape(844, 390), true)
  assert.equal(isLandscape(390, 844), false)
  const full = makeEnv(), none = makeEnv({fullscreen: 'missing', lock: 'missing'})
  assert.equal(canRequestFullscreen(full.doc), true)
  assert.equal(canLockOrientation(full.scr), true)
  assert.equal(canRequestFullscreen(none.doc), false)
  assert.equal(canLockOrientation(none.scr), false)
})

test('Fullscreen entry requests fullscreen and a landscape lock, and exit restores everything', async () => {
  const {env, win, doc, scr} = makeEnv()
  const controller = createLandscapeController(env)
  assert.deepEqual(controller.getSnapshot(), {open: false, landscape: false, fullscreen: false, lock: 'none'})
  await controller.open()
  assert.equal(controller.getSnapshot().open, true)
  assert.equal(controller.getSnapshot().fullscreen, true)
  assert.equal(controller.getSnapshot().lock, 'locked')
  assert.deepEqual(scr.locks, ['landscape'])
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), true)
  assert.equal(doc.body.classList.has(FULLSCREEN_CLASS), true)
  assert.equal(win.history.pushes, 1)
  assert.equal(win.history.state[HISTORY_MARKER], true)

  controller.close()
  assert.deepEqual(controller.getSnapshot(), {open: false, landscape: false, fullscreen: false, lock: 'none'})
  assert.equal(doc.exits, 1)
  assert.equal(scr.unlocks, 1)
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), false)
  assert.equal(doc.body.classList.has(FULLSCREEN_CLASS), false)
  assert.equal(win.history.backs, 1)
  controller.close() // ikinci çıkış zararsız
  assert.equal(doc.exits, 1)
  assert.equal(scr.unlocks, 1)
  assert.equal(win.history.backs, 1)
})

test('Cancelling the rotation overlay in portrait leaves no fullscreen, lock, class, history entry or listener behind', async () => {
  const {env, win, doc, scr} = makeEnv({width: 390, height: 844})
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(controller.getSnapshot().landscape, false) // dönme uyarısı bu durumda görünür
  controller.close() // İptal
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(doc.fullscreenElement, null)
  assert.equal(scr.unlocks, 1)
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), false)
  assert.equal(win.history.backs, 1)
  for (const target of [win, doc]) {
    assert.equal(listenerBalance(target), true)
    for (const type of ['resize', 'orientationchange', 'popstate', 'fullscreenchange', 'webkitfullscreenchange', 'keydown']) assert.equal(target.active(type), 0)
  }
})

test('Unsupported orientation lock falls back to manual rotation and never traps the user', async () => {
  const {env, win, doc} = makeEnv({lock: 'missing'})
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(controller.getSnapshot().lock, 'unsupported')
  assert.equal(controller.getSnapshot().open, true)
  assert.equal(controller.getSnapshot().landscape, false)
  // Kullanıcı telefonu elle çevirir.
  win.innerWidth = 844; win.innerHeight = 390; win.fire('resize')
  assert.equal(controller.getSnapshot().landscape, true)
  win.innerWidth = 390; win.innerHeight = 844; win.fire('orientationchange')
  assert.equal(controller.getSnapshot().landscape, false)
  controller.close()
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), false)
})

test('A rejected orientation lock is reported as failed but keeps the layout usable', async () => {
  const {env, scr} = makeEnv({lock: 'reject'})
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(controller.getSnapshot().lock, 'failed')
  assert.equal(controller.getSnapshot().open, true)
  controller.close()
  assert.equal(scr.unlocks, 0) // kilit alınmadıysa bırakılmaz
})

test('Browsers without the Fullscreen API still open the layout', async () => {
  const {env, doc} = makeEnv({fullscreen: 'missing', lock: 'missing'})
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(controller.getSnapshot().open, true)
  assert.equal(controller.getSnapshot().fullscreen, false)
  controller.close()
  assert.equal(doc.exits, 0)
  assert.equal(controller.getSnapshot().open, false)
})

test('A denied fullscreen request keeps the layout and lets the user rotate manually', async () => {
  const {env, doc} = makeEnv({fullscreen: 'reject'})
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(controller.getSnapshot().open, true)
  assert.equal(controller.getSnapshot().fullscreen, false)
  controller.close()
  assert.equal(doc.exits, 0)
})

test('Leaving fullscreen with the system gesture closes the layout and releases the lock', async () => {
  const {env, doc, scr} = makeEnv()
  const controller = createLandscapeController(env)
  await controller.open()
  doc.fullscreenElement = null; doc.fire('fullscreenchange')
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(scr.unlocks, 1)
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), false)
  assert.equal(doc.exits, 0) // tarayıcı zaten çıkmış; ikinci çıkış istenmez
})

test('Browser back closes the fullscreen layout instead of leaving the page', async () => {
  const {env, win} = makeEnv()
  const controller = createLandscapeController(env)
  await controller.open()
  win.history.state = null // tarayıcı geri gitti
  win.fire('popstate')
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(win.history.backs, 0) // bizim için ikinci bir geri adımı atılmaz
})

test('Escape closes the layout unless the guard says a menu or draw mode owns it', async () => {
  const {env, doc} = makeEnv()
  const controller = createLandscapeController(env)
  let guarded = true
  controller.setEscapeGuard(() => guarded)
  await controller.open()
  doc.fire('keydown', {key: 'Enter'})
  doc.fire('keydown', {key: 'Escape'})
  assert.equal(controller.getSnapshot().open, true)
  guarded = false
  doc.fire('keydown', {key: 'Escape'})
  assert.equal(controller.getSnapshot().open, false)
})

test('Cancelling while the fullscreen request is still pending releases the late fullscreen', async () => {
  const {env, doc} = makeEnv()
  let resolve
  doc.documentElement.requestFullscreen = () => new Promise(done => { resolve = () => { doc.fullscreenElement = doc.documentElement; done() } })
  const controller = createLandscapeController(env)
  const opening = controller.open()
  controller.close()
  resolve()
  await opening
  await tick()
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(doc.exits, 1)
  assert.equal(doc.fullscreenElement, null)
})

test('Repeated open/close cycles never duplicate or leak listeners', async () => {
  const {env, win, doc} = makeEnv()
  const controller = createLandscapeController(env)
  for (let cycle = 0; cycle < 3; cycle += 1) {
    await controller.open()
    await controller.open() // çift tıklama
    assert.equal(win.active('resize'), 1)
    assert.equal(doc.active('keydown'), 1)
    controller.close()
  }
  assert.equal(listenerBalance(win), true)
  assert.equal(listenerBalance(doc), true)
  assert.equal(win.active('resize'), 0)
  assert.equal(doc.active('fullscreenchange'), 0)
})

test('Subscribers are notified once per real change and dispose cleans up', async () => {
  const {env, doc} = makeEnv()
  const controller = createLandscapeController(env)
  let calls = 0
  const unsubscribe = controller.subscribe(() => { calls += 1 })
  await controller.open()
  const afterOpen = calls
  assert.ok(afterOpen >= 1)
  controller.close()
  assert.ok(calls > afterOpen)
  const settled = calls
  controller.close()
  assert.equal(calls, settled)
  unsubscribe()
  await controller.open()
  assert.equal(calls, settled)
  controller.dispose() // bileşen kalkarken açık düzen kapanır
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(doc.documentElement.classList.has(FULLSCREEN_CLASS), false)
})

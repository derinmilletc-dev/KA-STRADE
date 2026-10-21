import assert from 'node:assert/strict'
import {test} from 'node:test'
import {FULLSCREEN_CLASS, HISTORY_MARKER, canLockOrientation, canRequestFullscreen, createLandscapeController as create, isLandscape, readLandscape} from '../master-trade-fullscreen.ts'

// Testlerde bekleme kısa tutulur; davranış aynıdır.
const createLandscapeController = (env, options = {}) => create(env, {settleMs: 5, pollMs: 10, ...options})
const settle = () => new Promise(resolve => setTimeout(resolve, 40))

// Sahte tarayıcı ortamı: dinleyici sayaçları, tam ekran, ekran yönü kilidi ve geçmiş.
function makeEnv({width = 390, height = 844, fullscreen = 'ok', lock = 'ok', orientationType} = {}) {
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
  const mediaTarget = target()
  win.matchMedia = () => Object.assign(mediaTarget, {get matches() { return win.innerWidth > win.innerHeight }})
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
  const orientationTarget = target()
  const scr = {orientation: undefined, locks: [], unlocks: 0, orientationTarget}
  if (lock !== 'missing') {
    scr.orientation = Object.assign(orientationTarget, {
      get type() { return orientationType },
      lock: async orientation => { if (lock === 'hang') return new Promise(() => {}); if (lock === 'reject') throw new Error('NotSupportedError'); scr.locks.push(orientation) },
      unlock() { scr.unlocks += 1 },
    })
  }
  return {env: {window: win, document: doc, screen: scr}, win, doc, scr, mediaTarget}
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
  await settle()
  assert.equal(controller.getSnapshot().landscape, true)
  win.innerWidth = 390; win.innerHeight = 844; win.fire('orientationchange')
  await settle()
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
  const {env, doc, scr} = makeEnv({width: 844, height: 390}) // terminal aşaması: cihaz yatay
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

test('A single transient landscape reading (own fullscreen/lock side effect) never opens the terminal', async () => {
  const {env, win} = makeEnv()
  const controller = createLandscapeController(env, {settleMs: 30})
  await controller.open()
  win.innerWidth = 844; win.innerHeight = 390; win.fire('resize') // geçici
  await new Promise(resolve => setTimeout(resolve, 10))
  win.innerWidth = 390; win.innerHeight = 844; win.fire('resize') // eski hâline döndü
  await new Promise(resolve => setTimeout(resolve, 80))
  assert.equal(controller.getSnapshot().landscape, false)
  assert.equal(controller.getSnapshot().open, true) // uyarı kalır
  controller.close()
})

test('Real rotation is confirmed only after the layout settles, and a rotation back is confirmed the same way', async () => {
  const {env, win} = makeEnv()
  const controller = createLandscapeController(env, {settleMs: 30})
  await controller.open()
  win.innerWidth = 844; win.innerHeight = 390; win.fire('orientationchange')
  assert.equal(controller.getSnapshot().landscape, false) // henüz oturmadı
  await new Promise(resolve => setTimeout(resolve, 80))
  assert.equal(controller.getSnapshot().landscape, true)
  win.innerWidth = 390; win.innerHeight = 844; win.fire('resize')
  await new Promise(resolve => setTimeout(resolve, 80))
  assert.equal(controller.getSnapshot().landscape, false)
  controller.close()
})

test('A transient landscape reading during a pending request that bounces back is not trusted', async () => {
  const {env, win, doc} = makeEnv()
  let finish
  doc.documentElement.requestFullscreen = () => new Promise(done => { finish = () => { doc.fullscreenElement = doc.documentElement; done() } })
  const controller = createLandscapeController(env, {settleMs: 40})
  const opening = controller.open()
  win.innerWidth = 844; win.innerHeight = 390; win.fire('resize') // istek sürerken (geçici)
  await tick()
  win.innerWidth = 390; win.innerHeight = 844; win.fire('resize') // oturmadan eski hâline döner
  finish(); await opening; await settle(); await settle()
  assert.equal(controller.getSnapshot().landscape, false) // dikey kaldı: uyarı sürer
  assert.equal(controller.getSnapshot().open, true)
  controller.close()
})

test('Landscape is judged from the viewport and media query; screen.orientation is only a signal and never vetoes it', async () => {
  const stale = makeEnv({width: 844, height: 390, orientationType: 'portrait-primary'}) // eski/gecikmiş değer
  assert.equal(readLandscape(stale.env), true)
  const portrait = makeEnv({width: 390, height: 844, orientationType: 'landscape-primary'})
  assert.equal(readLandscape(portrait.env), false)
  const unknown = makeEnv({width: 844, height: 390})
  assert.equal(readLandscape(unknown.env), true)
})

test('A lock or fullscreen request that never settles does not block landscape detection', async () => {
  const {env, win} = makeEnv({lock: 'hang'})
  const controller = createLandscapeController(env)
  void controller.open() // lock() hiç tamamlanmaz
  await tick()
  win.innerWidth = 844; win.innerHeight = 390; win.fire('resize')
  await settle()
  assert.equal(controller.getSnapshot().landscape, true)
  controller.close()
})

test('A missed orientation event is recovered by re-reading the real state, and the poll is removed on close', async () => {
  const {env, win} = makeEnv({lock: 'missing', fullscreen: 'missing'})
  const controller = createLandscapeController(env)
  await controller.open()
  win.innerWidth = 844; win.innerHeight = 390 // hiçbir olay yok
  await settle(); await settle()
  assert.equal(controller.getSnapshot().landscape, true)
  controller.close()
  win.innerWidth = 390; win.innerHeight = 844
  await settle(); await settle()
  assert.equal(controller.getSnapshot().open, false) // kapalıyken yoklama durumu değiştirmez (zamanlayıcı temizlendi)
  assert.equal(controller.getSnapshot().landscape, true)
})

test('If the device is already landscape the terminal opens immediately', async () => {
  const {env} = makeEnv({width: 844, height: 390})
  const controller = createLandscapeController(env)
  const opening = controller.open()
  assert.equal(controller.getSnapshot().landscape, true)
  await opening
  controller.close()
})

test('A synthetic popstate that leaves our history marker in place does not dismiss the prompt', async () => {
  const {env, win} = makeEnv()
  const controller = createLandscapeController(env)
  await controller.open()
  win.fire('popstate') // uygulamanın kendi gönderdiği olay: işaret durumda duruyor
  assert.equal(controller.getSnapshot().open, true)
  win.history.state = null // gerçek geri: işaret gitti
  win.fire('popstate')
  assert.equal(controller.getSnapshot().open, false)
})

test('An involuntary fullscreen exit during the prompt keeps it; after landscape the same exit closes the terminal', async () => {
  const prompt = makeEnv()
  const first = createLandscapeController(prompt.env)
  await first.open()
  prompt.doc.fullscreenElement = null; prompt.doc.fire('fullscreenchange')
  assert.equal(first.getSnapshot().open, true)
  assert.equal(first.getSnapshot().fullscreen, false)
  first.close()
  const terminal = makeEnv({width: 844, height: 390})
  const second = createLandscapeController(terminal.env)
  await second.open()
  terminal.doc.fullscreenElement = null; terminal.doc.fire('fullscreenchange')
  assert.equal(second.getSnapshot().open, false)
})

test('Media-query and Screen Orientation change events feed the same settle path, and all of it is detached on close', async () => {
  const {env, win, mediaTarget, scr} = makeEnv()
  const controller = createLandscapeController(env)
  await controller.open()
  assert.equal(mediaTarget.active('change'), 1)
  assert.equal(scr.orientationTarget.active('change'), 1)
  win.innerWidth = 844; win.innerHeight = 390
  scr.orientationTarget.fire('change')
  await settle()
  assert.equal(controller.getSnapshot().landscape, true)
  controller.close()
  assert.equal(mediaTarget.active('change'), 0)
  assert.equal(scr.orientationTarget.active('change'), 0)
  assert.equal(listenerBalance(win), true)
})

test('No timer fires after close: a pending settle never reopens or updates anything', async () => {
  const {env, win} = makeEnv()
  const controller = createLandscapeController(env, {settleMs: 20})
  await controller.open()
  let calls = 0
  controller.subscribe(() => { calls += 1 })
  win.innerWidth = 844; win.innerHeight = 390; win.fire('resize')
  controller.close()
  const afterClose = calls
  await new Promise(resolve => setTimeout(resolve, 60))
  assert.equal(calls, afterClose)
  assert.equal(controller.getSnapshot().open, false)
  assert.equal(controller.getSnapshot().landscape, false)
})

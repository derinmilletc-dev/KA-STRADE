// Sayfa kendi kendine kayabilir (içerik yüksekliği değişir, tarayıcı konumu düzeltir). Üst/alt menüyü yalnız
// kullanıcının parmak/tekerlek/klavye hareketi sürerken saklayıp göstermek için son kullanıcı girdisinin zamanını tutar.
let lastInput = 0
let lastDirection: 'down' | 'up' | null = null
let lastDirectionAt = 0
let lastTouchY: number | null = null
let installed = false
const mark = () => { lastInput = Date.now() }
const setDirection = (direction: 'down' | 'up') => { lastDirection = direction; lastDirectionAt = Date.now() }
const onTouchStart = (event: Event) => { mark(); const touch = (event as TouchEvent).touches?.[0]; lastTouchY = touch ? touch.clientY : null; lastDirection = null }
const onTouchMove = (event: Event) => {
  mark()
  const touch = (event as TouchEvent).touches?.[0]
  if (!touch) return
  if (lastTouchY !== null && Math.abs(touch.clientY - lastTouchY) >= 2) setDirection(touch.clientY < lastTouchY ? 'down' : 'up')  // parmak yukarı = sayfa aşağı
  lastTouchY = touch.clientY
}
const onWheel = (event: Event) => { mark(); const dy = (event as WheelEvent).deltaY; if (dy) setDirection(dy > 0 ? 'down' : 'up') }
const onKey = (event: Event) => {
  mark()
  const key = (event as KeyboardEvent).key
  if (key === 'ArrowDown' || key === 'PageDown' || key === 'End' || key === ' ') setDirection('down')
  else if (key === 'ArrowUp' || key === 'PageUp' || key === 'Home') setDirection('up')
}

function install() {
  if (installed || typeof window === 'undefined') return
  installed = true
  window.addEventListener('touchstart', onTouchStart, {passive: true, capture: true})
  window.addEventListener('touchmove', onTouchMove, {passive: true, capture: true})
  window.addEventListener('wheel', onWheel, {passive: true, capture: true})
  window.addEventListener('keydown', onKey, {passive: true, capture: true})
  window.addEventListener('pointerdown', mark, {passive: true, capture: true})
}

export const USER_SCROLL_WINDOW_MS = 700

/** Son ~0,7 sn içinde kullanıcı girdisi (dokunma, tekerlek, klavye) var mı? Kaydırma sonrası ataletli kayma da bu pencereye girer. */
export function userIsScrolling(now = Date.now()): boolean {
  install()
  return now - lastInput <= USER_SCROLL_WINDOW_MS
}

/** Dinleyiciyi erken kurar; ilk kaydırmadan önce girdi kaydı başlasın. */
export function watchUserScroll() { install() }

/** Kullanıcının gerçek kaydırma yönü (parmak/tekerlek/klavye). Yalnız dokunma ya da yön yoksa null: içerik kendi kayınca menü oynamasın. */
export function userScrollDirection(now = Date.now()): 'down' | 'up' | null {
  install()
  return now - lastDirectionAt <= USER_SCROLL_WINDOW_MS ? lastDirection : null
}

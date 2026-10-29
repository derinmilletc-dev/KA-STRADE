// Sayfa kendi kendine kayabilir (içerik yüksekliği değişir, tarayıcı konumu düzeltir). Üst/alt menüyü yalnız
// kullanıcının parmak/tekerlek/klavye hareketi sürerken saklayıp göstermek için son kullanıcı girdisinin zamanını tutar.
let lastInput = 0
let installed = false
const mark = () => { lastInput = Date.now() }

function install() {
  if (installed || typeof window === 'undefined') return
  installed = true
  for (const name of ['touchstart', 'touchmove', 'wheel', 'keydown', 'pointerdown']) window.addEventListener(name, mark, {passive: true, capture: true})
}

export const USER_SCROLL_WINDOW_MS = 700

/** Son ~0,7 sn içinde kullanıcı girdisi (dokunma, tekerlek, klavye) var mı? Kaydırma sonrası ataletli kayma da bu pencereye girer. */
export function userIsScrolling(now = Date.now()): boolean {
  install()
  return now - lastInput <= USER_SCROLL_WINDOW_MS
}

/** Dinleyiciyi erken kurar; ilk kaydırmadan önce girdi kaydı başlasın. */
export function watchUserScroll() { install() }

// Sitenin tek bildirim (toast) merkezi. Bildirimler üstte, başlığın hemen altında çıkar: alttaki mobil menüyle,
// tarayıcı çubuğuyla ya da sayfa içi butonlarla çakışmaz. En fazla 3 tane üst üste görünür, yenisi en üstte.
// Kaydırarak (yana ya da yukarı) kapatılır; sayfa kaydırılınca acil olmayanlar kendiliğinden kaybolur.
// React'ten bağımsızdır (body'ye eklenir), bu yüzden sitenin #root içindeki genel stil kurallarından etkilenmez.

export type ToastTone = 'ok' | 'info' | 'warn' | 'bad'
export type ToastOptions = {
  title?: string
  body: string
  tone?: ToastTone
  /** ms; verilmezse tona göre (ok/info 5 sn, warn 7 sn, bad 10 sn). */
  duration?: number
  action?: {label: string; href?: string; onClick?: () => void}
}

const MAX_VISIBLE = 3
const STYLE_ID = 'kt-toast-style'
const ROOT_ID = 'kt-toasts'
const recent = new Map<string, number>()

const ICONS: Record<ToastTone, string> = {
  ok: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  info: '<circle cx="12" cy="12" r="0.5"/><path d="M12 11v6M12 7.5h.01"/>',
  warn: '<path d="M12 8v5M12 16.5h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  bad: '<path d="M15 9l-6 6M9 9l6 6"/><circle cx="12" cy="12" r="9"/>',
}

const CSS = `
#${ROOT_ID}{position:fixed;left:0;right:0;z-index:2147483000;display:grid;grid-template-areas:'s';justify-items:center;align-items:start;padding:0 12px 24px;pointer-events:none}
#${ROOT_ID} .ktT{--c:#3ddc97;grid-area:s;z-index:3;pointer-events:auto;position:relative;box-sizing:border-box;width:min(440px,100%);display:grid;grid-template-columns:40px minmax(0,1fr) auto;align-items:start;gap:12px;padding:14px 12px 16px 14px;border-radius:20px;overflow:hidden;
  background:linear-gradient(160deg,#1c2432,#121a26);color:#eef4fa;font:500 14px/1.45 Inter,system-ui,-apple-system,sans-serif;
  box-shadow:0 18px 44px rgba(0,0,0,.5),inset 0 0 0 1px rgba(255,255,255,.09);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
  touch-action:pan-y;user-select:none;-webkit-user-select:none;cursor:grab;
  transition:transform .32s cubic-bezier(.2,.9,.3,1),opacity .32s ease;animation:ktTIn .38s cubic-bezier(.2,.9,.3,1)}
#${ROOT_ID} .ktT[data-tone=info]{--c:#7cc4ff}
#${ROOT_ID} .ktT[data-tone=warn]{--c:#ffb84d}
#${ROOT_ID} .ktT[data-tone=bad]{--c:#ff7a7a}
#${ROOT_ID} .ktT::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c)}
#${ROOT_ID} .ktTIcon{width:40px;height:40px;border-radius:13px;display:grid;place-items:center;background:color-mix(in srgb,var(--c) 18%,transparent);color:var(--c)}
#${ROOT_ID} .ktTIcon svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}
#${ROOT_ID} .ktTText{display:grid;gap:2px;min-width:0;padding-top:1px}
#${ROOT_ID} .ktTText b{font-size:15px;font-weight:800;color:#fff;letter-spacing:-.01em}
#${ROOT_ID} .ktTText span{color:#b9c6d4;overflow-wrap:anywhere}
#${ROOT_ID} .ktTAction{justify-self:start;margin-top:8px;padding:8px 14px;border:0;border-radius:999px;background:var(--c);color:#0b1220;font:800 13px/1 Inter,system-ui,sans-serif;text-decoration:none;cursor:pointer}
#${ROOT_ID} .ktTClose{width:32px;height:32px;margin:-4px -2px 0 0;border:0;border-radius:10px;background:rgba(255,255,255,.06);color:#9fb0c2;display:grid;place-items:center;cursor:pointer;padding:0}
#${ROOT_ID} .ktTClose svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round}
#${ROOT_ID} .ktTBar{position:absolute;left:0;bottom:0;height:3px;width:100%;background:var(--c);opacity:.55;transform-origin:left;animation:ktTBar linear forwards}
#${ROOT_ID} .ktT[data-paused] .ktTBar{animation-play-state:paused}
#${ROOT_ID} .ktT[data-dragging]{transition:none;cursor:grabbing}
#${ROOT_ID} .ktT[data-stack="1"]{z-index:2;transform:translateY(12px) scale(.95);filter:brightness(.8)}
#${ROOT_ID} .ktT[data-stack="2"]{z-index:1;transform:translateY(24px) scale(.9);filter:brightness(.65)}
#${ROOT_ID} .ktT[data-stack] > *{visibility:hidden}
#${ROOT_ID} .ktT[data-leaving]{opacity:0;transform:translateY(-14px) scale(.96);pointer-events:none}
@keyframes ktTIn{from{opacity:0;transform:translateY(-16px) scale(.97)}to{opacity:1;transform:none}}
@keyframes ktTBar{from{transform:scaleX(1)}to{transform:scaleX(0)}}
@media (prefers-reduced-motion:reduce){#${ROOT_ID} .ktT{animation:none;transition:opacity .2s}#${ROOT_ID} .ktTBar{display:none}}
`

function ensureRoot(): HTMLElement {
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    document.head.appendChild(style)
  }
  let root = document.getElementById(ROOT_ID)
  if (!root) {
    root = document.createElement('div')
    root.id = ROOT_ID
    root.setAttribute('aria-live', 'polite')
    document.body.appendChild(root)
  }
  // Başlığın (sabit üst çubuk) hemen altına yerleş; başlık yoksa güvenli alanın altına.
  let top = 12
  for (const header of Array.from(document.querySelectorAll<HTMLElement>('header, [role="banner"]'))) {
    const rect = header.getBoundingClientRect()
    const position = getComputedStyle(header).position
    if ((position === 'fixed' || position === 'sticky') && rect.top <= 4 && rect.bottom > 0 && rect.bottom < 160) top = Math.max(top, rect.bottom + 10)
  }
  root.style.top = `max(${Math.round(top)}px, calc(env(safe-area-inset-top, 0px) + 12px))`
  return root
}

function restack(root: HTMLElement) {
  const items = Array.from(root.querySelectorAll<HTMLElement>('.ktT:not([data-leaving])'))
  items.forEach((item, index) => {
    if (index >= MAX_VISIBLE) { dismiss(item); return }
    if (!item.hasAttribute('data-dragging')) item.style.transform = ''
    if (index === 0) item.removeAttribute('data-stack'); else item.setAttribute('data-stack', String(index))
  })
}

function dismiss(element: HTMLElement, direction?: {x: number; y: number}) {
  if (element.hasAttribute('data-leaving')) return
  element.setAttribute('data-leaving', '')
  if (direction) element.style.transform = `translate(${direction.x}px, ${direction.y}px)`
  const cleanup = (element as HTMLElement & {_ktCleanup?: () => void})._ktCleanup
  cleanup?.()
  window.setTimeout(() => { element.remove(); const root = document.getElementById(ROOT_ID); if (root) restack(root) }, 320)
}

/** Bildirim gösterir. Aynı metin 3 sn içinde tekrar gelirse yinelenmez. Kapatmak için dönen fonksiyon çağrılabilir. */
export function showToast(options: ToastOptions): () => void {
  if (typeof document === 'undefined') return () => undefined
  const tone = options.tone ?? 'info'
  const key = `${tone}|${options.title ?? ''}|${options.body}`
  const now = Date.now()
  if ((recent.get(key) ?? 0) > now - 3000) return () => undefined
  recent.set(key, now)
  for (const [entry, at] of recent) if (at < now - 60_000) recent.delete(entry)

  const root = ensureRoot()
  const element = document.createElement('div')
  element.className = 'ktT'
  element.dataset.tone = tone
  element.setAttribute('role', tone === 'bad' ? 'alert' : 'status')

  const icon = document.createElement('span')
  icon.className = 'ktTIcon'
  icon.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[tone]}</svg>`
  const text = document.createElement('div')
  text.className = 'ktTText'
  if (options.title) { const title = document.createElement('b'); title.textContent = options.title; text.appendChild(title) }
  const body = document.createElement('span')
  body.textContent = options.body
  text.appendChild(body)
  if (options.action) {
    const action = document.createElement(options.action.href ? 'a' : 'button') as HTMLAnchorElement | HTMLButtonElement
    action.className = 'ktTAction'
    action.textContent = options.action.label
    if (options.action.href && action instanceof HTMLAnchorElement) action.href = options.action.href
    else if (action instanceof HTMLButtonElement) action.type = 'button'
    action.addEventListener('click', () => { options.action?.onClick?.(); dismiss(element) })
    text.appendChild(action)
  }
  const close = document.createElement('button')
  close.type = 'button'
  close.className = 'ktTClose'
  close.setAttribute('aria-label', 'Bildirimi kapat')
  close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'
  close.addEventListener('click', () => dismiss(element, {x: 0, y: -20}))
  const duration = options.duration ?? (tone === 'bad' ? 10_000 : tone === 'warn' ? 7000 : 5000)
  const bar = document.createElement('i')
  bar.className = 'ktTBar'
  bar.style.animationDuration = `${duration}ms`
  element.append(icon, text, close, bar)

  // Otomatik kapanma: dokunurken/üzerindeyken durur.
  let remaining = duration
  let startedAt = Date.now()
  let timer = window.setTimeout(() => dismiss(element), remaining)
  const pause = () => { if (element.hasAttribute('data-paused')) return; window.clearTimeout(timer); remaining -= Date.now() - startedAt; element.setAttribute('data-paused', '') }
  const resume = () => { if (!element.hasAttribute('data-paused')) return; element.removeAttribute('data-paused'); startedAt = Date.now(); timer = window.setTimeout(() => dismiss(element), Math.max(800, remaining)) }
  element.addEventListener('mouseenter', pause)
  element.addEventListener('mouseleave', resume)

  // Kaydırarak kapatma: yana ya da yukarı 60 px (veya hızlı fiske).
  let drag: {x: number; y: number; t: number; id: number} | null = null
  element.addEventListener('pointerdown', event => {
    if ((event.target as HTMLElement).closest('.ktTClose, .ktTAction')) return
    drag = {x: event.clientX, y: event.clientY, t: Date.now(), id: event.pointerId}
    element.setPointerCapture(event.pointerId)
    element.setAttribute('data-dragging', '')
    pause()
  })
  element.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return
    const dx = event.clientX - drag.x
    const dy = Math.min(0, event.clientY - drag.y)
    element.style.transform = `translate(${dx}px, ${dy}px)`
    element.style.opacity = String(Math.max(0.2, 1 - Math.max(Math.abs(dx), Math.abs(dy)) / 220))
  })
  const endDrag = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.id) return
    const dx = event.clientX - drag.x
    const dy = Math.min(0, event.clientY - drag.y)
    const fast = Date.now() - drag.t < 250 && (Math.abs(dx) > 25 || dy < -25)
    drag = null
    element.removeAttribute('data-dragging')
    if (Math.abs(dx) > 60 || dy < -45 || fast) {
      dismiss(element, Math.abs(dx) >= Math.abs(dy) ? {x: Math.sign(dx || 1) * 420, y: 0} : {x: 0, y: -160})
      return
    }
    element.style.transform = ''
    element.style.opacity = ''
    resume()
  }
  element.addEventListener('pointerup', endDrag)
  element.addEventListener('pointercancel', endDrag)

  // Sayfa kaydırılınca (en az 1,2 sn görünmüş ve acil değilse) kaybolsun.
  const shownAt = Date.now()
  const scrollStart = window.scrollY
  const onScroll = () => {
    if (tone === 'bad' || Date.now() - shownAt < 1200) return
    if (Math.abs(window.scrollY - scrollStart) > 80) dismiss(element, {x: 0, y: -40})
  }
  window.addEventListener('scroll', onScroll, {passive: true})
  ;(element as HTMLElement & {_ktCleanup?: () => void})._ktCleanup = () => { window.clearTimeout(timer); window.removeEventListener('scroll', onScroll) }

  root.prepend(element)
  restack(root)
  return () => dismiss(element)
}

/** Sunucunun teknik olay metnini müşteriye anlaşılır Türkçe'ye çevirir. */
export function humanizeLiveEvent(kind: string, message: string): string {
  const symbol = (message.match(/\b([A-Z0-9]{2,20}USDT)\b/) || [])[1]
  const coin = symbol ? symbol.replace(/USDT$/, '') : ''
  if (coin) {
    if (/STOP_HIT/.test(kind)) return `${coin} stopa değdi, pozisyon kapandı.`
    if (/TP_HIT/.test(kind)) return `${coin} kâr hedefine ulaştı.`
    if (kind === 'LIVE_CLOSE') return `${coin} pozisyonu kapatıldı.`
    if (kind === 'LIVE_ENTRY_CANCELLED') return `${coin} emri iptal edildi.`
  }
  return message
    .replace(/\b(tracked|reduce-only|reduceOnly)\b/gi, '')
    .replace(/_/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

import {useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode} from 'react'
import {Activity, X} from 'lucide-react'
import {createLandscapeController, type LandscapeController, type LandscapeSnapshot} from './master-trade-fullscreen'

const SERVER_SNAPSHOT: LandscapeSnapshot = {open: false, landscape: false, fullscreen: false, lock: 'none'}

/**
 * Yatay tam ekran durumu. Denetleyici bileşen başına bir kez kurulur; bileşen kalkınca tüm dinleyiciler,
 * ekran yönü kilidi ve tam ekran bırakılır. `escapeGuard` true dönerse Esc tam ekranı kapatmaz.
 */
export function useLandscapeFullscreen(escapeGuard: () => boolean) {
  const controller = useMemo<LandscapeController>(() => createLandscapeController(), [])
  const guard = useRef(escapeGuard)
  guard.current = escapeGuard
  useEffect(() => {
    controller.setEscapeGuard(() => guard.current())
    return () => controller.dispose()
  }, [controller])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, () => SERVER_SNAPSHOT)
  return {state, open: () => void controller.open(), close: controller.close}
}

/**
 * Tam ekran/uyarı açıkken arkadaki sayfa klavye odağı alamasın: seçilen öğenin atalarındaki tüm kardeşler `inert` olur,
 * kapanınca yalnız bizim eklediklerimiz geri alınır. `selector` null ise hiçbir şey yapılmaz.
 */
export function useIsolatedFocus(selector: string | null) {
  useEffect(() => {
    if (!selector) return
    const target = document.querySelector(selector)
    if (!target) return
    const changed: Element[] = []
    for (let node: Element | null = target; node && node !== document.body; node = node.parentElement) {
      for (const sibling of Array.from(node.parentElement?.children ?? [])) {
        if (sibling === node || sibling.hasAttribute('inert') || ['SCRIPT', 'STYLE', 'LINK'].includes(sibling.tagName)) continue
        sibling.setAttribute('inert', '')
        changed.push(sibling)
      }
    }
    return () => changed.forEach(element => element.removeAttribute('inert'))
  }, [selector])
}

/**
 * Bir öğenin kapanışında kısa bir çıkış geçişi için gecikmeli kaldırma. `visible` false olunca `leaving` true olur ve öğe
 * `ms` sonra kalkar; azaltılmış harekette gecikme yoktur. Zamanlayıcı kalkışta/yeniden açılışta temizlenir.
 */
export function useExitTransition(visible: boolean, ms = 220) {
  const [mounted, setMounted] = useState(visible)
  useEffect(() => {
    if (visible) { setMounted(true); return }
    const reduced = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) { setMounted(false); return }
    const timer = setTimeout(() => setMounted(false), ms)
    return () => clearTimeout(timer)
  }, [visible, ms])
  return {mounted: visible || mounted, leaving: !visible && mounted}
}

/** Dikey modda gösterilen "telefonu yan çevir" uyarısı. İptal her zaman çıkış sağlar. `studio`: Analysis Studio'dan açıldı. */
export function RotateOverlay({onCancel, canAutoRotate, studio = false, leaving = false}: {onCancel: () => void; canAutoRotate: boolean; studio?: boolean; leaving?: boolean}) {
  const cancel = useRef<HTMLButtonElement>(null)
  useEffect(() => { cancel.current?.focus() }, [])
  return <div className="kfsOverlay" data-leaving={leaving ? 'true' : undefined} aria-hidden={leaving || undefined} role="dialog" aria-modal="true" aria-labelledby="kfsRotateTitle" aria-describedby="kfsRotateText">
    <div className="kfsOverlayCard">
      <span className="kfsBrand" translate="no" data-i18n-skip><Activity aria-hidden="true"/>kais<b>trade</b></span>
      <div className="kfsPhoneStage" aria-hidden="true"><div className="kfsPhone"><i/></div></div>
      <h2 id="kfsRotateTitle">Turn your phone sideways</h2>
      <p id="kfsRotateText">{studio ? 'Rotate your device to open Analysis Studio.' : 'Switch to landscape for a wider trading chart.'}</p>
      {!canAutoRotate && <p className="kfsOverlayHint">If it does not rotate, turn on auto-rotate or turn your phone by hand.</p>}
      <button ref={cancel} type="button" className="kfsCancel" onClick={onCancel}>Cancel</button>
    </div>
  </div>
}

/** Sağdan açılan analiz paneli. Hep monte kalır (geçiş animasyonu için); kapalıyken `inert` olur. */
export function AnalysisDrawer({open, onClose, id, children}: {open: boolean; onClose: () => void; id: string; children: ReactNode}) {
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    // Panel açıkken Esc yalnız paneli kapatır; tam ekranı kapatmamak için üst bileşendeki escapeGuard true döner.
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])
  return <aside id={id} className="kfsDrawer" data-open={open} aria-label="Analysis" aria-hidden={!open} inert={!open}>
    <div className="kfsDrawerInner">
      <header><h3>Analysis</h3><button ref={close} type="button" aria-label="Close analysis" title="Close analysis" onClick={onClose}><X aria-hidden="true"/></button></header>
      <div className="kfsDrawerBody">{children}</div>
    </div>
  </aside>
}

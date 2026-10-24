/** Sayfa geçişi: sinematik bilinç geçişi. Tek bir zaman çizelgesi (requestAnimationFrame) dört aşamayı sürer:
 *  AWARENESS 0–350 ms · SYNCHRONIZATION 350–750 ms · TRANSIT 750–1250 ms · MATERIALIZATION 1100–1800 ms (kais-transit-scene).
 *  Gerçek sayfa değişimi (commit) SYNCHRONIZATION içinde yapılır, böylece hedef sayfa portal açılmadan önce arkada hazırlanır.
 *  Sayfa hazır değilse zaman çizelgesi portal açılmadan hemen önce bekler (en çok 2,4 sn); sonra güvenli biçimde devam eder.
 *  Yeni komut gelirse eski geçiş iptal edilir (eski sayfa değişimi yine yapılır) ve yenisi başlar. Ses/komut/yönlendirme mantığına
 *  dokunmaz: yalnız görsel katman. Sahne kurulamazsa ya da hareket azaltma açıksa kısa, sade bir kararma/açılma yapılır. */
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {PAGE_GLYPH} from './kais-portal-glyphs'
import {createTransitScene, TRANSIT} from './kais-transit-scene'
import './kais-pupil-dive.css'

export type PupilDiveOrigin = {x: number; y: number; size: number}
export type PupilDive = {view: string; label: string; origin: PupilDiveOrigin; commit: () => void; done: () => void}

/** Ekrandaki gerçek Kais gözünün konumu: önce hologram gözü, sonra üst menüdeki göz; yoksa ekran ortası. */
export function pupilDiveOrigin(preferred?: Element | null): PupilDiveOrigin {
  const candidates = [preferred, document.querySelector('.kaisHolo .kaisHoloEye'), document.querySelector('.assistantLauncher .kaisEye')]
  for (const element of candidates) {
    const rect = element?.getBoundingClientRect()
    if (rect && rect.width > 8 && rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth) {
      return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, size: Math.max(56, Math.min(rect.width, rect.height))}
    }
  }
  return {x: window.innerWidth / 2, y: window.innerHeight / 2, size: Math.min(240, window.innerWidth * .5)}
}

const MAX_HOLD_MS = 2400

export default function KaisPupilDive({dive}: {dive: PupilDive}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const root = useRef<HTMLDivElement>(null)
  const [flat, setFlat] = useState<'' | 'in' | 'out'>('')
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let committed = false, finished = false
    const commit = () => {
      if (committed) return
      committed = true
      try { dive.commit() } catch { /* sayfa açılamazsa mevcut güvenli davranış */ }
    }
    const finish = () => { if (finished) return; finished = true; try { dive.done() } catch { /* yok say */ } }
    const timers: number[] = []
    const at = (ms: number, fn: () => void) => timers.push(window.setTimeout(fn, ms))
    // Sade yol: hareket azaltma, gizli sekme ya da sahne kurulamadı
    const simple = () => { setFlat('in'); at(180, commit); at(240, () => setFlat('out')); at(560, finish) }
    const node = canvas.current
    if (reduced || !node || document.hidden) { simple(); return () => { timers.forEach(window.clearTimeout); commit() } }
    const holo = document.querySelector('.kaisHolo')
    const khCore = holo?.querySelector<HTMLCanvasElement>('.khCore')?.getBoundingClientRect()
    const levelOf = () => Number(document.querySelector<HTMLElement>('.kaisHolo')?.style.getPropertyValue('--kh-level')) || 0
    let scene: ReturnType<typeof createTransitScene> = null
    try {
      scene = createTransitScene({
        canvas: node, origin: dive.origin, lite: window.matchMedia('(max-width: 1024px)').matches, label: dive.label,
        glyph: PAGE_GLYPH[dive.view] ?? PAGE_GLYPH.dashboard, getLevel: levelOf,
        core: khCore && khCore.width > 40 ? {x: khCore.left + khCore.width / 2, y: khCore.top + khCore.height / 2, r: khCore.width / 4} : null,
      })
    } catch { scene = null }
    if (!scene) { simple(); return () => { timers.forEach(window.clearTimeout); commit() } }
    scene.resize()
    const resize = () => scene?.resize()
    window.addEventListener('resize', resize)
    let raf = 0, last = 0, held = 0, waited = 0, commitAt = 0, commitFrames = 0
    const start = performance.now()
    const ready = (now: number) => committed && commitFrames >= 4 && now - commitAt >= 450 && document.readyState !== 'loading' && !document.querySelector('.moduleLazyLoading')
    const frame = (now: number) => {
      const dt = last ? now - last : 16; last = now
      let tt = now - start - held
      if (committed) commitFrames += 1
      if (tt >= TRANSIT.commitAt && !committed) { commit(); commitAt = now; commitFrames = 0 }
      // Sayfa hazır değilse portal açılmadan önce bekle (sahne ortam hareketiyle canlı kalır); zaman aşımında güvenle devam et
      if (tt >= TRANSIT.holdAt && waited < MAX_HOLD_MS && !ready(now)) { held += dt; waited += dt; tt = TRANSIT.holdAt }
      try { scene?.draw(tt, (now - start) / 1000) } catch { cleanup(); commit(); finish(); return }
      if (root.current) root.current.style.pointerEvents = tt >= TRANSIT.holdAt ? 'none' : 'auto'
      if (tt >= TRANSIT.endAt) { finish(); return }
      raf = requestAnimationFrame(frame)
    }
    const cleanup = () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
    raf = requestAnimationFrame(frame)
    return () => { cleanup(); timers.forEach(window.clearTimeout); scene?.dispose(); commit() }
  }, [dive])
  return createPortal(<div ref={root} className="kaisPupilDive kpdCinema" data-flat={flat || undefined} aria-hidden="true">
    <canvas ref={canvas} className="kpdCanvas"/>
    <i className="kpdShade"/>
  </div>, document.body)
}

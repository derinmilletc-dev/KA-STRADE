/** Canvas enerji çekirdeği bileşeni: çizim motorunu (kais-holo-render) bağlar. Durum, ses seviyesi, bakış ve paralaks GERÇEK uygulama
 *  durumundan okunur (kök öğedeki data-phase ve --kh-level/--kh-px/--kh-py; gözün data-look-x/y). Gizli sekmede ve geçişlerde durur,
 *  hareket-azaltma açıkken tek kare çizer, bileşen kalkınca döngü ve dinleyiciler temizlenir. */
import {useEffect, useRef} from 'react'
import {createHoloRenderer, type CorePhase} from './kais-holo-render'

export default function KaisHoloCanvas({phase, root, paused}: {phase: CorePhase; root: React.RefObject<HTMLDivElement | null>; paused: boolean}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  const renderer = useRef<ReturnType<typeof createHoloRenderer>>(null)
  useEffect(() => {
    const node = canvas.current
    if (!node) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let eye: SVGSVGElement | null = null
    let eyeChecked = 0
    const instance = createHoloRenderer(node, {
      getPhase: () => phaseRef.current,
      getLevel: () => Number(root.current?.style.getPropertyValue('--kh-level')) || 0,
      getParallax: () => [Number(root.current?.style.getPropertyValue('--kh-px')) || 0, Number(root.current?.style.getPropertyValue('--kh-py')) || 0],
      getLook: () => {
        const now = performance.now()
        if (!eye?.isConnected && now - eyeChecked > 400) { eyeChecked = now; eye = node.closest('.kaisHolo')?.querySelector<SVGSVGElement>('.kaisEye') ?? null }
        return [Number(eye?.dataset.lookX) || 0, Number(eye?.dataset.lookY) || 0]
      },
      lite: !window.matchMedia('(min-width: 1025px)').matches,
      reduced,
    })
    renderer.current = instance
    if (!instance) return
    instance.resize()
    const observer = new ResizeObserver(() => { instance.resize(); if (reduced) instance.redraw() })
    observer.observe(node)
    if (!paused) instance.start()
    return () => { observer.disconnect(); instance.dispose(); renderer.current = null }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const instance = renderer.current
    if (!instance) return
    if (paused) instance.stop(); else instance.start()
  }, [paused])
  useEffect(() => { if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) renderer.current?.redraw() }, [phase])
  return <canvas ref={canvas} className="khCore" aria-hidden="true"/>
}

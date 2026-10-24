/** Canlı dijital evren arka planı (Canvas). Durum, ses seviyesi, paralaks ve hologram konumu GERÇEK uygulama durumundan okunur
 *  (kök öğedeki data-phase ve --kh-level/--kh-px/--kh-py; hologram konumu .khCore tuvalinden). Gizli sekmede durur, hareket-azaltma
 *  açıkken tek kare çizer, telefonda yoğunluk azalır, bileşen kalkınca döngü ve dinleyiciler temizlenir. Yalnız dekoratiftir. */
import {useEffect, useRef} from 'react'
import {createUniverse, type UniversePhase} from './kais-holo-universe'

export default function KaisHoloUniverse({phase, root, paused}: {phase: UniversePhase; root: React.RefObject<HTMLDivElement | null>; paused: boolean}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  const instance = useRef<ReturnType<typeof createUniverse>>(null)
  useEffect(() => {
    const node = canvas.current
    if (!node) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const read = (name: string) => Number(root.current?.style.getPropertyValue(name)) || 0
    const engine = createUniverse(node, {
      getPhase: () => phaseRef.current,
      getLevel: () => read('--kh-level'),
      getParallax: () => [read('--kh-px'), read('--kh-py')],
      getCore: () => {
        const core = root.current?.querySelector<HTMLCanvasElement>('.khCore')
        if (!core) return null
        const box = core.getBoundingClientRect(), mine = node.getBoundingClientRect()
        if (!box.width) return null
        return {x: box.left + box.width / 2 - mine.left, y: box.top + box.height / 2 - mine.top, r: box.width / 4}
      },
      lite: window.matchMedia('(max-width: 1024px)').matches,
      reduced,
    })
    instance.current = engine
    if (!engine) return
    engine.resize()
    const observer = new ResizeObserver(() => { engine.resize(); if (reduced) engine.redraw() })
    observer.observe(node)
    if (!paused) engine.start()
    return () => { observer.disconnect(); engine.dispose(); instance.current = null }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const engine = instance.current
    if (!engine) return
    if (paused) engine.stop(); else engine.start()
  }, [paused])
  useEffect(() => { if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) instance.current?.redraw() }, [phase])
  return <canvas ref={canvas} className="khUniverse" aria-hidden="true"/>
}

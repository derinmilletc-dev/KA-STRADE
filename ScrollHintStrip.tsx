import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

/** Yatay kaydırılan şerit: kaydırılabildiğini belli eder.
 * - Devamı olan kenarda yumuşak solma ve küçük ok düğmesi (dokununca şerit kayar)
 * - Altta konumu gösteren ince çizgi
 * - İlk girişte bir kez hafifçe kayıp geri gelir (hareket azaltma açıksa yapılmaz) */
const NUDGE_KEY = 'kais-strip-nudge-seen'

export default function ScrollHintStrip({className, label, children}: {className: string; label: string; children: ReactNode}) {
  const strip = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({start: true, end: true, ratio: 1, offset: 0})

  const measure = useCallback(() => {
    const element = strip.current
    if (!element) return
    const max = element.scrollWidth - element.clientWidth
    const ratio = element.scrollWidth > 0 ? Math.min(1, element.clientWidth / element.scrollWidth) : 1
    setEdges({
      start: element.scrollLeft <= 2,
      end: element.scrollLeft >= max - 2,
      ratio,
      offset: max > 0 ? (element.scrollLeft / max) * (1 - ratio) : 0,
    })
  }, [])

  useEffect(() => {
    const element = strip.current
    if (!element) return
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    for (const child of Array.from(element.children)) observer?.observe(child)
    return () => observer?.disconnect()
  }, [measure, children])

  useEffect(() => {
    const element = strip.current
    if (!element || element.scrollWidth <= element.clientWidth + 4) return
    let seen = false
    try { seen = localStorage.getItem(NUDGE_KEY) === '1' } catch { seen = true }
    if (seen || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const timers = [
      window.setTimeout(() => element.scrollTo({left: 56, behavior: 'smooth'}), 700),
      window.setTimeout(() => element.scrollTo({left: 0, behavior: 'smooth'}), 1500),
    ]
    try { localStorage.setItem(NUDGE_KEY, '1') } catch { /* yalnız bir kez göstermek içindir */ }
    return () => timers.forEach(window.clearTimeout)
  }, [])

  const page = (direction: 1 | -1) => {
    const element = strip.current
    if (element) element.scrollBy({left: direction * Math.max(120, element.clientWidth * 0.7), behavior: 'smooth'})
  }
  const scrollable = edges.ratio < 0.999

  return <div className="scrollHintWrap" data-start={edges.start || undefined} data-end={edges.end || undefined} data-scrollable={scrollable || undefined}>
    <div ref={strip} className={className} aria-label={label} onScroll={measure}>{children}</div>
    {scrollable && !edges.start && <button type="button" className="scrollHintArrow isStart" aria-label="Sola kaydır" onClick={() => page(-1)}><ChevronLeft aria-hidden="true"/></button>}
    {scrollable && !edges.end && <button type="button" className="scrollHintArrow isEnd" aria-label="Diğer coinleri göster" onClick={() => page(1)}><ChevronRight aria-hidden="true"/></button>}
    {scrollable && <span className="scrollHintTrack" aria-hidden="true"><i style={{width: `${edges.ratio * 100}%`, transform: `translateX(${(edges.offset / Math.max(edges.ratio, 0.01)) * 100}%)`}}/></span>}
  </div>
}

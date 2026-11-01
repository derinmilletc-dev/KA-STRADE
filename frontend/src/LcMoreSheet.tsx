import {useEffect, useRef, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import './lc-more-sheet.css'

/** "⋯" menüsü: kartın içinde değil, ekranın altında açılan sade bir sayfa.
 * Kartın kesmesinden ve düğmelerin üstüne binmesinden etkilenmesin diye sayfanın en üstüne (body) çizilir. */
export type LcMoreItem = {key: string; label: string; icon: ReactNode; tone?: 'stop'; onSelect: () => void}

export default function LcMoreSheet({open, title = 'Diğer işlemler', items, onClose}: {open: boolean; title?: string; items: LcMoreItem[]; onClose: () => void}) {
  const first = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    first.current?.focus({preventScroll: true})
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open || typeof document === 'undefined') return null
  const normal = items.filter(item => item.tone !== 'stop')
  const stops = items.filter(item => item.tone === 'stop')
  const row = (item: LcMoreItem, index: number) => <button key={item.key} ref={index === 0 ? first : undefined} type="button" role="menuitem" className="kmsItem" data-tone={item.tone} onClick={() => { onClose(); item.onSelect() }}>
    <i className="kmsIco" data-k={item.key} aria-hidden="true">{item.icon}</i><span>{item.label}</span>
  </button>
  return createPortal(<>
    <div className="kmsShade" role="presentation" onClick={onClose}/>
    <div className="kmsSheet" role="menu" aria-label={title}>
      <span className="kmsGrip" aria-hidden="true"/>
      <p className="kmsTitle">{title}</p>
      {normal.map((item, index) => row(item, index))}
      {stops.length > 0 && <><div className="kmsSep" role="separator"/>{stops.map((item, index) => row(item, normal.length + index))}</>}
    </div>
  </>, document.body)
}

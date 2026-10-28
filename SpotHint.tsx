import {useEffect, useState, type CSSProperties} from 'react'
import {createPortal} from 'react-dom'
import {useMemberAccess} from './premium-access'
import './site-tour.css'

// Akıllı ipucu: yalnız bir kez, hedef düğmeyi parlatıp "buradan başla" der. "Anladım"a ya da hedefe basılınca bir daha çıkmaz.
// Site turu açıkken görünmez. Hiçbir işlem yapmaz; yalnız vurgular.
type Box = {top: number; left: number; width: number; height: number}
const key = (userId: string, id: string) => `kais-hint:v1:${userId}:${id}`
const seen = (userId: string, id: string) => { try { return localStorage.getItem(key(userId, id)) === 'seen' } catch { return true } }
const remember = (userId: string, id: string) => { try { localStorage.setItem(key(userId, id), 'seen') } catch { /* saklama kapalıysa bu oturumda kapanır */ } }
const findTarget = (selectors: string[]): HTMLElement | null => {
  for (const selector of selectors) {
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const rect = element.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden') return element
    }
  }
  return null
}

export default function SpotHint({id, active, targets, title, text}: {id: string; active: boolean; targets: string[]; title: string; text: string}) {
  const {userId} = useMemberAccess()
  const [dismissed, setDismissed] = useState(false)
  const [box, setBox] = useState<Box | null>(null)
  const show = Boolean(active && userId && !dismissed && !seen(userId, id))

  useEffect(() => {
    if (!show || !userId) { setBox(null); return }
    let element: HTMLElement | null = null
    const done = () => { remember(userId, id); setDismissed(true) }
    const measure = () => {
      const found = findTarget(targets)
      if (found !== element) { element?.removeEventListener('click', done); element = found; element?.addEventListener('click', done) }
      if (!found) { setBox(null); return }
      const rect = found.getBoundingClientRect()
      const value = {top: Math.round(rect.top - 8), left: Math.round(rect.left - 8), width: Math.round(rect.width + 16), height: Math.round(rect.height + 16)}
      setBox(current => current && current.top === value.top && current.left === value.left && current.width === value.width && current.height === value.height ? current : value)
    }
    measure()
    const timer = window.setInterval(measure, 300)
    window.addEventListener('resize', measure); window.addEventListener('scroll', measure, true)
    return () => { window.clearInterval(timer); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true); element?.removeEventListener('click', done) }
  }, [show, userId, id, targets])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!show || !box || !userId) return null
  const vw = window.innerWidth, vh = window.innerHeight
  const width = Math.min(360, vw - 24)
  const below = vh - (box.top + box.height)
  const style: CSSProperties = {left: Math.min(Math.max(12, box.left + box.width / 2 - width / 2), vw - width - 12), width,
    ...(below >= 150 ? {top: box.top + box.height + 14} : {bottom: Math.max(12, vh - box.top + 14)})}
  return createPortal(<div className="ktHint" data-hint={id}>
    <div className="ktHintSpot" style={{top: box.top, left: box.left, width: box.width, height: box.height}} aria-hidden="true"/>
    <div className="ktHintCard" style={style} role="status">
      <span><b>{title}</b> {text}</span>
      <button type="button" onClick={() => { remember(userId, id); setDismissed(true) }}>Anladım</button>
    </div>
  </div>, document.body)
}

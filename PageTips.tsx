import {useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties} from 'react'
import {createPortal} from 'react-dom'
import {HelpCircle, X} from 'lucide-react'
import {useMemberAccess} from './premium-access'
import {TOUR_OPEN_ATTRIBUTE, tourMemoryKey} from './site-tour'
import {PAGE_TIPS, PAGE_TIPS_OPEN_EVENT, nextTipIndex, pageTipsKey, previousTipIndex, tipPlacement} from './page-tips'
import './site-tour.css'

// Sayfa ipuçları: bir bölüme ilk girişte kısa baloncuklar, sonra bölümdeki "?" tuşuyla her zaman tekrar.
// Hiçbir işlem yapmaz, hiçbir ayar kaydetmez; yalnız vurgular. Site turu açıkken ya da hiç sorulmamışken görünmez.
type Box = {top: number; left: number; width: number; height: number}
type TipsId = keyof typeof PAGE_TIPS

const seen = (userId: string, id: string) => { try { return localStorage.getItem(pageTipsKey(userId, id)) === 'seen' } catch { return true } }
const remember = (userId: string, id: string) => { try { localStorage.setItem(pageTipsKey(userId, id), 'seen') } catch { /* saklama kapalıysa bu oturumda kapanır */ } }
/** Site turu hiç sorulmadıysa (yeni üye) ipuçları beklemeye alınır; tur önce gelir. */
const tourHandled = (userId: string) => { try { return localStorage.getItem(tourMemoryKey(userId)) !== null } catch { return false } }
const findTarget = (selectors: string[]): HTMLElement | null => {
  for (const selector of selectors) {
    let list: HTMLElement[] = []
    try { list = Array.from(document.querySelectorAll<HTMLElement>(selector)) } catch { continue }
    for (const element of list) {
      const rect = element.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden') return element
    }
  }
  return null
}

// Aynı anda yalnız bir bölümün ipuçları açık olur (iki bölüm birlikte monte olsa da kartlar üst üste binmesin).
let owner: string | null = null

/** Bölümün köşesine konan küçük "?" tuşu; ipuçlarını baştan açar. */
export function PageTipsHelp({id}: {id: TipsId}) {
  return <button type="button" className="ktPtHelp" data-testid={`page-tips-help-${id}`} aria-label={`${PAGE_TIPS[id].label} için kısa anlatım`} title="Bu bölümü kısaca anlat"
    onClick={() => window.dispatchEvent(new CustomEvent(PAGE_TIPS_OPEN_EVENT, {detail: id}))}><HelpCircle aria-hidden="true"/></button>
}

export default function PageTips({id, active}: {id: TipsId; active: boolean}) {
  const {userId, ready} = useMemberAccess()
  const definition = PAGE_TIPS[id]
  const [index, setIndex] = useState<number | null>(null)
  const [box, setBox] = useState<Box | null>(null)
  const [cardHeight, setCardHeight] = useState(170)
  const cardHeightRef = useRef(170)
  cardHeightRef.current = cardHeight
  const card = useRef<HTMLDivElement | null>(null)
  const primary = useRef<HTMLButtonElement | null>(null)
  const open = index !== null

  const close = useCallback((markSeen: boolean) => {
    if (markSeen && userId) remember(userId, id)
    if (owner === id) owner = null
    setIndex(null)
  }, [userId, id])
  const focusOnOpen = useRef(false)
  const begin = useCallback((manual = false) => {
    if (owner && owner !== id) return
    owner = id
    focusOnOpen.current = manual
    setIndex(0)
  }, [id])

  // İlk girişte kendiliğinden: bölüm görünür, tur bitmiş/kapatılmış, daha önce görülmemiş ve tur şu an açık değil.
  useEffect(() => {
    if (!active || !ready || !userId || open || seen(userId, id) || !tourHandled(userId)) return
    const timer = window.setTimeout(() => {
      if (document.body.hasAttribute(TOUR_OPEN_ATTRIBUTE) || !findTarget(definition.tips[0]?.targets ?? [])) return
      begin(false)
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [active, ready, userId, id, open, begin, definition])
  // "?" tuşu: görülmüş olsa da baştan açar.
  useEffect(() => {
    const onOpen = (event: Event) => {
      if ((event as CustomEvent).detail === id && active && !document.body.hasAttribute(TOUR_OPEN_ATTRIBUTE)) begin(true)
    }
    window.addEventListener(PAGE_TIPS_OPEN_EVENT, onOpen)
    return () => window.removeEventListener(PAGE_TIPS_OPEN_EVENT, onOpen)
  }, [id, active, begin])
  // Bölüm kapanırsa / bileşen kalkarsa sahiplik bırakılır ve kart kapanır.
  useEffect(() => {
    if (!active && open) close(false)
  }, [active, open, close])
  useEffect(() => () => { if (owner === id) owner = null }, [id])
  // Site turu açılırsa ipuçları çekilir.
  useEffect(() => {
    if (!open) return
    const observer = new MutationObserver(() => { if (document.body.hasAttribute(TOUR_OPEN_ATTRIBUTE)) close(false) })
    observer.observe(document.body, {attributes: true, attributeFilter: [TOUR_OPEN_ATTRIBUTE]})
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close(true) }
    window.addEventListener('keydown', onKey)
    return () => { observer.disconnect(); window.removeEventListener('keydown', onKey) }
  }, [open, close])

  // Vurgulanan yerin konumu (kaydırma/yeniden boyutlandırmada da); hedef ilk göründüğünde bir kez görünür alana getirilir.
  useEffect(() => {
    if (index === null) { setBox(null); return }
    const tip = definition.tips[index]
    if (!tip) return
    let scrolled = false
    let missing = 0
    const measure = () => {
      // Bölümün ana öğesi 2 sn boyunca görünmezse (sekme değişti, bölüm kapandı) ipuçları sessizce kapanır.
      if (findTarget(definition.tips[0]?.targets ?? [])) missing = 0
      else if (++missing >= 8) { close(false); return }
      const element = findTarget(tip.targets)
      if (!element) { setBox(null); return }
      if (!scrolled) {
        scrolled = true
        const rect = element.getBoundingClientRect()
        const viewport = window.innerHeight
        const room = cardHeightRef.current + 36
        // Kart hedefin altına ya da üstüne sığmıyorsa hedef, kartın üstündeki boş alana getirilir; hiç görünmüyorsa ortaya.
        const fits = viewport - rect.bottom >= room || rect.top >= room
        if (rect.top < 70 || rect.bottom > viewport - 70 || !fits) {
          const top = !fits && rect.height < viewport - room - 100
          const previous = element.style.scrollMarginTop
          if (top) element.style.scrollMarginTop = '84px'
          try { element.scrollIntoView({block: top ? 'start' : 'center', behavior: 'smooth'}) } catch { /* eski tarayıcı */ }
          window.setTimeout(() => { element.style.scrollMarginTop = previous }, 1200)
        }
      }
      const rect = element.getBoundingClientRect()
      const pad = 6
      const value = {top: Math.round(rect.top - pad), left: Math.round(rect.left - pad), width: Math.round(rect.width + pad * 2), height: Math.round(rect.height + pad * 2)}
      setBox(current => current && current.top === value.top && current.left === value.left && current.width === value.width && current.height === value.height ? current : value)
    }
    measure()
    const timer = window.setInterval(measure, 250)
    window.addEventListener('resize', measure); window.addEventListener('scroll', measure, true)
    return () => { window.clearInterval(timer); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true) }
  }, [index, definition, close])

  useLayoutEffect(() => {
    if (index === null || !card.current) return
    const height = Math.round(card.current.getBoundingClientRect().height)
    if (height && Math.abs(height - cardHeight) > 1) setCardHeight(height)
    if (focusOnOpen.current) primary.current?.focus({preventScroll: true})
  }, [index, box, cardHeight])

  if (index === null || !userId) return null
  const tip = definition.tips[index]
  if (!tip) return null
  const total = definition.tips.length
  const vw = window.innerWidth, vh = window.innerHeight
  const width = Math.min(380, vw - 24)
  const placement = tipPlacement(box, cardHeight, vh)
  let style: CSSProperties
  const left = box ? Math.min(Math.max(12, box.left + box.width / 2 - width / 2), vw - width - 12) : Math.max(12, (vw - width) / 2)
  if (placement === 'below' && box) style = {left, width, top: box.top + box.height + 14}
  else if (placement === 'above' && box) style = {left, width, top: box.top - cardHeight - 14}
  else {
    // Telefonda alt menünün hemen üstüne oturur (menüyü örtmez).
    const nav = document.querySelector<HTMLElement>('.v26MobileBottomNav')
    const navTop = nav && nav.getBoundingClientRect().height > 0 ? nav.getBoundingClientRect().top : vh
    style = {left, width, bottom: Math.max(14, vh - navTop + 10)}
  }
  const last = nextTipIndex(index, total) === null
  const go = () => { const next = nextTipIndex(index, total); if (next === null) close(true); else setIndex(next) }

  return createPortal(<div className="ktPt" data-tips={id} data-step={index}>
    {box ? <div className="ktPtSpot" style={{top: box.top, left: box.left, width: box.width, height: box.height}} aria-hidden="true"/> : <div className="ktPtDim" aria-hidden="true"/>}
    <div ref={card} className="ktPtCard" data-placement={placement} style={style} role="dialog" aria-modal="false" aria-labelledby="kt-pt-title" data-testid="page-tips">
      <button type="button" className="ktPtX" aria-label="İpuçlarını kapat" onClick={() => close(true)}><X aria-hidden="true"/></button>
      <small className="ktPtKicker">{definition.label.toUpperCase()} · {index + 1} / {total}</small>
      <div className="ktPtBody" key={index}>
        <h4 id="kt-pt-title">{tip.title}</h4>
        <p>{tip.text}</p>
      </div>
      <div className="ktPtDots" aria-hidden="true">{definition.tips.map((item, position) => <i key={item.title} data-on={position <= index || undefined}/>)}</div>
      <div className="ktPtActions">
        {index > 0 ? <button type="button" className="ktPtGhost" onClick={() => setIndex(previousTipIndex(index))}>← Geri</button>
          : <button type="button" className="ktPtGhost" onClick={() => close(true)}>Atla</button>}
        <button ref={primary} type="button" className="ktPtGo" data-testid="page-tips-next" onClick={go}>{last ? 'Anladım' : 'İleri →'}</button>
      </div>
    </div>
  </div>, document.body)
}

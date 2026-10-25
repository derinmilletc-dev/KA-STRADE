import {useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties} from 'react'
import {X} from 'lucide-react'
import './master-trade-tour.css'

type Tab = 'analiz' | 'canli' | 'pozisyonlar' | 'baglanti'
type Step = {id: string; title: string; body: string; targets?: string[]; need?: Tab; press?: Tab}

const tabTargets = (id: Tab) => [`#master-tab-${id}`, `#masterTradeTab-${id}`]
const STEPS: Step[] = [
  {id: 'welcome', title: 'Master Trade’e hoş geldin', body: 'Burası KaisTrade’in ana ekranı. 4 bölümü birlikte gezelim, 1 dakika sürer. Tur sırasında hiçbir şey kaydedilmez ve hiçbir işlem açılmaz.'},
  {id: 'analiz', title: '1 · Analiz', body: 'Burada coini seçersin, Kais yönü ve güven puanını gösterir. Önce buraya bakıp fikir edinirsin.', targets: tabTargets('analiz')},
  {id: 'press-canli', title: 'Şimdi sen bas', body: 'Parlayan “Canlı İşlem” sekmesine bas. Sen basmadan ilerlemem.', targets: tabTargets('canli'), press: 'canli'},
  {id: 'canli-guide', title: '2 · Canlı İşlem · Kais Rehber', body: 'Bu kart sıradaki işi sana söyler. Her aşamada önünde tek bir büyük buton olur; ne yapacağını düşünmene gerek kalmaz.', targets: ['[data-testid="live-guide"]', '#master-panel-canli'], need: 'canli'},
  {id: 'canli-launch', title: 'Hepsini hazırla ve başlat', body: 'Bu buton risk, günlük sınır, teminat ve izinleri adım adım ayarlar. Her adımı sen onaylarsın; en sonda ONAYLIYORUM yazmadan hiçbir şey çalışmaz.', targets: ['[data-testid="launch-open"]', '[data-testid="live-guide"]', '#master-panel-canli'], need: 'canli'},
  {id: 'press-poz', title: 'Şimdi sen bas', body: 'Parlayan “Pozisyonlar” sekmesine bas.', targets: tabTargets('pozisyonlar'), press: 'pozisyonlar'},
  {id: 'poz', title: '3 · Pozisyonlar', body: 'Açık işlemlerin burada görünür. Kâr ve zararını buradan izlersin.', targets: ['#master-panel-pozisyonlar'], need: 'pozisyonlar'},
  {id: 'press-bag', title: 'Şimdi sen bas', body: 'Parlayan “Bağlantı” sekmesine bas.', targets: tabTargets('baglanti'), press: 'baglanti'},
  {id: 'bag', title: '4 · Bağlantı', body: 'Binance anahtarını burada kaydedersin; kasada şifreli tutulur. Para çekme iznini asla açma. Turda anahtar yazmana gerek yok.', targets: ['#master-panel-baglanti'], need: 'baglanti'},
  {id: 'end', title: 'Hazırsın', body: 'Sıradaki adım: Canlı İşlem’de “Hepsini hazırla ve başlat”. Turu istediğin zaman sol alttaki “Tanıtım” butonundan yeniden başlatabilirsin.'},
]
const KEY = (userId: string) => `kais-master-tour:v1:${userId}`
const readTab = (): Tab => {
  const value = new URLSearchParams(window.location.search).get('tab')
  return value === 'canli' || value === 'pozisyonlar' || value === 'baglanti' ? value : 'analiz'
}
const goTab = (tab: Tab) => {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  window.history.pushState(window.history.state, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
const findTarget = (selectors?: string[]): HTMLElement | null => {
  for (const selector of selectors ?? []) {
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const box = element.getBoundingClientRect()
      if (box.width > 0 && box.height > 0 && getComputedStyle(element).visibility !== 'hidden') return element
    }
  }
  return null
}
type Box = {top: number; left: number; width: number; height: number}

export default function MasterTradeTour({userId}: {userId: string | null | undefined}) {
  const [open, setOpen] = useState(false)
  const [index, setIndex] = useState(0)
  const [tab, setTab] = useState<Tab>(() => readTab())
  const [box, setBox] = useState<Box | null>(null)
  const [panelHeight, setPanelHeight] = useState(240)
  const panel = useRef<HTMLDivElement | null>(null)
  const next = useRef<HTMLButtonElement | null>(null)
  const step = STEPS[index]
  const last = index === STEPS.length - 1

  const finish = useCallback(() => {
    setOpen(false)
    try { if (userId) localStorage.setItem(KEY(userId), 'done') } catch { /* kayıt kapalıysa tur yine kapanır */ }
  }, [userId])
  const start = () => { setIndex(0); setOpen(true) }

  useEffect(() => {
    const update = () => setTab(readTab())
    window.addEventListener('popstate', update)
    return () => window.removeEventListener('popstate', update)
  }, [])
  // İlk girişte kendiliğinden açılır; bir kez bitince ya da atlanınca bir daha açılmaz.
  useEffect(() => {
    if (!userId) return
    let seen = true
    try { seen = localStorage.getItem(KEY(userId)) === 'done' } catch { seen = true }
    if (seen) return
    const timer = window.setTimeout(() => setOpen(true), 1800)
    return () => window.clearTimeout(timer)
  }, [userId])
  // Vurgulanan yerin konumunu izler (yerleşim değişse de, kaydırılsa da).
  useEffect(() => {
    if (!open) return
    const measure = () => {
      const element = findTarget(step.targets)
      if (!element) { setBox(null); return }
      const rect = element.getBoundingClientRect()
      const pad = step.press ? 6 : 8
      const value = {top: Math.round(rect.top - pad), left: Math.round(rect.left - pad), width: Math.round(rect.width + pad * 2), height: Math.round(rect.height + pad * 2)}
      setBox(current => current && current.top === value.top && current.left === value.left && current.width === value.width && current.height === value.height ? current : value)
    }
    const element = findTarget(step.targets)
    if (element) { try { element.scrollIntoView({block: 'center', behavior: 'smooth'}) } catch { /* eski tarayıcı */ } }
    measure()
    const timer = window.setInterval(measure, 250)
    window.addEventListener('resize', measure); window.addEventListener('scroll', measure, true)
    return () => { window.clearInterval(timer); window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true) }
  }, [open, index, tab, step])
  // "Şimdi sen bas" adımı: kullanıcı sekmeye basınca kendiliğinden ilerler.
  useEffect(() => {
    if (!open || !step.press || tab !== step.press) return
    const timer = window.setTimeout(() => setIndex(current => STEPS[current]?.press === step.press ? current + 1 : current), 450)
    return () => window.clearTimeout(timer)
  }, [open, step, tab])
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') finish() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, finish])
  useLayoutEffect(() => {
    if (!open || !panel.current) return
    const height = Math.round(panel.current.getBoundingClientRect().height)
    if (height && height !== panelHeight) setPanelHeight(height)
    next.current?.focus({preventScroll: true})
  }, [open, index, box, panelHeight])

  if (!userId) return null
  if (!open) return <button type="button" className="mtTourReplay" onClick={start} aria-label="Master Trade tanıtımını başlat"><i aria-hidden="true"/>Tanıtım</button>

  const wrongTab = Boolean(step.need && tab !== step.need)
  const pressWaiting = Boolean(step.press && tab !== step.press)
  const targetMissing = Boolean(step.targets && !box)
  const blocked = wrongTab || pressWaiting
  const mobile = window.innerWidth <= 640
  const vw = window.innerWidth, vh = window.innerHeight
  const width = Math.min(400, vw - 24)
  let style: CSSProperties | undefined
  if (!mobile) {
    if (!box || box.height > vh * 0.55) style = box ? {right: 24, bottom: 24, width} : {left: Math.max(12, (vw - width) / 2), top: Math.max(12, (vh - panelHeight) / 2), width}
    else {
      const below = vh - (box.top + box.height)
      const top = below >= panelHeight + 28 ? box.top + box.height + 16 : box.top >= panelHeight + 28 ? box.top - panelHeight - 16 : Math.max(12, vh - panelHeight - 24)
      style = {top, left: Math.min(Math.max(12, box.left + box.width / 2 - width / 2), vw - width - 12), width}
    }
  }
  const goHere = step.press ?? step.need
  return <div className="mtTour" data-step={step.id}>
    {box ? <div className="mtTourSpot" data-press={step.press ? '' : undefined} style={{top: box.top, left: box.left, width: box.width, height: box.height}} aria-hidden="true"/> : <div className="mtTourDim" aria-hidden="true"/>}
    <div ref={panel} className="mtTourPanel" data-mobile={mobile || undefined} style={style} role="dialog" aria-modal="false" aria-labelledby="mt-tour-title" data-testid="master-tour">
      <button type="button" className="mtTourClose" aria-label="Turu kapat" onClick={finish}><X aria-hidden="true"/></button>
      <small className="mtTourKicker">MASTER TRADE · {index + 1}/{STEPS.length}</small>
      <h3 id="mt-tour-title">{step.title}</h3>
      <p>{step.body}</p>
      {wrongTab && <p className="mtTourHint">Bu adım için önce doğru sekmede olmalısın.</p>}
      {(wrongTab || (pressWaiting && targetMissing)) && goHere && <button type="button" className="mtTourLink" onClick={() => goTab(goHere)}>Beni oraya götür</button>}
      <div className="mtTourDots" aria-hidden="true">{STEPS.map((item, position) => <i key={item.id} data-on={position <= index || undefined}/>)}</div>
      <div className="mtTourActions">
        <button type="button" className="mtTourGhost" onClick={finish}>{last ? 'Kapat' : 'Atla'}</button>
        <span>
          {index > 0 && <button type="button" className="mtTourGhost" onClick={() => setIndex(current => Math.max(0, current - 1))}>← Geri</button>}
          <button ref={next} type="button" className="mtTourGo" disabled={blocked} data-testid="master-tour-next" onClick={() => last ? finish() : setIndex(current => current + 1)}>{last ? 'Bitir ✓' : pressWaiting ? 'Sekmeye bas…' : 'İleri →'}</button>
        </span>
      </div>
    </div>
  </div>
}

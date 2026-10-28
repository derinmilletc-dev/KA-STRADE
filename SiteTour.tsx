import {useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties} from 'react'
import {createPortal} from 'react-dom'
import {X} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import {startKaisVoice} from './kais-ask'
import {useMemberAccess} from './premium-access'
import {
  PHONE_QUERY, TOUR_OPEN_ATTRIBUTE, TOUR_START_EVENT, buildTourSteps, firstName, shouldOfferTour, tourMemoryKey,
  type TourMemory, type TourStep, type TourView,
} from './site-tour'
import './site-tour.css'

// Site turu: ilk girişte ortada karşılama kartı, "Turu başlat" ile adım adım gezinti. ✕ ve bitirme bir daha sormaz,
// "Sonra" 2 gün sormaz; Profil · Site Turu kutusundan her zaman yeniden açılır. Tur hiçbir ayar kaydetmez, hiçbir işlem açmaz.
type Box = {top: number; left: number; width: number; height: number}

const readTab = () => new URLSearchParams(window.location.search).get('tab')
const goTab = (tab: string) => {
  const url = new URL(window.location.href)
  url.searchParams.set('tab', tab)
  window.history.pushState(window.history.state, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}
const goView = (view: TourView) => window.dispatchEvent(new CustomEvent('protrebot-navigate', {detail: view}))
const findTarget = (selectors?: string[]): HTMLElement | null => {
  for (const selector of selectors ?? []) {
    for (const element of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const box = element.getBoundingClientRect()
      if (box.width > 0 && box.height > 0 && getComputedStyle(element).visibility !== 'hidden') return element
    }
  }
  return null
}
function load(userId: string): string | null { try { return localStorage.getItem(tourMemoryKey(userId)) } catch { return null } }
function save(userId: string, kind: TourMemory) { try { localStorage.setItem(tourMemoryKey(userId), `${kind}|${Date.now()}`) } catch { /* saklama kapalıysa tur yine kapanır */ } }

const FREE_ITEMS = [
  'Kais’le sohbet ve sorular (günlük sınırlı)',
  'Analist ve Tarama: her analiz günlük krediden düşer',
  'Piyasa özeti ve coin karşılaştırma: günde birkaç kez, kredili',
  'Master Trade canlı işlem alanı yok',
]
const PREMIUM_ITEMS = [
  'Sınırsız analiz: kredi hesabı yapmazsın',
  'Sınırsız piyasa özeti ve coin karşılaştırma',
  'Master Trade: canlı işlem alanı ve Kais Rehber',
  'Kendi açık pozisyonlarının giriş, stop ve hedef seviyelerini Kais’ten sorabilirsin',
  'Canlı işlemde Acil durdur ve akıllı koruma',
]
function Plans({premium}: {premium: boolean}) {
  if (premium) return <>
    <div className="ktTourPlan" data-premium=""><em>AKTİF · MASTER MODE</em><ul>{PREMIUM_ITEMS.map(text => <li key={text}>{text}</li>)}</ul></div>
    <p className="ktTourFine">Planını, yenileme tarihini ve faturalarını Paketler ve Fatura sayfalarından yönetirsin. İstediğin zaman iptal edebilirsin.</p>
  </>
  return <>
    <div className="ktTourPlan"><b>Ücretsiz</b><ul>{FREE_ITEMS.map(text => <li key={text} data-no={text.endsWith(' yok') || undefined}>{text}</li>)}</ul></div>
    <div className="ktTourPlan" data-premium=""><em>PREMIUM</em><b>Master Mode · 119,90 USD / ay</b><ul>{PREMIUM_ITEMS.map(text => <li key={text}>{text}</li>)}</ul></div>
    <p className="ktTourFine">İstediğin zaman iptal edebilirsin. Deneme ve ödeme seçeneklerini Paketler sayfasında görürsün. Hiçbir paket kazanç garantisi vermez; risk kontrolü her zaman sende.</p>
  </>
}

function usePhone() {
  const [phone, setPhone] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia(PHONE_QUERY).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(PHONE_QUERY)
    const update = () => setPhone(query.matches)
    update(); query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return phone
}

export default function SiteTour() {
  const {userId, premium, displayName, ready} = useMemberAccess()
  const [mode, setMode] = useState<'closed' | 'welcome' | 'tour'>('closed')
  const [index, setIndex] = useState(0)
  const [tab, setTab] = useState<string | null>(() => readTab())
  const [box, setBox] = useState<Box | null>(null)
  const [panelHeight, setPanelHeight] = useState(260)
  const panel = useRef<HTMLDivElement | null>(null)
  const primary = useRef<HTMLButtonElement | null>(null)
  const phone = usePhone()
  const steps = buildTourSteps(premium, phone)
  const step: TourStep | undefined = steps[index]

  // Başka kartların (ana ekrana ekle, bildirim) tur sürerken çıkmaması için gövdeye işaret konur.
  useEffect(() => {
    if (mode === 'closed') return
    document.body.setAttribute(TOUR_OPEN_ATTRIBUTE, '')
    return () => document.body.removeAttribute(TOUR_OPEN_ATTRIBUTE)
  }, [mode])

  const close = useCallback((kind: TourMemory | null) => {
    setMode('closed')
    setIndex(0)
    if (kind && userId) save(userId, kind)
  }, [userId])
  const begin = useCallback(() => { setIndex(0); setMode('tour') }, [])

  // İlk girişte kendiliğinden açılır (hafızaya göre).
  useEffect(() => {
    if (!ready || !userId) return
    if (!shouldOfferTour(load(userId), Date.now())) return
    const timer = window.setTimeout(() => setMode(current => current === 'closed' ? 'welcome' : current), 1500)
    return () => window.clearTimeout(timer)
  }, [ready, userId])
  // Profil · Site Turu kutusu her zaman yeniden açar.
  useEffect(() => {
    const open = () => { setIndex(0); setMode('welcome') }
    window.addEventListener(TOUR_START_EVENT, open)
    return () => window.removeEventListener(TOUR_START_EVENT, open)
  }, [])
  useEffect(() => {
    const update = () => setTab(readTab())
    window.addEventListener('popstate', update)
    return () => window.removeEventListener('popstate', update)
  }, [])
  useEffect(() => {
    if (mode === 'closed') return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close('later') }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode, close])

  // Adıma girince ilgili sayfaya geçilir (yalnız adım sayfa belirtiyorsa).
  const entered = useRef<string>('')
  useEffect(() => {
    if (mode !== 'tour' || !step) { entered.current = ''; return }
    if (entered.current === step.id) return
    entered.current = step.id
    if (step.view) goView(step.view)
    if (step.need && readTab() !== step.need) goTab(step.need)
  }, [mode, step])

  // Vurgulanan yerin konumunu izler (yerleşim değişse de, kaydırılsa da).
  useEffect(() => {
    if (mode !== 'tour' || !step) { setBox(null); return }
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
  }, [mode, step, tab])

  // "Şimdi sen dokun" adımı: kullanıcı sekmeye basınca kendiliğinden ilerler.
  const pressed = Boolean(step?.press && tab === step.press)
  useEffect(() => {
    if (mode !== 'tour' || !step?.press || !pressed) return
    const id = step.id
    const timer = window.setTimeout(() => setIndex(current => steps[current]?.id === id ? current + 1 : current), 450)
    return () => window.clearTimeout(timer)
  }, [mode, step, pressed]) // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (mode === 'closed' || !panel.current) return
    const height = Math.round(panel.current.getBoundingClientRect().height)
    if (height && Math.abs(height - panelHeight) > 1) setPanelHeight(height)
    primary.current?.focus({preventScroll: true})
  }, [mode, index, box, panelHeight])

  if (mode === 'closed' || !userId) return null

  const name = firstName(displayName)
  if (mode === 'welcome') {
    return createPortal(<div className="ktTour" data-mode="welcome">
      <div className="ktTourDim" aria-hidden="true"/>
      <div ref={panel} className="ktTourCard ktTourWelcome" role="dialog" aria-modal="true" aria-labelledby="kt-tour-title" data-testid="site-tour-welcome">
        <button type="button" className="ktTourX" aria-label="Kapat, bir daha sorma" onClick={() => close('never')}><X aria-hidden="true"/></button>
        <div className="ktTourEye" aria-hidden="true"><KaisEye size={120} state="idle"/></div>
        <small className="ktTourKicker">KAİS · HOŞ GELDİN</small>
        <h2 id="kt-tour-title">{name ? `Merhaba ${name}` : 'Merhaba'} 👋</h2>
        <p>Ben Kais. Seni siteyle tanıştırayım: nerede ne var, Ücretsiz ve Premium’da neler değişiyor, takıldığında nasıl yardım alırsın. Yaklaşık 2 dakika.</p>
        <button ref={primary} type="button" className="ktTourGo" data-testid="site-tour-start" onClick={begin}>Turu başlat</button>
        <button type="button" className="ktTourGhost" onClick={() => close('later')}>Sonra hatırlat</button>
        <small className="ktTourFine">Sağ üstteki ✕ ile kapatırsan bir daha sormam. Profil’deki Site Turu kutusundan tekrar açabilirsin.</small>
      </div>
    </div>, document.body)
  }
  if (!step) return null

  const last = index === steps.length - 1
  const wrongTab = Boolean(step.need && tab !== step.need)
  const pressWaiting = Boolean(step.press && !pressed)
  const blocked = pressWaiting
  const mobile = window.innerWidth <= 640
  const vw = window.innerWidth, vh = window.innerHeight
  const width = Math.min(420, vw - 24)
  let style: CSSProperties | undefined
  if (mobile) {
    // Telefon: kart alt menünün üstünde durur; vurgulanan yer alt yarıdaysa kart üste geçer, vurguyu kapatmaz.
    const nav = document.querySelector<HTMLElement>('.v26MobileBottomNav')
    const navTop = nav && nav.getBoundingClientRect().height > 0 ? nav.getBoundingClientRect().top : vh
    const targetLow = Boolean(box && box.top + box.height / 2 > vh * 0.45)
    style = targetLow
      ? {left: 12, right: 12, bottom: 'auto', top: 'max(12px, env(safe-area-inset-top))', maxHeight: Math.max(160, navTop - 24), overflowY: 'auto'}
      : {left: 12, right: 12, bottom: Math.max(12, vh - navTop + 10), maxHeight: Math.max(160, navTop - 24), overflowY: 'auto'}
  } else if (!box || box.height > vh * 0.55) {
    style = box ? {right: 24, bottom: 24, width} : {left: Math.max(12, (vw - width) / 2), top: Math.max(12, (vh - panelHeight) / 2), width}
  } else {
    const below = vh - (box.top + box.height)
    const top = below >= panelHeight + 28 ? box.top + box.height + 16 : box.top >= panelHeight + 28 ? box.top - panelHeight - 16 : Math.max(12, vh - panelHeight - 24)
    style = {top, left: Math.min(Math.max(12, box.left + box.width / 2 - width / 2), vw - width - 12), width}
  }
  const next = () => { if (last) close('never'); else setIndex(current => Math.min(steps.length - 1, current + 1)) }

  return createPortal(<div className="ktTour" data-mode="tour" data-step={step.id}>
    {box ? <div className="ktTourSpot" data-press={step.press ? '' : undefined} style={{top: box.top, left: box.left, width: box.width, height: box.height}} aria-hidden="true"/> : <div className="ktTourDim" aria-hidden="true"/>}
    {box && step.press && <span className="ktTourFinger" aria-hidden="true" style={{top: box.top + box.height - 10, left: box.left + box.width / 2 - 14}}>👆</span>}
    <div ref={panel} className="ktTourCard" data-mobile={mobile || undefined} style={style} role="dialog" aria-modal="false" aria-labelledby="kt-tour-title" data-testid="site-tour">
      <button type="button" className="ktTourX" aria-label="Turu kapat, bir daha sorma" onClick={() => close('never')}><X aria-hidden="true"/></button>
      {step.hero === 'studio' && <div className="ktTourHero" data-hero="studio" aria-hidden="true"><span className="ktTourRail"><i>✎</i><i>╱</i><i>⏰</i></span><span className="ktTourCandles">{[40,52,34,58,46,62,50,66,44,56,38,60,48].map((h, i) => <i key={i} data-dn={i % 3 === 1 || undefined} style={{height: h}}/>)}</span><b data-k="tp">TP1 79 229</b><b data-k="entry">GİRİŞ 80 526</b><b data-k="sl">SL 81 823</b></div>}
      {step.hero === 'trade' && <div className="ktTourHero" data-hero="trade" aria-hidden="true"><span>⚡ İşlem aç</span><em>→</em><span>Manuel işlem<small>BTCUSDT hazır</small></span><em>→</em><span data-on>Canlı takip<small>+0,84 USDT</small></span></div>}
      <div className="ktTourDots" aria-hidden="true">{steps.map((item, position) => <i key={item.id} data-on={position <= index || undefined}/>)}</div>
      {step.extra === 'voice' && <div className="ktTourWave" aria-hidden="true"><i/><i/><i/><KaisEye size={96} state="idle"/></div>}
      <small className="ktTourKicker">{index + 1} / {steps.length} · {step.kicker}</small>
      <h3 id="kt-tour-title">{step.title}</h3>
      <p>{step.body}</p>
      {step.extra === 'plans' && <Plans premium={premium}/>}
      {step.extra === 'voice' && <div className="ktTourSay">{['Hey Kais, bugün nasıl gitti?', 'Hey Kais, BTC mi ETH mi?', 'Hey Kais, neden işlem açmadın?'].map(text => <span key={text}>🎙️ “{text}”</span>)}</div>}
      {step.feats && <div className="ktTourFeats">{step.feats.map(item => <div key={item.text} data-wide={item.wide || undefined}><span aria-hidden="true">{item.icon}</span>{item.text}</div>)}</div>}
      {step.note && <p className="ktTourNote">{step.note}</p>}
      {step.bullets && <ul className="ktTourList">{step.bullets.map(text => <li key={text}>{text}</li>)}</ul>}
      {pressWaiting && <p className="ktTourNudge">👆 Parlayan sekmeye dokun.</p>}
      {wrongTab && <button type="button" className="ktTourLink" onClick={() => step.need && goTab(step.need)}>Sizi oraya götüreyim</button>}
      {step.extra === 'voice' ? <>
        <button ref={primary} type="button" className="ktTourGo" data-testid="site-tour-voice" onClick={() => { close('never'); startKaisVoice() }}>Hey Kais’i dene</button>
        <button type="button" className="ktTourGhost" onClick={next}>Turu bitir 🎉</button>
      </> : <div className="ktTourActions">
        <button type="button" className="ktTourGhost" disabled={index === 0} onClick={() => setIndex(current => Math.max(0, current - 1))}>← Geri</button>
        <button ref={primary} type="button" className="ktTourGo" disabled={blocked} data-testid="site-tour-next" onClick={next}>{pressWaiting ? 'Sekmeye dokun…' : 'İleri →'}</button>
      </div>}
    </div>
  </div>, document.body)
}


import {useEffect, useState} from 'react'
import './install-prompt.css'

// "Ana ekrana ekle" kartı. Android/Chrome: tarayıcının kendi yükleme penceresini tek dokunuşla açar.
// iPhone/iPad: tarayıcı buna izin vermediği için iki adımlı kısa yol gösterir. Uygulama zaten
// ana ekrandan açıldıysa hiç görünmez. "Sonra" denirse 7 gün boyunca tekrar sorulmaz.

type InstallEvent = Event & {prompt: () => Promise<void>; userChoice: Promise<{outcome: 'accepted' | 'dismissed'}>}

let deferred: InstallEvent | null = null
const listeners = new Set<() => void>()
const notify = () => listeners.forEach(fn => fn())

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); deferred = event as InstallEvent; notify() })
  window.addEventListener('appinstalled', () => { deferred = null; remember('installed'); notify() })
  // Yükleme önerisi için servis işçisinin kayıtlı olması gerekir. sw.js yalnız bildirim gösterir, önbellek tutmaz.
  if ('serviceWorker' in navigator) window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => undefined) })
}

const KEY = 'kt-install-prompt'
const WAIT_DAYS = 7
function remember(value: string) { try { localStorage.setItem(KEY, `${value}|${Date.now()}`) } catch { /* saklama kapalı olabilir */ } }
function snoozed() {
  try {
    const [value, at] = (localStorage.getItem(KEY) || '').split('|')
    if (value === 'installed') return true
    return value === 'later' && Date.now() - Number(at) < WAIT_DAYS * 86400000
  } catch { return false }
}
const standalone = () => window.matchMedia?.('(display-mode: standalone)').matches || (navigator as {standalone?: boolean}).standalone === true
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)

export default function InstallPrompt() {
  const [, force] = useState(0)
  const [ready, setReady] = useState(false)
  const [closed, setClosed] = useState(false)
  const [iosSteps, setIosSteps] = useState(false)
  useEffect(() => {
    const update = () => force(n => n + 1)
    listeners.add(update)
    const timer = window.setTimeout(() => setReady(true), 2500)
    return () => { listeners.delete(update); window.clearTimeout(timer) }
  }, [])
  if (!ready || closed || standalone() || snoozed()) return null
  const ios = isIos()
  if (!deferred && !ios) return null

  const later = () => { remember('later'); setClosed(true) }
  const install = async () => {
    if (ios) { setIosSteps(true); return }
    const event = deferred
    if (!event) return
    deferred = null
    await event.prompt().catch(() => undefined)
    const choice = await event.userChoice.catch(() => ({outcome: 'dismissed' as const}))
    remember(choice.outcome === 'accepted' ? 'installed' : 'later')
    setClosed(true)
  }

  return <div className="ktInstall" role="dialog" aria-labelledby="kt-install-title" aria-describedby="kt-install-text">
    <button type="button" className="ktInstallClose" aria-label="Kapat" onClick={later}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
    </button>
    <div className="ktInstallTop">
      <img src="/icon-192.png" alt="" width={52} height={52}/>
      <div>
        <b id="kt-install-title">KaisTrade’i ana ekrana ekle</b>
        <p id="kt-install-text">Tek dokunuşla aç, Kais'e sesle sor, bildirimleri kaçırma.</p>
      </div>
    </div>
    {iosSteps ? <ol className="ktInstallSteps">
      <li><i>1</i><span>Alttaki <b>Paylaş</b> düğmesine dokun</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg></li>
      <li><i>2</i><span><b>Ana Ekrana Ekle</b>’yi seç</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8v8M8 12h8"/></svg></li>
    </ol> : <div className="ktInstallActions">
      <button type="button" className="ktInstallLater" onClick={later}>Sonra</button>
      <button type="button" className="ktInstallGo" data-testid="install-app" onClick={() => void install()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/></svg>Ana ekrana ekle</button>
    </div>}
  </div>
}

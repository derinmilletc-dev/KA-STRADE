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
/** "Sonra" 7 gün sorulmaz. "Yüklendi" notu yalnız tarayıcı yükleme sinyali VERMEDİĞİNDE (elle adım kartı) dikkate alınır:
 * uygulama kaldırılınca Chrome yeniden sinyal verir ve kart tekrar çıkar. */
function snoozed(hasPrompt: boolean) {
  try {
    const [value, at] = (localStorage.getItem(KEY) || '').split('|')
    if (value === 'installed') return !hasPrompt && Date.now() - Number(at) < WAIT_DAYS * 86400000
    return value === 'later' && Date.now() - Number(at) < WAIT_DAYS * 86400000
  } catch { return false }
}
const standalone = () => window.matchMedia?.('(display-mode: standalone)').matches || (navigator as {standalone?: boolean}).standalone === true
const isAndroid = () => /android/i.test(navigator.userAgent)
/** Uygulama içi tarayıcılar (Google uygulaması, Instagram, Facebook …) ana ekrana eklemeyi desteklemez. */
const inAppBrowser = () => /\bGSA\/|FBAN|FBAV|Instagram|Line\/|; wv\)/i.test(navigator.userAgent)
const CHROME_INTENT = () => `intent://${location.host}${location.pathname}${location.search}#Intent;scheme=https;package=com.android.chrome;end`
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)

export default function InstallPrompt() {
  const [, force] = useState(0)
  const [ready, setReady] = useState(false)
  const [closed, setClosed] = useState(false)
  const [iosSteps, setIosSteps] = useState(false)
  const [manual, setManual] = useState(false)
  useEffect(() => {
    const update = () => force(n => n + 1)
    listeners.add(update)
    const timer = window.setTimeout(() => setReady(true), 2500)
    // Android'de tarayıcı yükleme sinyali gelmezse (uygulama içi tarayıcı, yeni kaldırılmış uygulama …) elle adımları göster.
    const fallback = window.setTimeout(() => { if (!deferred && isAndroid()) setManual(true) }, 6000)
    return () => { listeners.delete(update); window.clearTimeout(timer); window.clearTimeout(fallback) }
  }, [])
  if (!ready || closed || standalone() || snoozed(Boolean(deferred))) return null
  const ios = isIos()
  const androidManual = !deferred && manual && isAndroid()
  if (!deferred && !ios && !androidManual) return null

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
    {androidManual ? (inAppBrowser() ? <div className="ktInstallActions">
      <button type="button" className="ktInstallLater" onClick={later}>Sonra</button>
      <a className="ktInstallGo" href={CHROME_INTENT()}>Chrome'da aç</a>
    </div> : <ol className="ktInstallSteps">
      <li><i>1</i><span>Sağ üstteki <b>⋮</b> menüsüne dokun</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg></li>
      <li><i>2</i><span><b>Ana ekrana ekle</b> ya da <b>Uygulamayı yükle</b>’yi seç</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8v8M8 12h8"/></svg></li>
    </ol>) : iosSteps ? <ol className="ktInstallSteps">
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

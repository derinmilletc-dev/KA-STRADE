import {useEffect, useState} from 'react'
import {entryScript, isNewVersion} from './update-check'
import './install-prompt.css'

// "Yeni sürüm var · Güncelle": uygulama arka planda açık kaldıysa eski sürümle çalışır; yayına yeni sürüm çıkınca bu şerit görünür.
// Ana ekrana eklenmiş uygulamayı silip yeniden yüklemeye gerek yoktur; Güncelle sayfayı yeniler.
const CHECK_MS = 5 * 60 * 1000

function currentScript(): string | null {
  const element = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')
  return element ? new URL(element.src).pathname : null
}

export default function UpdateBanner() {
  const [available, setAvailable] = useState(false)
  useEffect(() => {
    const current = currentScript()
    if (!current) return  // geliştirme ortamı: denetim yok
    let alive = true
    let busy = false
    const check = async () => {
      if (busy || document.visibilityState !== 'visible') return
      busy = true
      try {
        const response = await fetch(`/?v=${Date.now()}`, {cache: 'no-store', credentials: 'same-origin'})
        if (response.ok && alive && isNewVersion(current, entryScript(await response.text()))) setAvailable(true)
      } catch { /* çevrimdışı: sonra tekrar denenir */ }
      finally { busy = false }
    }
    const visible = () => { if (document.visibilityState === 'visible') void check() }
    const timer = window.setInterval(() => void check(), CHECK_MS)
    document.addEventListener('visibilitychange', visible)
    window.addEventListener('online', visible)
    return () => { alive = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); window.removeEventListener('online', visible) }
  }, [])
  if (!available) return null
  return <div className="ktUpdate" role="status">
    <span><b>Yeni sürüm hazır</b><small>Yeni özellikler için güncelle.</small></span>
    <button type="button" data-testid="app-update" onClick={() => window.location.reload()}>Güncelle</button>
  </div>
}

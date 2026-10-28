import {useEffect, useState} from 'react'
import {Bot} from 'lucide-react'
import {API_BASE} from './api'
import './notify-settings.css'

// Profil: "Kais hesabımı görebilir". Açıkken Kais bakiye, ayarlar, kapanan işlemler, olaylar ve açık pozisyon
// seviyelerini SADECE OKUR. Emir açamaz, kapatamaz, ayar değiştiremez; API anahtarını hiç görmez.
export default function KaisAccessSetting() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API_BASE}/assistant/account-access`, {signal: controller.signal})
      .then(response => response.ok ? response.json() : Promise.reject(new Error(String(response.status))))
      .then((body: {enabled?: unknown}) => setEnabled(body.enabled !== false))
      .catch(() => { if (!controller.signal.aborted) setError('Ayar okunamadı; daha sonra tekrar dene.') })
    return () => controller.abort()
  }, [])
  const change = async (value: boolean) => {
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/assistant/account-access`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: value})})
      if (!response.ok) throw new Error(String(response.status))
      const body = await response.json() as {enabled?: unknown}
      setEnabled(body.enabled !== false)
    } catch {
      setError('Kaydedilemedi; tekrar dene.')
    } finally {
      setBusy(false)
    }
  }
  const on = enabled === true
  return <section className="accountCard nsCard" id="kais-access">
    <h2>Kais AI</h2>
    <div className="nsList">
      <div className="nsRow">
        <span><b><Bot/>Kais hesabımı görebilir</b><small>Bakiye, ayarlar, kapanan işlemler, olaylar ve açık pozisyonların giriş/stop/TP seviyeleri. Kais yalnız okur: emir açamaz, kapatamaz, ayar değiştiremez, API anahtarını görmez.</small></span>
        <button type="button" role="switch" aria-checked={on} aria-label="Kais hesabımı görebilir" className={`nsSwitch${on ? ' on' : ''}`} disabled={busy || enabled === null} onClick={() => void change(!on)}><i/></button>
      </div>
    </div>
    {error && <p className="accountMuted" role="alert">{error}</p>}
  </section>
}

import {useEffect, useState} from 'react'
import {Bot} from 'lucide-react'
import {API_BASE} from './api'
import './notify-settings.css'
import './kais-access.css'

// Profil: "Kais hesabımı görebilir". Açıkken Kais bakiye, ayarlar, kapanan işlemler, olaylar ve açık pozisyon
// seviyelerini SADECE OKUR. Emir açamaz, kapatamaz, ayar değiştiremez; API anahtarını hiç görmez.
const VOICE_URL = 'https://kaistrade.com/?kais=voice'

export default function KaisAccessSetting() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
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
    <details className="nsHelp kaisVoiceHelp" id="kais-voice">
      <summary>Kais'i sesle kullan</summary>
      <ol>
        <li><b>“Hey Kais”:</b> Kais sohbetini aç → sağ üstteki ⋯ → <b>“Hey Kais ile uyandır”</b>. KaisTrade ekranda açıkken “Hey Kais, bakiyem ne?” demen yeter. Telefon kapalıyken ya da başka uygulamadayken dinlemez; telefonlar buna izin vermiyor.</li>
        <li><b>Android:</b> Ana ekrandaki KaisTrade ikonuna basılı tut → <b>“Kais'e sor”</b>. Uygulama mikrofon açık başlar.</li>
        <li><b>iPhone (Siri):</b> Kısayollar uygulamasını aç → <b>+</b> → <b>Eylem ekle</b> → <b>“URL'leri aç”</b> → adres olarak <code>{VOICE_URL}</code> yaz → kısayolun adını <b>Kais</b> yap. Artık “Hey Siri, Kais” deyince KaisTrade mikrofon açık başlar. Siri sayfayı Safari'de açar; Safari'de de giriş yapmış olman gerekir.</li>
      </ol>
      <button type="button" className="nsTest" onClick={() => { void navigator.clipboard?.writeText(VOICE_URL).then(() => setCopied(true)).catch(() => undefined) }}>{copied ? 'Adres kopyalandı ✓' : 'Siri adresini kopyala'}</button>
    </details>
  </section>
}

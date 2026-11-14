import {useEffect, useState} from 'react'
import {currentLang} from './i18n'
import './studio-a2hs-tip.css'

// iPhone Safari, web sitelerinin adres/sekme çubuğunu gizlemesine izin vermez; Analiz Studio'da tam ekran ancak
// uygulama ana ekrandan açılınca olur. Bu kart yalnız iPhone/iPad Safari'de (ana ekrandan açılmamışsa) Studio açılınca çıkar.
// "Sonra" denirse bir gün sorulmaz. Dil kendi başına seçilir (çeviri katmanı dışında).

const KEY = 'kt-studio-a2hs-tip'
const WAIT_MS = 24 * 3600 * 1000
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
const standalone = () => window.matchMedia?.('(display-mode: standalone)').matches || (navigator as {standalone?: boolean}).standalone === true
const inAppBrowser = () => /\bGSA\/|FBAN|FBAV|Instagram|Line\/|; wv\)/i.test(navigator.userAgent)
function snoozed() {
  try { const at = Number(localStorage.getItem(KEY)); return Boolean(at) && Date.now() - at < WAIT_MS } catch { return false }
}
function snooze() { try { localStorage.setItem(KEY, String(Date.now())) } catch { /* saklama kapalı olabilir */ } }

export const studioTipApplies = () => typeof window !== 'undefined' && isIos() && !standalone() && !inAppBrowser() && !snoozed()

const SHARE = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V4M8.5 7.5 12 4l3.5 3.5M6 11H5.5A1.5 1.5 0 0 0 4 12.5v6A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5v-6a1.5 1.5 0 0 0-1.5-1.5H18"/></svg>
const PLUS = <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8.5v7M8.5 12h7"/></svg>

export default function StudioHomeScreenTip() {
  const en = currentLang() === 'en'
  const [shown, setShown] = useState(false)
  const [steps, setSteps] = useState(false)
  useEffect(() => {
    if (!studioTipApplies()) return
    const timer = window.setTimeout(() => setShown(true), 600)
    return () => window.clearTimeout(timer)
  }, [])
  if (!shown) return null
  const later = () => { snooze(); setShown(false) }
  return <div className="ktA2hs" role="dialog" aria-labelledby="kt-a2hs-title" translate="no">
    <div className="ktA2hsHead">
      <span className="ktA2hsIcon" aria-hidden="true">{PLUS}</span>
      <div>
        <b id="kt-a2hs-title">{en ? 'Get the full-screen chart' : 'Tam ekran grafik için'}</b>
        <p>{en
          ? 'Safari does not allow full screen on iPhone. Add KaisTrade to your Home Screen and follow your trades more smoothly from there.'
          : 'Safari iPhone’da tam ekrana izin vermiyor. KaisTrade’i Ana Ekrana ekleyip oradan açarsan grafiği ve işlemlerini çok daha verimli takip edersin.'}</p>
      </div>
      <button type="button" className="ktA2hsClose" aria-label={en ? 'Close' : 'Kapat'} onClick={later}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
      </button>
    </div>
    {steps && <ol className="ktA2hsSteps">
      <li><span>1</span>{en ? <>Open this page in <b>Safari</b>.</> : <>Bu sayfayı <b>Safari</b>’de aç.</>}</li>
      <li><span>2</span>{en ? <>Tap <b>⋯</b> next to the address bar (or the <b>Share</b> button at the bottom on older iOS), then <b>Share</b></> : <>Adres çubuğunun yanındaki <b>⋯</b> düğmesine bas (eski iOS’ta alttaki <b>Paylaş</b> düğmesi), sonra <b>Paylaş</b></>} <i className="ktA2hsGlyph">{SHARE}</i></li>
      <li><span>3</span>{en ? <>Scroll down and choose <b>Add to Home Screen</b>.</> : <>Aşağı kaydır, <b>Ana Ekrana Ekle</b>’yi seç.</>}</li>
      <li><span>4</span>{en ? <>Keep <b>Open as Web App</b> on (if shown), then tap <b>Add</b>.</> : <><b>Web Uygulaması Olarak Aç</b> seçeneği varsa açık kalsın, sonra <b>Ekle</b>’ye bas.</>}</li>
      <li><span>5</span>{en ? <>Open KaisTrade from the new icon.</> : <>Yeni KaisTrade simgesinden aç.</>}</li>
    </ol>}
    <div className="ktA2hsActions">
      <button type="button" className="ktA2hsPrimary" onClick={() => setSteps(value => !value)} aria-expanded={steps}>
        {steps ? (en ? 'Hide steps' : 'Adımları gizle') : (en ? 'Add to Home Screen' : 'Ana Ekrana Ekle')}
      </button>
      <button type="button" className="ktA2hsGhost" onClick={later}>{en ? 'Later' : 'Sonra'}</button>
    </div>
  </div>
}

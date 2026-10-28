import {useEffect, useState} from 'react'
import {Play} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import {useMemberAccess} from './premium-access'
import {TOUR_START_EVENT, parseTourMemory, tourMemoryKey} from './site-tour'
import './site-tour.css'

// Profil · Site Turu: kapatılmış olsa bile turu her zaman yeniden açar.
export default function SiteTourSetting() {
  const {userId} = useMemberAccess()
  const [closed, setClosed] = useState(false)
  useEffect(() => {
    if (!userId) return
    try { setClosed(parseTourMemory(localStorage.getItem(tourMemoryKey(userId)))?.kind === 'never') } catch { setClosed(false) }
  }, [userId])
  return <section className="accountCard" id="site-tour-setting">
    <h2>Site Turu</h2>
    <div className="stRow">
      <KaisEye size={52} state="idle"/>
      <span><b>Kais seni siteyle tanıştırsın</b><small>{closed ? 'Tur şu an kapalı; istediğin zaman buradan tekrar açabilirsin.' : 'Hangi sayfada ne olduğunu, Ücretsiz ve Premium farkını ve Hey Kais’i kısaca anlatır.'}</small></span>
    </div>
    <button type="button" className="stGo" data-testid="site-tour-replay" onClick={() => window.dispatchEvent(new Event(TOUR_START_EVENT))}><Play aria-hidden="true"/>Turu tekrar başlat</button>
  </section>
}

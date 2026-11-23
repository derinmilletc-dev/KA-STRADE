import {useEffect, useSyncExternalStore} from 'react'
import {connectionHealth} from './connection-health'
import {refreshAllNow} from './visible-interval'
import {API_BASE} from './api'

// Artık ekranda küçük bildirim göstermez (sayfadaki tek bağlantı kartı yeterli; ikisi üst üste biniyordu).
// Arka plandaki işi sürer: bağlantı geri gelince açık sayfaları hemen yeniler, kopukken sunucuyu kendisi yoklar.
export default function ConnectionBanner() {
  const snapshot = useSyncExternalStore(connectionHealth.subscribe, connectionHealth.get, connectionHealth.get)

  useEffect(() => connectionHealth.onRecovered(() => { refreshAllNow() }), [])

  useEffect(() => {
    if (snapshot.state === 'ok') return
    const probe = () => { void fetch(`${API_BASE}/health`, {cache: 'no-store'}).catch(() => undefined) }
    const timer = window.setInterval(probe, 5000)
    probe()
    return () => window.clearInterval(timer)
  }, [snapshot.state])

  return null
}

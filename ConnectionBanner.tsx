import {useEffect, useState, useSyncExternalStore} from 'react'
import {connectionHealth} from './connection-health'
import {refreshAllNow} from './visible-interval'
import {API_BASE} from './api'
import './connection-banner.css'

// Üstte ince şerit: bağlantı kopunca görünür, geri gelince "Bağlantı geri geldi" deyip kaybolur, açık sayfalardaki veriyi hemen yeniler.
export default function ConnectionBanner() {
  const snapshot = useSyncExternalStore(connectionHealth.subscribe, connectionHealth.get, connectionHealth.get)
  const [justRecovered, setJustRecovered] = useState(false)
  const [checking, setChecking] = useState(false)

  useEffect(() => connectionHealth.onRecovered(() => {
    refreshAllNow()
    setJustRecovered(true)
    const timer = window.setTimeout(() => setJustRecovered(false), 2800)
    return () => window.clearTimeout(timer)
  }), [])

  // Kopukken sunucuyu kendisi yoklar: kullanıcı beklemeden toparlanır.
  useEffect(() => {
    if (snapshot.state === 'ok') return
    const probe = () => { void fetch(`${API_BASE}/health`, {cache: 'no-store'}).catch(() => undefined) }
    const timer = window.setInterval(probe, 5000)
    probe()
    return () => window.clearInterval(timer)
  }, [snapshot.state])

  const retry = () => {
    setChecking(true)
    void fetch(`${API_BASE}/health`, {cache: 'no-store'}).catch(() => undefined).finally(() => window.setTimeout(() => setChecking(false), 600))
  }

  if (snapshot.state === 'ok' && !justRecovered) return null
  const offline = snapshot.state === 'offline'
  const kind = snapshot.state === 'ok' ? 'isOk' : offline ? 'isOffline' : 'isDegraded'
  return <div className={`connBanner ${kind}`} role="status" aria-live="polite">
    <i aria-hidden="true"/>
    <span>{snapshot.state === 'ok' ? 'Bağlantı geri geldi' : offline ? 'İnternet bağlantın yok' : 'Sunucuya ulaşılamıyor, yeniden bağlanıyor…'}</span>
    {snapshot.state !== 'ok' && <button type="button" onClick={retry} disabled={checking}>{checking ? 'Deneniyor…' : 'Şimdi dene'}</button>}
  </div>
}

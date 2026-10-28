import {useEffect, useState} from 'react'
import {enablePush, pushState} from './push-client'
import {loadNotifyPrefs} from './notification-prefs'
import {isStandaloneApp, readWakeSetting, wakeSupported, writeWake} from './useKaisWake'
import './install-prompt.css'

// Ana ekrandan açılan uygulamada ilk açılışta: tek dokunuşla telefona işlem bildirimleri + "Hey Kais".
// İzinler telefonun kendi penceresiyle istenir (web uygulaması izinleri kendiliğinden açamaz). "Sonra" 3 gün sorulmaz.
const KEY = 'kt-app-ready'
const WAIT_DAYS = 3
function snoozed() {
  try {
    const [value, at] = (localStorage.getItem(KEY) || '').split('|')
    return value === 'done' || (value === 'later' && Date.now() - Number(at) < WAIT_DAYS * 86400000)
  } catch { return false }
}
function remember(value: 'done' | 'later') { try { localStorage.setItem(KEY, `${value}|${Date.now()}`) } catch { /* yok */ } }

async function micGranted(): Promise<boolean> {
  try { return (await navigator.permissions.query({name: 'microphone' as PermissionName})).state === 'granted' } catch { return false }
}

export default function AppReadyPrompt() {
  const [need, setNeed] = useState<{push: boolean; mic: boolean} | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => {
    if (!isStandaloneApp() || snoozed()) return
    let alive = true
    const timer = window.setTimeout(() => {
      void Promise.all([pushState(), micGranted()]).then(([push, mic]) => {
        if (!alive) return
        const wantPush = push === 'off'
        const wantMic = wakeSupported() && readWakeSetting() !== 'off' && !mic
        if (wantPush || wantMic) setNeed({push: wantPush, mic: wantMic})
      })
    }, 3000)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [])
  if (!need) return null

  const later = () => { remember('later'); setNeed(null) }
  const enableAll = async () => {
    setBusy(true); setNote('')
    const problems: string[] = []
    if (need.mic && navigator.mediaDevices?.getUserMedia) {
      try { (await navigator.mediaDevices.getUserMedia({audio: true})).getTracks().forEach(track => track.stop()); writeWake(true) }
      catch { problems.push('Mikrofon izni verilmedi; "Hey Kais" kapalı kaldı.') }
    }
    if (need.push) {
      const error = await enablePush(loadNotifyPrefs())
      if (error) problems.push(error)
    }
    setBusy(false)
    remember('done')
    if (problems.length) setNote(problems.join(' '))
    else setNeed(null)
  }

  return <div className="ktInstall" role="dialog" aria-labelledby="kt-ready-title" aria-describedby="kt-ready-text">
    <button type="button" className="ktInstallClose" aria-label="Kapat" onClick={later}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
    </button>
    <div className="ktInstallTop">
      <img src="/icon-192.png" alt="" width={52} height={52}/>
      <div>
        <b id="kt-ready-title">Uygulama hazır</b>
        <p id="kt-ready-text">{need.push && need.mic ? 'İşlem açılınca, kapanınca ve stop değişince telefonuna bildirim gelsin; "Hey Kais" deyince Kais açılsın.'
          : need.push ? 'İşlem açılınca, kapanınca ve stop değişince telefonuna bildirim gelsin.'
          : '"Hey Kais" deyince Kais açılsın (uygulama açıkken).'}</p>
      </div>
    </div>
    {note ? <p className="ktReadyNote" role="alert">{note}</p> : <div className="ktInstallActions">
      <button type="button" className="ktInstallLater" disabled={busy} onClick={later}>Sonra</button>
      <button type="button" className="ktInstallGo" data-testid="app-ready-enable" disabled={busy} onClick={() => void enableAll()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>{busy ? 'Açılıyor…' : 'Hepsini aç'}</button>
    </div>}
  </div>
}

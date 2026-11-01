import { useEffect, useRef, useState } from 'react'
import { Wrench } from 'lucide-react'
import { API_BASE } from './api'
import { currentLang } from './i18n'
import { countdownPhase, formatCountdown, nextPollDelay, parsePublicMaintenance, remainingMs } from './maintenance-countdown'
import './maintenance-countdown.css'

type Info = {mode: string; at: number | null; offset: number}

/**
 * Planlı bakım geri sayımı. Giriş ekranında ve giriş yapmış kullanıcıların sitesinde görünür.
 * Süre dolunca sunucudan bakımın başladığını doğrular ve `onActivated` ile haber verir;
 * böylece üye ekranı yenilemeden bakım ekranına geçer.
 */
export default function MaintenanceCountdown({variant, onActivated}: {variant: 'login' | 'app'; onActivated?: (mode: string) => void}) {
  const [info, setInfo] = useState<Info | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const infoRef = useRef<Info | null>(null)
  const callback = useRef(onActivated)
  useEffect(() => { callback.current = onActivated }, [onActivated])

  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const controller = new AbortController()
    const poll = async () => {
      window.clearTimeout(timer)
      let failed = false
      try {
        const response = await fetch(`${API_BASE}/v22/maintenance/public`, {cache: 'no-store', signal: controller.signal})
        if (!response.ok) throw new Error('status')
        const parsed = parsePublicMaintenance(await response.json())
        if (!parsed) throw new Error('shape')
        if (stopped) return
        const next = {mode: parsed.mode, at: parsed.scheduledAt, offset: parsed.serverNow === null ? 0 : parsed.serverNow - Date.now()}
        infoRef.current = next
        setInfo(next)
        if (next.mode !== 'NORMAL') callback.current?.(next.mode)
      } catch {
        failed = true
      }
      if (stopped) return
      const current = infoRef.current
      const remaining = current?.at != null && current.mode === 'NORMAL' ? remainingMs(current.at, current.offset, Date.now()) : null
      timer = window.setTimeout(() => void poll(), nextPollDelay(remaining, failed))
    }
    const onVisible = () => { if (document.visibilityState === 'visible') void poll() }
    void poll()
    document.addEventListener('visibilitychange', onVisible)
    return () => { stopped = true; controller.abort(); window.clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  const counting = Boolean(info && info.mode === 'NORMAL' && info.at != null)
  useEffect(() => {
    if (!counting) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [counting])

  const running = Boolean(info && info.mode !== 'NORMAL')
  const visible = counting || (variant === 'login' && running)
  useEffect(() => {
    if (variant !== 'login' || !visible) return
    document.documentElement.classList.add('ktMaintLoginOffset')
    return () => document.documentElement.classList.remove('ktMaintLoginOffset')
  }, [variant, visible])

  if (!info || !visible) return null
  if (running) {
    return <div className="ktMaint" data-variant={variant} data-phase="due" role="status"><Wrench aria-hidden="true"/><span className="ktMaintText">Sistem şu anda bakımda.</span></div>
  }
  const left = remainingMs(info.at as number, info.offset, now)
  const phase = countdownPhase(left)
  return <div className="ktMaint" data-variant={variant} data-phase={phase} role="status">
    <Wrench aria-hidden="true"/>
    <span className="ktMaintText">{phase === 'due' ? 'Bakım başlıyor…' : 'Planlı bakıma kalan süre'}</span>
    {phase !== 'due' && <b className="ktMaintTime" aria-hidden="true">{formatCountdown(left, currentLang() === 'en' ? 'en' : 'tr')}</b>}
    <span className="ktMaintNote">Açık pozisyonlar ve risk korumaları çalışmaya devam eder.</span>
  </div>
}

import { useEffect, useRef, useState } from 'react'
import { Wrench } from 'lucide-react'
import { API_BASE } from './api'
import { currentLang } from './i18n'
import { countdownParts, countdownPhase, nextPollDelay, parsePublicMaintenance, remainingMs } from './maintenance-countdown'
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
  const card = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // Giriş ekranında kart sabit durur; telefonda sayfa içeriğini kartın altına iter (ölçüyle, tahminsiz).
    if (variant !== 'login' || !visible) return
    const root = document.documentElement
    root.classList.add('ktMaintLoginOffset')
    const element = card.current
    const measure = () => root.style.setProperty('--ktMaintH', `${Math.ceil((element?.getBoundingClientRect().height ?? 0) + 8)}px`)
    measure()
    const observer = typeof ResizeObserver === 'function' && element ? new ResizeObserver(measure) : null
    if (observer && element) observer.observe(element)
    return () => { observer?.disconnect(); root.classList.remove('ktMaintLoginOffset'); root.style.removeProperty('--ktMaintH') }
  }, [variant, visible, running])

  if (!info || !visible) return null
  if (running) {
    return <div ref={card} className="ktMaint" data-variant={variant} data-phase="due" role="status">
      <span className="ktMaintBadge" aria-hidden="true"><Wrench/></span>
      <div className="ktMaintBody"><b className="ktMaintTitle">Sistem şu anda bakımda.</b><span className="ktMaintNote">Açık pozisyonlar ve risk korumaları çalışmaya devam eder.</span></div>
    </div>
  }
  const left = remainingMs(info.at as number, info.offset, now)
  const phase = countdownPhase(left)
  const parts = countdownParts(left)
  const labels = currentLang() === 'en' ? ['Days', 'Hours', 'Min', 'Sec'] : ['Gün', 'Saat', 'Dk', 'Sn']
  const units: Array<[string, number]> = [...(parts.days > 0 ? [[labels[0], parts.days] as [string, number]] : []), [labels[1], parts.hours], [labels[2], parts.minutes], [labels[3], parts.seconds]]
  const pad = (value: number) => String(value).padStart(2, '0')
  return <div ref={card} className="ktMaint" data-variant={variant} data-phase={phase} role="status">
    <span className="ktMaintBadge" aria-hidden="true"><Wrench/></span>
    <div className="ktMaintBody">
      <b className="ktMaintTitle">{phase === 'due' ? 'Bakım başlıyor…' : 'Planlı bakım'}</b>
      <span className="ktMaintNote">{phase === 'due' ? 'Birkaç saniye içinde bakım ekranına geçilir.' : 'Açık pozisyonlar ve risk korumaları çalışmaya devam eder.'}</span>
    </div>
    {phase !== 'due' && <div className="ktMaintClock" aria-hidden="true">{units.map(([label, value]) => <div className="ktMaintUnit" key={label}><b>{pad(value)}</b><small>{label}</small></div>)}</div>}
  </div>
}

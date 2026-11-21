// Basılı tutarak onay: yanlışlıkla dokunmayı önler. Süre dolunca onConfirm bir kez çağrılır; bırakınca sıfırlanır.
import {useEffect, useRef, useState} from 'react'

type Props = {label: string; busyLabel: string; busy: boolean; disabled?: boolean; ms?: number; onConfirm: () => void}

export default function HoldButton({label, busyLabel, busy, disabled, ms = 900, onConfirm}: Props) {
  const [progress, setProgress] = useState(0)
  const start = useRef(0)
  const frame = useRef(0)
  const fired = useRef(false)
  const confirmRef = useRef(onConfirm)
  confirmRef.current = onConfirm
  const stop = () => { window.cancelAnimationFrame(frame.current); frame.current = 0; if (!fired.current) setProgress(0) }
  const step = () => {
    const value = Math.min(1, (Date.now() - start.current) / ms)
    setProgress(value)
    if (value >= 1) { fired.current = true; frame.current = 0; confirmRef.current(); return }
    frame.current = window.requestAnimationFrame(step)
  }
  const begin = () => {
    if (busy || disabled || frame.current) return
    fired.current = false
    start.current = Date.now()
    frame.current = window.requestAnimationFrame(step)
  }
  useEffect(() => { if (!busy) { fired.current = false; setProgress(0) } }, [busy])
  useEffect(() => () => window.cancelAnimationFrame(frame.current), [])
  return <button type="button" className="lcHold" data-busy={busy || undefined} disabled={Boolean(disabled) && !busy} aria-busy={busy}
    onPointerDown={begin} onPointerUp={stop} onPointerLeave={stop} onPointerCancel={stop}
    onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) { event.preventDefault(); begin() } }}
    onKeyUp={event => { if (event.key === 'Enter' || event.key === ' ') stop() }}
    onContextMenu={event => event.preventDefault()}>
    <i style={{width: `${busy ? 100 : progress * 100}%`}} aria-hidden="true"/>
    <span>{busy ? busyLabel : label}</span>
  </button>
}

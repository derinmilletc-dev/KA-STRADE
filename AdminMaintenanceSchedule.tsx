import { useEffect, useRef, useState } from 'react'
import { Timer } from 'lucide-react'
import { formatCountdown, remainingMs } from './maintenance-countdown'

type Schedule = {scheduled_at: number | null; now?: number; mode: string}
const PRESETS = [{label: '15 min', minutes: 15}, {label: '30 min', minutes: 30}, {label: '1 hour', minutes: 60}, {label: '2 hours', minutes: 120}, {label: '6 hours', minutes: 360}]

/** Admin: bakımı ileri bir ana planlar. Süre dolunca sunucu bakım modunu kendiliğinden açar. */
export default function AdminMaintenanceSchedule({maintenance, busy, onSchedule, onCancel, onDue}: {maintenance: Schedule | null; busy: boolean; onSchedule: (minutes: number) => Promise<void>; onCancel: () => Promise<void>; onDue: () => void}) {
  const [preset, setPreset] = useState<number | 'custom'>(30)
  const [custom, setCustom] = useState('')
  const [unit, setUnit] = useState<'min' | 'hour'>('min')
  const [now, setNow] = useState(() => Date.now())
  const [offset, setOffset] = useState(0)
  const at = maintenance && maintenance.mode === 'NORMAL' && maintenance.scheduled_at ? maintenance.scheduled_at * 1000 : null
  useEffect(() => { if (maintenance?.now) setOffset(maintenance.now * 1000 - Date.now()) }, [maintenance])
  useEffect(() => {
    if (!at) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [at])
  const left = at ? remainingMs(at, offset, now) : null
  const dueTick = left !== null && left <= 0 ? Math.floor(now / 3000) : -1
  const dueRef = useRef(onDue)
  useEffect(() => { dueRef.current = onDue }, [onDue])
  useEffect(() => {
    if (dueTick < 0) return
    const timer = window.setTimeout(() => dueRef.current(), 500)
    return () => window.clearTimeout(timer)
  }, [dueTick])

  const customMinutes = Math.round(Number(custom.replace(',', '.')) * (unit === 'hour' ? 60 : 1))
  const customValid = Number.isFinite(customMinutes) && customMinutes >= 1 && customMinutes <= 30 * 24 * 60
  const minutes = preset === 'custom' ? customMinutes : preset
  const canStart = !busy && maintenance?.mode === 'NORMAL' && !at && (preset !== 'custom' || customValid)

  return <div className="adminMaintSchedule">
    <h4><Timer aria-hidden="true"/>Scheduled maintenance</h4>
    {at && left !== null ? <>
      <p className="adminMaintLive">Maintenance mode starts automatically in <b>{left > 0 ? formatCountdown(left, 'en') : 'a moment'}</b>. Members see a countdown on the login screen and across the site.</p>
      <button type="button" className="adminMaintCancel" disabled={busy} onClick={() => void onCancel()}>Cancel countdown</button>
    </> : <>
      <p className="adminModeHint">Start a countdown. When it ends, Maintenance mode turns on automatically. Open positions and protection keep running.</p>
      <div className="adminMaintPresets" role="group" aria-label="Countdown length">
        {PRESETS.map(item => <button type="button" key={item.minutes} aria-pressed={preset === item.minutes} onClick={() => setPreset(item.minutes)} disabled={busy || maintenance?.mode !== 'NORMAL'}>{item.label}</button>)}
        <button type="button" aria-pressed={preset === 'custom'} onClick={() => setPreset('custom')} disabled={busy || maintenance?.mode !== 'NORMAL'}>Custom</button>
      </div>
      {preset === 'custom' && <div className="adminMaintCustom">
        <input type="number" inputMode="decimal" min="1" step="any" placeholder="e.g. 45" value={custom} onChange={event => setCustom(event.target.value)} aria-label="Custom length" aria-invalid={custom !== '' && !customValid}/>
        <select value={unit} onChange={event => setUnit(event.target.value as 'min' | 'hour')} aria-label="Unit"><option value="min">minutes</option><option value="hour">hours</option></select>
        {custom !== '' && !customValid && <small role="alert">Enter 1 minute to 30 days.</small>}
      </div>}
      <button type="button" className="adminMaintStart" disabled={!canStart} onClick={() => void onSchedule(minutes)}>{busy ? 'Starting…' : 'Start countdown'}</button>
      {maintenance && maintenance.mode !== 'NORMAL' && <small className="adminMaintHint">Return to Normal first to schedule a new maintenance.</small>}
    </>}
  </div>
}

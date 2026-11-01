import { useEffect, useRef, useState } from 'react'
import { CalendarClock, Timer } from 'lucide-react'
import { countdownParts, remainingMs, startClock } from './maintenance-countdown'

type Schedule = {scheduled_at: number | null; now?: number; mode: string}
const PRESETS = [{value: 15, unit: 'min', minutes: 15}, {value: 30, unit: 'min', minutes: 30}, {value: 1, unit: 'hour', minutes: 60}, {value: 2, unit: 'hours', minutes: 120}, {value: 6, unit: 'hours', minutes: 360}]
const pad = (value: number) => String(value).padStart(2, '0')

function Clock({ms}: {ms: number}) {
  const parts = countdownParts(ms)
  const units: Array<[string, number]> = [...(parts.days > 0 ? [['Days', parts.days] as [string, number]] : []), ['Hours', parts.hours], ['Min', parts.minutes], ['Sec', parts.seconds]]
  return <div className="adminMaintClock" aria-hidden="true">{units.map(([label, value]) => <div key={label}><b>{pad(value)}</b><small>{label}</small></div>)}</div>
}

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
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
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
  const locked = busy || maintenance?.mode !== 'NORMAL'
  const canStart = !locked && !at && (preset !== 'custom' || customValid)
  const previewStart = preset !== 'custom' || customValid ? startClock(now + offset + minutes * 60_000) : null

  return <div className="adminMaint">
    <div className="adminMaintHead">
      <h4><Timer aria-hidden="true"/>Scheduled maintenance</h4>
      <span className={`adminMaintChip${at ? ' live' : ''}`}><i/>{at ? 'Countdown running' : 'Not scheduled'}</span>
    </div>
    {at && left !== null ? <div className="adminMaintRun">
      <Clock ms={left}/>
      <div className="adminMaintRunInfo">
        <p><CalendarClock aria-hidden="true"/><span>{left > 0 ? <>Maintenance starts automatically at <b>{startClock(at)}</b>.</> : <>Starting maintenance…</>}</span></p>
        <small>Members see this countdown on the login screen and across the site. Open positions and protection keep running.</small>
      </div>
      <button type="button" className="adminMaintCancel" disabled={busy} onClick={() => void onCancel()}>Cancel countdown</button>
    </div> : <>
      <p className="adminMaintLead">Warn members in advance. When the countdown ends, Maintenance mode turns on automatically. Open positions and protection keep running.</p>
      <div className="adminMaintTiles" role="group" aria-label="Countdown length">
        {PRESETS.map(item => <button type="button" key={item.minutes} aria-pressed={preset === item.minutes} onClick={() => setPreset(item.minutes)} disabled={locked}><b>{item.value}</b><small>{item.unit}</small></button>)}
        <button type="button" aria-pressed={preset === 'custom'} onClick={() => setPreset('custom')} disabled={locked}><b>···</b><small>Custom</small></button>
      </div>
      {preset === 'custom' && <div className="adminMaintCustom">
        <input type="number" inputMode="decimal" min="1" step="any" placeholder="e.g. 45" value={custom} onChange={event => setCustom(event.target.value)} aria-label="Custom length" aria-invalid={custom !== '' && !customValid} disabled={locked}/>
        <div className="adminMaintUnit" role="group" aria-label="Unit">
          <button type="button" aria-pressed={unit === 'min'} onClick={() => setUnit('min')} disabled={locked}>minutes</button>
          <button type="button" aria-pressed={unit === 'hour'} onClick={() => setUnit('hour')} disabled={locked}>hours</button>
        </div>
        {custom !== '' && !customValid && <small role="alert">Enter 1 minute to 30 days.</small>}
      </div>}
      <div className="adminMaintFoot">
        {previewStart && !locked ? <p className="adminMaintWhen"><CalendarClock aria-hidden="true"/><span>Maintenance would start at <b>{previewStart}</b></span></p> : <span/>}
        <button type="button" className="adminMaintStart" disabled={!canStart} onClick={() => void onSchedule(minutes)}>{busy ? 'Starting…' : 'Start countdown'}</button>
      </div>
      {maintenance && maintenance.mode !== 'NORMAL' && <small className="adminMaintHint">Return to Normal first to schedule a new maintenance.</small>}
    </>}
  </div>
}

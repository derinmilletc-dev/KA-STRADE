import { useEffect, useRef, useState } from 'react'
import { CalendarClock, Timer } from 'lucide-react'
import { countdownParts, remainingMs, startClock } from './maintenance-countdown'

type Schedule = {scheduled_at: number | null; now?: number; mode: string}
const PRESETS = [{value: 15, unit: 'dk', minutes: 15}, {value: 30, unit: 'dk', minutes: 30}, {value: 1, unit: 'saat', minutes: 60}, {value: 2, unit: 'saat', minutes: 120}, {value: 6, unit: 'saat', minutes: 360}]
const pad = (value: number) => String(value).padStart(2, '0')

function Clock({ms}: {ms: number}) {
  const parts = countdownParts(ms)
  const units: Array<[string, number]> = [...(parts.days > 0 ? [['Gün', parts.days] as [string, number]] : []), ['Saat', parts.hours], ['Dk', parts.minutes], ['Sn', parts.seconds]]
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
  const previewStart = preset !== 'custom' || customValid ? startClock(now + offset + minutes * 60_000, 'tr-TR') : null

  return <div className="adminMaint">
    <div className="adminMaintHead">
      <h4><Timer aria-hidden="true"/>Planlı bakım</h4>
      <span className={`adminMaintChip${at ? ' live' : ''}`}><i/>{at ? 'Geri sayım sürüyor' : 'Planlanmadı'}</span>
    </div>
    {at && left !== null ? <div className="adminMaintRun">
      <Clock ms={left}/>
      <div className="adminMaintRunInfo">
        <p><CalendarClock aria-hidden="true"/><span>{left > 0 ? <>Bakım <b>{startClock(at, 'tr-TR')}</b> saatinde otomatik başlar.</> : <>Bakım başlatılıyor…</>}</span></p>
        <small>Üyeler bu geri sayımı giriş ekranında ve sitede görür. Açık pozisyonlar ve korumalar çalışmaya devam eder.</small>
      </div>
      <button type="button" className="adminMaintCancel" disabled={busy} onClick={() => void onCancel()}>Geri sayımı iptal et</button>
    </div> : <>
      <p className="adminMaintLead">Üyeleri önceden uyar. Süre dolunca bakım modu kendiliğinden açılır; açık pozisyonlar ve korumalar çalışmaya devam eder.</p>
      <div className="adminMaintTiles" role="group" aria-label="Geri sayım süresi">
        {PRESETS.map(item => <button type="button" key={item.minutes} aria-pressed={preset === item.minutes} onClick={() => setPreset(item.minutes)} disabled={locked}><b>{item.value}</b><small>{item.unit}</small></button>)}
        <button type="button" aria-pressed={preset === 'custom'} onClick={() => setPreset('custom')} disabled={locked}><b>···</b><small>Özel</small></button>
      </div>
      {preset === 'custom' && <div className="adminMaintCustom">
        <input type="number" inputMode="decimal" min="1" step="any" placeholder="örn. 45" value={custom} onChange={event => setCustom(event.target.value)} aria-label="Özel süre" aria-invalid={custom !== '' && !customValid} disabled={locked}/>
        <div className="adminMaintUnit" role="group" aria-label="Birim">
          <button type="button" aria-pressed={unit === 'min'} onClick={() => setUnit('min')} disabled={locked}>dakika</button>
          <button type="button" aria-pressed={unit === 'hour'} onClick={() => setUnit('hour')} disabled={locked}>saat</button>
        </div>
        {custom !== '' && !customValid && <small role="alert">1 dakika ile 30 gün arası gir.</small>}
      </div>}
      <div className="adminMaintFoot">
        {previewStart && !locked ? <p className="adminMaintWhen"><CalendarClock aria-hidden="true"/><span>Bakım <b>{previewStart}</b> saatinde başlar</span></p> : <span/>}
        <button type="button" className="adminMaintStart" disabled={!canStart} onClick={() => void onSchedule(minutes)}>{busy ? 'Başlatılıyor…' : 'Geri sayımı başlat'}</button>
      </div>
      {maintenance && maintenance.mode !== 'NORMAL' && <small className="adminMaintHint">Yeni bakım planlamak için önce Normal moda dön.</small>}
    </>}
  </div>
}

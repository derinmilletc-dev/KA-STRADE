import './macro-notice.css'
import {useMacroEvents} from './coin-read-api'
import {topEvent} from './coin-read-view'

// İşlem sihirbazında makro veri (FOMC, TÜFE, istihdam) ve fonlama saati uyarısı. YALNIZ bilgi: hiçbir düğmeyi kilitlemez, emri engellemez.
export default function MacroNotice() {
  const events = useMacroEvents()
  const event = topEvent(events)
  if (!event || event.level === 'info') return null
  return <p className="mnBar" data-level={event.level} role="status">
    <span aria-hidden="true">{event.kind === 'funding' ? '⏱' : '🗓'}</span>
    <span>{event.text} <em>Bilgi amaçlı; işlem açmanı engellemez.</em></span>
  </p>
}

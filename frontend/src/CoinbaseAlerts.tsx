import {useEffect, useState} from 'react'
import './price-alert.css'
import {Bell, BellRing, TrendingDown, TrendingUp, X} from 'lucide-react'
import {coinbaseAlertCall, type AlertCall} from './price-alert-api'
import {enablePush, pushState, sendTestPush, type PushState} from '../../push-client'
import {loadNotifyPrefs} from '../../notification-prefs'
import {alertPresets, alertsFor, distancePct, pctText, previewLevel, type CbAlert} from './coinbase-alert'

type Props = {productId: string; display: string; price: number | null; alerts: CbAlert[]; max?: number; onChanged: () => void; call?: AlertCall}

const fmt = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2})

const PUSH_WHY: Partial<Record<PushState, string>> = {
  'ios-install': 'iPhone’da önce siteyi Ana Ekrana ekle, sonra oradan aç.', denied: 'Bildirim izni kapalı. Telefon ayarlarından bu site için izin ver.',
  unsupported: 'Bu tarayıcı telefon bildirimini desteklemiyor.', 'not-ready': 'Sunucuda telefon bildirimi henüz hazır değil.', off: 'Bu telefonda bildirim kapalı; alarm çalınca haber gelmez.',
}

/** Alarm kutusunda telefon bildirimi durumu: kapalıysa açma düğmesi, açıksa test gönderme. */
export function PushLine() {
  const [state, setState] = useState<PushState | 'busy' | 'unknown'>('unknown')
  const [note, setNote] = useState('')
  useEffect(() => { let alive = true; void pushState().then(next => { if (alive) setState(next) }).catch(() => { if (alive) setState('unsupported') }); return () => { alive = false } }, [])
  if (state === 'unknown') return null
  const turnOn = async () => { setState('busy'); const problem = await enablePush(loadNotifyPrefs()); setNote(problem ?? 'Bildirim açıldı.'); setState(problem ? await pushState() : 'on') }
  const test = async () => { setNote('Gönderiliyor…'); setNote((await sendTestPush()) ?? 'Test bildirimi gönderildi.') }
  if (state === 'on') return <p className="mxaPush" data-on="1">🔔 Telefon bildirimi açık <button type="button" onClick={() => void test()}>Test gönder</button>{note && <small>{note}</small>}</p>
  return <p className="mxaPush">⚠ {PUSH_WHY[state as PushState] ?? 'Telefon bildirimi kapalı.'}{(state === 'off' || state === 'busy') && <button type="button" disabled={state === 'busy'} onClick={() => void turnOn()}>{state === 'busy' ? 'Açılıyor…' : 'Bildirimi aç'}</button>}{note && <small>{note}</small>}</p>
}

// Analiz çekmecesindeki "Fiyat alarmı" bölümü: tek dokunuşla hazır seviye ya da kendi fiyatın. Emir göndermez, yalnız bildirir.
export default function CoinbaseAlerts({productId, display, price, alerts, max = 20, onChanged, call = coinbaseAlertCall}: Props) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{tone: 'ok' | 'bad'; text: string} | null>(null)
  const mine = alertsFor(alerts, productId)
  const presets = alertPresets(price)
  const preview = previewLevel(text, price)
  const full = alerts.length >= max

  const add = async (level: number) => {
    if (busy) return
    setBusy(true); setNote(null)
    try {
      await call('/alerts', {method: 'POST', body: JSON.stringify({product_id: productId, level: String(level)})})
      setText(''); setNote({tone: 'ok', text: `${fmt(level)} için alarm kuruldu. Fiyat oraya gelince bildirim gelir.`}); onChanged()
    } catch (error) {
      setNote({tone: 'bad', text: error instanceof Error && error.message ? error.message : 'Alarm kurulamadı.'})
    } finally { setBusy(false) }
  }
  const remove = async (id: number) => {
    if (busy) return
    setBusy(true); setNote(null)
    try { await call(`/alerts/${id}`, {method: 'DELETE'}); onChanged() } catch (error) {
      setNote({tone: 'bad', text: error instanceof Error && error.message ? error.message : 'Alarm silinemedi.'})
    } finally { setBusy(false) }
  }

  const chips = (list: ReturnType<typeof alertPresets>['up'], dir: 'up' | 'down') => <div className="mxaChips" data-dir={dir}>
    {list.map(item => <button type="button" key={item.pct} disabled={busy || full} onClick={() => void add(item.level)}>
      <b>{dir === 'up' ? '+' : '−'}%{item.pct}</b><small>{fmt(item.level)}</small></button>)}
  </div>

  return <section className="mxaBox" aria-label={`${display} fiyat alarmı`}>
    <header className="mxaHead">{mine.length > 0 ? <BellRing aria-hidden="true"/> : <Bell aria-hidden="true"/>}
      <span><b>Fiyat alarmı</b><small>{mine.length > 0 ? `${mine.length} aktif` : 'Seviyeye gelince telefona haber ver'}</small></span></header>

    {price !== null && <>
      <div className="mxaLabel"><TrendingUp aria-hidden="true"/>Yukarı çıkınca</div>{chips(presets.up, 'up')}
      <div className="mxaLabel"><TrendingDown aria-hidden="true"/>Aşağı inince</div>{chips(presets.down, 'down')}
    </>}

    <PushLine/>
    <div className="mxaCustom">
      <input value={text} onChange={event => setText(event.target.value)} inputMode="decimal" autoComplete="off" placeholder="Kendi fiyatın" aria-label="Alarm fiyatı"
        onKeyDown={event => { if (event.key === 'Enter' && preview.ok && !busy) { const n = Number(text.trim().replace(',', '.')); if (n > 0) void add(n) } }}/>
      <button type="button" disabled={busy || full || !preview.ok} onClick={() => { const n = Number(text.trim().replace(',', '.')); if (n > 0) void add(n) }}>Kur</button>
    </div>
    {text.trim() !== '' && preview.hint && <p className="mxaHint" data-bad={!preview.ok || undefined}>{preview.pct !== null && preview.ok ? `${pctText(preview.pct)} · ` : ''}{preview.hint}</p>}
    {full && <p className="mxaHint" data-bad="1">En fazla {max} alarm kurulabilir. Birini sil.</p>}
    {note && <p className="mxaHint" data-bad={note.tone === 'bad' || undefined} role="status">{note.text}</p>}

    {mine.length > 0 && <ul className="mxaList">{mine.map(item => {
      const pct = distancePct(item.level, price)
      return <li key={item.id} data-dir={item.direction}>
        {item.direction === 'above' ? <TrendingUp aria-hidden="true"/> : <TrendingDown aria-hidden="true"/>}
        <span><b>{fmt(Number(item.level))}</b><small>{item.direction === 'above' ? 'yukarı çıkınca' : 'aşağı inince'}{pct !== null ? ` · şimdi ${pctText(pct)} uzakta` : ''}</small></span>
        <button type="button" aria-label="Alarmı sil" disabled={busy} onClick={() => void remove(item.id)}><X aria-hidden="true"/></button>
      </li>
    })}</ul>}
  </section>
}

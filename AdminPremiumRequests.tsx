import {useCallback, useEffect, useState} from 'react'
import {API_BASE} from './api'
import {withRequestDeadline} from './browser-request'
import {ModCard} from './moderator-ui'
import {parseItem, parseList, type PremiumItem} from './premium-requests-model'
import './moderator-panel.css'

function ownerRequest<T>(path: string, parse: (v: unknown) => T, signal: AbortSignal, options: RequestInit = {}): Promise<T> {
  return withRequestDeadline(async transportSignal => {
    const headers = new Headers(options.headers)
    headers.set('X-Requested-With', 'XMLHttpRequest')
    if (options.body) headers.set('Content-Type', 'application/json')
    const response = await fetch(`${API_BASE}/v22/admin/premium-requests${path}`, {...options, headers, credentials: 'same-origin', signal: transportSignal})
    if (!response.ok) throw new Error(response.status === 409 ? 'Bu talep artık beklemede değil.' : 'İşlem yapılamadı.')
    return parse(await response.json())
  }, {signal, timeoutMs: 20000})
}

function failureText(failure: unknown) {
  return failure instanceof Error && failure.message.includes('Bu talep') ? failure.message : 'İşlem yapılamadı. Biraz sonra tekrar deneyin.'
}

/** Admin: pending premium requests from moderators. */
export function AdminPremiumRequests() {
  const [items, setItems] = useState<PremiumItem[] | null>(null)
  const [error, setError] = useState('')
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState('')
  const [tick, setTick] = useState(0)
  const load = useCallback((signal: AbortSignal) => ownerRequest('?status=pending', parseList, signal)
    .then(value => { if (!signal.aborted) setItems(value) })
    .catch(failure => { if (!signal.aborted) setError(failureText(failure)) }), [])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load, tick])
  const decide = async (item: PremiumItem, approve: boolean) => {
    const ask = approve ? `${item.target_email ?? item.target_user_id} kullanıcısına ${item.days} gün premium verilsin mi?` : 'Talep reddedilsin mi?'
    if (!window.confirm(ask)) return
    setBusyId(item.id); setError('')
    try {
      await ownerRequest(`/${encodeURIComponent(item.id)}/${approve ? 'approve' : 'reject'}`, parseItem, new AbortController().signal,
        {method: 'POST', body: JSON.stringify({note: notes[item.id] ?? ''})})
      setTick(v => v + 1)
    } catch (failure) { setError(failureText(failure)); setTick(v => v + 1) }
    finally { setBusyId('') }
  }
  return <ModCard title="Moderatörlerin premium talepleri">
    {error && <p role="alert">{error}</p>}
    {items === null ? <p>Yükleniyor…</p> : items.length === 0 ? <p>Bekleyen premium talebi yok.</p> :
      items.map(item => <div key={item.id} className="mod-row">
        <p><strong>{item.target_email ?? item.target_name ?? item.target_user_id}</strong> için <strong>{item.days} gün</strong> premium istendi.</p>
        <p>İsteyen moderatör: {item.requester_email ?? 'bilinmiyor'}</p>
        <p>Neden: {item.reason}</p>
        <label>Not (isteğe bağlı) <input value={notes[item.id] ?? ''} maxLength={500} onChange={event => setNotes(v => ({...v, [item.id]: event.target.value}))}/></label>
        <div className="mod-actions">
          <button disabled={busyId === item.id} onClick={() => void decide(item, true)}>Onayla ve premium ver</button>
          <button disabled={busyId === item.id} onClick={() => void decide(item, false)}>Reddet</button>
        </div>
      </div>)}
  </ModCard>
}

import {useEffect, useRef, useState} from 'react'
import {moderatorApiRequest} from './moderator-api'
import {ModCard} from './moderator-ui'
import {parseItem, parseList, statusText, type PremiumItem} from './premium-requests-model'
import './moderator-panel.css'

/** Moderator: ask for premium for one customer. Nothing changes until the admin approves. */
export function PremiumRequestForm({userId, onCreated}: {userId: string; onCreated?: () => void}) {
  const [open, setOpen] = useState(false)
  const [days, setDays] = useState(30)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const submit = async () => {
    if (inFlight.current || reason.trim().length < 10) return
    inFlight.current = true; setBusy(true); setError(''); setMessage('')
    try {
      await moderatorApiRequest('/premium-requests', parseItem, new AbortController().signal, {
        method: 'POST', body: JSON.stringify({target_user_id: userId, days, reason: reason.trim()}),
      })
      setMessage('Talep admine gönderildi. Admin onaylayana kadar müşterinin hesabı değişmez.')
      setOpen(false); setReason(''); onCreated?.()
    } catch { setError('Talep gönderilemedi. Bu müşteri için bekleyen bir talep olabilir.') }
    finally { inFlight.current = false; setBusy(false) }
  }
  return <ModCard title="Premium verme talebi">
    <p>Premium'u siz veremezsiniz; talep admine gider, admin onaylarsa verilir.</p>
    <div className="mod-actions"><button disabled={busy} onClick={() => setOpen(v => !v)}>Premium için talep oluştur</button></div>
    {open && <form onSubmit={event => { event.preventDefault(); void submit() }}>
      <label>Kaç gün? <input type="number" min={1} max={90} value={days} onChange={event => setDays(Math.min(90, Math.max(1, Number(event.target.value) || 1)))}/></label>
      <label>Neden? (en az 10 harf)<textarea value={reason} maxLength={500} onChange={event => setReason(event.target.value)}/></label>
      <button type="submit" disabled={busy || reason.trim().length < 10}>Admine gönder</button>
    </form>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </ModCard>
}

/** Moderator: own premium requests. */
export function ModeratorPremiumRequests({version = 0}: {version?: number}) {
  const [items, setItems] = useState<PremiumItem[] | null>(null)
  const [error, setError] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    moderatorApiRequest('/premium-requests', parseList, controller.signal, {method: 'GET'})
      .then(value => { if (!controller.signal.aborted) setItems(value) })
      .catch(() => { if (!controller.signal.aborted) setError('Premium talepleri okunamadı.') })
    return () => controller.abort()
  }, [version, tick])
  const cancel = async (id: string) => {
    try { await moderatorApiRequest(`/premium-requests/${encodeURIComponent(id)}/cancel`, parseItem, new AbortController().signal, {method: 'POST'}); setTick(v => v + 1) }
    catch { setError('Talep geri çekilemedi.') }
  }
  return <ModCard title="Premium taleplerim">
    {error && <p role="alert">{error}</p>}
    {items === null ? <p>Yükleniyor…</p> : items.length === 0 ? <p>Henüz premium talebiniz yok.</p> :
      items.map(item => <div key={item.id} className="mod-row">
        <strong>{item.target_email ?? item.target_name ?? item.target_user_id}</strong> — {item.days} gün — {statusText[item.status] ?? item.status}
        {item.decision_note && <div>Admin notu: {item.decision_note}</div>}
        {item.status === 'pending' && <div className="mod-actions"><button onClick={() => void cancel(item.id)}>Geri çek</button></div>}
      </div>)}
  </ModCard>
}

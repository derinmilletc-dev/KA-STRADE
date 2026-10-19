import {useEffect, useMemo, useState} from 'react'
import {CheckCircle2, ChevronLeft, ChevronRight, Clock3, Lock, RefreshCw, Search, Send, ShieldCheck, XCircle} from 'lucide-react'
import {moderatorApiRequest, moderatorRequest} from './moderator-api'
import {ModBadge, ModEmpty, ModError} from './moderator-ui'
import {
  moderatorDate, parsePaymentQueuePage, parsePaymentRecommendation, paymentQueueStatusLabels, subscriptionLabels,
  type PaymentQueueItem, type PaymentQueuePage, type PaymentQueueStatus,
} from './moderator-model'
import './moderator-payment-requests.css'

type Filter = PaymentQueueStatus | 'all'
const filters: {id: Filter; label: string}[] = [
  {id: 'pending', label: 'Bekleyen'}, {id: 'approved', label: 'Onaylanan'}, {id: 'rejected', label: 'Reddedilen'},
  {id: 'expired', label: 'Süresi dolan'}, {id: 'all', label: 'Tümü'},
]
const planLabel = (plan: PaymentQueueItem['customer']['plan']) => plan === 'MASTER_MODE' ? 'Master Mode' : plan === 'TRIAL' ? 'Deneme' : 'Üyelik yok'
const tone = (status: PaymentQueueStatus) => status === 'approved' ? 'positive' : status === 'pending' ? 'warning' : 'neutral'
const initial = (email: string) => (email.match(/[A-Za-z0-9]/)?.[0] ?? '?').toUpperCase()

function RecommendBox({row, onSent}: {row: PaymentQueueItem; onSent: () => void}) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  if (row.recommended_at) return <p className="mod-pay-sent" role="status"><Send aria-hidden="true"/>Yöneticiye gönderildi · {moderatorDate(row.recommended_at)}. Erişim, yönetici onaylayınca açılır.</p>
  const send = () => {
    if (busy) return
    setBusy(true); setError(null)
    void moderatorApiRequest(`/payment-requests/${row.id}/recommend`, parsePaymentRecommendation, new AbortController().signal,
      {method: 'POST', body: JSON.stringify({note: note.trim() || null})})
      .then(() => onSent()).catch(failure => setError(failure)).finally(() => setBusy(false))
  }
  if (!open) return <button className="mod-pay-send" onClick={() => setOpen(true)}><Send aria-hidden="true"/>Onaya gönder</button>
  return <div className="mod-pay-send-form"><label htmlFor={`mod-pay-note-${row.id}`}>Yöneticiye not (isteğe bağlı)</label>
    <textarea id={`mod-pay-note-${row.id}`} value={note} maxLength={500} disabled={busy} onChange={event => setNote(event.target.value)} placeholder="Örn. Ödemeyi Binance'te gördüm, tutar doğru."/>
    <p className="mod-secondary">Bu işlem üyelik açmaz. Yönetici onayladığında erişim açılır.</p>
    {error !== null && <ModError error={error} onRetry={send}/>}
    <div className="mod-actions"><button disabled={busy} onClick={send}>{busy ? 'Gönderiliyor...' : 'Yöneticiye gönder'}</button><button disabled={busy} onClick={() => {setOpen(false); setError(null)}}>Vazgeç</button></div></div>
}

function RequestCard({row}: {row: PaymentQueueItem}) {
  const customer = row.customer
  return <article className={`mod-pay-card mod-pay-${row.status}`} aria-label={`Ödeme talebi ${row.reference_code}`}>
    <header>
      <span className="mod-pay-avatar" aria-hidden="true">{initial(customer.email_masked)}</span>
      <div className="mod-pay-who"><strong>{customer.email_masked}</strong><small>{customer.user_id}</small></div>
      <div className="mod-pay-amount"><strong>{row.amount} {row.currency}</strong><ModBadge tone={tone(row.status)}>{paymentQueueStatusLabels[row.status]}</ModBadge></div>
    </header>
    <div className="mod-pay-columns">
      <section aria-label="Ödeme bilgileri"><h3>Ödeme</h3>
        <dl className="mod-facts">
          <div><dt>Referans kodu</dt><dd className="mod-pay-code">{row.reference_code}</dd></div>
          <div><dt>İşlem numarası</dt><dd>{row.transaction_hint ?? 'Henüz gönderilmedi'}</dd></div>
          <div><dt>Talep zamanı</dt><dd>{moderatorDate(row.created_at)}</dd></div>
          <div><dt>İncelemeye gönderildi</dt><dd>{moderatorDate(row.submitted_at)}</dd></div>
          {row.decided_at && <div><dt>Karar zamanı</dt><dd>{moderatorDate(row.decided_at)}</dd></div>}
        </dl>
        {row.decision_note && <p className="mod-pay-note"><b>Red gerekçesi:</b> {row.decision_note}</p>}
      </section>
      <section aria-label="Müşteri bilgileri"><h3>Müşteri</h3>
        <div className="mod-badges">
          <ModBadge tone={customer.active ? 'positive' : 'warning'}>{customer.active ? 'Hesap aktif' : 'Hesap devre dışı'}</ModBadge>
          <ModBadge tone={customer.email_verified ? 'positive' : 'warning'}>{customer.email_verified ? 'E-posta doğrulandı' : 'E-posta doğrulanmadı'}</ModBadge>
          <ModBadge tone={customer.mfa_enabled ? 'positive' : 'neutral'}>{customer.mfa_enabled ? '2FA açık' : '2FA kapalı'}</ModBadge>
        </div>
        <dl className="mod-facts">
          <div><dt>Kayıt tarihi</dt><dd>{moderatorDate(customer.registered_at)}</dd></div>
          <div><dt>Plan</dt><dd>{planLabel(customer.plan)}{customer.subscription_status && !(customer.plan === 'TRIAL' && customer.subscription_status === 'TRIALING') ? ` · ${subscriptionLabels[customer.subscription_status]}` : ''}</dd></div>
          <div><dt>Üyelik bitişi</dt><dd>{moderatorDate(customer.membership_end ?? customer.plan_period_end)}</dd></div>
          <div><dt>Önceki talepler</dt><dd>{customer.approved_count} onaylı · {customer.rejected_count} red · {customer.expired_count} süresi dolan</dd></div>
        </dl>
      </section>
    </div>
  </article>
}

export default function ModeratorPaymentRequests({onCustomer, canRecommend = false}: {onCustomer?: (userId: string) => void; canRecommend?: boolean}) {
  const [query, setQuery] = useState<{status: Filter; offset: number; version: number}>({status: 'pending', offset: 0, version: 0})
  const [page, setPage] = useState<PaymentQueuePage | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(true)
  const [search, setSearch] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setBusy(true); setError(null)
    const params = new URLSearchParams({limit: '25', offset: String(query.offset)})
    if (query.status !== 'all') params.set('status', query.status)
    void moderatorRequest(`/payment-requests?${params}`, parsePaymentQueuePage, controller.signal)
      .then(value => {if (!controller.signal.aborted) setPage(value)})
      .catch(failure => {if (!controller.signal.aborted) setError(failure)})
      .finally(() => {if (!controller.signal.aborted) setBusy(false)})
    return () => controller.abort()
  }, [query])
  const term = search.trim().toLowerCase()
  const rows = useMemo(() => (page?.items ?? []).filter(row => !term ||
    [row.reference_code, row.customer.user_id, row.customer.email_masked].some(text => text.toLowerCase().includes(term))), [page, term])
  const move = (patch: Partial<typeof query>) => setQuery(value => ({...value, ...patch, version: value.version + 1}))
  return <div className="mod-stack mod-pay">
    <div className="mod-pay-readonly" role="note"><Lock aria-hidden="true"/><p><b>Karar yöneticide.</b> Ödemeyi onaylama veya reddetme yetkisi yalnız yöneticidedir; moderatör olarak talebi <b>Onaya gönder</b> ile yöneticiye iletebilirsin. E-posta ve işlem numarası gizlenmiş gösterilir.</p></div>
    <div className="mod-pay-summary">
      <div><Clock3 aria-hidden="true"/><small>Bekleyen</small><strong>{page?.summary.pending ?? '—'}</strong></div>
      <div><CheckCircle2 aria-hidden="true"/><small>Onaylanan</small><strong>{page?.summary.approved ?? '—'}</strong></div>
      <div><XCircle aria-hidden="true"/><small>Reddedilen</small><strong>{page?.summary.rejected ?? '—'}</strong></div>
    </div>
    <div className="mod-pay-toolbar">
      <div role="tablist" aria-label="Ödeme talebi durumu" className="mod-pay-tabs">{filters.map(item =>
        <button key={item.id} role="tab" aria-selected={query.status === item.id} disabled={busy && query.status !== item.id}
          onClick={() => {setPage(null); setSearch(''); setQuery(value => ({status: item.id, offset: 0, version: value.version + 1}))}}>{item.label}</button>)}</div>
      <label className="mod-pay-search"><Search aria-hidden="true"/><span className="mod-sr">Bu sayfada ara</span>
        <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Referans kodu, e-posta veya kullanıcı no" autoComplete="off" spellCheck={false}/></label>
      <button className="mod-refresh" onClick={() => move({})} disabled={busy}><RefreshCw aria-hidden="true"/>Yenile</button>
    </div>
    {busy && <p className="mod-secondary" role="status">Ödeme talepleri yükleniyor...</p>}
    {error !== null && <ModError error={error} onRetry={() => move({})}/>}
    {page && !busy && !rows.length && <ModEmpty text={term ? 'Aramanızla eşleşen talep yok.' : 'Bu durumda ödeme talebi yok.'}/>}
    {rows.map(row => <div key={row.id} className="mod-pay-item"><RequestCard row={row}/>
      {canRecommend && row.status === 'pending' && <RecommendBox row={row} onSent={() => move({})}/>}
      {onCustomer && <button className="mod-pay-open" onClick={() => onCustomer(row.customer.user_id)}><ShieldCheck aria-hidden="true"/>Müşteri profilini aç</button>}</div>)}
    {page && <div className="mod-pagination"><span className="mod-secondary">{page.total} talep</span>
      <button disabled={busy || page.offset === 0} onClick={() => move({offset: Math.max(0, page.offset - page.limit)})}><ChevronLeft aria-hidden="true"/>Önceki</button>
      <button disabled={busy || page.offset + page.limit >= page.total} onClick={() => move({offset: page.offset + page.limit})}>Sonraki<ChevronRight aria-hidden="true"/></button></div>}
  </div>
}

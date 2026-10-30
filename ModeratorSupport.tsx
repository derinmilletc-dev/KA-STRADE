import {useEffect, useRef, useState} from 'react'
import {ChevronLeft, ChevronRight} from 'lucide-react'
import {moderatorApiRequest, moderatorBlob, moderatorRequest} from './moderator-api'
import {moderatorDate, ModeratorRequestError} from './moderator-model'
import {ModEmpty, ModError} from './moderator-ui'
import {SupportDetailCards, SupportLanguageBadge, SupportPriorityBadge, SupportStatusBadge, SupportSummaryCards} from './support-ui'
import {
  customerWaiting, parseSupportCase, parseSupportDetail, parseSupportPage, parseSupportSummary, PRIORITIES, priorityLabels,
  supportFilters, supportFilterQuery, type CaseStatus, type SupportDetail, type SupportFilter, type SupportPage, type SupportPriority, type SupportSummary,
} from './support-model'

export function SupportOverview() {
  const [summary, setSummary] = useState<SupportSummary | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setSummary(null); setError(null)
    void moderatorRequest('/support/summary', parseSupportSummary, controller.signal)
      .then(value => {if (!controller.signal.aborted) setSummary(value)})
      .catch(reason => {if (!controller.signal.aborted) setError(reason)})
    return () => controller.abort()
  }, [version])
  return <div className="mod-stack"><SupportSummaryCards summary={summary}/>
    {error !== null && <ModError error={error} onRetry={() => setVersion(value => value + 1)}/>}</div>
}

/** Listede göreli zaman: "5 dk önce", "3 sa önce", "2 gün önce"; daha eskisi tarih. */
function ago(value: string | null): string {
  if (!value) return ''
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 60000))
  if (!Number.isFinite(minutes)) return ''
  if (minutes < 1) return 'şimdi'
  if (minutes < 60) return `${minutes} dk önce`
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} sa önce`
  if (minutes < 7 * 24 * 60) return `${Math.round(minutes / 1440)} gün önce`
  return moderatorDate(value)
}

function SupportDetailPanel({id, manage, onBack, onCustomer, onChanged}: {id: string; manage: boolean; onBack: () => void; onCustomer?: (id: string) => void; onChanged: () => void}) {
  const [detail, setDetail] = useState<SupportDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [version, setVersion] = useState(0)
  const [note, setNote] = useState('')
  const [reply, setReply] = useState('')
  const [resolve, setResolve] = useState(false)
  const [status, setStatus] = useState<CaseStatus>('NEW')
  const operation = useRef<AbortController | null>(null)
  const inFlight = useRef(false)
  const path = `/support/cases/${encodeURIComponent(id)}`
  useEffect(() => {
    const controller = new AbortController()
    setDetail(null); setError(null); setBusy(true)
    void moderatorRequest(path, parseSupportDetail, controller.signal)
      .then(value => {if (!controller.signal.aborted) {setDetail(value); setStatus(value.case_status)}})
      .catch(reason => {if (!controller.signal.aborted) setError(reason)})
      .finally(() => {if (!controller.signal.aborted) setBusy(false)})
    return () => controller.abort()
  }, [path, version])
  useEffect(() => { setNote(''); setReply(''); setResolve(false) }, [id])
  useEffect(() => () => operation.current?.abort(), [])
  const mutate = async (action: 'take' | 'release' | 'status' | 'notes' | 'reply') => {
    if (!detail || busy || inFlight.current) return
    const controller = new AbortController()
    operation.current = controller; inFlight.current = true; setBusy(true); setError(null)
    const body = action === 'status' ? {status, expected_version: detail.version} : action === 'notes' ? {body: note} : action === 'reply' ? {body: reply.trim(), resolve} : undefined
    try {
      await moderatorApiRequest(`${path}/${action}`, parseSupportCase, controller.signal, {method: 'POST', ...(body ? {body: JSON.stringify(body)} : {})})
      if (!controller.signal.aborted) {setDetail(null); if (action === 'notes') setNote(''); if (action === 'reply') {setReply(''); setResolve(false)}; setVersion(value => value + 1); onChanged()}
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason)
        if (reason instanceof ModeratorRequestError && [401, 403, 404, 409].includes(reason.status)) setDetail(null)
      }
    } finally {
      inFlight.current = false
      if (!controller.signal.aborted) setBusy(false)
    }
  }
  return <div className="ms-detail-inner">
    {!detail && <button type="button" className="ms-back" onClick={onBack} disabled={busy}>‹ Taleplere dön</button>}
    {busy && !detail && <p role="status" className="mod-secondary">Talep yükleniyor...</p>}
    {error !== null && <ModError error={error} onRetry={() => setVersion(value => value + 1)}/>}
    {detail && <SupportDetailCards detail={detail} manage={manage} busy={busy} note={note} status={status} onNoteChange={setNote} onStatusChange={setStatus}
      onTake={() => void mutate('take')} onRelease={() => void mutate('release')} onSaveStatus={() => void mutate('status')} onAddNote={event => {event.preventDefault(); void mutate('notes')}}
      onCustomer={onCustomer ? () => onCustomer(detail.user_id) : undefined}
      onLoadAttachment={item => moderatorBlob(`${path}/attachments/${encodeURIComponent(item.id)}`, new AbortController().signal)}
      reply={reply} resolve={resolve} onReplyChange={setReply} onResolveChange={setResolve} onSendReply={() => void mutate('reply')} onBack={onBack}/>}
  </div>
}

export default function ModeratorSupport({manage, onCustomer}: {manage: boolean; onCustomer?: (id: string) => void}) {
  const [query, setQuery] = useState<{filter: SupportFilter; priority: SupportPriority | ''; offset: number; version: number}>({filter: 'all', priority: '', offset: 0, version: 0})
  const [page, setPage] = useState<SupportPage | null>(null)
  const [summary, setSummary] = useState<SupportSummary | null>(null)
  const [selected, setSelected] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    setError(null); setBusy(true)
    void moderatorRequest(`/support/cases?${supportFilterQuery(query.filter, query.priority, query.offset)}`, parseSupportPage, controller.signal)
      .then(value => {if (!controller.signal.aborted) setPage(value)})
      .catch(reason => {if (!controller.signal.aborted) setError(reason)})
      .finally(() => {if (!controller.signal.aborted) setBusy(false)})
    void moderatorRequest('/support/summary', parseSupportSummary, controller.signal)
      .then(value => {if (!controller.signal.aborted) setSummary(value)}).catch(() => {})
    return () => controller.abort()
  }, [query])
  const refresh = () => setQuery(current => ({...current, version: current.version + 1}))
  return <div className="ms-root"><div className="ms-layout" data-selected={selected ? '' : undefined}>
    <section className="ms-list" aria-label="Destek talepleri">
      <header className="ms-list-head"><h2>Destek talepleri</h2><p>Müşteri yazışmaları</p></header>
      <div className="ms-stats">{([['Açık', summary?.open_cases], ['Cevap bekleyen', summary?.customer_waiting], ['Atanmamış', summary?.unassigned], ['Bende', summary?.assigned_to_me]] as const).map(([label, count]) =>
        <div key={label}><b>{count ?? '—'}</b><small>{label}</small></div>)}</div>
      <div className="mod-support-filters ms-chips" role="group" aria-label="Talep filtreleri">
        {supportFilters.map(filter => <button key={filter.id} type="button" aria-pressed={query.filter === filter.id} onClick={() => setQuery({...query, filter: filter.id, offset: 0})}>{filter.label}</button>)}
      </div>
      <label className="ms-priority">Öncelik<select value={query.priority} onChange={event => {
        const priority = PRIORITIES.find(value => value === event.target.value) ?? ''
        setQuery({...query, priority, offset: 0})
      }}><option value="">Tüm öncelikler</option>{PRIORITIES.map(value => <option key={value} value={value}>{priorityLabels[value]}</option>)}</select></label>
      {busy && !page && <p role="status" className="mod-secondary">Talepler yükleniyor...</p>}
      {error !== null && <ModError error={error} onRetry={refresh}/>}
      {page && <>{!page.items.length ? <ModEmpty text="Henüz destek talebi yok"/> :
        <ul className="ms-items">{page.items.map(item => {
          const waiting = customerWaiting(item)
          return <li key={item.id}><button type="button" className="ms-item" aria-current={selected === item.id ? 'true' : undefined} onClick={() => setSelected(item.id)}>
            <span className="ms-item-top"><b className="ms-item-title">{item.subject}</b><time>{ago(item.last_at ?? item.created_at)}</time></span>
            <small className="ms-item-last">{item.last_preview ? (item.last_author === 'STAFF' ? `Ekip: ${item.last_preview}` : item.last_preview) : `Müşteri ${item.user_id}`}</small>
            <span className="ms-item-tags">
              <SupportLanguageBadge language={item.language}/>
              {waiting && item.case_status !== 'NEW' ? <span className="ms-pill ms-pill-wait">Müşteri yazdı</span> : <SupportStatusBadge status={item.case_status}/>}
              {item.priority === 'HIGH' && <SupportPriorityBadge priority={item.priority}/>}
              <span className="ms-assign">{item.assignee_user_id === null ? 'Atanmamış' : item.assigned_to_me ? 'Bende' : 'Başkasında'}</span>
            </span>
          </button></li>
        })}</ul>}
        <div className="mod-pagination ms-pages"><button type="button" disabled={busy || query.offset === 0} onClick={() => setQuery({...query, offset: Math.max(0, query.offset - page.limit)})}><ChevronLeft aria-hidden="true"/>Önceki</button>
          <button type="button" disabled={busy || page.offset + page.limit >= page.total} onClick={() => setQuery({...query, offset: query.offset + page.limit})}>Sonraki<ChevronRight aria-hidden="true"/></button></div>
      </>}
    </section>
    <section className="ms-detail" aria-label="Talep detayı">
      {selected ? <SupportDetailPanel key={selected} id={selected} manage={manage} onBack={() => setSelected('')} onCustomer={onCustomer} onChanged={refresh}/>
        : <div className="ms-empty"><b>Bir talep seç</b><p>Soldaki listeden bir talebe tıkla; yazışma ve müşteri bilgisi burada açılır.</p></div>}
    </section>
  </div></div>
}

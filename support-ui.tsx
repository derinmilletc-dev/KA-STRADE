import {useEffect, useRef, useState, type FormEvent} from 'react'
import {ModBadge, ModCard, ModEmpty} from './moderator-ui'
import {moderatorDate} from './moderator-model'
import {CASE_STATUSES, priorityLabels, statusLabels, supportCanEdit, type CaseStatus, type SupportAttachment, type SupportDetail, type SupportPriority, type SupportSummary} from './support-model'

const sizeText = (size: number) => size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`

/** Müşterinin talebe eklediği dosyalar: görsel panelde önizlenir, PDF indirilir. */
function SupportAttachments({items, load}: {items: SupportAttachment[]; load: (item: SupportAttachment) => Promise<Blob>}) {
  const [preview, setPreview] = useState<{id: string; url: string} | null>(null)
  const [busy, setBusy] = useState('')
  const [failed, setFailed] = useState('')
  const urls = useRef<string[]>([])
  useEffect(() => () => urls.current.forEach(url => URL.revokeObjectURL(url)), [])
  const open = async (item: SupportAttachment) => {
    if (preview?.id === item.id) { setPreview(null); return }
    setBusy(item.id); setFailed('')
    try {
      const blob = await load(item)
      const url = URL.createObjectURL(blob)
      urls.current.push(url)
      if (item.type.startsWith('image/')) setPreview({id: item.id, url})
      else {
        const link = document.createElement('a')
        link.href = url; link.download = item.name
        document.body.appendChild(link); link.click(); link.remove()
      }
    } catch { setFailed(item.id) } finally { setBusy('') }
  }
  return <div className="mod-support-files">
    <h3>Müşterinin eklediği dosyalar</h3>
    <ul>{items.map(item => <li key={item.id}>
      <span><b>{item.name}</b><small>{`${item.type === 'application/pdf' ? 'PDF' : 'Görsel'} · ${sizeText(item.size)}`}</small></span>
      <button type="button" disabled={busy === item.id} onClick={() => void open(item)}>{busy === item.id ? 'Açılıyor…' : preview?.id === item.id ? 'Kapat' : item.type.startsWith('image/') ? 'Görüntüle' : 'İndir'}</button>
      {failed === item.id && <small className="mod-support-file-error" role="alert">Dosya açılamadı, tekrar dene.</small>}
      {preview?.id === item.id && <img src={preview.url} alt={item.name}/>}
    </li>)}</ul>
  </div>
}

export function SupportStatusBadge({status}: {status: CaseStatus}) {
  return <ModBadge tone={status === 'RESOLVED' ? 'positive' : status === 'WAITING' ? 'warning' : 'neutral'}>{statusLabels[status]}</ModBadge>
}
export function SupportPriorityBadge({priority}: {priority: SupportPriority}) {
  return <ModBadge tone={priority === 'HIGH' ? 'warning' : 'neutral'}>{priorityLabels[priority]}</ModBadge>
}
export function SupportSummaryCards({summary}: {summary: SupportSummary | null}) {
  return <div className="mod-support-summary">
    {[['Açık talepler', summary?.open_cases], ['Atanmamış', summary?.unassigned], ['Bana atanmış', summary?.assigned_to_me]].map(([label, count]) =>
      <ModCard key={label} title={String(label)}><p className="mod-primary">{count ?? 'veri yok'}</p></ModCard>)}
  </div>
}
export function SupportDetailCards({detail, manage, busy, note, status, onNoteChange, onStatusChange, onTake, onRelease, onSaveStatus, onAddNote, onCustomer, onLoadAttachment}: {
  detail: SupportDetail; manage: boolean; busy: boolean; note: string; status: CaseStatus;
  onNoteChange: (value: string) => void; onStatusChange: (value: CaseStatus) => void;
  onTake: () => void; onRelease: () => void; onSaveStatus: () => void; onAddNote: (event: FormEvent) => void; onCustomer?: () => void
  onLoadAttachment?: (item: SupportAttachment) => Promise<Blob>
}) {
  const editable = supportCanEdit(detail, manage)
  return <div className="mod-stack">
    <ModCard title="Yazışma"><p className="mod-primary">{detail.subject}</p>
      <div className="mod-badges"><SupportStatusBadge status={detail.case_status}/><SupportPriorityBadge priority={detail.priority}/></div>
      <ol className="mod-support-thread" aria-label="Müşteriyle yazışma">
        <li data-author="CUSTOMER"><small>{`Müşteri · ${moderatorDate(detail.created_at)}`}</small><p className="mod-support-text">{detail.message || 'veri yok'}</p></li>
        {(detail.thread ?? []).map(item => <li key={item.id} data-author={item.author}>
          <small>{item.author === 'STAFF' ? `Destek ekibi${item.created_at ? ` · ${moderatorDate(item.created_at)}` : ''}` : `Müşteri${item.created_at ? ` · ${moderatorDate(item.created_at)}` : ''}`}</small>
          <p className="mod-support-text">{item.body}</p>
        </li>)}
        {!(detail.thread ?? []).length && detail.legacy_response_note && <li data-author="STAFF"><small>Destek ekibi</small><p className="mod-support-text">{detail.legacy_response_note}</p></li>}
      </ol>
      {onLoadAttachment && (detail.attachments ?? []).length > 0 && <SupportAttachments items={detail.attachments} load={onLoadAttachment}/>}
    </ModCard>
    <ModCard title="Müşteri özeti">
      <p className="mod-primary">{detail.customer.email_masked}</p><p className="mod-secondary">{detail.customer.user_id}</p>
      <div className="mod-badges"><ModBadge>{detail.customer.active ? 'Aktif' : 'Devre dışı'}</ModBadge>
        <ModBadge>{detail.customer.email_verified ? 'E-posta doğrulandı' : 'E-posta doğrulanmadı'}</ModBadge><ModBadge>{detail.customer.mfa_enabled ? 'MFA açık' : 'MFA kapalı'}</ModBadge></div>
      {onCustomer && <button className="mod-refresh" onClick={onCustomer}>Müşteri profilini görüntüle</button>}
    </ModCard>
    <ModCard title="Ekip yönetimi">
      <p className="mod-secondary">{detail.assignee_user_id === null ? 'Atanmamış' : detail.assigned_to_me ? 'Bu talep size atanmış.' : `Atanan kullanıcı: ${detail.assignee_user_id}`}</p>
      {!editable && <p className="mod-secondary">Salt okunur: düzenleme için yönetme izni ve talebin size atanması gerekir.</p>}
      <div className="mod-actions">{manage && detail.assignee_user_id === null && <button disabled={busy} onClick={onTake}>Üstlen</button>}
        {editable && <button disabled={busy} onClick={onRelease}>Bırak</button>}</div>
      <fieldset className="mod-support-status" disabled={!editable || busy}>
        <legend>Talep durumu</legend><label htmlFor="mod-case-status">Yeni durum</label>
        <div><select id="mod-case-status" value={status} onChange={event => {const next = CASE_STATUSES.find(value => value === event.target.value); if (next) onStatusChange(next)}}>
          {CASE_STATUSES.map(value => <option key={value} value={value}>{statusLabels[value]}</option>)}</select>
          <button onClick={onSaveStatus} disabled={!editable || busy || status === detail.case_status}>Durumu kaydet</button></div>
      </fieldset>
    </ModCard>
    <ModCard title="Ekip notları">
      {detail.notes.length ? <ol className="mod-support-timeline">{detail.notes.map(item => <li key={item.id}>
        <p className="mod-support-text">{item.body || 'Hesap silme işlemi kapsamında temizlendi.'}</p>
        <small>{item.author_user_id} · {moderatorDate(item.created_at)}</small>
      </li>)}</ol> : <ModEmpty text="Henüz ekip notu yok"/>}
      <form className="mod-support-note" onSubmit={onAddNote}>
        <label htmlFor="mod-team-note">Ekip içi not</label><textarea id="mod-team-note" value={note} disabled={!editable || busy} maxLength={2000} rows={4} onChange={event => onNoteChange(event.target.value)} required aria-describedby="mod-note-hint"/>
        <p id="mod-note-hint" className="mod-secondary">En fazla 2000 karakter. Anahtar, token veya kart bilgisi eklemeyin. Notlar sonradan düzenlenemez.</p>
        <button type="submit" disabled={!editable || busy || !note.trim()}>Not ekle</button>
      </form>
    </ModCard>
  </div>
}

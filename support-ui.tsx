import {useEffect, useRef, useState, type FormEvent} from 'react'
import {ModBadge, ModCard, ModEmpty} from './moderator-ui'
import {moderatorDate} from './moderator-model'
import {CASE_STATUSES, languageNames, priorityLabels, statusLabels, supportCanEdit, type CaseStatus, type SupportAttachment, type SupportDetail, type SupportLanguage, type SupportPriority, type SupportSummary} from './support-model'

const sizeText = (size: number) => size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`
// Müşteriye yanıt olarak kaydedilen ekip notunun kopyası; yazışmada zaten görünür, iç not olarak tekrar gösterilmez.
const REPLY_COPY = '[Müşteriye yanıt] '

/** Hazır yanıtlar: müşterinin diline göre metin kutusuna eklenir, gönderilmeden önce düzenlenebilir. */
export const SUPPORT_TEMPLATES: Record<SupportLanguage, {label: string; text: string}[]> = {
  tr: [
    {label: 'Talep alındı', text: 'Merhaba, talebini aldık ve inceliyoruz. En kısa sürede buradan dönüş yapacağız.'},
    {label: 'Ekran görüntüsü iste', text: 'Merhaba, daha iyi yardımcı olabilmemiz için hatanın ekran görüntüsünü bu talebe ekleyebilir misin?'},
    {label: 'Binance API izinleri', text: 'Merhaba, Binance API anahtarında Futures izninin açık ve IP kısıtlamasının kapalı olduğunu kontrol edip anahtarı yeniden kaydeder misin?'},
    {label: 'Sorun çözüldü', text: 'Merhaba, sorunu çözdük. Tekrar dener misin? Devam ederse bu talebe yazman yeterli.'},
  ],
  en: [
    {label: 'Talep alındı', text: 'Hi, we received your request and we are looking into it. We will reply here as soon as possible.'},
    {label: 'Ekran görüntüsü iste', text: 'Hi, to help you better, could you add a screenshot of the error to this request?'},
    {label: 'Binance API izinleri', text: 'Hi, could you check that the Futures permission is enabled and the IP restriction is turned off on your Binance API key, then save the key again?'},
    {label: 'Sorun çözüldü', text: 'Hi, we fixed the issue. Could you try again? If it continues, just write to this request.'},
  ],
}

export function SupportStatusBadge({status}: {status: CaseStatus}) {
  return <ModBadge tone={status === 'RESOLVED' ? 'positive' : status === 'WAITING' ? 'warning' : 'neutral'}>{statusLabels[status]}</ModBadge>
}
export function SupportPriorityBadge({priority}: {priority: SupportPriority}) {
  return <ModBadge tone={priority === 'HIGH' ? 'warning' : 'neutral'}>{priorityLabels[priority]}</ModBadge>
}
export function SupportLanguageBadge({language}: {language: SupportLanguage | null}) {
  return <span className="ms-lang" data-lang={language ?? 'unknown'} title={language ? `Müşteri dili: ${languageNames[language]}` : 'Müşteri dili bilinmiyor'}>{language ? language.toUpperCase() : '?'}</span>
}
export function SupportSummaryCards({summary}: {summary: SupportSummary | null}) {
  return <div className="mod-support-summary">
    {[['Açık talepler', summary?.open_cases], ['Atanmamış', summary?.unassigned], ['Bana atanmış', summary?.assigned_to_me]].map(([label, count]) =>
      <ModCard key={label} title={String(label)}><p className="mod-primary">{count ?? 'veri yok'}</p></ModCard>)}
  </div>
}

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
      <span><b title={item.name}>{item.name}</b><small>{`${item.type === 'application/pdf' ? 'PDF' : 'Görsel'} · ${sizeText(item.size)}`}</small></span>
      <button type="button" disabled={busy === item.id} onClick={() => void open(item)}>{busy === item.id ? 'Açılıyor…' : preview?.id === item.id ? 'Kapat' : item.type.startsWith('image/') ? 'Görüntüle' : 'İndir'}</button>
      {failed === item.id && <small className="mod-support-file-error" role="alert">Dosya açılamadı, tekrar dene.</small>}
      {preview?.id === item.id && <img src={preview.url} alt={item.name}/>}
    </li>)}</ul>
  </div>
}

type Entry = {key: string; kind: 'CUSTOMER' | 'STAFF' | 'NOTE'; body: string; at: string | null; label: string}
function conversation(detail: SupportDetail): Entry[] {
  const entries: Entry[] = [{key: 'first', kind: 'CUSTOMER', body: detail.message || 'veri yok', at: detail.created_at, label: 'Müşteri'}]
  for (const item of detail.thread ?? []) entries.push({key: item.id, kind: item.author, body: item.body, at: item.created_at, label: item.author === 'STAFF' ? 'Destek ekibi' : 'Müşteri'})
  if (!(detail.thread ?? []).length && detail.legacy_response_note) entries.push({key: 'legacy', kind: 'STAFF', body: detail.legacy_response_note, at: null, label: 'Destek ekibi'})
  for (const note of detail.notes) {
    if (note.body.startsWith(REPLY_COPY)) continue
    entries.push({key: note.id, kind: 'NOTE', body: note.body || 'Hesap silme işlemi kapsamında temizlendi.', at: note.created_at, label: `İç not · ${note.author_user_id}`})
  }
  // Zaman sırası; zamanı bilinmeyen eski yanıt kendi yerinde kalır.
  return entries.map((entry, index) => ({entry, index})).sort((a, b) => {
    const left = a.entry.at ? Date.parse(a.entry.at) : NaN, right = b.entry.at ? Date.parse(b.entry.at) : NaN
    return Number.isFinite(left) && Number.isFinite(right) && left !== right ? left - right : a.index - b.index
  }).map(row => row.entry)
}

export function SupportDetailCards({detail, manage, busy, note, status, onNoteChange, onStatusChange, onTake, onRelease, onSaveStatus, onAddNote, onCustomer, onLoadAttachment,
  reply = '', resolve = false, onReplyChange, onResolveChange, onSendReply, onBack}: {
  detail: SupportDetail; manage: boolean; busy: boolean; note: string; status: CaseStatus;
  onNoteChange: (value: string) => void; onStatusChange: (value: CaseStatus) => void;
  onTake: () => void; onRelease: () => void; onSaveStatus: () => void; onAddNote: (event: FormEvent) => void; onCustomer?: () => void
  onLoadAttachment?: (item: SupportAttachment) => Promise<Blob>
  reply?: string; resolve?: boolean; onReplyChange?: (value: string) => void; onResolveChange?: (value: boolean) => void; onSendReply?: () => void
  onBack?: () => void
}) {
  const editable = supportCanEdit(detail, manage)
  const canReply = editable && Boolean(onSendReply && onReplyChange)
  const [mode, setMode] = useState<'reply' | 'note'>('reply')
  const tab = canReply ? mode : 'note'
  const language = detail.language ?? null
  const entries = conversation(detail)
  const notes = detail.notes.filter(item => !item.body.startsWith(REPLY_COPY))
  const send = (event: FormEvent) => {
    if (tab === 'note') { onAddNote(event); return }
    event.preventDefault()
    if (reply.trim().length >= 2) onSendReply?.()
  }
  return <article className="ms-case">
    <header className="ms-case-head">
      {onBack && <button type="button" className="ms-back" onClick={onBack} disabled={busy}>‹ Taleplere dön</button>}
      <div className="ms-case-title">
        <h2>{detail.subject}</h2>
        <div className="mod-badges"><SupportStatusBadge status={detail.case_status}/><SupportPriorityBadge priority={detail.priority}/><SupportLanguageBadge language={language}/></div>
        <small>{moderatorDate(detail.created_at)}</small>
      </div>
      <div className="ms-case-actions">
        {manage && detail.assignee_user_id === null && <button type="button" className="ms-btn ms-go" disabled={busy} onClick={onTake}>Üstlen</button>}
        {editable && <button type="button" className="ms-btn" disabled={busy} onClick={onRelease}>Bırak</button>}
      </div>
    </header>
    <div className="ms-case-body"><div className="ms-case-grid">
      <section className="ms-conv" aria-label="Yazışma">
        <h3 className="ms-section-title">Yazışma</h3>
        <ol className="mod-support-thread" aria-label="Müşteriyle yazışma">
          {entries.map(entry => <li key={entry.key} data-author={entry.kind}>
            <small>{entry.at ? `${entry.label} · ${moderatorDate(entry.at)}` : entry.label}</small>
            <p className="mod-support-text">{entry.body}</p>
          </li>)}
        </ol>
        {onLoadAttachment && (detail.attachments ?? []).length > 0 && <SupportAttachments items={detail.attachments} load={onLoadAttachment}/>}
        <form className="ms-composer" data-mode={tab} onSubmit={send}>
          <div className="ms-tabs" role="tablist" aria-label="Yazma türü">
            {canReply && <button type="button" role="tab" aria-selected={tab === 'reply'} onClick={() => setMode('reply')}>Müşteriye yanıt</button>}
            <button type="button" role="tab" aria-selected={tab === 'note'} onClick={() => setMode('note')}>İç not</button>
            {tab === 'reply' && <select className="ms-templates" aria-label="Hazır yanıtlar" value="" disabled={busy}
              onChange={event => { const item = SUPPORT_TEMPLATES[language ?? 'tr'].find(row => row.label === event.target.value); if (item) onReplyChange?.(item.text) }}>
              <option value="">Hazır yanıtlar</option>
              {SUPPORT_TEMPLATES[language ?? 'tr'].map(item => <option key={item.label} value={item.label}>{item.label}</option>)}
            </select>}
          </div>
          {!editable && <p className="ms-readonly">Salt okunur: düzenleme için yönetme izni ve talebin size atanması gerekir.</p>}
          {tab === 'reply' && <p className="ms-langbar" data-lang={language ?? 'unknown'}>{language === 'en'
            ? 'Müşteri İngilizce kullanıyor: yanıtını İngilizce yaz. E-posta ve bildirim de İngilizce gider.'
            : language === 'tr' ? 'Müşteri Türkçe kullanıyor. E-posta ve bildirim Türkçe gider.'
            : 'Müşterinin dili bilinmiyor (eski talep). E-posta Türkçe gider.'}</p>}
          {tab === 'reply'
            ? <textarea aria-label="Müşteriye yanıt" value={reply} maxLength={1000} rows={4} disabled={busy || !editable} onChange={event => onReplyChange?.(event.target.value)}
              placeholder={language === 'en' ? 'Yanıtını İngilizce yaz…' : 'Müşteriye yazacağın yanıt…'}/>
            : <textarea id="mod-team-note" aria-label="Ekip içi not" value={note} maxLength={2000} rows={4} disabled={!editable || busy} onChange={event => onNoteChange(event.target.value)}
              placeholder="Yalnız ekibin göreceği not…" aria-describedby="mod-note-hint"/>}
          <div className="ms-composer-foot">
            {tab === 'reply'
              ? <label className="ms-check"><input type="checkbox" checked={resolve} disabled={busy || !editable} onChange={event => onResolveChange?.(event.target.checked)}/>Gönderince çözüldü yap</label>
              : <small id="mod-note-hint">En fazla 2000 karakter. Anahtar, token veya kart bilgisi eklemeyin. Notlar sonradan düzenlenemez.</small>}
            {tab === 'reply' && <small className="ms-foot-hint">Müşteri e-posta ve bildirim alır.</small>}
            <button type="submit" className="ms-btn ms-go" disabled={!editable || busy || (tab === 'reply' ? reply.trim().length < 2 : !note.trim())}>{tab === 'reply' ? 'Yanıtı gönder' : 'Not ekle'}</button>
          </div>
        </form>
      </section>
      <aside className="ms-side">
        <section className="ms-panel">
          <h4>Müşteri</h4>
          <p className="ms-strong">{detail.customer.email_masked}</p>
          <p className="ms-muted ms-break">{detail.customer.user_id}</p>
          <div className="mod-badges"><ModBadge>{detail.customer.active ? 'Aktif' : 'Devre dışı'}</ModBadge>
            <ModBadge>{detail.customer.email_verified ? 'E-posta doğrulandı' : 'E-posta doğrulanmadı'}</ModBadge><ModBadge>{detail.customer.mfa_enabled ? 'MFA açık' : 'MFA kapalı'}</ModBadge></div>
          {onCustomer && <button type="button" className="ms-btn ms-wide" onClick={onCustomer}>Müşteri profilini görüntüle</button>}
        </section>
        <section className="ms-panel">
          <h4>Dil</h4>
          <div className="ms-langcard" data-lang={language ?? 'unknown'}>
            <SupportLanguageBadge language={language}/>
            <span><b>{language ? languageNames[language] : 'Bilinmiyor'}</b><small>{language ? 'Müşterinin talebi yazdığı dil' : 'Eski talep; dil kaydı yok'}</small></span>
          </div>
        </section>
        <section className="ms-panel">
          <h4>Talep</h4>
          <p className="ms-muted">{detail.assignee_user_id === null ? 'Atanmamış' : detail.assigned_to_me ? 'Bu talep size atanmış.' : `Atanan kullanıcı: ${detail.assignee_user_id}`}</p>
          <fieldset className="mod-support-status" disabled={!editable || busy}>
            <legend>Talep durumu</legend><label htmlFor="mod-case-status">Yeni durum</label>
            <div><select id="mod-case-status" value={status} onChange={event => {const next = CASE_STATUSES.find(value => value === event.target.value); if (next) onStatusChange(next)}}>
              {CASE_STATUSES.map(value => <option key={value} value={value}>{statusLabels[value]}</option>)}</select>
              <button type="button" onClick={onSaveStatus} disabled={!editable || busy || status === detail.case_status}>Durumu kaydet</button></div>
          </fieldset>
        </section>
        <section className="ms-panel">
          <h4>Ekip notları</h4>
          {notes.length ? <p className="ms-muted">{`${notes.length} iç not yazışmada sarı olarak görünür.`}</p> : <ModEmpty text="Henüz ekip notu yok"/>}
        </section>
      </aside>
    </div></div>
  </article>
}

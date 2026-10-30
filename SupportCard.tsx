import {useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent} from 'react'
import {Check, ChevronLeft, ChevronRight, CreditCard, Download, FileText, KeyRound, LineChart, MessageCircle, Paperclip, Send, UserRound, X} from 'lucide-react'
import {accountDate, accountRequest} from './account-settings-api'
import {API_BASE} from './api'
import {ACCEPT, MAX_FILES, formatSize, prepare, toBase64, type Picked} from './support-files'
import {SupportIcon} from './MenuIcons'
import {currentLang} from './i18n'
import './support-card.css'

type Attachment = {id: string; name: string; type: string; size: number}
type ThreadMessage = {id: string; author: 'CUSTOMER' | 'STAFF'; body: string; created_at: string | null}
type Ticket = {id: string; subject: string; message: string; status: string; created_at: string; response_note?: string; attachments?: Attachment[]; thread?: ThreadMessage[]; unread_replies?: number}
type Home = {support_tickets?: Ticket[]}

const TOPICS = [
  {name: 'Ödeme ve abonelik', hint: 'Fatura, üyelik', Icon: CreditCard},
  {name: 'Binance bağlantısı', hint: 'API, bağlanamıyorum', Icon: KeyRound},
  {name: 'İşlem ve strateji', hint: 'Bot, emir, sonuçlar', Icon: LineChart},
  {name: 'Hesap ve giriş', hint: 'Şifre, güvenlik', Icon: UserRound},
  {name: 'Diğer', hint: 'Başka bir konu', Icon: MessageCircle},
]
const STATUS: Record<string, string> = {OPEN: 'Alındı', IN_PROGRESS: 'İnceleniyor', RESOLVED: 'Çözüldü', CLOSED: 'Kapandı'}
const STEP: Record<string, number> = {OPEN: 1, IN_PROGRESS: 2, RESOLVED: 3, CLOSED: 3}

export default function SupportCard() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [topic, setTopic] = useState(TOPICS[0].name)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [files, setFiles] = useState<Picked[]>([])
  const [drag, setDrag] = useState(false)
  const [fileError, setFileError] = useState('')
  const [warn, setWarn] = useState('')
  const [replies, setReplies] = useState<Record<string, string>>({})
  const [replyBusy, setReplyBusy] = useState<string | null>(null)
  const [replyError, setReplyError] = useState<{id: string; text: string} | null>(null)
  const [replyFiles, setReplyFiles] = useState<Picked[]>([])
  const replyInput = useRef<HTMLInputElement>(null)
  const replyFilesRef = useRef<Picked[]>([])
  replyFilesRef.current = replyFiles
  useEffect(() => () => { replyFilesRef.current.forEach(item => item.preview && URL.revokeObjectURL(item.preview)) }, [])
  const autoOpened = useRef(false)
  const input = useRef<HTMLInputElement>(null)
  const filesRef = useRef<Picked[]>([])
  filesRef.current = files
  useEffect(() => () => { filesRef.current.forEach(item => item.preview && URL.revokeObjectURL(item.preview)) }, [])
  const addFiles = async (list: FileList | File[] | null) => {
    if (!list) return
    setFileError('')
    const incoming = Array.from(list)
    const room = MAX_FILES - filesRef.current.length
    if (incoming.length > room) setFileError(`En fazla ${MAX_FILES} dosya ekleyebilirsin.`)
    for (const file of incoming.slice(0, Math.max(room, 0))) {
      try {
        const picked = await prepare(file)
        setFiles(current => current.length >= MAX_FILES ? current : [...current, picked])
      } catch (e) { setFileError(e instanceof Error ? e.message : 'Dosya eklenemedi.') }
    }
    if (input.current) input.current.value = ''
  }
  const removeFile = (key: string) => setFiles(current => {
    current.find(item => item.key === key && item.preview && URL.revokeObjectURL(item.preview))
    return current.filter(item => item.key !== key)
  })
  const onDrop = (event: DragEvent) => { event.preventDefault(); setDrag(false); void addFiles(event.dataTransfer.files) }
  const onPaste = (event: ClipboardEvent) => {
    const pasted = Array.from(event.clipboardData.files)
    if (pasted.length) { event.preventDefault(); void addFiles(pasted) }
  }
  const download = async (ticketId: string, item: Attachment) => {
    try {
      const response = await fetch(`${API_BASE}/v22/commerce/support/${ticketId}/attachments/${item.id}`, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
      if (!response.ok) throw new Error()
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = item.name
      document.body.appendChild(link); link.click(); link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 4000)
    } catch { setError('Dosya indirilemedi, tekrar dene.') }
  }
  const load = useCallback(async () => {
    try {
      const home = await accountRequest<Home>('/commerce/customer-home')
      const list = home.support_tickets || []
      setTickets(list)
      // Bildirim veya e-postadan gelindiyse yeni yanıtı olan talep kendiliğinden açılır.
      const fresh = list.find(item => (item.unread_replies ?? 0) > 0)
      if (!autoOpened.current && fresh) { autoOpened.current = true; void openTicket(fresh) }
    } catch { setTickets([]) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!done) return
    const t = window.setTimeout(() => { setDone(false); setWarn('') }, warn ? 9000 : 4500)
    return () => window.clearTimeout(t)
  }, [done])
  const send = async () => {
    const text = message.trim()
    if (text.length < 5) { setError('Mesajın en az 5 karakter olmalı.'); return }
    setBusy(true); setError('')
    try {
      const subject = `${topic}: ${text.replace(/\s+/g, ' ').slice(0, 80)}`
      const created = await accountRequest<{id: string}>('/commerce/support', {method: 'POST', body: JSON.stringify({subject, message: text, priority: 'NORMAL', language: currentLang()})})
      let attachWarning = ''
      if (files.length) {
        try {
          const payload = {files: await Promise.all(files.map(async item => ({name: item.file.name, data: await toBase64(item.file)})))}
          await accountRequest(`/commerce/support/${created.id}/attachments`, {method: 'POST', body: JSON.stringify(payload)})
        } catch (e) {
          attachWarning = e instanceof Error && e.message ? e.message : 'Dosyalar yüklenemedi.'
        }
      }
      files.forEach(item => item.preview && URL.revokeObjectURL(item.preview))
      setFiles([]); setFileError('')
      setMessage(''); setDone(true); setWarn(attachWarning)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Talep gönderilemedi, tekrar dene.')
    } finally { setBusy(false) }
  }
  const threadOf = (item: Ticket): ThreadMessage[] => item.thread
    ?? (item.response_note ? [{id: `legacy-${item.id}`, author: 'STAFF', body: item.response_note, created_at: null}] : [])
  async function openTicket(item: Ticket, scroll = false) {
    setOpen(item.id)
    if (scroll) window.requestAnimationFrame(() => document.getElementById('support-card')?.scrollIntoView({behavior: 'smooth', block: 'start'}))
    if ((item.unread_replies ?? 0) === 0) return
    setTickets(current => current?.map(row => row.id === item.id ? {...row, unread_replies: 0} : row) ?? current)
    try { await accountRequest(`/commerce/support/${item.id}/read`, {method: 'POST'}) } catch { /* okundu bilgisi bir sonraki açılışta yeniden denenir */ }
  }
  const clearReplyFiles = () => { replyFilesRef.current.forEach(item => item.preview && URL.revokeObjectURL(item.preview)); setReplyFiles([]) }
  const addReplyFiles = async (list: FileList | null, room: number) => {
    if (!list) return
    setReplyError(null)
    const incoming = Array.from(list)
    if (incoming.length > room) setReplyError({id: open || '', text: room > 0 ? `Bu talebe en fazla ${room} dosya daha ekleyebilirsin.` : 'Bu talebe en fazla 3 dosya eklenebilir.'})
    for (const file of incoming.slice(0, Math.max(room, 0))) {
      try {
        const picked = await prepare(file)
        setReplyFiles(current => current.length >= room ? current : [...current, picked])
      } catch (e) { setReplyError({id: open || '', text: e instanceof Error ? e.message : 'Dosya eklenemedi.'}) }
    }
    if (replyInput.current) replyInput.current.value = ''
  }
  const sendReply = async (item: Ticket) => {
    const text = (replies[item.id] || '').trim()
    if (text.length < 2) return
    setReplyBusy(item.id); setReplyError(null)
    try {
      const updated = await accountRequest<Ticket>(`/commerce/support/${item.id}/messages`, {method: 'POST', body: JSON.stringify({body: text, language: currentLang()})})
      setTickets(current => current?.map(row => row.id === item.id ? {...row, ...updated} : row) ?? current)
      setReplies(current => ({...current, [item.id]: ''}))
      if (replyFilesRef.current.length) {
        try {
          const payload = {files: await Promise.all(replyFilesRef.current.map(async picked => ({name: picked.file.name, data: await toBase64(picked.file)})))}
          const saved = await accountRequest<{attachments: Attachment[]}>(`/commerce/support/${item.id}/attachments`, {method: 'POST', body: JSON.stringify(payload)})
          setTickets(current => current?.map(row => row.id === item.id ? {...row, attachments: saved.attachments} : row) ?? current)
        } catch (e) {
          setReplyError({id: item.id, text: `Mesajın gönderildi ancak dosyalar eklenemedi: ${e instanceof Error && e.message ? e.message : 'tekrar dene.'}`})
        }
        clearReplyFiles()
      }
    } catch (e) {
      setReplyError({id: item.id, text: e instanceof Error && e.message ? e.message : 'Mesaj gönderilemedi, tekrar dene.'})
    } finally { setReplyBusy(null) }
  }
  const active = open ? tickets?.find(item => item.id === open) ?? null : null
  const ready = message.trim().length >= 5
  return <section className="accountCard" id="support-card">
    <div className="spHead">
      <span className="spBadge"><SupportIcon aria-hidden="true"/></span>
      <div><h2>Destek</h2><p>Yanıtımız burada, e-postanda ve bildirimlerinde görünür.</p></div>
    </div>
    {active ? (() => {
      const item = active
      const thread = threadOf(item)
      const answered = thread.some(row => row.author === 'STAFF')
      const step = STEP[item.status] ?? 1
      const room = Math.max(0, MAX_FILES - (item.attachments?.length ?? 0))
      const closed = item.status === 'RESOLVED' || item.status === 'CLOSED'
      return <div className="spConv" data-status={item.status}>
        <div className="spConvHead">
          <button type="button" className="spBack" aria-label="Taleplerime dön" onClick={() => { setOpen(null); clearReplyFiles() }}><ChevronLeft aria-hidden="true"/></button>
          <span><b>{item.subject}</b><small><i aria-hidden="true"/>{STATUS[item.status] || item.status}</small></span>
        </div>
        <ol className="spSteps" aria-label="Talep durumu">
          {['Alındı', 'İnceleniyor', item.status === 'CLOSED' ? 'Kapandı' : 'Çözüldü'].map((label, index) => <li key={label} data-on={index < step || undefined}><i aria-hidden="true"/><small>{label}</small></li>)}
        </ol>
        <ol className="spThread" aria-label="Yazışma">
          <li data-author="CUSTOMER"><small>{`Sen · ${accountDate(item.created_at)}`}</small><p>{item.message}</p></li>
          {thread.map(row => <li key={row.id} data-author={row.author}>
            <small>{row.author === 'STAFF' ? (row.created_at ? `KaisTrade Destek · ${accountDate(row.created_at)}` : 'KaisTrade Destek') : (row.created_at ? `Sen · ${accountDate(row.created_at)}` : 'Sen')}</small>
            <p>{row.body}</p>
          </li>)}
        </ol>
        {item.attachments && item.attachments.length > 0 && <ul className="spAttached">{item.attachments.map(file => <li key={file.id}><FileText aria-hidden="true"/><span><b title={file.name}>{file.name}</b><small>{formatSize(file.size)}</small></span><button type="button" aria-label={`${file.name} dosyasını indir`} onClick={() => void download(item.id, file)}><Download aria-hidden="true"/>İndir</button></li>)}</ul>}
        {!answered && <small className="spWait">Yanıt bekleniyor · yanıt gelince e-posta ve bildirim alacaksın</small>}
        {replyFiles.length > 0 && <ul className="spFiles" aria-label="Eklenecek dosyalar">
          {replyFiles.map(picked => <li key={picked.key}>
            {picked.preview ? <img src={picked.preview} alt=""/> : <span className="spFileIcon"><FileText aria-hidden="true"/></span>}
            <span><b title={picked.file.name}>{picked.file.name}</b><small>{formatSize(picked.file.size)}</small></span>
            <button type="button" aria-label={`${picked.file.name} dosyasını kaldır`} onClick={() => setReplyFiles(current => { const gone = current.find(row => row.key === picked.key); if (gone?.preview) URL.revokeObjectURL(gone.preview); return current.filter(row => row.key !== picked.key) })}><X aria-hidden="true"/></button>
          </li>)}
        </ul>}
        <div className="spComposer">
          <input ref={replyInput} type="file" accept={ACCEPT} multiple hidden onChange={e => void addReplyFiles(e.target.files, room - replyFiles.length)}/>
          <button type="button" className="spClip" aria-label="Dosya ekle" title={room - replyFiles.length > 0 ? 'Dosya ekle' : 'Bu talebe en fazla 3 dosya eklenebilir'} disabled={room - replyFiles.length <= 0 || replyBusy === item.id} onClick={() => replyInput.current?.click()}><Paperclip aria-hidden="true"/></button>
          <textarea value={replies[item.id] || ''} maxLength={2000} rows={1} aria-label="Bu talebe yaz" placeholder={closed ? 'Sorun devam ediyorsa yaz, talep yeniden açılır…' : 'Mesaj yaz…'}
            onChange={e => setReplies(current => ({...current, [item.id]: e.target.value}))}/>
          <button type="button" className="spSendIcon" aria-label="Gönder" disabled={replyBusy === item.id || (replies[item.id] || '').trim().length < 2} onClick={() => void sendReply(item)}><Send aria-hidden="true"/></button>
        </div>
        {replyError?.id === item.id && <p className="spError" role="alert">{replyError.text}</p>}
        <p className="spNote">Yanıtlar burada, e-postanda ve bildirimlerinde görünür.</p>
      </div>
    })() : <>
    {done ? <div className="spDone" role="status"><span><Check aria-hidden="true"/></span><b>Talebin alındı</b><small>{warn ? `Talebin kaydedildi ancak dosyalar eklenemedi: ${warn}` : 'En kısa sürede dönüş yapacağız.'}</small></div> : <>
      <div className="spTopics" role="radiogroup" aria-label="Konu">
        {TOPICS.map(({name, hint, Icon}) => <button type="button" role="radio" aria-checked={name === topic} key={name} className={name === topic ? 'on' : ''} onClick={() => setTopic(name)}>
          <Icon aria-hidden="true"/><b>{name}</b><small>{hint}</small>
        </button>)}
      </div>
      <div className={`spBox${drag ? ' drag' : ''}`} onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
        <textarea value={message} maxLength={2000} rows={3} placeholder="Sorununu kısaca anlat…" onChange={e => setMessage(e.target.value)} onPaste={onPaste}/>
        {files.length > 0 && <ul className="spFiles" aria-label="Eklenen dosyalar">
          {files.map(item => <li key={item.key}>
            {item.preview ? <img src={item.preview} alt=""/> : <span className="spFileIcon"><FileText aria-hidden="true"/></span>}
            <span><b title={item.file.name}>{item.file.name}</b><small>{formatSize(item.file.size)}</small></span>
            <button type="button" aria-label={`${item.file.name} dosyasını kaldır`} onClick={() => removeFile(item.key)}><X aria-hidden="true"/></button>
          </li>)}
        </ul>}
        <div className="spAttach">
          <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={e => void addFiles(e.target.files)}/>
          <button type="button" className="spAddFile" disabled={files.length >= MAX_FILES} onClick={() => input.current?.click()}><Paperclip aria-hidden="true"/>Fotoğraf veya dosya ekle</button>
          <small>PNG, JPG, WEBP, PDF · en fazla {MAX_FILES} dosya · her biri 2 MB · sürükleyip bırakabilir veya ekran görüntüsünü yapıştırabilirsin</small>
        </div>
        <div className="spBar">
          <small>Şifreni, API gizli anahtarını yazma veya görselde gösterme · {message.length}/2000</small>
          <button type="button" className="spSend" disabled={busy || !ready} onClick={() => void send()}><Send aria-hidden="true"/>{busy ? 'Gönderiliyor' : 'Gönder'}</button>
        </div>
      </div>
      {fileError && <p className="spError" role="alert">{fileError}</p>}
      {error && <p className="spError" role="alert">{error}</p>}
    </>}
      {tickets !== null && tickets.length > 0 && <div className="spMine">
        <h3>Taleplerim</h3>
        {tickets.map(item => {
          const thread = threadOf(item)
          const last = thread.length ? thread[thread.length - 1] : null
          const unread = item.unread_replies ?? 0
          return <button type="button" key={item.id} className="spCase" data-status={item.status} data-unread={unread > 0 || undefined} onClick={() => void openTicket(item, true)}>
            <i aria-hidden="true"/>
            <span className="spCaseMain">
              <b>{item.subject}</b>
              <small>{last ? `${last.author === 'STAFF' ? 'Destek' : 'Sen'}: ${last.body}` : item.message}</small>
            </span>
            <span className="spCaseSide">
              <em>{unread > 0 ? 'Yeni yanıt' : STATUS[item.status] || item.status}</em>
              <small>{accountDate(item.created_at)}</small>
            </span>
            <ChevronRight aria-hidden="true"/>
          </button>
        })}
      </div>}
    </>}
  </section>
}

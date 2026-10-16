import {useCallback, useEffect, useState} from 'react'
import {LifeBuoy, Send} from 'lucide-react'
import {accountDate, accountRequest} from './account-settings-api'
import './support-card.css'

type Ticket = {id: string; subject: string; message: string; status: string; priority?: string; created_at: string; updated_at?: string; response_note?: string}
type Home = {support_tickets?: Ticket[]}

const TOPICS = ['Ödeme ve abonelik', 'Binance bağlantısı', 'İşlem ve strateji', 'Hesap ve giriş', 'Diğer']
const STATUS: Record<string, string> = {OPEN: 'Alındı', IN_PROGRESS: 'İnceleniyor', RESOLVED: 'Çözüldü', CLOSED: 'Kapandı'}

export default function SupportCard() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [topic, setTopic] = useState(TOPICS[0])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{kind: 'ok' | 'error'; text: string} | null>(null)
  const load = useCallback(async () => {
    try {
      const home = await accountRequest<Home>('/commerce/customer-home')
      setTickets(home.support_tickets || [])
    } catch { setTickets([]) }
  }, [])
  useEffect(() => { void load() }, [load])
  const send = async () => {
    const text = message.trim()
    if (text.length < 5) { setNotice({kind: 'error', text: 'Mesajın en az 5 karakter olmalı.'}); return }
    setBusy(true); setNotice(null)
    try {
      const subject = `${topic}: ${text.replace(/\s+/g, ' ').slice(0, 80)}`
      await accountRequest('/commerce/support', {method: 'POST', body: JSON.stringify({subject, message: text, priority: 'NORMAL'})})
      setMessage(''); setNotice({kind: 'ok', text: 'Talebin alındı. Destek ekibi en kısa sürede dönüş yapacak.'})
      await load()
    } catch (error) {
      setNotice({kind: 'error', text: error instanceof Error ? error.message : 'Talep gönderilemedi, tekrar dene.'})
    } finally { setBusy(false) }
  }
  return <section className="accountCard" id="support-card">
    <h2><LifeBuoy aria-hidden="true"/>Destek</h2>
    <p className="spLead">Bir sorunun mu var? Konuyu seç, yaz; destek ekibimiz sana buradan dönüş yapar. Şifreni veya API gizli anahtarını asla yazma.</p>
    <div className="spChips" role="group" aria-label="Konu">
      {TOPICS.map(item => <button type="button" key={item} className={item === topic ? 'on' : ''} aria-pressed={item === topic} onClick={() => setTopic(item)}>{item}</button>)}
    </div>
    <textarea className="spText" value={message} maxLength={2000} rows={4} placeholder="Sorununu veya sorunu kısaca anlat…" onChange={event => setMessage(event.target.value)}/>
    <div className="spFoot">
      <small>{message.length}/2000</small>
      <button type="button" className="spSend" disabled={busy || message.trim().length < 5} onClick={() => void send()}><Send aria-hidden="true"/>{busy ? 'Gönderiliyor…' : 'Gönder'}</button>
    </div>
    {notice && <p className={`spNotice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.text}</p>}
    <h3 className="spSub">Taleplerim</h3>
    {tickets === null && <p className="spLead">Yükleniyor…</p>}
    {tickets !== null && !tickets.length && <p className="spLead">Henüz bir talebin yok.</p>}
    <div className="spList">
      {(tickets || []).map(item => <article className="spItem" key={item.id}>
        <header><b>{item.subject}</b><em data-status={item.status}>{STATUS[item.status] || item.status}</em></header>
        <p>{item.message}</p>
        <small>{accountDate(item.created_at)}</small>
        {item.response_note && <div className="spReply"><b>Destek ekibi</b><p>{item.response_note}</p></div>}
      </article>)}
    </div>
  </section>
}

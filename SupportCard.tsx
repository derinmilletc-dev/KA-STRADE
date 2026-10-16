import {useCallback, useEffect, useState} from 'react'
import {Check, ChevronDown, CreditCard, KeyRound, LifeBuoy, LineChart, MessageCircle, Send, UserRound} from 'lucide-react'
import {accountDate, accountRequest} from './account-settings-api'
import './support-card.css'

type Ticket = {id: string; subject: string; message: string; status: string; created_at: string; response_note?: string}
type Home = {support_tickets?: Ticket[]}

const TOPICS = [
  {name: 'Ödeme ve abonelik', hint: 'Fatura, üyelik', Icon: CreditCard},
  {name: 'Binance bağlantısı', hint: 'API, bağlanamıyorum', Icon: KeyRound},
  {name: 'İşlem ve strateji', hint: 'Bot, emir, sonuçlar', Icon: LineChart},
  {name: 'Hesap ve giriş', hint: 'Şifre, güvenlik', Icon: UserRound},
  {name: 'Diğer', hint: 'Başka bir konu', Icon: MessageCircle},
]
const STATUS: Record<string, string> = {OPEN: 'Alındı', IN_PROGRESS: 'İnceleniyor', RESOLVED: 'Çözüldü', CLOSED: 'Kapandı'}

export default function SupportCard() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null)
  const [topic, setTopic] = useState(TOPICS[0].name)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      const home = await accountRequest<Home>('/commerce/customer-home')
      setTickets(home.support_tickets || [])
    } catch { setTickets([]) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!done) return
    const t = window.setTimeout(() => setDone(false), 4500)
    return () => window.clearTimeout(t)
  }, [done])
  const send = async () => {
    const text = message.trim()
    if (text.length < 5) { setError('Mesajın en az 5 karakter olmalı.'); return }
    setBusy(true); setError('')
    try {
      const subject = `${topic}: ${text.replace(/\s+/g, ' ').slice(0, 80)}`
      await accountRequest('/commerce/support', {method: 'POST', body: JSON.stringify({subject, message: text, priority: 'NORMAL'})})
      setMessage(''); setDone(true)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Talep gönderilemedi, tekrar dene.')
    } finally { setBusy(false) }
  }
  const ready = message.trim().length >= 5
  return <section className="accountCard" id="support-card">
    <div className="spHead">
      <span className="spBadge"><LifeBuoy aria-hidden="true"/></span>
      <div><h2>Destek</h2><p>Destek ekibimiz sana buradan dönüş yapar.</p></div>
    </div>
    {done ? <div className="spDone" role="status"><span><Check aria-hidden="true"/></span><b>Talebin alındı</b><small>En kısa sürede dönüş yapacağız.</small></div> : <>
      <div className="spTopics" role="radiogroup" aria-label="Konu">
        {TOPICS.map(({name, hint, Icon}) => <button type="button" role="radio" aria-checked={name === topic} key={name} className={name === topic ? 'on' : ''} onClick={() => setTopic(name)}>
          <Icon aria-hidden="true"/><b>{name}</b><small>{hint}</small>
        </button>)}
      </div>
      <div className="spBox">
        <textarea value={message} maxLength={2000} rows={3} placeholder="Sorununu kısaca anlat…" onChange={e => setMessage(e.target.value)}/>
        <div className="spBar">
          <small>Şifreni veya API gizli anahtarını yazma · {message.length}/2000</small>
          <button type="button" className="spSend" disabled={busy || !ready} onClick={() => void send()}><Send aria-hidden="true"/>{busy ? 'Gönderiliyor' : 'Gönder'}</button>
        </div>
      </div>
      {error && <p className="spError" role="alert">{error}</p>}
    </>}
    {tickets !== null && tickets.length > 0 && <div className="spMine">
      <h3>Taleplerim</h3>
      {tickets.map(item => {
        const isOpen = open === item.id
        return <article key={item.id} className={`spItem${isOpen ? ' open' : ''}`} data-status={item.status}>
          <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : item.id)}>
            <i aria-hidden="true"/>
            <span><b>{item.subject}</b><small>{accountDate(item.created_at)}</small></span>
            <em>{STATUS[item.status] || item.status}</em>
            <ChevronDown aria-hidden="true"/>
          </button>
          <div className="spBody"><div>
            <p>{item.message}</p>
            {item.response_note ? <div className="spReply"><b>Destek ekibi</b><p>{item.response_note}</p></div> : <small className="spWait">Yanıt bekleniyor</small>}
          </div></div>
        </article>
      })}
    </div>}
  </section>
}

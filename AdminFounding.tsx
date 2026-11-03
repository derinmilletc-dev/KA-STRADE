import {useCallback, useEffect, useState} from 'react'
import {API_BASE} from './api'
import './admin-founding.css'

type Status = 'pending_email' | 'new' | 'approved' | 'waitlist' | 'rejected' | 'invited' | 'invite_expired' | 'account_created' | 'payment_pending' | 'active'
type Item = {id: number; email: string; name: string; country: string; lang: string; status: Status; note: string; approved_seq: number | null; futures_experience: string | null; trading_experience: string | null; reason: string; reference: string | null; heard_from?: string | null; source?: string; invite_sent_at?: string | null; invite_expires_at?: string | null; invite_accepted_at?: string | null; invite_count?: number; invite_open?: boolean; activated_at?: string | null; price_locked_until?: string | null; email_confirmed_at: string | null; decided_at: string | null; created_at: string}
type Settings = {seats: number; applications_open: boolean; launch_at: string | null; price_text: string; registration_open?: boolean}
type FunnelKey = 'view' | 'start' | 'step2' | 'submit' | 'success'
type Funnel = Record<'d7' | 'd30' | 'total', Record<FunnelKey, number>>
type Payload = {items: Item[]; counts: Partial<Record<Status, number>>; settings: Settings; funnel?: Funnel; suggestions?: Item[]}
const FUNNEL: [FunnelKey, string][] = [['view', 'Sayfayı açtı'], ['start', 'Forma yazmaya başladı'], ['step2', '2. adıma geçti'], ['submit', 'Gönder’e bastı'], ['success', 'Başvurusu kaydedildi']]

function FunnelCard({funnel}: {funnel: Funnel}) {
  const [win, setWin] = useState<'d7' | 'd30' | 'total'>('d7')
  const data = funnel[win]
  const top = Math.max(1, data.view)
  return <div className="afFunnel">
    <div className="afFunnelHead"><b>Dönüşüm hunisi</b><span>{(['d7', 'd30', 'total'] as const).map(key => <button type="button" key={key} className={win === key ? 'on' : ''} onClick={() => setWin(key)}>{key === 'd7' ? '7 gün' : key === 'd30' ? '30 gün' : 'Tümü'}</button>)}</span></div>
    <ol>{FUNNEL.map(([key, label], index) => {
      const value = data[key]; const prev = index ? data[FUNNEL[index - 1][0]] : 0
      return <li key={key}><span className="afFunnelLabel">{label}</span><span className="afFunnelBar"><i style={{width: `${Math.round(value / top * 100)}%`}}/></span>
        <b>{value}</b><small>{index === 0 ? '' : prev ? `%${Math.round(value / prev * 100)}` : '–'}</small></li>
    })}</ol>
    <p>Genel dönüşüm: <b>{data.view ? `%${(data.success / data.view * 100).toFixed(1)}` : '–'}</b> · Her tarayıcı oturumu bir kez sayılır; kişisel veri tutulmaz.</p>
  </div>
}

const LABEL: Record<Status, string> = {pending_email: 'E-posta bekliyor', new: 'İnceleme bekliyor', approved: 'Onaylı · davet bekliyor', waitlist: 'Bekleme listesi', rejected: 'Reddedildi', invited: 'Davet gönderildi', invite_expired: 'Davet süresi doldu', account_created: 'Hesap açıldı', payment_pending: 'Ödeme bekleniyor', active: 'Aktif üye'}
const HEARD: Record<string, string> = {instagram: 'Instagram', tiktok: 'TikTok', x: 'X', youtube: 'YouTube', google: 'Google', friend: 'Arkadaş', telegram: 'Telegram', other: 'Diğer'}
const DECIDABLE: Status[] = ['new', 'approved', 'waitlist', 'rejected', 'invited', 'invite_expired']
const FUTURES: Record<string, string> = {active: 'Aktif kullanıyor', past: 'Daha önce kullandı', never: 'Henüz kullanmadı'}
const TRADING: Record<string, string> = {new: 'Yeni başlıyor', lt6m: '6 aydan az', '6to12m': '6–12 ay', gt1y: '1 yıldan fazla'}
const ORDER: Status[] = ['new', 'approved', 'invited', 'account_created', 'payment_pending', 'active', 'waitlist', 'invite_expired', 'rejected', 'pending_email']

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-Requested-With', 'XMLHttpRequest')
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}/v22/admin/founding${path}`, {...init, headers, credentials: 'same-origin'})
  if (!response.ok) {
    let detail = ''
    try { const body = await response.json(); detail = typeof body?.detail === 'string' ? body.detail : '' } catch { /* gövde yok */ }
    throw new Error(detail || `İstek başarısız (${response.status})`)
  }
  return response.json() as Promise<T>
}

const toLocalInput = (iso: string | null) => {
  if (!iso) return ''
  const date = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export default function AdminFounding() {
  const [data, setData] = useState<Payload | null>(null)
  const [filter, setFilter] = useState<Status | ''>('new')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<{seats: string; open: boolean; launch: string; price: string; register: boolean}>({seats: '30', open: true, launch: '', price: '', register: false})
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [search, setSearch] = useState('')

  const load = useCallback(async () => {
    setBusy(true); setError('')
    try {
      const value = await call<Payload>(filter ? `?status=${filter}` : '')
      setData(value)
      setForm({seats: String(value.settings.seats), open: value.settings.applications_open, launch: toLocalInput(value.settings.launch_at), price: value.settings.price_text, register: value.settings.registration_open === true})
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Yüklenemedi') } finally { setBusy(false) }
  }, [filter])
  useEffect(() => { void load() }, [load])

  const saveSettings = async () => {
    setBusy(true); setError(''); setInfo('')
    try {
      await call('/settings', {method: 'PUT', body: JSON.stringify({seats: Number(form.seats), applications_open: form.open, launch_at: form.launch ? new Date(form.launch).toISOString() : null, price_text: form.price, registration_open: form.register})})
      setInfo('Ayarlar kaydedildi.'); await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Kaydedilemedi'); setBusy(false) }
  }
  const decide = async (item: Item, action: 'approve' | 'waitlist' | 'reject') => {
    if (action === 'reject' && !window.confirm(`${item.name} başvurusunu reddetmek istiyor musunuz?`)) return
    setBusy(true); setError(''); setInfo('')
    try {
      const result = await call<Item & {email_sent: boolean}>(`/${item.id}/${action}`, {method: 'POST', body: JSON.stringify({note: notes[item.id] ?? ''})})
      setInfo(action === 'approve' ? (result.email_sent ? `${item.name} onaylandı; onay e-postası gönderildi. Davet lansmandan 7 gün önce otomatik gider.` : `${item.name} onaylandı ama e-posta gönderilemedi.`) : 'Güncellendi.')
      await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'İşlem yapılamadı'); setBusy(false) }
  }

  const invite = async (item: Item, action: 'invite' | 'revoke-invite' | 'extend-invite') => {
    const ask = action === 'invite' ? (item.invite_count ? `${item.name} için yeni davet gönderilsin mi? Eski bağlantı geçersiz olur.` : `${item.name} adresine davet gönderilsin mi?`)
      : action === 'revoke-invite' ? `${item.name} davetini iptal etmek istiyor musunuz?` : ''
    if (ask && !window.confirm(ask)) return
    setBusy(true); setError(''); setInfo('')
    try {
      const result = await call<Item & {email_sent: boolean}>(`/${item.id}/${action}`, {method: 'POST'})
      setInfo(action === 'invite' ? (result.email_sent ? `Davet ${item.email} adresine gönderildi (48 saat geçerli).` : 'Davet oluşturuldu ama e-posta gönderilemedi; tekrar deneyin.') : action === 'revoke-invite' ? 'Davet iptal edildi.' : 'Davet 48 saat uzatıldı.')
      await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'İşlem yapılamadı'); setBusy(false) }
  }
  const [manual, setManual] = useState({email: '', name: '', country: ''})
  const manualInvite = async () => {
    if (!window.confirm(`${manual.email} adresine başvuru olmadan davet gönderilsin mi? Bir koltuk kullanılır.`)) return
    setBusy(true); setError(''); setInfo('')
    try {
      const result = await call<Item & {email_sent: boolean}>('/invite', {method: 'POST', body: JSON.stringify({email: manual.email, name: manual.name, country: manual.country})})
      setInfo(result.email_sent ? `Davet ${result.email} adresine gönderildi.` : 'Davet oluşturuldu ama e-posta gönderilemedi.'); setManual({email: '', name: '', country: ''})
      await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'İşlem yapılamadı'); setBusy(false) }
  }

  const counts = data?.counts ?? {}
  const seats = data?.settings.seats ?? 30
  const active = counts.active ?? 0
  const inSetup = (counts.account_created ?? 0) + (counts.payment_pending ?? 0)
  const invitedNow = counts.invited ?? 0
  const approvedWaiting = counts.approved ?? 0
  const taken = active + inSetup + invitedNow + approvedWaiting
  return <section className="adminFounding" aria-label="Founding 30 başvuruları">
    <header><h2>Üyelik yönetimi · Founding 30</h2><p>KaisTrade yalnız davetle üye alır. Onaylılara davet lansmandan 7 gün önce otomatik gider; buradan elle de gönderebilirsiniz.</p></header>
    <div className="afCapacity" aria-label="Kontenjan">
      <div><b>{active}</b><span>Aktif üye</span></div>
      <div><b>{inSetup}</b><span>Hesap / ödeme aşamasında</span></div>
      <div><b>{invitedNow}</b><span>Açık davet</span></div>
      <div><b>{approvedWaiting}</b><span>Onaylı · davet bekliyor</span></div>
      <div className={seats - taken <= 0 ? 'full' : ''}><b>{Math.max(0, seats - taken)}</b><span>Boş koltuk / {seats}</span></div>
    </div>
    {error && <p className="afError" role="alert">{error}</p>}
    {info && <p className="afInfo" role="status">{info}</p>}

    {data?.funnel && <FunnelCard funnel={data.funnel}/>}
    <div className="afSettings">
      <label>Koltuk sayısı<input type="number" min={1} max={200} value={form.seats} onChange={event => setForm({...form, seats: event.target.value})}/></label>
      <label>Lansman tarihi ve saati<input type="datetime-local" value={form.launch} onChange={event => setForm({...form, launch: event.target.value})}/></label>
      <label>Kurucu fiyatı (görünen metin)<input maxLength={40} placeholder="örn. 29 USDT / ay" value={form.price} onChange={event => setForm({...form, price: event.target.value})}/></label>
      <label className="afToggle"><input type="checkbox" checked={form.open} onChange={event => setForm({...form, open: event.target.checked})}/> Başvurular açık</label>
      <label className="afToggle" title="Normalde kapalı kalmalı: yeni hesap yalnız davetle açılır. Açıksa herkes davetsiz hesap açabilir."><input type="checkbox" checked={form.register} onChange={event => { if (event.target.checked && !window.confirm('Davetsiz kaydı herkese açmak istiyor musunuz? Kapalı üyelik devre dışı kalır.')) return; setForm({...form, register: event.target.checked}) }}/> Davetsiz kayıt (acil durum)</label>
      <button type="button" disabled={busy} onClick={() => void saveSettings()}>Ayarları kaydet</button>
    </div>

    {data?.suggestions && data.suggestions.length > 0 && <div className="afSuggest">
      <b>{Math.max(0, seats - taken)} boş koltuk var · bekleme listesinden öneri (en eski başvurular)</b>
      <ul>{data.suggestions.map(item => <li key={item.id}><span><strong>{item.name}</strong><small>{item.email} · {item.country}{item.reference ? ` · ${item.reference}` : ''}</small></span>
        <button type="button" className="ok" disabled={busy} onClick={() => void decide(item, 'approve')}>Onayla</button></li>)}</ul>
      <small>Onaylanan kişiye onay e-postası gider; davet lansmandan 7 gün önce (ya da sen gönderince) gider.</small>
    </div>}
    <details className="afManual"><summary>Başvurusu olmayan birine davet gönder</summary>
      <div><input placeholder="Ad soyad" value={manual.name} onChange={event => setManual({...manual, name: event.target.value})}/>
        <input type="email" placeholder="E-posta" value={manual.email} onChange={event => setManual({...manual, email: event.target.value})}/>
        <input placeholder="Ülke (Türkiye ise Türkçe e-posta)" value={manual.country} onChange={event => setManual({...manual, country: event.target.value})}/>
        <button type="button" className="ok" disabled={busy || manual.name.trim().length < 2 || !/^\S+@\S+\.\S+$/.test(manual.email)} onClick={() => void manualInvite()}>Davet gönder</button></div>
    </details>

    <div className="afChips" role="tablist" aria-label="Durum">
      <button type="button" className={filter === '' ? 'on' : ''} onClick={() => setFilter('')}>Tümü</button>
      {ORDER.map(status => <button type="button" key={status} className={filter === status ? 'on' : ''} onClick={() => setFilter(status)}>{LABEL[status]} <b>{counts[status] ?? 0}</b></button>)}
    </div>

    <input className="afSearch" type="search" placeholder="Ad, e-posta veya başvuru numarası ara" value={search} onChange={event => setSearch(event.target.value)} aria-label="Başvurularda ara"/>
    {busy && !data && <p>Yükleniyor...</p>}
    {data && data.items.length === 0 && <p className="afEmpty">Bu durumda başvuru yok.</p>}
    <ul className="afList">
      {data?.items.filter(item => { const q = search.trim().toLowerCase(); return !q || [item.name, item.email, item.reference ?? ''].some(value => value.toLowerCase().includes(q)) }).map(item => <li key={item.id} className={`afRow ${item.status}`}>
        <div className="afWho"><span className="afHead"><b>{item.name}</b><span className={`afBadge ${item.status}`}>{LABEL[item.status]}{item.approved_seq ? ` #${item.approved_seq}` : ''}</span>{item.reference && <code className="afRef">{item.reference}</code>}</span>
          <small>{item.email} · {item.country} · {item.lang.toUpperCase()} · {new Date(item.created_at).toLocaleString('tr-TR')}</small>
          {(item.futures_experience || item.trading_experience) && <span className="afTags">{item.futures_experience && <em className={item.futures_experience === 'active' ? 'yes' : ''}>Binance Futures: {FUTURES[item.futures_experience] ?? item.futures_experience}</em>}{item.trading_experience && <em className={item.trading_experience === 'gt1y' ? 'yes' : ''}>Trading: {TRADING[item.trading_experience] ?? item.trading_experience}</em>}</span>}
          {item.heard_from && <span className="afTags"><em>Nereden: {HEARD[item.heard_from] ?? item.heard_from}</em>{item.source === 'admin' && <em>Yönetici daveti</em>}</span>}
          {(item.invite_sent_at || item.invite_accepted_at) && <small className="afInvite">{item.invite_accepted_at ? `Davet kullanıldı · ${new Date(item.invite_accepted_at).toLocaleString('tr-TR')}` : item.invite_open && item.invite_expires_at ? `Davet açık · bitiş ${new Date(item.invite_expires_at).toLocaleString('tr-TR')}` : item.invite_sent_at ? `Son davet ${new Date(item.invite_sent_at).toLocaleString('tr-TR')} (kullanılmadı)` : ''}{item.invite_count ? ` · ${item.invite_count}. davet` : ''}</small>}
          {item.price_locked_until && <small className="afInvite">Kurucu fiyatı kilitli · {new Date(item.price_locked_until).toLocaleDateString('tr-TR')} tarihine kadar (üyelik kesintisiz sürerse)</small>}
          {item.reason && <q className="afReason">{item.reason}</q>}</div>
        {DECIDABLE.includes(item.status) && <div className="afActs">
          <input aria-label="Not" placeholder="Not (isteğe bağlı)" maxLength={500} value={notes[item.id] ?? item.note} onChange={event => setNotes({...notes, [item.id]: event.target.value})}/>
          {['new', 'waitlist', 'rejected'].includes(item.status) && <button type="button" className="ok" disabled={busy} onClick={() => void decide(item, 'approve')}>Onayla</button>}
          {['approved', 'invited', 'invite_expired'].includes(item.status) && <button type="button" className="ok" disabled={busy} onClick={() => void invite(item, 'invite')}>{item.invite_count ? 'Daveti yeniden gönder' : 'Davet gönder'}</button>}
          {item.status === 'invited' && <button type="button" disabled={busy} onClick={() => void invite(item, 'extend-invite')}>48 saat uzat</button>}
          {item.status === 'invited' && <button type="button" className="no" disabled={busy} onClick={() => void invite(item, 'revoke-invite')}>Daveti iptal et</button>}
          {item.status !== 'waitlist' && <button type="button" disabled={busy} onClick={() => void decide(item, 'waitlist')}>Bekleme</button>}
          {item.status !== 'rejected' && <button type="button" className="no" disabled={busy} onClick={() => void decide(item, 'reject')}>Reddet</button>}
        </div>}
      </li>)}
    </ul>
  </section>
}

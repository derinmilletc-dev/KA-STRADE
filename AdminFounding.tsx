import {useCallback, useEffect, useState} from 'react'
import {API_BASE} from './api'
import './admin-founding.css'

type Status = 'pending_email' | 'new' | 'approved' | 'waitlist' | 'rejected'
type Item = {id: number; email: string; name: string; country: string; lang: string; status: Status; note: string; approved_seq: number | null; futures_experience: string | null; trading_experience: string | null; reason: string; email_confirmed_at: string | null; decided_at: string | null; created_at: string}
type Settings = {seats: number; applications_open: boolean; launch_at: string | null; price_text: string}
type Payload = {items: Item[]; counts: Partial<Record<Status, number>>; settings: Settings}

const LABEL: Record<Status, string> = {pending_email: 'E-posta bekliyor', new: 'Yeni', approved: 'Onaylı', waitlist: 'Bekleme listesi', rejected: 'Reddedildi'}
const FUTURES: Record<string, string> = {active: 'Aktif kullanıyor', past: 'Daha önce kullandı', never: 'Henüz kullanmadı'}
const TRADING: Record<string, string> = {new: 'Yeni başlıyor', lt6m: '6 aydan az', '6to12m': '6–12 ay', gt1y: '1 yıldan fazla'}
const ORDER: Status[] = ['new', 'approved', 'waitlist', 'rejected', 'pending_email']

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
  const [form, setForm] = useState<{seats: string; open: boolean; launch: string; price: string}>({seats: '30', open: true, launch: '', price: ''})
  const [notes, setNotes] = useState<Record<number, string>>({})

  const load = useCallback(async () => {
    setBusy(true); setError('')
    try {
      const value = await call<Payload>(filter ? `?status=${filter}` : '')
      setData(value)
      setForm({seats: String(value.settings.seats), open: value.settings.applications_open, launch: toLocalInput(value.settings.launch_at), price: value.settings.price_text})
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Yüklenemedi') } finally { setBusy(false) }
  }, [filter])
  useEffect(() => { void load() }, [load])

  const saveSettings = async () => {
    setBusy(true); setError(''); setInfo('')
    try {
      await call('/settings', {method: 'PUT', body: JSON.stringify({seats: Number(form.seats), applications_open: form.open, launch_at: form.launch ? new Date(form.launch).toISOString() : null, price_text: form.price})})
      setInfo('Ayarlar kaydedildi.'); await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Kaydedilemedi'); setBusy(false) }
  }
  const decide = async (item: Item, action: 'approve' | 'waitlist' | 'reject') => {
    if (action === 'reject' && !window.confirm(`${item.name} başvurusunu reddetmek istiyor musunuz?`)) return
    setBusy(true); setError(''); setInfo('')
    try {
      const result = await call<Item & {email_sent: boolean}>(`/${item.id}/${action}`, {method: 'POST', body: JSON.stringify({note: notes[item.id] ?? ''})})
      setInfo(action === 'approve' ? (result.email_sent ? `${item.name} onaylandı; onay e-postası gönderildi.` : `${item.name} onaylandı ama e-posta gönderilemedi; ayrıca yazın.`) : 'Güncellendi.')
      await load()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'İşlem yapılamadı'); setBusy(false) }
  }

  const counts = data?.counts ?? {}
  const taken = counts.approved ?? 0
  return <section className="adminFounding" aria-label="Founding 30 başvuruları">
    <header><h2>Founding 30 başvuruları</h2><p>Kontenjan {taken} / {data?.settings.seats ?? 30} dolu. Başvuranlar e-postalarını onayladıktan sonra “Yeni” olarak görünür.</p></header>
    {error && <p className="afError" role="alert">{error}</p>}
    {info && <p className="afInfo" role="status">{info}</p>}

    <div className="afSettings">
      <label>Koltuk sayısı<input type="number" min={1} max={200} value={form.seats} onChange={event => setForm({...form, seats: event.target.value})}/></label>
      <label>Lansman tarihi ve saati<input type="datetime-local" value={form.launch} onChange={event => setForm({...form, launch: event.target.value})}/></label>
      <label>Kurucu fiyatı (görünen metin)<input maxLength={40} placeholder="örn. 29 USDT / ay" value={form.price} onChange={event => setForm({...form, price: event.target.value})}/></label>
      <label className="afToggle"><input type="checkbox" checked={form.open} onChange={event => setForm({...form, open: event.target.checked})}/> Başvurular açık</label>
      <button type="button" disabled={busy} onClick={() => void saveSettings()}>Ayarları kaydet</button>
    </div>

    <div className="afChips" role="tablist" aria-label="Durum">
      <button type="button" className={filter === '' ? 'on' : ''} onClick={() => setFilter('')}>Tümü</button>
      {ORDER.map(status => <button type="button" key={status} className={filter === status ? 'on' : ''} onClick={() => setFilter(status)}>{LABEL[status]} <b>{counts[status] ?? 0}</b></button>)}
    </div>

    {busy && !data && <p>Yükleniyor...</p>}
    {data && data.items.length === 0 && <p className="afEmpty">Bu durumda başvuru yok.</p>}
    <ul className="afList">
      {data?.items.map(item => <li key={item.id} className={`afRow ${item.status}`}>
        <div className="afWho"><b>{item.name}</b> <span className={`afBadge ${item.status}`}>{LABEL[item.status]}{item.approved_seq ? ` #${item.approved_seq}` : ''}</span>
          <small>{item.email} · {item.country} · {item.lang.toUpperCase()} · {new Date(item.created_at).toLocaleString('tr-TR')}</small>
          {(item.futures_experience || item.trading_experience) && <span className="afTags">{item.futures_experience && <em className={item.futures_experience === 'active' ? 'yes' : ''}>Binance Futures: {FUTURES[item.futures_experience] ?? item.futures_experience}</em>}{item.trading_experience && <em className={item.trading_experience === 'gt1y' ? 'yes' : ''}>Trading: {TRADING[item.trading_experience] ?? item.trading_experience}</em>}</span>}
          {item.reason && <q className="afReason">{item.reason}</q>}</div>
        {item.status !== 'pending_email' && <div className="afActs">
          <input aria-label="Not" placeholder="Not (isteğe bağlı)" maxLength={500} value={notes[item.id] ?? item.note} onChange={event => setNotes({...notes, [item.id]: event.target.value})}/>
          {item.status !== 'approved' && <button type="button" className="ok" disabled={busy} onClick={() => void decide(item, 'approve')}>Onayla</button>}
          {item.status !== 'waitlist' && <button type="button" disabled={busy} onClick={() => void decide(item, 'waitlist')}>Bekleme</button>}
          {item.status !== 'rejected' && <button type="button" className="no" disabled={busy} onClick={() => void decide(item, 'reject')}>Reddet</button>}
        </div>}
      </li>)}
    </ul>
  </section>
}

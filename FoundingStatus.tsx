import {useEffect, useState, type FormEvent} from 'react'
import {ArrowRight, Check, ClipboardList, Clock, Home, Layers, LoaderCircle, Mail, Search, TriangleAlert} from 'lucide-react'
import {API_BASE} from './api'
import './founding-success.css'

type Status = 'pending_email' | 'new' | 'approved' | 'waitlist' | 'rejected' | 'invited' | 'invite_expired' | 'account_created' | 'payment_pending' | 'active'
type View = {reference: string; first_name: string; status: Status; created_at: string | null; email_confirmed_at: string | null; decided_at: string | null}
type Props = {t: (tr: string, en: string) => string}

export const TRACK_HANDOFF = 'fdTrack'
const REF_RE = /^KT-F30-[2-9A-HJ-NP-Z]{5}$/

function parse(body: unknown): View | null {
  if (!body || typeof body !== 'object') return null
  const value = body as Record<string, unknown>
  if (typeof value.reference !== 'string' || typeof value.status !== 'string') return null
  if (!['pending_email', 'new', 'approved', 'waitlist', 'rejected', 'invited', 'invite_expired', 'account_created', 'payment_pending', 'active'].includes(value.status)) return null
  const date = (key: string) => typeof value[key] === 'string' ? value[key] as string : null
  return {reference: value.reference, first_name: typeof value.first_name === 'string' ? value.first_name : '', status: value.status as Status,
    created_at: date('created_at'), email_confirmed_at: date('email_confirmed_at'), decided_at: date('decided_at')}
}

/** Başvuru durumu sayfası (/basvuru-durumu): e-postadaki kişisel bağlantı ya da numara + e-posta ile. */
export default function FoundingStatus({t}: Props) {
  const [view, setView] = useState<View | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [email, setEmail] = useState('')
  const [reference, setReference] = useState('')

  const lookup = async (request: Promise<Response>) => {
    setBusy(true); setError('')
    try {
      const response = await request
      if (response.ok) { const parsed = parse(await response.json()); if (parsed) { setView(parsed); return } }
      setError(response.status === 429
        ? t('Çok fazla deneme yapıldı. Lütfen biraz sonra tekrar dene.', 'Too many attempts. Please try again shortly.')
        : t('Bu bilgilerle bir başvuru bulunamadı. Numarayı ve e-postayı kontrol et.', 'No application found with these details. Check the number and email.'))
    } catch {
      setError(t('Sunucuya ulaşılamadı. Bağlantını kontrol edip tekrar dene.', 'Could not reach the server. Check your connection and try again.'))
    } finally { setBusy(false) }
  }
  const byEmail = (mail: string, ref: string) => lookup(fetch(`${API_BASE}/v22/founding/track`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: mail.trim(), reference: ref.trim().toUpperCase()})}))

  useEffect(() => {
    document.title = t('Başvuru Durumu · KaisTrade Founding 30', 'Application status · KaisTrade Founding 30')
    const params = new URLSearchParams(window.location.search)
    const r = params.get('r') ?? '', k = params.get('k') ?? ''
    if (params.has('r') || params.has('k')) window.history.replaceState(null, '', window.location.pathname)  // imza adres çubuğunda kalmasın
    if (REF_RE.test(r) && /^[0-9a-f]{32}$/.test(k)) { void lookup(fetch(`${API_BASE}/v22/founding/track-link?r=${r}&k=${k}`)); return }
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(TRACK_HANDOFF) ?? 'null') as {email?: string; reference?: string} | null
      window.sessionStorage.removeItem(TRACK_HANDOFF)
      if (saved?.email && saved.reference && REF_RE.test(saved.reference)) { setEmail(saved.email); setReference(saved.reference); void byEmail(saved.email, saved.reference); return }
    } catch { /* depolama yok */ }
    setBusy(false)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = (event: FormEvent) => { event.preventDefault(); if (!busy) void byEmail(email, reference) }
  const formValid = /^\S+@\S+\.\S+$/.test(email.trim()) && REF_RE.test(reference.trim().toUpperCase())
  const when = (iso: string | null) => iso ? new Date(iso).toLocaleDateString(t('tr-TR', 'en-GB'), {day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit'}) : ''

  if (view) {
    const result: Record<Status, [string, string]> = {
      pending_email: [t('E-posta onayı bekliyor', 'Awaiting email confirmation'), 'wait'], new: [t('İnceleme bekliyor', 'Under review'), 'wait'],
      approved: [t('Onaylandı', 'Approved'), 'ok'], waitlist: [t('Bekleme listesi', 'Waitlist'), 'wait'], rejected: [t('Olumsuz', 'Not approved'), 'no'],
      invited: [t('Davet gönderildi', 'Invitation sent'), 'ok'], invite_expired: [t('Davet süresi doldu', 'Invitation expired'), 'no'],
      account_created: [t('Hesap oluşturuldu', 'Account created'), 'ok'], payment_pending: [t('Ödeme bekleniyor', 'Payment pending'), 'wait'],
      active: [t('Aktif üye', 'Active member'), 'ok'],
    }
    const [label, tone] = result[view.status]
    // Sekiz aşama: hangisinin bittiği, hangisinde olunduğu
    const at: Record<Status, number> = {pending_email: 0, new: 1, waitlist: 2, rejected: 2, approved: 3, invited: 3, invite_expired: 3,
      account_created: 5, payment_pending: 6, active: 8}
    const current = at[view.status]
    const stalled = view.status === 'rejected' || view.status === 'invite_expired'
    const STAGES: [string, string, string, JSX.Element][] = [
      [t('Başvuru alındı', 'Application submitted'), when(view.created_at), view.status === 'pending_email' ? t('E-postandaki onay bağlantısına tıkla', 'Click the link in your email') : '', <ClipboardList key="a" aria-hidden="true"/>],
      [t('Başvuru incelemesi', 'Application review'), '', t('Ekibimiz manuel inceliyor', 'Our team is reviewing it'), <Search key="b" aria-hidden="true"/>],
      [t('Üyelik onayı', 'Membership approval'), when(view.decided_at), view.status === 'waitlist' ? t('Bekleme listesindesin', 'You are on the waitlist') : view.status === 'rejected' ? t('Bu dönem kabul edilmedi', 'Not accepted this round') : '', <Check key="c" aria-hidden="true"/>],
      [t('Davet', 'Invitation'), '', view.status === 'approved' ? t('Lansmandan 7 gün önce e-postana gelir', 'Arrives 7 days before launch') : view.status === 'invite_expired' ? t('Süresi doldu · destekle iletişime geç', 'Expired · contact support') : t('Davet e-postandaki bağlantıyla hesabını aç', 'Open your account from the invitation email'), <Mail key="d" aria-hidden="true"/>],
      [t('Hesap kurulumu', 'Account setup'), '', '', <Layers key="e" aria-hidden="true"/>],
      [t('Güvenlik doğrulaması (2FA)', 'Security verification (2FA)'), '', t('Authenticator uygulamanı bağla', 'Connect your authenticator app'), <Clock key="f" aria-hidden="true"/>],
      [t('Ödeme', 'Payment'), '', t('Ödemen doğrulanınca üyelik açılır', 'Membership opens once payment is verified'), <Clock key="g" aria-hidden="true"/>],
      [t('Üyelik aktif', 'Membership activated'), '', '', <Check key="h" aria-hidden="true"/>],
    ]
    const steps = STAGES.map(([title, date, hint, icon], index) => {
      const done = index < current && !(stalled && index === current)
      const isCurrent = index === current
      return {title, icon, done, current: isCurrent && !stalled, failed: isCurrent && stalled,
        sub: done ? (date || t('Tamamlandı', 'Completed')) : isCurrent ? (hint || t('Devam ediyor', 'In progress')) : t('Bekliyor', 'Pending')}
    })
    return <main className="fdSuccess fdStatus" aria-labelledby="fd-status-title">
      <div className="fdSuccessInner">
        <div className={`fdStatusMark ${tone}`} aria-hidden="true">{tone === 'ok' ? <Check/> : tone === 'no' ? <TriangleAlert/> : <Clock/>}</div>
        <h1 id="fd-status-title">{view.first_name ? t(`Merhaba ${view.first_name}`, `Hi ${view.first_name}`) : t('Başvuru Durumu', 'Application status')}</h1>
        <p className="fdSuccessLead">{t('Başvurundan aktif üyeliğe kadar tüm aşamalar aşağıda.', 'Every step from application to active membership.')}</p>
        <dl className="fdSuccessCard">
          <div><dt><ClipboardList aria-hidden="true"/>{t('Başvuru Numarası', 'Application number')}</dt><dd className="mono">{view.reference}</dd></div>
          <div><dt><Layers aria-hidden="true"/>{t('Program', 'Program')}</dt><dd>Founding 30</dd></div>
          <div><dt><Clock aria-hidden="true"/>{t('Durum', 'Status')}</dt><dd><span className={`fdPill ${tone}`}>{label}</span></dd></div>
        </dl>
        <ol className="fdTimeline">{steps.map(step => <li key={step.title} className={step.done ? 'done' : step.current ? 'current' : step.failed ? 'failed' : ''}>
          <i>{step.done ? <Check aria-hidden="true"/> : step.icon}</i><span><strong>{step.title}</strong><small>{step.sub}</small></span></li>)}</ol>
        <div className="fdSuccessActions">
          <a className="fdPrimary" href="/"><Home aria-hidden="true"/>{t('Ana Sayfaya Dön', 'Back to home')}</a>
          <button type="button" className="fdGhost" onClick={() => { setView(null); setError('') }}><Search aria-hidden="true"/>{t('Başka bir başvuru sorgula', 'Look up another application')}</button>
        </div>
        <p className="fdSuccessFoot">{t('Durum değiştiğinde sana ayrıca e-posta gönderiyoruz.', 'We also email you whenever your status changes.')}</p>
      </div>
    </main>
  }

  return <main className="fdSuccess fdStatus" aria-labelledby="fd-status-title">
    <div className="fdSuccessInner">
      <div className="fdStatusMark wait" aria-hidden="true">{busy ? <LoaderCircle className="spin"/> : <Search/>}</div>
      <h1 id="fd-status-title">{t('Başvurunu Sorgula', 'Track your application')}</h1>
      <p className="fdSuccessLead">{t('Başvuru numaranı ve başvururken kullandığın e-postayı gir. Numara, başvuru e-postanda yazıyor.', 'Enter your application number and the email you applied with. The number is in your application email.')}</p>
      <form className="fdForm fdStatusForm" onSubmit={submit} noValidate>
        <input aria-label={t('Başvuru numarası', 'Application number')} placeholder="KT-F30-XXXXX" autoCapitalize="characters" autoComplete="off" spellCheck={false} maxLength={12}
          value={reference} onChange={event => setReference(event.target.value.toUpperCase())}/>
        <input type="email" aria-label={t('E-posta adresi', 'Email address')} placeholder={t('E-posta Adresi', 'Email address')} autoComplete="email" value={email} onChange={event => setEmail(event.target.value)}/>
        {error && <p className="fdError" role="alert"><TriangleAlert aria-hidden="true"/>{error}</p>}
        <button className="fdPrimary fdSubmit" type="submit" disabled={!formValid || busy}>{busy ? t('Sorgulanıyor…', 'Checking…') : t('Durumu Göster', 'Show status')}<ArrowRight aria-hidden="true"/></button>
      </form>
      <a className="fdStatusBack" href="/">{t('Ana sayfaya dön', 'Back to home')}</a>
    </div>
  </main>
}

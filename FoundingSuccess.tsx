import {useState} from 'react'
import {ClipboardList, Clock, FileText, Home, Info, Layers, ListChecks, LoaderCircle, Mail, Search, Tag} from 'lucide-react'
import {TRACK_HANDOFF} from './FoundingStatus'
import './founding-success.css'

type Status = 'pending_email' | 'new' | 'approved' | 'waitlist' | 'rejected'
type Props = {reference: string | null; email: string; t: (tr: string, en: string) => string; onHome: () => void}

function Laurel({flip}: {flip?: boolean}) {
  return <svg className={`fdLaurel ${flip ? 'flip' : ''}`} viewBox="0 0 24 40" aria-hidden="true">
    <path d="M17 38C8 31 6 20 10 6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
    {[[11, 9, -30], [9.5, 15, -20], [9, 21, -8], [10, 27, 6], [12.5, 32, 20]].map(([x, y, r], i) => <g key={i}>
      <ellipse cx={x - 3.2} cy={y} rx="3.4" ry="1.6" transform={`rotate(${r - 35} ${x - 3.2} ${y})`} fill="currentColor"/>
      <ellipse cx={x + 3} cy={y - 1.2} rx="3.2" ry="1.5" transform={`rotate(${r + 40} ${x + 3} ${y - 1.2})`} fill="currentColor" opacity=".85"/>
    </g>)}
  </svg>
}

/** Başvuru sonrası ekran: tek ekrana sığar, durumu canlı sorgulayabilir. */
export default function FoundingSuccess({reference, email, t, onHome}: Props) {
  const status: Status = 'pending_email'
  const [checking, setChecking] = useState(false)

  const LABEL: Record<Status, [string, string]> = {
    pending_email: [t('E-posta onayı bekliyor', 'Awaiting email confirmation'), 'wait'],
    new: [t('İnceleme bekliyor', 'Awaiting review'), 'wait'],
    approved: [t('Onaylandı', 'Approved'), 'ok'],
    waitlist: [t('Bekleme listesinde', 'Waitlisted'), 'wait'],
    rejected: [t('Olumsuz sonuçlandı', 'Not approved'), 'no'],
  }

  const check = () => {
    if (!reference || checking) return
    setChecking(true)
    try { window.sessionStorage.setItem(TRACK_HANDOFF, JSON.stringify({email, reference})) } catch { /* depolama yok: sayfada elle girilir */ }
    window.location.assign('/basvuru-durumu')
  }

  const [statusText, tone] = LABEL[status]
  return <main className="fdSuccess" aria-labelledby="fd-success-title">
    <div className="fdSuccessWaves" aria-hidden="true">
      <svg viewBox="0 0 400 120" preserveAspectRatio="none"><path d="M0 80 C 80 40, 160 120, 240 70 S 360 40, 400 64" /><path d="M0 96 C 90 60, 170 128, 250 86 S 350 58, 400 82" /><path d="M0 110 C 100 84, 190 132, 260 102 S 360 80, 400 98" /></svg>
    </div>
    <div className="fdSuccessInner">
      <div className="fdSuccessMark" aria-hidden="true"><span className="fdSuccessRing"/><svg viewBox="0 0 52 52"><path d="M15 27 l8 8 l15 -17"/></svg></div>
      <h1 id="fd-success-title">{t('Başvurun Alındı!', 'Application received!')}</h1>
      <p className="fdSuccessProgram"><Laurel/><span>FOUNDING 30</span><Laurel flip/></p>
      <p className="fdSuccessLead">{t('KaisTrade’e gösterdiğin ilgi için teşekkür ederiz. Başvurun başarıyla sistemimize kaydedildi.', 'Thank you for your interest in KaisTrade. Your application has been recorded.')}</p>

      <dl className="fdSuccessCard">
        {reference && <div><dt><ClipboardList aria-hidden="true"/>{t('Başvuru Numarası', 'Application number')}</dt><dd className="mono">{reference}</dd></div>}
        <div><dt><Layers aria-hidden="true"/>{t('Program', 'Program')}</dt><dd>Founding 30</dd></div>
        <div><dt><Tag aria-hidden="true"/>{t('Başvuru Ücreti', 'Application fee')}</dt><dd className="free">{t('Ücretsiz', 'Free')}</dd></div>
        <div><dt><Clock aria-hidden="true"/>{t('Başvuru Durumu', 'Status')}</dt><dd><span className={`fdPill ${tone}`}>{statusText}</span></dd></div>
      </dl>

      <h2>{t('Bundan Sonra Ne Olacak?', 'What happens next?')}</h2>
      <ol className="fdNext">
        <li><i><FileText aria-hidden="true"/><b>1</b></i><span><strong>{t('Başvurun Alındı', 'Application received')}</strong><small>{t('Bilgilerin güvenli şekilde kaydedildi.', 'Your details are stored securely.')}</small></span></li>
        <li><i><Search aria-hidden="true"/><b>2</b></i><span><strong>{t('Ekibimiz İnceleyecek', 'We review it')}</strong><small>{t('Başvurun manuel olarak değerlendirilecek.', 'Your application is reviewed manually.')}</small></span></li>
        <li><i><Mail aria-hidden="true"/><b>3</b></i><span><strong>{t('Sonuç Bildirimi', 'You hear back')}</strong><small>{t('Sonuç e-posta ile sana iletilecek.', 'We email you the result.')}</small></span></li>
      </ol>

      <div className="fdMailCard">
        <i><Mail aria-hidden="true"/></i>
        <span><strong>{t('E-postanı Kontrol Et', 'Check your inbox')}</strong><small>{t(`Başvurunu tamamlamak için ${email} adresine gönderdiğimiz onay bağlantısına tıkla.`, `To complete your application, click the confirmation link we sent to ${email}.`)}</small></span>
        <svg className="fdEnvelope" viewBox="0 0 120 84" aria-hidden="true"><defs><linearGradient id="fdEnv" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#1d5a43"/><stop offset="1" stopColor="#0a2219"/></linearGradient></defs>
          <g transform="rotate(-12 60 42)"><rect x="12" y="16" width="96" height="56" rx="6" fill="url(#fdEnv)" stroke="#2ee59d" strokeOpacity=".7"/><path d="M14 20 L60 50 L106 20" fill="none" stroke="#2ee59d" strokeWidth="1.6"/><path d="M14 70 L48 44 M106 70 L72 44" stroke="#2ee59d" strokeOpacity=".45"/></g></svg>
      </div>

      <div className="fdSuccessActions">
        <button type="button" className="fdPrimary" onClick={onHome}><Home aria-hidden="true"/>{t('Ana Sayfaya Dön', 'Back to home')}</button>
        {reference && <button type="button" className="fdGhost" onClick={check} disabled={checking}>{checking ? <LoaderCircle className="spin" aria-hidden="true"/> : <ListChecks aria-hidden="true"/>}{t('Başvuru Durumunu Takip Et', 'Track application status')}</button>}
      </div>
      <p className="fdSuccessFoot"><Info aria-hidden="true"/>{t('Başvuru ücretsizdir. Bu aşamada herhangi bir ödeme alınmaz ve üyelik başlatılmaz.', 'Applying is free. No payment is taken and no membership starts at this stage.')}</p>
    </div>
  </main>
}

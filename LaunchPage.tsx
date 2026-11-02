import {useEffect, useRef, useState, type FormEvent} from 'react'
import {ArrowRight, BarChart3, Check, TriangleAlert, Eye, Gauge, Lock, ShieldCheck, Sparkles, Users} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang, setLang} from './i18n'
import LanguagePicker from './LanguagePicker'
import './launch-page.css'

/** Founding 30 – Erken Erişim lansman sayfası. Giriş ekranı dondurulduğunda herkese açık ana sayfadır. */
const t = (tr: string, en: string) => (currentLang() === 'en' ? en : tr)
const PRICE = String(import.meta.env.VITE_FOUNDING_PRICE ?? '').trim()
const SEATS = 30

type Form = {email: string; name: string; country: string; experience: string; capital: string; reason: string; feedback: boolean; price: boolean; consent: boolean; website: string}
const EMPTY: Form = {email: '', name: '', country: '', experience: '', capital: '', reason: '', feedback: true, price: false, consent: false, website: ''}
type Phase = 'idle' | 'busy' | 'done' | 'error'

const EXPERIENCE = (): [string, string][] => [['new', t('Yeni başlıyorum', 'Just starting')], ['1y', t('1 yıldan az', 'Under 1 year')], ['1-3y', t('1–3 yıl', '1–3 years')], ['3y+', t('3 yıldan fazla', 'Over 3 years')]]
const CAPITAL = (): [string, string][] => [['<500', t('500 USD altı', 'Under 500 USD')], ['500-2k', '500 – 2.000 USD'], ['2k-10k', '2.000 – 10.000 USD'], ['10k+', t('10.000 USD üstü', 'Over 10,000 USD')]]

export default function LaunchPage() {
  const [, force] = useState(0)
  const [form, setForm] = useState<Form>(EMPTY)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const formRef = useRef<HTMLElement>(null)
  useEffect(() => { document.title = t('KaisTrade · Founding 30 Erken Erişim', 'KaisTrade · Founding 30 Early Access') }, [])
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm(current => ({...current, [key]: value}))
  const changeLang = (lang: 'tr' | 'en') => { setLang(lang); force(n => n + 1) }
  const goForm = () => formRef.current?.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'})

  const valid = /^\S+@\S+\.\S+$/.test(form.email) && form.name.trim().length >= 2 && form.country.trim().length >= 2 && !!form.experience && !!form.capital && form.reason.trim().length >= 10 && form.consent && form.price
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!valid || phase === 'busy') return
    setPhase('busy'); setMessage('')
    try {
      const response = await fetch(`${API_BASE}/v22/founding/apply`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...form, lang: currentLang()})})
      if (response.ok) { setPhase('done'); return }
      let detail = ''
      try { const body = await response.json(); detail = typeof body?.detail === 'string' ? body.detail : '' } catch { /* gövde yok */ }
      setPhase('error'); setMessage(detail || t('Başvuru şu anda alınamadı. Lütfen biraz sonra tekrar deneyin.', 'We could not take your application right now. Please try again shortly.'))
    } catch {
      setPhase('error'); setMessage(t('Sunucuya ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.', 'Could not reach the server. Check your connection and try again.'))
    }
  }

  return <div className="fdRoot" data-i18n-skip>
    <header className="fdTop">
      <a className="fdBrand" href="/" aria-label="KaisTrade"><img src="/kaistrade-logo.png" alt="KaisTrade"/></a>
      <div className="fdTopRight"><LanguagePicker value={currentLang()} onChange={changeLang}/><button type="button" className="fdTopCta" onClick={goForm}>{t('Başvur', 'Apply')}</button></div>
    </header>

    <main>
      <section className="fdHero">
        <div className="fdHeroCopy">
          <span className="fdBadge"><Sparkles aria-hidden="true"/>{t('FOUNDING 30 · ERKEN ERİŞİM', 'FOUNDING 30 · EARLY ACCESS')}</span>
          <h1>{t('Vadeli işlemde profesyonel kontrol.', 'Professional control for futures trading.')}<span>{t('İlk 30 kurucudan biri ol.', 'Be one of the first 30 founders.')}</span></h1>
          <p>{t('Analiz, risk kilidi ve otomatik grid işlem tek çalışma alanında. Lansmandan önce yalnızca 30 kişiyi elle seçiyoruz.', 'Analysis, risk locks and automated grid trading in one workspace. Before launch we hand-pick only 30 people.')}</p>
          <div className="fdActions"><button type="button" className="fdPrimary" onClick={goForm}>{t('Ücretsiz başvur', 'Apply for free')}<ArrowRight aria-hidden="true"/></button><a className="fdGhost" href="#nasil">{t('Nasıl çalışır?', 'How it works')}</a></div>
          <ul className="fdFacts"><li><Check aria-hidden="true"/>{t('Başvuru ücretsiz, ödeme yok', 'Free to apply, no payment')}</li><li><Check aria-hidden="true"/>{t('Her başvuru elle incelenir', 'Every application is reviewed by hand')}</li><li><Check aria-hidden="true"/>{t('12 ay sabit fiyat', '12-month locked price')}</li></ul>
        </div>
        <aside className="fdSeatCard" aria-label={t('Kurucu koltukları', 'Founder seats')}>
          <header><b>{t('Kurucu koltukları', 'Founder seats')}</b><span>{SEATS}</span></header>
          <div className="fdSeats" aria-hidden="true">{Array.from({length: SEATS}, (_, index) => <i key={index}/>)}</div>
          <p>{t('Koltuklar elle onaylanır. Kontenjan dolunca başvurular sıralı bekleme listesine alınır.', 'Seats are approved by hand. When full, applications join an ordered waitlist.')}</p>
          <dl>
            <div><dt>{t('Karar süresi', 'Decision time')}</dt><dd>{t('72 saat içinde', 'Within 72 hours')}</dd></div>
            <div><dt>{t('Lansmanda', 'At launch')}</dt><dd>{t('48 saat içinde aktive et', 'Activate within 48 hours')}</dd></div>
          </dl>
        </aside>
      </section>

      <section className="fdTrust" aria-label={t('Güven', 'Trust')}>
        <div><Lock aria-hidden="true"/><b>{t('Paran sende kalır', 'Your funds stay with you')}</b><span>{t('Para çekme yetkisi olmayan API anahtarıyla çalışırız.', 'We work with API keys that have no withdrawal permission.')}</span></div>
        <div><ShieldCheck aria-hidden="true"/><b>{t('Risk kilitleri açık', 'Risk locks included')}</b><span>{t('Zaman sınırlı izin, 2FA ve acil durdurma.', 'Time-limited permission, 2FA and emergency stop.')}</span></div>
        <div><Eye aria-hidden="true"/><b>{t('Dürüst anlatım', 'Honest messaging')}</b><span>{t('Kâr garantisi vermeyiz; riskleri açıkça yazarız.', 'We never promise profit and state risks plainly.')}</span></div>
      </section>

      <section className="fdSection">
        <h2>{t('Ne alıyorsun?', 'What do you get?')}</h2>
        <div className="fdCards">
          <article><BarChart3 aria-hidden="true"/><h3>{t('Analiz Stüdyosu', 'Analysis Studio')}</h3><p>{t('Grafik, seviyeler ve hızlı emir hazırlığı tek ekranda.', 'Charts, levels and quick order preparation on one screen.')}</p></article>
          <article><Gauge aria-hidden="true"/><h3>{t('Grid + koruma', 'Grid + protection')}</h3><p>{t('Kâr kilidi, likidasyon izleme ve günlük zarar limiti.', 'Profit lock, liquidation monitoring and a daily loss limit.')}</p></article>
          <article><ShieldCheck aria-hidden="true"/><h3>{t('Risk izni', 'Risk permission')}</h3><p>{t('Gerçek işlem için süreli izin; bitmeden uyarı alırsın.', 'Time-limited permission for live trading, with warnings before it ends.')}</p></article>
          <article><Users aria-hidden="true"/><h3>{t('Kurucu ayrıcalığı', 'Founder perks')}</h3><p>{t('Doğrudan kanal, yol haritasına söz ve kurucu rozeti.', 'A direct channel, a say in the roadmap and a founder badge.')}</p></article>
        </div>
      </section>

      <section className="fdSection" id="nasil">
        <h2>{t('Nasıl çalışır?', 'How it works')}</h2>
        <ol className="fdSteps">
          <li><b>1</b><h3>{t('Başvur', 'Apply')}</h3><p>{t('Kısa formu doldur. Ücret ve ön ödeme yok.', 'Fill in the short form. No fee, no deposit.')}</p></li>
          <li><b>2</b><h3>{t('İnceleme', 'Review')}</h3><p>{t('72 saat içinde elle değerlendirip dönüş yaparız.', 'We review by hand and reply within 72 hours.')}</p></li>
          <li><b>3</b><h3>{t('Teyit', 'Confirm')}</h3><p>{t('Onaylanırsan lansmandan önce koltuğunu teyit edersin.', 'If approved, you confirm your seat before launch.')}</p></li>
          <li><b>4</b><h3>{t('Aktive et', 'Activate')}</h3><p>{t('Lansman günü 48 saat içinde ödeme yapınca üyeliğin açılır.', 'On launch day, pay within 48 hours and your membership opens.')}</p></li>
        </ol>
      </section>

      <section className="fdSection fdPrice">
        <div><span className="fdEyebrow">{t('KURUCU FİYATI', 'FOUNDER PRICE')}</span><strong>{PRICE || t('Yakında', 'Soon')}</strong><p>{PRICE ? t('12 ay boyunca sabit. Aboneliğin kesintisiz sürdüğü sürece geçerli.', 'Locked for 12 months while your subscription stays active.') : t('Kurucu fiyatı başvurudan önce bu sayfada açıklanır.', 'The founder price is published on this page before you apply.')}</p></div>
        <ul><li><Check aria-hidden="true"/>{t('12 ay fiyat garantisi', '12-month price guarantee')}</li><li><Check aria-hidden="true"/>{t('İstediğin zaman iptal', 'Cancel any time')}</li><li><Check aria-hidden="true"/>{t('Ömür boyu vaadi yok, net şartlar var', 'No lifetime promises, just clear terms')}</li></ul>
      </section>

      <section className="fdSection fdFormWrap" ref={formRef} id="basvuru">
        <h2>{t('Başvuru', 'Application')}</h2>
        {phase === 'done' ? <div className="fdDone" role="status"><Check aria-hidden="true"/><h3>{t('Başvurun alındı', 'Application received')}</h3><p>{t('E-postanı kontrol et ve başvurunu onayla. 72 saat içinde sana dönüş yapacağım.', 'Check your email and confirm your application. I will get back to you within 72 hours.')}</p></div>
        : <form className="fdForm" onSubmit={submit} noValidate>
          <div className="fdGrid">
            <label>{t('E-posta', 'Email')}<input type="email" autoComplete="email" value={form.email} onChange={event => set('email', event.target.value)} required/></label>
            <label>{t('Ad soyad', 'Full name')}<input autoComplete="name" value={form.name} onChange={event => set('name', event.target.value)} required/></label>
            <label>{t('Ülke', 'Country')}<input autoComplete="country-name" value={form.country} onChange={event => set('country', event.target.value)} required/></label>
            <label>{t('Binance Futures deneyimin', 'Binance Futures experience')}<select value={form.experience} onChange={event => set('experience', event.target.value)} required><option value="">{t('Seç', 'Select')}</option>{EXPERIENCE().map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>{t('Yaklaşık sermaye aralığı', 'Approximate capital range')}<select value={form.capital} onChange={event => set('capital', event.target.value)} required><option value="">{t('Seç', 'Select')}</option>{CAPITAL().map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label className="fdWide">{t('Neden KaisTrade?', 'Why KaisTrade?')}<textarea rows={3} maxLength={600} value={form.reason} onChange={event => set('reason', event.target.value)} required/></label>
          </div>
          <input className="fdHoney" tabIndex={-1} autoComplete="off" aria-hidden="true" value={form.website} onChange={event => set('website', event.target.value)}/>
          <label className="fdCheck"><input type="checkbox" checked={form.feedback} onChange={event => set('feedback', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{t('Geri bildirim veririm', 'I will give feedback')}</b><small>{t('Kurucular ürünü birlikte şekillendirir.', 'Founders help shape the product.')}</small></div></label>
          <label className="fdCheck"><input type="checkbox" checked={form.price} onChange={event => set('price', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{PRICE ? t(`Kurucu fiyatını (${PRICE}) kabul ediyorum`, `I accept the founder price (${PRICE})`) : t('Kurucu fiyatının onayda bildirileceğini anlıyorum', 'I understand the founder price is shared on approval')}</b><small>{t('Onaylanırsam lansmanda 48 saat içinde ödeme yapmam gerektiğini biliyorum.', 'If approved I must pay within 48 hours at launch.')}</small></div></label>
          <label className="fdCheck"><input type="checkbox" checked={form.consent} onChange={event => set('consent', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{t('Gizlilik Politikası’nı okudum', 'I have read the Privacy Policy')}</b><small>{t('Verilerim yalnızca bu başvuru için kullanılır.', 'My data is used only for this application.')} <a href="/privacy">{t('Oku', 'Read')}</a></small></div></label>
          {phase === 'error' && <p className="fdError" role="alert"><TriangleAlert aria-hidden="true"/>{message}</p>}
          <button className="fdPrimary fdSubmit" type="submit" disabled={!valid || phase === 'busy'}>{phase === 'busy' ? t('Gönderiliyor…', 'Sending…') : t('Başvuruyu gönder', 'Submit application')}<ArrowRight aria-hidden="true"/></button>
          <p className="fdFine">{t('Başvuru ücretsizdir. Hiçbir kart bilgisi istenmez.', 'Applying is free. No card details are requested.')}</p>
        </form>}
      </section>

      <section className="fdSection fdFaq">
        <h2>{t('Sık sorulanlar', 'FAQ')}</h2>
        <details><summary>{t('Şimdi ödeme yapıyor muyum?', 'Do I pay now?')}</summary><p>{t('Hayır. Başvuru ücretsiz. Onaylanırsan ödemeyi yalnızca lansman gününde, 48 saatlik pencerede yaparsın.', 'No. Applying is free. If approved you pay only on launch day within a 48-hour window.')}</p></details>
        <details><summary>{t('Kâr garantisi var mı?', 'Is profit guaranteed?')}</summary><p>{t('Hayır. Vadeli işlemler yüksek risk taşır ve sermayenin tamamını kaybedebilirsin. Geçmiş sonuçlar geleceği garanti etmez.', 'No. Futures trading is high risk and you can lose all of your capital. Past results do not guarantee future results.')}</p></details>
        <details><summary>{t('Paramı tutuyor musunuz?', 'Do you hold my funds?')}</summary><p>{t('Hayır. Paran Binance hesabında kalır. Yalnızca işlem yetkili, para çekme yetkisiz API anahtarı kullanırız.', 'No. Your funds stay on your Binance account. We only use trade-enabled API keys without withdrawal permission.')}</p></details>
        <details><summary>{t('Lansman ne zaman?', 'When is the launch?')}</summary><p>{t('Tarih belli olunca onaylı kurucuları önce e-postayla bilgilendiririz.', 'Once the date is set, approved founders hear first by email.')}</p></details>
      </section>
    </main>

    <footer className="fdFoot">
      <p>{t('Kripto vadeli işlemler yüksek risk içerir. Kâr garantisi verilmez; bu sayfa yatırım tavsiyesi değildir.', 'Crypto futures carry high risk. No profit is guaranteed; this page is not investment advice.')}</p>
      <nav><a href="/privacy">{t('Gizlilik', 'Privacy')}</a><a href="/terms">{t('Şartlar', 'Terms')}</a><a href="/risk">{t('Risk uyarısı', 'Risk notice')}</a></nav>
      <small>© 2026 KaisTrade</small>
    </footer>
  </div>
}

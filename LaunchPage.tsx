import {useEffect, useMemo, useRef, useState, type FormEvent} from 'react'
import {ArrowRight, Users, BarChart3, Check, Crown, Eye, Gauge, Headphones, Lock, Rocket, Search, ShieldCheck, Tag, TriangleAlert, X, Zap} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang} from './i18n'
import KaisEye from './KaisEye'
import CountryPicker, {countryName, guessCountry} from './CountryPicker'
import './launch-page.css'

/** Founding 30 – Erken Erişim lansman sayfası. Giriş ekranı dondurulduğunda herkese açık ana sayfadır. */
const t = (tr: string, en: string) => (currentLang() === 'en' ? en : tr)
const PRICE = String(import.meta.env.VITE_FOUNDING_PRICE ?? '').trim()
const SEATS = 30
/** Gerçek onaylı koltuk sayısı; boşsa sayaç çubuğu gösterilmez (sahte doluluk yok). */
const TAKEN = Math.max(0, Math.min(SEATS, Number(import.meta.env.VITE_FOUNDING_TAKEN ?? '')) || 0)
const EYE_PHOTO = '/founding-eye.webp'

type Form = {email: string; name: string; country: string; experience: string; capital: string; reason: string; feedback: boolean; price: boolean; consent: boolean; website: string}
const EMPTY: Form = {email: '', name: '', country: '', experience: '', capital: '', reason: '', feedback: true, price: false, consent: false, website: ''}
type Phase = 'idle' | 'busy' | 'done' | 'error'


const FAQ = (): [string, string][] => [
  [t('Başvuru ücretsiz mi?', 'Is applying free?'), t('Evet. Başvuru ücretsizdir ve kart bilgisi istenmez. Onaylanırsan ödemeyi yalnızca lansman gününde, 48 saatlik pencerede yaparsın.', 'Yes. Applying is free and no card details are requested. If approved you pay only on launch day within a 48-hour window.')],
  [t('Başvurum ne zaman onaylanır?', 'When is my application approved?'), t('Her başvuru elle incelenir; 72 saat içinde e-postayla dönüş yaparız. Onay sonrası kısa bir tanışma anketi göndeririz.', 'Every application is reviewed by hand; we reply by email within 72 hours. After approval we send a short intro survey.')],
  [t('Ödeme ne zaman yapılır?', 'When do I pay?'), t('Yalnızca lansman gününde, onaylı kurucular için açılan 48 saatlik pencerede. Süre dolarsa koltuk sıradaki adaya geçer.', 'Only on launch day, in a 48-hour window for approved founders. If it expires the seat moves to the next applicant.')],
  [t('12 aylık fiyat garantisi nasıl çalışır?', 'How does the 12-month price lock work?'), t('Aboneliğin kesintisiz sürdüğü sürece seçtiğin plan için aynı fiyatı 12 ay ödersin. İptal edersen kilit biter. Ömür boyu vaadi yoktur.', 'While your subscription stays active you pay the same price for your plan for 12 months. Cancelling ends the lock. No lifetime promise.')],
  [t('KaisTrade kâr garantisi veriyor mu?', 'Does KaisTrade guarantee profit?'), t('Hayır. Vadeli işlemler yüksek risk taşır ve sermayenin tamamını kaybedebilirsin. Geçmiş sonuçlar geleceği garanti etmez.', 'No. Futures trading is high risk and you can lose all of your capital. Past results do not guarantee future results.')],
  [t('Binance API güvenliği nasıl sağlanıyor?', 'How is Binance API security handled?'), t('Paran Binance hesabında kalır. Yalnızca işlem yetkili, para çekme yetkisiz API anahtarı kullanırız.', 'Your funds stay on your Binance account. We only use trade-enabled API keys without withdrawal permission.')],
]

export default function LaunchPage() {
  const [form, setForm] = useState<Form>(EMPTY)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const formRef = useRef<HTMLElement>(null)
  const [photo, setPhoto] = useState(false)
  const [sheet, setSheet] = useState(false)
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches)
  useEffect(() => { const q = window.matchMedia('(max-width: 700px)'); const on = () => setMobile(q.matches); q.addEventListener('change', on); return () => q.removeEventListener('change', on) }, [])
  useEffect(() => { const img = new Image(); img.onload = () => setPhoto(true); img.src = EYE_PHOTO }, [])
  useEffect(() => { document.title = t('KaisTrade · Founding 30 Erken Erişim', 'KaisTrade · Founding 30 Early Access') }, [])
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm(current => ({...current, [key]: value}))
  const guess = useMemo(guessCountry, [])
  useEffect(() => { if (guess) setForm(current => current.country ? current : {...current, country: guess}) }, [guess])
  const goForm = () => formRef.current?.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'})

  const valid = /^\S+@\S+\.\S+$/.test(form.email) && form.name.trim().length >= 2 && form.country.length === 2
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!valid || phase === 'busy') return
    setPhase('busy'); setMessage('')
    try {
      const response = await fetch(`${API_BASE}/v22/founding/apply`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: form.email, name: form.name, country: countryName(form.country, 'en'), website: form.website, feedback: true, price: true, consent: true, lang: currentLang()})})
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
      <a className="fdBrand" href="/" aria-label="KaisTrade"><img src="/kaistrade-logo-trim.png" alt="KaisTrade"/></a>
      <nav className="fdNav" aria-label={t('Sayfa bölümleri', 'Page sections')}><a href="#ozellikler">{t('Özellikler', 'Features')}</a><a href="#basvuru">Founding 30</a><a href="#sss">{t('Sıkça Sorulan Sorular', 'FAQ')}</a></nav>
      <div className="fdTopRight"><button type="button" className="fdTopCta" onClick={goForm}>{t('Başvur', 'Apply')}</button></div>
    </header>

    <main>
      <section className="fdHero">
        <div className="fdHeroCopy">
          <span className="fdBadge">EARLY ACCESS</span>
          <h1>Founding <b>30</b></h1>
          <h2>{t('İlk 30 kurucu üyeden biri ol.', 'Be one of the first 30 founding members.')}</h2>
          <p>{t('Lansmana özel 12 ay sabit üyelik fiyatı avantajıyla KaisTrade’e erken katıl.', 'Join KaisTrade early with a 12-month locked membership price.')}</p>
          <div className="fdActions">
            <button type="button" className="fdPrimary" onClick={goForm}>{t('Ücretsiz Başvur', 'Apply for free')}<ArrowRight aria-hidden="true"/></button>
            <div className="fdQuota"><Users aria-hidden="true"/><span><small>{t('Kontenjan', 'Quota')}</small><strong>{TAKEN ? <>{String(TAKEN).padStart(2, '0')} / {SEATS} <em>{t('Dolu', 'Taken')}</em></> : <>{SEATS} <em>{t('koltuk · elle onay', 'seats · hand-picked')}</em></>}</strong></span>{TAKEN > 0 && <i className="fdMeter" aria-hidden="true"><b style={{width: `${TAKEN / SEATS * 100}%`}}/></i>}</div>
          </div>
        </div>
        <div className="fdEyeStage" aria-hidden="true">
          <i className="fdBar fdBar1"/><i className="fdBar fdBar2"/><i className="fdBar fdBar3"/><i className="fdBar fdBar4"/><i className="fdBar fdBar5"/>
          <span className="fdHalo"/><span className="fdRing"/>
          {photo ? <img className="fdEyePhoto" src={EYE_PHOTO} alt=""/> : <div className="fdEye"><KaisEye size={300} state="idle" aria-label="KaisTrade"/></div>}
        </div>
      </section>

      <section className="fdStrip" id="ozellikler" aria-label={t('Kurucu avantajları', 'Founder perks')}>
        <div><ShieldCheck aria-hidden="true"/><span><b>{t('12 Ay', '12-Month')}</b>{t('Sabit Fiyat', 'Locked Price')}</span></div>
        <div><Crown aria-hidden="true"/><span><b>{t('Kurucu', 'Founder')}</b>{t('Üye Rozeti', 'Member Badge')}</span></div>
        <div><Zap aria-hidden="true"/><span><b>{t('Erken', 'Early')}</b>{t('Erişim', 'Access')}</span></div>
        <div><Headphones aria-hidden="true"/><span><b>{t('Öncelikli', 'Priority')}</b>{t('Destek', 'Support')}</span></div>
      </section>

      <div className="fdLayout">
        <div className="fdColA">
          <section className="fdBlock">
            <h2>{t('Nasıl Çalışır?', 'How it works')}</h2>
            <ol className="fdSteps">
              <li><i><BarChart3 aria-hidden="true"/></i><b>1. {t('Başvur', 'Apply')}</b><p>{t('Kısa formu doldur. Ücret ve ön ödeme yok.', 'Fill in the short form. No fee, no deposit.')}</p></li>
              <li><i><Search aria-hidden="true"/></i><b>2. {t('İncele', 'Review')}</b><p>{t('72 saat içinde elle değerlendirip dönüş yaparız.', 'We review by hand and reply within 72 hours.')}</p></li>
              <li><i><Check aria-hidden="true"/></i><b>3. {t('Onay', 'Approval')}</b><p>{t('Uygun görülürsen koltuğunu teyit edersin.', 'If approved, you confirm your seat.')}</p></li>
              <li><i><Rocket aria-hidden="true"/></i><b>4. {t('Lansman', 'Launch')}</b><p>{t('Lansman günü 48 saat içinde ödeme yapınca üyeliğin açılır.', 'On launch day, pay within 48 hours and your membership opens.')}</p></li>
            </ol>
          </section>

          <section className="fdBenefit">
            <Tag aria-hidden="true"/>
            <div><h3>{t('Kurucu Üyelik Avantajı', 'Founding membership perk')}</h3><p>{t('Onaylanan ilk 30 kişiye seçilen üyelik planında 12 ay boyunca sabit fiyat garantisi sunulur.', 'The first 30 approved members get a 12-month locked price on their chosen plan.')}</p></div>
            <div className="fdPriceTag"><small>{t('KURUCU FİYATI', 'FOUNDER PRICE')}</small><strong>{PRICE || t('Yakında', 'Soon')}</strong></div>
          </section>

          <section className="fdTrust" aria-label={t('Güven', 'Trust')}>
            <div><Lock aria-hidden="true"/><b>{t('Paran sende kalır', 'Your funds stay with you')}</b><span>{t('Para çekme yetkisi olmayan API anahtarıyla çalışırız.', 'We use API keys with no withdrawal permission.')}</span></div>
            <div><Gauge aria-hidden="true"/><b>{t('Risk kilitleri açık', 'Risk locks included')}</b><span>{t('Zaman sınırlı izin, 2FA, acil durdurma.', 'Time-limited permission, 2FA, emergency stop.')}</span></div>
            <div><Eye aria-hidden="true"/><b>{t('Dürüst anlatım', 'Honest messaging')}</b><span>{t('Kâr garantisi vermeyiz; riskleri açıkça yazarız.', 'We never promise profit and state risks plainly.')}</span></div>
          </section>
        </div>

        <section className="fdFormWrap" ref={formRef} id="basvuru">
          {phase === 'done' ? <div className="fdDone" role="status"><Check aria-hidden="true"/><h3>{t('Başvurun alındı', 'Application received')}</h3><p>{t('E-postanı kontrol et ve başvurunu onayla. 72 saat içinde sana dönüş yapacağım.', 'Check your email and confirm your application. I will get back to you within 72 hours.')}</p></div>
          : <form className="fdForm" onSubmit={submit} noValidate>
            <header><h2>{t('Kurucu Üyelik Başvurusu', 'Founding Member Application')}</h2><p>{t('Ücretsiz başvurunu şimdi yap. Onaylanan adaylara lansman daveti gönderilir.', 'Apply for free now. Approved applicants receive a launch invitation.')}</p></header>
            <label>{t('Ad soyad', 'Full name')}<input placeholder={t('Ad Soyad', 'Full name')} autoComplete="name" value={form.name} onChange={event => set('name', event.target.value)} required/></label>
            <label>{t('E-posta adresi', 'Email address')}<input type="email" placeholder={t('E-posta Adresi', 'Email address')} autoComplete="email" value={form.email} onChange={event => set('email', event.target.value)} required/></label>
            <CountryPicker value={form.country} onChange={code => set('country', code)} lang={currentLang()} mobile={mobile} suggested={guess || undefined} label={t('Ülke', 'Country')} placeholder={t('Ülke seç', 'Select country')} searchPlaceholder={t('Ülke ara…', 'Search country…')} emptyText={t('Sonuç yok', 'No results')} suggestedLabel={t('Önerilen', 'Suggested')}/>
            <input className="fdHoney" tabIndex={-1} autoComplete="off" aria-hidden="true" value={form.website} onChange={event => set('website', event.target.value)}/>
            {phase === 'error' && <p className="fdError" role="alert"><TriangleAlert aria-hidden="true"/>{message}</p>}
            <button className="fdPrimary fdSubmit" type="submit" disabled={!valid || phase === 'busy'}>{phase === 'busy' ? t('Gönderiliyor…', 'Sending…') : t('Başvuruyu Gönder', 'Submit application')}<ArrowRight aria-hidden="true"/></button>
            <p className="fdFine">{t('Başvuru ücretsizdir, kart bilgisi istenmez. Göndererek ', 'Applying is free, no card details. By submitting you accept the ')}<a href="/privacy">{t('Gizlilik Politikası', 'Privacy Policy')}</a>{t('’nı, geri bildirim vermeyi ve kurucu fiyatının onayda bildirileceğini kabul edersin.', ', agree to give feedback, and understand the founder price is shared on approval.')}</p>
          </form>}
        </section>

        {mobile
          ? <button type="button" className="fdFaqRow" onClick={() => setSheet(true)}><span>{t('Sıkça Sorulan Sorular', 'FAQ')}</span><b>{t('Tümünü Gör', 'See all')}</b></button>
          : <section className="fdColB fdFaq" id="sss"><h2>{t('Sıkça Sorulan Sorular', 'Frequently asked questions')}</h2>{FAQ().map(([q, answer]) => <details key={q}><summary>{q}</summary><p>{answer}</p></details>)}</section>}
      </div>
    </main>

    {mobile && sheet && <div className="fdSheetBack" onClick={() => setSheet(false)}><section className="fdSheet" role="dialog" aria-modal="true" aria-label={t('Sıkça Sorulan Sorular', 'FAQ')} onClick={event => event.stopPropagation()}>
      <header><h2>{t('Sıkça Sorulan Sorular', 'Frequently asked questions')}</h2><button type="button" onClick={() => setSheet(false)} aria-label={t('Kapat', 'Close')}><X aria-hidden="true"/></button></header>
      {FAQ().map(([q, answer]) => <details key={q}><summary>{q}</summary><p>{answer}</p></details>)}
    </section></div>}
    <footer className="fdFoot">
      <p>{t('Kripto vadeli işlemler yüksek risk içerir. Kâr garantisi verilmez; bu sayfa yatırım tavsiyesi değildir.', 'Crypto futures carry high risk. No profit is guaranteed; this page is not investment advice.')}</p>
      <nav><a href="/privacy">{t('Gizlilik', 'Privacy')}</a><a href="/terms">{t('Şartlar', 'Terms')}</a><a href="/risk">{t('Risk uyarısı', 'Risk notice')}</a></nav>
      <small>© 2026 KaisTrade</small>
    </footer>
  </div>
}

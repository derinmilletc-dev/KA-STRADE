import {useEffect, useRef, useState, type FormEvent} from 'react'
import {ArrowRight, BarChart3, Check, Crown, Eye, Gauge, Headphones, Lock, Rocket, Search, ShieldCheck, Tag, TriangleAlert, Zap} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang, setLang} from './i18n'
import KaisEye from './KaisEye'
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
      <nav className="fdNav" aria-label={t('Sayfa bölümleri', 'Page sections')}><a href="#ozellikler">{t('Özellikler', 'Features')}</a><a href="#basvuru">Founding 30</a><a href="#sss">{t('Sıkça Sorulan Sorular', 'FAQ')}</a></nav>
      <div className="fdTopRight"><LanguagePicker value={currentLang()} onChange={changeLang}/><button type="button" className="fdTopCta" onClick={goForm}>{t('Başvur', 'Apply')}</button></div>
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
            <div className="fdQuota"><small>{t('Kontenjan', 'Quota')}</small><strong>{SEATS} <em>{t('koltuk · elle onay', 'seats · hand-picked')}</em></strong></div>
          </div>
        </div>
        <div className="fdEyeStage" aria-hidden="true">
          <i className="fdBar fdBar1"/><i className="fdBar fdBar2"/><i className="fdBar fdBar3"/><i className="fdBar fdBar4"/><i className="fdBar fdBar5"/>
          <span className="fdHalo"/><span className="fdRing"/>
          <div className="fdEye"><KaisEye size={300} state="idle" aria-label="KaisTrade"/></div>
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
            <label>{t('Ad soyad', 'Full name')}<input autoComplete="name" value={form.name} onChange={event => set('name', event.target.value)} required/></label>
            <label>{t('E-posta adresi', 'Email address')}<input type="email" autoComplete="email" value={form.email} onChange={event => set('email', event.target.value)} required/></label>
            <label>{t('Ülke', 'Country')}<input autoComplete="country-name" value={form.country} onChange={event => set('country', event.target.value)} required/></label>
            <div className="fdTwo">
              <label>{t('Binance Futures deneyimin', 'Binance Futures experience')}<select value={form.experience} onChange={event => set('experience', event.target.value)} required><option value="">{t('Seç', 'Select')}</option>{EXPERIENCE().map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label>{t('Sermaye aralığı', 'Capital range')}<select value={form.capital} onChange={event => set('capital', event.target.value)} required><option value="">{t('Seç', 'Select')}</option>{CAPITAL().map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            </div>
            <label>{t('KaisTrade’i neden kullanmak istiyorsun?', 'Why do you want to use KaisTrade?')}<textarea rows={3} maxLength={600} value={form.reason} onChange={event => set('reason', event.target.value)} required/></label>
            <input className="fdHoney" tabIndex={-1} autoComplete="off" aria-hidden="true" value={form.website} onChange={event => set('website', event.target.value)}/>
            <label className="fdCheck"><input type="checkbox" checked={form.feedback} onChange={event => set('feedback', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{t('Geri bildirim vermeyi kabul ediyorum', 'I agree to give feedback')}</b></div></label>
            <label className="fdCheck"><input type="checkbox" checked={form.price} onChange={event => set('price', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{PRICE ? t(`Kurucu fiyatını (${PRICE}) kabul ediyorum`, `I accept the founder price (${PRICE})`) : t('Kurucu fiyatının onayda bildirileceğini anlıyorum', 'I understand the founder price is shared on approval')}</b><small>{t('Onaylanırsam lansmanda 48 saat içinde ödeme yapmam gerekir.', 'If approved I must pay within 48 hours at launch.')}</small></div></label>
            <label className="fdCheck"><input type="checkbox" checked={form.consent} onChange={event => set('consent', event.target.checked)}/><span aria-hidden="true"><Check/></span><div><b>{t('Gizlilik Politikası’nı okudum', 'I have read the Privacy Policy')}</b><small><a href="/privacy">{t('Gizlilik Politikası', 'Privacy Policy')}</a></small></div></label>
            {phase === 'error' && <p className="fdError" role="alert"><TriangleAlert aria-hidden="true"/>{message}</p>}
            <button className="fdPrimary fdSubmit" type="submit" disabled={!valid || phase === 'busy'}>{phase === 'busy' ? t('Gönderiliyor…', 'Sending…') : t('Başvuruyu Gönder', 'Submit application')}<ArrowRight aria-hidden="true"/></button>
            <p className="fdFine">{t('Başvuru ücretsizdir. Hiçbir kart bilgisi istenmez.', 'Applying is free. No card details are requested.')}</p>
          </form>}
        </section>

        <section className="fdColB fdFaq" id="sss">
          <h2>{t('Sıkça Sorulan Sorular', 'Frequently asked questions')}</h2>
          <details><summary>{t('Başvuru ücretsiz mi?', 'Is applying free?')}</summary><p>{t('Evet. Başvuru ücretsizdir ve kart bilgisi istenmez. Onaylanırsan ödemeyi yalnızca lansman gününde, 48 saatlik pencerede yaparsın.', 'Yes. Applying is free and no card details are requested. If approved you pay only on launch day within a 48-hour window.')}</p></details>
          <details><summary>{t('Başvurum ne zaman onaylanır?', 'When is my application approved?')}</summary><p>{t('Her başvuru elle incelenir; 72 saat içinde e-postayla dönüş yaparız.', 'Every application is reviewed by hand; we reply by email within 72 hours.')}</p></details>
          <details><summary>{t('Ödeme ne zaman yapılır?', 'When do I pay?')}</summary><p>{t('Yalnızca lansman gününde, onaylı kurucular için açılan 48 saatlik pencerede. Süre dolarsa koltuk sıradaki adaya geçer.', 'Only on launch day, in a 48-hour window for approved founders. If it expires the seat moves to the next applicant.')}</p></details>
          <details><summary>{t('12 aylık fiyat garantisi nasıl çalışır?', 'How does the 12-month price lock work?')}</summary><p>{t('Aboneliğin kesintisiz sürdüğü sürece seçtiğin plan için aynı fiyatı 12 ay ödersin. İptal edersen kilit biter. Ömür boyu vaadi yoktur.', 'While your subscription stays active you pay the same price for your plan for 12 months. Cancelling ends the lock. No lifetime promise.')}</p></details>
          <details><summary>{t('KaisTrade kâr garantisi veriyor mu?', 'Does KaisTrade guarantee profit?')}</summary><p>{t('Hayır. Vadeli işlemler yüksek risk taşır ve sermayenin tamamını kaybedebilirsin. Geçmiş sonuçlar geleceği garanti etmez.', 'No. Futures trading is high risk and you can lose all of your capital. Past results do not guarantee future results.')}</p></details>
          <details><summary>{t('Binance API güvenliği nasıl sağlanıyor?', 'How is Binance API security handled?')}</summary><p>{t('Paran Binance hesabında kalır. Yalnızca işlem yetkili, para çekme yetkisiz API anahtarı kullanırız.', 'Your funds stay on your Binance account. We only use trade-enabled API keys without withdrawal permission.')}</p></details>
        </section>
      </div>
    </main>

    <footer className="fdFoot">
      <p>{t('Kripto vadeli işlemler yüksek risk içerir. Kâr garantisi verilmez; bu sayfa yatırım tavsiyesi değildir.', 'Crypto futures carry high risk. No profit is guaranteed; this page is not investment advice.')}</p>
      <nav><a href="/privacy">{t('Gizlilik', 'Privacy')}</a><a href="/terms">{t('Şartlar', 'Terms')}</a><a href="/risk">{t('Risk uyarısı', 'Risk notice')}</a></nav>
      <small>© 2026 KaisTrade</small>
    </footer>
  </div>
}

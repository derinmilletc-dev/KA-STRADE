import React, {useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent} from 'react'
import {ArrowLeft, ArrowRight, Users, BarChart3, Check, Crown, Eye, Gauge, Headphones, Lock, Rocket, Search, ShieldCheck, Tag, TriangleAlert, X, Zap} from 'lucide-react'
import {API_BASE} from './api'
import {browserLang, currentLang as savedLang, translateText} from './i18n'
import KaisEye from './KaisEye'
import OptionPicker from './OptionPicker'
import FoundingIntro, {preloadIntro, type IntroOutcome} from './FoundingIntro'
import FoundingSuccess from './FoundingSuccess'
import FoundingStatus from './FoundingStatus'
import CountryPicker, {countryName, guessCountry} from './CountryPicker'
import './launch-page.css'

/** Founding 30 – Erken Erişim lansman sayfası. Giriş ekranı dondurulduğunda herkese açık ana sayfadır. */
/** Herkese açık lansman sayfaları ziyaretçinin tarayıcı dilinde açılır (Türkçe değilse İngilizce). */
const PAGE_LANG = browserLang()
const currentLang = () => PAGE_LANG
const t = (tr: string, en: string) => (PAGE_LANG === 'en' ? en : tr)
const ENV_PRICE = String(import.meta.env.VITE_FOUNDING_PRICE ?? '').trim()
/** Lansmana özel kurucu fiyatı (admin'den farklı bir metin girilirse o gösterilir). */
const FOUNDER_PRICE = '$119.90'
const LIST_PRICE = '$399.90'
const PRICE_PERIOD = (): string => t('/ay', '/mo')
const SEATS = 30
const EYE_PHOTO = '/founding-eye.webp'

type Form = {email: string; name: string; country: string; futures: string; trading: string; heard: string; reason: string; website: string}
const EMPTY: Form = {email: '', name: '', country: '', futures: '', trading: '', heard: '', reason: '', website: ''}
const REASON_MIN = 10
const FUTURES = (): [string, string][] => [['active', t('Aktif kullanıyorum', 'I use it actively')], ['past', t('Daha önce kullandım', 'I have used it before')], ['never', t('Henüz kullanmadım', 'I have not used it yet')]]
const HEARD = (): [string, string][] => [['instagram', 'Instagram'], ['tiktok', 'TikTok'], ['x', 'X (Twitter)'], ['youtube', 'YouTube'], ['google', t('Google araması', 'Google search')], ['friend', t('Arkadaş tavsiyesi', 'A friend')], ['telegram', 'Telegram'], ['other', t('Diğer', 'Other')]]
const TRADING = (): [string, string][] => [['new', t('Yeni başlıyorum', 'Just starting')], ['lt6m', t('6 aydan az', 'Less than 6 months')], ['6to12m', t('6–12 ay', '6–12 months')], ['gt1y', t('1 yıldan fazla', 'More than 1 year')]]
type Phase = 'idle' | 'busy' | 'done' | 'error'


const FAQ = (): [string, string][] => [
  [t('Başvuru ücretsiz mi?', 'Is applying free?'), t('Evet. Başvuru ücretsizdir ve kart bilgisi istenmez. Onaylanırsan ödemeyi yalnızca lansman gününde, 48 saatlik pencerede yaparsın.', 'Yes. Applying is free and no card details are requested. If approved you pay only on launch day within a 48-hour window.')],
  [t('Başvurum ne zaman onaylanır?', 'When is my application approved?'), t('Her başvuru manuel olarak incelenir ve onaylanır; 72 saat içinde e-postayla dönüş yaparız.', 'Every application is reviewed and approved manually; we reply by email within 72 hours.')],
  [t('Ödeme ne zaman yapılır?', 'When do I pay?'), t('Yalnızca lansman gününde, onaylı kurucular için açılan 48 saatlik pencerede. Süre dolarsa koltuk sıradaki adaya geçer.', 'Only on launch day, in a 48-hour window for approved founders. If it expires the seat moves to the next applicant.')],
  [t('12 aylık fiyat garantisi nasıl çalışır?', 'How does the 12-month price lock work?'), t('Aboneliğin kesintisiz sürdüğü sürece seçtiğin plan için aynı fiyatı 12 ay ödersin. İptal edersen kilit biter. Ömür boyu vaadi yoktur.', 'While your subscription stays active you pay the same price for your plan for 12 months. Cancelling ends the lock. No lifetime promise.')],
  [t('Binance API güvenliği nasıl sağlanıyor?', 'How is Binance API security handled?'), t('Paran Binance hesabında kalır. Yalnızca işlem yetkili, para çekme yetkisiz API anahtarı kullanırız.', 'Your funds stay on your Binance account. We only use trade-enabled API keys without withdrawal permission.')],
]

type FunnelStep = 'view' | 'start' | 'step2' | 'submit' | 'success'
/** Anonim dönüşüm sayacı: her olay tarayıcı oturumu başına bir kez gönderilir (kimlik/çerez yok). */
function track(event: FunnelStep) {
  try {
    const key = `fdFunnel:${event}`
    if (window.sessionStorage.getItem(key)) return
    window.sessionStorage.setItem(key, '1')
  } catch { /* depolama yoksa yine de bir kez gönderilir */ }
  try { void fetch(`${API_BASE}/v22/founding/event`, {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({event})}).catch(() => undefined) } catch { /* sessiz */ }
}

const STATUS_ROUTE = typeof window !== 'undefined' && /^\/basvuru-durumu\/?$/.test(window.location.pathname)

/** Eski bir ziyaretten kalan dil tercihi tarayıcı dilinden farklıysa site çevirisi de tarayıcı diline eşitlenir (bir kez yenilenir). */
function useBrowserLanguage() {
  useLayoutEffect(() => {
    if (savedLang() === PAGE_LANG) return
    try {
      if (window.sessionStorage.getItem('kt_lang_sync') === PAGE_LANG) return  // döngü koruması
      window.sessionStorage.setItem('kt_lang_sync', PAGE_LANG)
      window.localStorage.setItem('kt_lang', PAGE_LANG)
      window.location.reload()
    } catch { /* depolama yoksa sayfa yine tarayıcı dilinde gösterilir */ }
  }, [])
}

export default function LaunchPage() {
  useBrowserLanguage()
  const [form, setForm] = useState<Form>(EMPTY)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const formRef = useRef<HTMLElement>(null)
  useEffect(() => {
    document.documentElement.classList.add('fdNoScroll')
    return () => document.documentElement.classList.remove('fdNoScroll')
  }, [])
  useEffect(() => { if (!STATUS_ROUTE) track('view'); try { document.documentElement.lang = PAGE_LANG } catch { /* yok */ } }, [])
  const [photo, setPhoto] = useState(false)
  const [live, setLive] = useState<{open: boolean; seats: number; taken: number; price: string | null}>({open: true, seats: SEATS, taken: 0, price: null})
  const [banner, setBanner] = useState<'confirmed' | 'expired' | 'invalid' | null>(null)
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get('founding')
    if (value === 'confirmed' || value === 'expired' || value === 'invalid') { setBanner(value); window.history.replaceState(null, '', window.location.pathname) }
    const controller = new AbortController()
    fetch(`${API_BASE}/v22/founding/status`, {signal: controller.signal}).then(r => r.ok ? r.json() : null).then(body => {
      if (body && typeof body.seats === 'number') setLive({open: body.open !== false, seats: body.seats, taken: Number(body.taken) || 0, price: typeof body.price === 'string' ? body.price : null})
    }).catch(() => { /* durum alınamazsa varsayılanlarla devam */ })
    return () => controller.abort()
  }, [])
  const PRICE = live.price || ENV_PRICE || FOUNDER_PRICE
  const discounted = PRICE === FOUNDER_PRICE
  const TAKEN = live.taken
  const [sheet, setSheet] = useState(false)
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches)
  useEffect(() => { const q = window.matchMedia('(max-width: 700px)'); const on = () => setMobile(q.matches); q.addEventListener('change', on); return () => q.removeEventListener('change', on) }, [])
  useEffect(() => { const img = new Image(); img.onload = () => setPhoto(true); img.src = EYE_PHOTO }, [])
  useEffect(() => { document.title = t('KaisTrade · Founding 30 Erken Erişim', 'KaisTrade · Founding 30 Early Access') }, [])
  const set = <K extends keyof Form>(key: K, value: Form[K]) => { if (key !== 'country' && key !== 'website') track('start'); setForm(current => ({...current, [key]: value})) }
  const guess = useMemo(guessCountry, [])
  useEffect(() => { if (guess) setForm(current => current.country ? current : {...current, country: guess}) }, [guess])
  const goForm = () => formRef.current?.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'})

  const [step, setStep] = useState<1 | 2>(1)
  const stepOne = /^\S+@\S+\.\S+$/.test(form.email) && form.name.trim().length >= 2 && form.country.length === 2 && !!form.heard
  // 1. aşamadan 2. aşamaya giden çizgi, formun doluluğuna göre ilerler (gönderilince tamamlanır)
  const filledOne = [form.name.trim().length >= 2, /^\S+@\S+\.\S+$/.test(form.email), form.country.length === 2, !!form.heard].filter(Boolean).length
  const filledTwo = [!!form.futures, !!form.trading, form.reason.trim().length >= REASON_MIN].filter(Boolean).length
  const progress = phase === 'busy' || phase === 'done' ? 1 : step === 1 ? filledOne / 8 : 0.5 + filledTwo / 6
  const valid = stepOne && !!form.futures && !!form.trading && form.reason.trim().length >= REASON_MIN
  const goStep = (next: 1 | 2) => { setStep(next); setPhase(current => current === 'error' ? 'idle' : current); setMessage('') }
  const [intro, setIntro] = useState<IntroOutcome | null>(null)
  const [reference, setReference] = useState<string | null>(null)
  useEffect(() => (step === 2 ? preloadIntro() : undefined), [step])
  const goHome = () => { setForm(current => ({...EMPTY, country: guess || current.country})); setStep(1); setPhase('idle'); setReference(null); setMessage('') }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (step === 1) { if (stepOne) { track('step2'); goStep(2) } return }
    if (!valid || phase === 'busy') return
    // "Hareketi azalt" açıksa geçiş hiç gösterilmez; yanıt gelince doğrudan onay ekranı açılır
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    track('submit')
    setPhase('busy'); setMessage(''); if (!calm) setIntro('pending')
    const fail = (text: string) => { setIntro(current => current ? 'error' : current); setPhase('error'); setMessage(text) }
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch(`${API_BASE}/v22/founding/apply`, {method: 'POST', signal: controller.signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: form.email, name: form.name, country: countryName(form.country, 'en'), country_code: form.country || undefined, futures_experience: form.futures, trading_experience: form.trading, heard_from: form.heard, reason: form.reason.trim(), website: form.website, feedback: true, price: true, consent: true, lang: currentLang()})})
      if (response.ok) {
        let ref: string | null = null
        try { const body = await response.json(); ref = typeof body?.reference === 'string' && /^KT-F30-[A-Z0-9]{5}$/.test(body.reference) ? body.reference : null } catch { /* gövde yok */ }
        setReference(ref); track('success')
        if (calm) { setPhase('done'); try { window.scrollTo({top: 0}) } catch { /* eski tarayıcı */ } } else setIntro('ok')
        return
      }
      let detail = ''
      try { const body = await response.json(); detail = typeof body?.detail === 'string' ? body.detail : '' } catch { /* gövde yok */ }
      fail((detail && translateText(detail)) || t('Başvuru şu anda alınamadı. Lütfen biraz sonra tekrar deneyin.', 'We could not take your application right now. Please try again shortly.'))
    } catch {
      fail(t('Sunucuya ulaşılamadı. Bağlantınızı kontrol edip tekrar deneyin.', 'Could not reach the server. Check your connection and try again.'))
    } finally { window.clearTimeout(timeout) }
  }

  return <div className={`fdRoot ${step === 2 && phase !== 'done' ? 'fdOnStep2' : ''}`} data-i18n-skip>
    <header className="fdTop">
      <a className="fdBrand" href="/" aria-label="KaisTrade"><img src="/kaistrade-logo-trim.png" alt="KaisTrade"/></a>
      {phase !== 'done' && !STATUS_ROUTE && <nav className="fdNav" aria-label={t('Sayfa bölümleri', 'Page sections')}><a href="#ozellikler">{t('Özellikler', 'Features')}</a><a href="#basvuru">Founding 30</a><a href="#sss">{t('Sıkça Sorulan Sorular', 'FAQ')}</a></nav>}
      <div className="fdTopRight">{phase !== 'done' && !STATUS_ROUTE && <button type="button" className="fdTopCta" onClick={goForm}>{t('Başvur', 'Apply')}</button>}</div>
    </header>

    {STATUS_ROUTE ? <FoundingStatus t={t}/> : phase === 'done' ? <FoundingSuccess reference={reference} email={form.email} t={t} onHome={goHome}/> : <main>
      {banner && <p className={`fdBanner ${banner}`} role="status">{banner === 'confirmed' ? t('E-postan onaylandı. Başvurun incelemeye alındı; 72 saat içinde dönüş yapacağız.', 'Email confirmed. Your application is in review; we will reply within 72 hours.') : banner === 'expired' ? t('Onay bağlantısının süresi dolmuş. Formu yeniden gönderirsen yeni bağlantı yollarız.', 'This confirmation link has expired. Submit the form again and we will send a new one.') : t('Onay bağlantısı geçersiz.', 'This confirmation link is invalid.')}</p>}
      <section className="fdHero">
        <div className="fdHeroCopy">
          <span className="fdBadge">EARLY ACCESS</span>
          <h1>Founding <b>30</b></h1>
          <h2>{t('İlk 30 kurucu üyeden biri ol.', 'Be one of the first 30 founding members.')}</h2>
          <p className="fdOffer"><span className="fdOfferPrice">{discounted && <s>{LIST_PRICE}</s>}<b>{PRICE}</b>{discounted && <small>{PRICE_PERIOD()}</small>}</span><span className="fdOfferText"><em>{t('Lansmana özel', 'Launch offer')}</em><span className="fdQLong">{t('Master Trade · sınırsız, tüm özelliklere erişim', 'Master Trade · unlimited, full access')}</span><span className="fdQShort">{t('Master Trade · sınırsız erişim', 'Master Trade · unlimited')}</span></span></p>
          <p>{t('Lansmana özel 12 ay sabit üyelik fiyatı avantajıyla KaisTrade’e erken katıl.', 'Join KaisTrade early with a 12-month locked membership price.')}</p>
          <div className="fdActions">
            <button type="button" className="fdPrimary" onClick={goForm}>{t('Ücretsiz Başvur', 'Apply for free')}<ArrowRight aria-hidden="true"/></button>
            <div className="fdQuota"><Users aria-hidden="true"/><span><small lang="en">Founding Members</small><strong>{TAKEN ? <>{String(TAKEN).padStart(2, '0')} / {live.seats} <em>Taken</em></> : <>{live.seats} <em><span className="fdQLong">Founding Members · </span><span className="fdQShort">Members · </span>Application Only</em></>}</strong></span>{TAKEN > 0 && <i className="fdMeter" aria-hidden="true"><b style={{width: `${TAKEN / live.seats * 100}%`}}/></i>}</div>
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
            <ol className="fdSteps" style={{'--p': progress} as React.CSSProperties} aria-label={t(`Başvuru ilerlemesi: %${Math.round(progress * 100)}`, `Application progress: ${Math.round(progress * 100)}%`)}>
              <span className="fdStepsFill" aria-hidden="true"/>
              <li className="is-active" aria-current="step"><i><BarChart3 aria-hidden="true"/></i><b>1. {t('Başvur', 'Apply')}</b><p>{t('Kısa formu doldur. Ücret ve ön ödeme yok.', 'Fill in the short form. No fee, no deposit.')}</p></li>
              <li className="is-todo"><i><Search aria-hidden="true"/></i><b>2. {t('İncele', 'Review')}</b><p>{t('72 saat içinde manuel değerlendirip dönüş yaparız.', 'We review manually and reply within 72 hours.')}</p></li>
              <li className="is-todo"><i><Check aria-hidden="true"/></i><b>3. {t('Onay', 'Approval')}</b><p>{t('Uygun görülürsen koltuğunu teyit edersin.', 'If approved, you confirm your seat.')}</p></li>
              <li className="is-todo"><i><Rocket aria-hidden="true"/></i><b>4. {t('Lansman', 'Launch')}</b><p>{t('Lansman günü 48 saat içinde ödeme yapınca üyeliğin açılır.', 'On launch day, pay within 48 hours and your membership opens.')}</p></li>
            </ol>
          </section>

          <section className="fdBenefit">
            <Tag aria-hidden="true"/>
            <div><h3>{t('Kurucu Üyelik Avantajı', 'Founding membership perk')}</h3><p>{t('Lansmana özel Master Trade: sınırsız, tüm özelliklere erişim. Onaylanan ilk 30 kişiye bu fiyat 12 ay boyunca sabit kalır.', 'Launch offer Master Trade: unlimited, full access. The first 30 approved members keep this price for 12 months.')}</p></div>
            <div className="fdPriceTag"><small>{t('KURUCU FİYATI', 'FOUNDER PRICE')}</small><strong>{discounted && <s>{LIST_PRICE}</s>}{PRICE}{discounted && <small>{PRICE_PERIOD()}</small>}</strong></div>
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
            <div className="fdProgress" aria-label={t(`Adım ${step} / 2`, `Step ${step} of 2`)}><span className="on"/><span className={step === 2 ? 'on' : ''}/><small>{t(`Adım ${step}/2`, `Step ${step}/2`)}</small></div>
            {step === 1 ? <div className="fdStepPane" key="s1">
            <label>{t('Ad soyad', 'Full name')}<input placeholder={t('Ad Soyad', 'Full name')} autoComplete="name" value={form.name} onChange={event => set('name', event.target.value)} required/></label>
            <label>{t('E-posta adresi', 'Email address')}<input type="email" placeholder={t('E-posta Adresi', 'Email address')} autoComplete="email" value={form.email} onChange={event => set('email', event.target.value)} required/></label>
            <div className="fdPair">
            <CountryPicker value={form.country} onChange={code => set('country', code)} lang={currentLang()} mobile={mobile} label={t('Ülke', 'Country')} placeholder={t('Ülke seç', 'Select country')} searchPlaceholder={t('Ülke ara…', 'Search country…')} emptyText={t('Sonuç yok', 'No results')}/>
              <div className="fdQ fdHeard"><span className="fdQLabel">{t('KaisTrade’i nereden duydun?', 'How did you hear about KaisTrade?')}</span>
                <OptionPicker value={form.heard} onChange={value => set('heard', value)} mobile={mobile} label={t('KaisTrade’i nereden duydun?', 'How did you hear about KaisTrade?')} placeholder={t('Nereden duydun?', 'How did you hear?')} options={HEARD().map(([value, label]) => ({value, label}))}/></div>
            </div>
            </div> : <div className="fdStepPane" key="s2">
              <fieldset className="fdQ"><legend>{t('Binance Futures deneyimin', 'Binance Futures experience')}</legend>
                <div className="fdRadios">{FUTURES().map(([value, label]) => <label key={value} className={form.futures === value ? 'on' : ''}><input type="radio" name="futures" value={value} checked={form.futures === value} onChange={() => set('futures', value)}/><i aria-hidden="true"/>{label}</label>)}</div></fieldset>
              <div className="fdQ"><span className="fdQLabel" id="fd-trading">{t('Trading deneyimin', 'Trading experience')}</span>
                <OptionPicker value={form.trading} onChange={value => set('trading', value)} mobile={mobile} label={t('Trading deneyimin', 'Trading experience')} placeholder={t('Deneyim süreni seç', 'Select your experience')} options={TRADING().map(([value, label]) => ({value, label}))}/></div>
              <label className="fdReason">{t('Neden KaisTrade kullanmak istiyorsun?', 'Why do you want to use KaisTrade?')}<textarea rows={3} maxLength={600} placeholder={t('Beklentilerini kısaca anlat…', 'Briefly describe what you expect…')} value={form.reason} onChange={event => set('reason', event.target.value)}/>
                <small>{form.reason.trim().length < REASON_MIN ? t(`En az ${REASON_MIN} karakter`, `At least ${REASON_MIN} characters`) : `${form.reason.length}/600`}</small></label>
            </div>}
            <input className="fdHoney" tabIndex={-1} autoComplete="off" aria-hidden="true" value={form.website} onChange={event => set('website', event.target.value)}/>
            {phase === 'error' && <p className="fdError" role="alert"><TriangleAlert aria-hidden="true"/>{message}</p>}
            <div className="fdNavBtns">
              {step === 2 && <button type="button" className="fdBack" onClick={() => goStep(1)} disabled={phase === 'busy'}><ArrowLeft aria-hidden="true"/>{t('Geri', 'Back')}</button>}
              {step === 1
                ? <button className="fdPrimary fdSubmit" type="submit" disabled={!stepOne || !live.open}>{t('Devam', 'Continue')}<ArrowRight aria-hidden="true"/></button>
                : <button className="fdPrimary fdSubmit" type="submit" disabled={!valid || phase === 'busy' || !live.open}>{phase === 'busy' ? t('Gönderiliyor…', 'Sending…') : t('Başvuruyu Gönder', 'Submit application')}<ArrowRight aria-hidden="true"/></button>}
            </div>
            {!live.open && <p className="fdError" role="status"><TriangleAlert aria-hidden="true"/>{t('Başvurular şu anda kapalı.', 'Applications are currently closed.')}</p>}
            {step === 1 ? <p className="fdFine">{t('Başvuru ücretsizdir, kart bilgisi istenmez.', 'Applying is free, no card details required.')} <a href="/basvuru-durumu">{t('Başvurunu sorgula', 'Track your application')}</a></p>
  : <p className="fdFine">{t('Başvuru ücretsizdir, kart bilgisi istenmez. Göndererek ', 'Applying is free, no card details. By submitting you accept the ')}<a href="/privacy">{t('Gizlilik Politikası', 'Privacy Policy')}</a>{t(`’nı, geri bildirim vermeyi ve onaylanırsan lansmanda kurucu fiyatıyla (${PRICE}${discounted ? PRICE_PERIOD() : ''}) ödeme yapacağını kabul edersin.`, `, agree to give feedback, and accept paying the founder price (${PRICE}${discounted ? PRICE_PERIOD() : ''}) at launch if approved.`)}</p>}
          </form>}
        </section>

        {mobile
          ? <button type="button" className="fdFaqRow" onClick={() => setSheet(true)}><span>{t('Sıkça Sorulan Sorular', 'FAQ')}</span><b>{t('Tümünü Gör', 'See all')}</b></button>
          : <section className="fdColB fdFaq" id="sss"><h2>{t('Sıkça Sorulan Sorular', 'Frequently asked questions')}</h2>{FAQ().map(([q, answer]) => <details key={q}><summary>{q}</summary><p>{answer}</p></details>)}</section>}
      </div>
    </main>}
    {intro && <FoundingIntro outcome={intro} holdText={t('Başvurun kaydediliyor…', 'Saving your application…')} onReveal={() => { setPhase('done'); try { window.scrollTo({top: 0}) } catch { /* eski tarayıcı */ } }} onClosed={() => setIntro(null)}/>}

    {mobile && sheet && <div className="fdSheetBack" onClick={() => setSheet(false)}><section className="fdSheet" role="dialog" aria-modal="true" aria-label={t('Sıkça Sorulan Sorular', 'FAQ')} onClick={event => event.stopPropagation()}>
      <header><h2>{t('Sıkça Sorulan Sorular', 'Frequently asked questions')}</h2><button type="button" onClick={() => setSheet(false)} aria-label={t('Kapat', 'Close')}><X aria-hidden="true"/></button></header>
      {FAQ().map(([q, answer]) => <details key={q}><summary>{q}</summary><p>{answer}</p></details>)}
    </section></div>}
    <footer className="fdFoot"><small>© 2026 KaisTrade · {t('Tüm Hakları Saklıdır', 'All Rights Reserved')}</small></footer>
  </div>
}

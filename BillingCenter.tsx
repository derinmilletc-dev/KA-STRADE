import {useEffect, useRef, useState, type MouseEvent as ReactMouseEvent} from 'react'
import {ArrowRight, BarChart3, Bot, Check, ChevronLeft, ChevronRight, Clock3, CreditCard, Crown, Grid3X3, Lock, MessageCircle, RefreshCw, ShieldCheck, Sparkles, Wallet, X, Zap} from 'lucide-react'
import {userSessionToken} from './api'
import {askKais} from './kais-ask'
import BinanceManualPayment from './BinanceManualPayment'
import {trialRequest, type Trial} from './FreeTrialPricing'
import {PLAN_BY_CODE} from './subscription'
import {PAID_PLAN_AMOUNT, PAID_PLAN_NAME, type PaidPlan} from './smart-trade-plans'
import './billing-center.css'

export type BillingSubscription = {
  status: string
  plan: string | null
  master_trade_access: boolean
  trial_end?: string | null
  current_period_end?: string | null
  mode?: string
}

const STATUS_TR: Record<string, string> = {ACTIVE: 'Aktif', TRIALING: 'Deneme dönemi', PAST_DUE: 'Ödeme gecikti', UNPAID: 'Ödenmedi', CANCELLED: 'İptal edildi', EXPIRED: 'Süresi doldu'}
const trDate = (value?: string | null) => value ? new Date(value).toLocaleDateString('tr-TR', {day: 'numeric', month: 'long', year: 'numeric'}) : '—'
type Feat = {t: string; tag?: string; hot?: boolean}
const TRIAL_FEATURES: Feat[] = [{t: 'Master Trade analiz'}, {t: 'KAIS AI asistanı', tag: 'Günde 15'}, {t: 'KAIS Rehber', tag: 'Sınırlı'}, {t: 'Analiz Stüdyosu'}, {t: 'AI Analist', tag: 'Sınırlı'}, {t: 'KAIS stratejileri · Demo Auto Trade'}, {t: 'Pozisyon koruması'}, {t: 'Manuel ve Grid işlemler', tag: 'Demo'}]
const SMART_FEATURES: Feat[] = [{t: 'Manuel işlem · Demo ve Canlı'}, {t: 'KAIS Rehber'}, {t: 'KAIS AI asistanı', tag: 'Günde 40'}, {t: 'Analiz Stüdyosu'}, {t: 'AI Analist ve Scanner'}, {t: 'Stop, hedef ve pozisyon koruması'}, {t: 'Grid işlemleri'}, {t: 'Demo hesapta tüm stratejiler'}, {t: '7/24 destek'}]
const featureIcon = (text: string) => /Grid/.test(text) ? <Grid3X3/> : /LIVE|canlı/.test(text) ? <Zap/> : /koruma|Stop/.test(text) ? <ShieldCheck/> : /analiz|grafik/i.test(text) ? <BarChart3/> : /AI|Kais/.test(text) ? <Bot/> : <Check/>
const FeatureList = ({items, locked}: {items: readonly Feat[]; locked?: string}) => <ul className="ktBillFeatures">{items.map(feature => <li key={feature.t} className={feature.hot ? 'isHot' : undefined}><i>{featureIcon(feature.t)}</i><span className="ktBillFeatText">{feature.t}</span>{feature.tag && <small className="ktBillFeatTag">{feature.tag}</small>}<em aria-label="Dahil"><Check/></em></li>)}{locked && <li className="isLocked"><i><Lock/></i><span className="ktBillFeatText">{locked}</span><em className="srOnly">Bu planda yok</em></li>}</ul>
const PLAN_TABS = [{key: 'trial', label: 'Deneme', icon: <Sparkles/>}, {key: 'smart', label: 'Smart Trade', icon: <BarChart3/>}, {key: 'master', label: 'Master Mode', icon: <Crown/>}] as const
const MASTER_FEATURES: Feat[] = [{t: 'Master Trade analiz'}, {t: 'Canlı işlem'}, {t: 'Manuel ve Grid işlemler'}, {t: 'KAIS Rehber'}, {t: 'Analiz Stüdyosu'}, {t: 'AI Analist ve Scanner'}, {t: 'KAIS stratejileri ve Auto Trade'}, {t: 'Stop, hedef ve pozisyon koruması'}, {t: 'KAIS AI asistanı', tag: 'Günde 40'}, {t: 'Öncelikli ve hızlı 7/24 destek', tag: 'Öncelikli', hot: true}]

export default function BillingCenter({subscription, loading, loadError, trialEnabled, binanceEnabled, methodsLoading, methodsError, onRetry, onMasterTrade}: {
  subscription: BillingSubscription | null
  loading: boolean
  loadError: string
  trialEnabled: boolean
  binanceEnabled: boolean
  methodsLoading: boolean
  methodsError: string
  onRetry: () => void
  onMasterTrade: () => void
}) {
  const authenticated = Boolean(userSessionToken())
  const [trial, setTrial] = useState<Trial | null>(null)
  const [trialLoading, setTrialLoading] = useState(false)
  const [trialBusy, setTrialBusy] = useState(false)
  const [trialError, setTrialError] = useState('')
  const [trialAttempt, setTrialAttempt] = useState(0)
  const [now, setNow] = useState(Date.now)
  const inFlight = useRef(false)
  const [checkoutOpen, setCheckoutOpen] = useState(false)
  const [sheetPlan, setSheetPlan] = useState<number | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const lastFocus = useRef<HTMLElement | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<PaidPlan>('MASTER_MODE')
  const plansRef = useRef<HTMLElement>(null)
  const [activePlan, setActivePlan] = useState(2)
  const [swiped, setSwiped] = useState(false)

  const scrollToPlan = (index: number, behavior: ScrollBehavior) => {
    const list = plansRef.current
    const card = list?.children[index] as HTMLElement | undefined
    if (!list || !card) return
    list.scrollTo({left: card.offsetLeft - (list.clientWidth - card.offsetWidth) / 2, behavior})
  }
  const onPlansScroll = () => {
    const list = plansRef.current
    if (!list) return
    const middle = list.scrollLeft + list.clientWidth / 2
    let best = 0, distance = Infinity
    Array.from(list.children).forEach((child, index) => {
      const card = child as HTMLElement
      const gap = Math.abs(card.offsetLeft + card.offsetWidth / 2 - middle)
      if (gap < distance) { distance = gap; best = index }
    })
    setActivePlan(best)
  }
  useEffect(() => {
    if (window.matchMedia('(max-width: 700px)').matches) scrollToPlan(2, 'auto')
  }, [])
  useEffect(() => {
    const root = document.documentElement
    const place = () => {
      const nav = document.querySelector('.v26MobileBottomNav')
      const top = nav ? nav.getBoundingClientRect().top : NaN
      if (Number.isFinite(top) && top > 0 && window.matchMedia('(max-width: 700px)').matches) root.style.setProperty('--ktDockBottom', `${Math.max(0, Math.round(window.innerHeight - top + 10))}px`)
      else root.style.removeProperty('--ktDockBottom')
    }
    place()
    window.addEventListener('resize', place); window.addEventListener('orientationchange', place)
    const timer = window.setTimeout(place, 400)
    return () => { window.removeEventListener('resize', place); window.removeEventListener('orientationchange', place); window.clearTimeout(timer); root.style.removeProperty('--ktDockBottom') }
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setTrial(null); setTrialError('')
    if (!trialEnabled || !authenticated) return () => controller.abort()
    setTrialLoading(true)
    void trialRequest('', controller.signal).then(value => {
      if (!controller.signal.aborted) setTrial(value)
    }).catch((exception: unknown) => {
      if (!controller.signal.aborted) setTrialError(exception instanceof Error ? exception.message : 'Deneme bilgileri yüklenemedi.')
    }).finally(() => { if (!controller.signal.aborted) setTrialLoading(false) })
    return () => controller.abort()
  }, [trialEnabled, authenticated, trialAttempt])

  const startTrial = async () => {
    if (inFlight.current) return
    inFlight.current = true; setTrialBusy(true); setTrialError('')
    try {
      const value = await trialRequest('/start')
      setTrial(value); setNow(Date.now())
      window.dispatchEvent(new Event('protrebot-access-refresh'))
    } catch (exception) {
      setTrialError(exception instanceof Error ? exception.message : 'Deneme başlatılamadı.')
    } finally { inFlight.current = false; setTrialBusy(false) }
  }

  const status = subscription?.status || ''
  const hasAccess = Boolean(subscription?.master_trade_access)
  const onTrial = subscription?.mode === 'FREE_TRIAL'
  const binancePaid = subscription?.mode === 'BINANCE_MANUAL'
  const expiry = subscription?.current_period_end || subscription?.trial_end
  const smartTrade = subscription?.plan === 'SMART_TRADE'
  const planName = onTrial ? '1 Hafta Ücretsiz Deneme' : smartTrade ? 'SMART TRADE' : subscription?.plan ? 'MASTER MODE' : 'Aktif plan yok'
  const noPlan = !hasAccess && !onTrial && !subscription?.plan
  const badge = loading ? 'Yükleniyor' : loadError ? 'Bilgi alınamadı' : hasAccess ? (onTrial ? 'Deneme aktif' : 'Aktif') : noPlan ? 'Abonelik yok' : STATUS_TR[status] || 'Kapalı'
  const badgeTone = loading || loadError || noPlan ? 'neutral' : hasAccess ? 'positive' : 'warning'

  const trialUsed = trial?.enabled === true && trial.used
  const trialDays = trial?.enabled === true && trial.ends_at ? Math.max(0, Math.ceil((Date.parse(trial.ends_at) - now) / 86400000)) : 0
  const trialActive = trial?.enabled === true && trial.active && trialDays > 0
  const trialLabel = !trialEnabled ? 'Deneme yakında açılacak' : !authenticated ? 'Giriş yapın'
    : trialLoading ? 'Yükleniyor…' : trialBusy ? 'Başlatılıyor…' : trialActive ? `Deneme aktif · ${trialDays} gün kaldı` : trialUsed ? 'Deneme hakkı kullanıldı' : 'Ücretsiz denemeyi başlat'
  const trialDisabled = !trialEnabled || !authenticated || trialLoading || trialBusy || !trial || trialUsed

  const masterLabel = methodsLoading ? 'Yükleniyor…' : methodsError ? 'Ödeme bilgisi alınamadı' : !binanceEnabled ? 'Ödeme yakında açılacak'
    : binancePaid && hasAccess ? 'Erişimin aktif' : 'Ödemeye geç'
  const smartLabel = methodsLoading ? 'Yükleniyor…' : methodsError ? 'Ödeme bilgisi alınamadı' : !binanceEnabled ? 'Ödeme yakında açılacak'
    : binancePaid && hasAccess && smartTrade ? 'Erişimin aktif' : 'Ödemeye geç'
  const goToPayment = (plan: PaidPlan) => { lastFocus.current = document.activeElement as HTMLElement | null; setSelectedPlan(plan); setCheckoutOpen(true) }
  const closeCheckout = () => { setCheckoutOpen(false); window.setTimeout(() => lastFocus.current?.focus?.(), 0) }
  const canPay = binanceEnabled && authenticated && !(binancePaid && hasAccess)
  const onCardTap = (index: number, event: ReactMouseEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button, a')) return
    if (window.getSelection()?.toString()) return
    if (window.matchMedia('(max-width: 700px)').matches) { openSheet(index); return }
    if (index === 1 && canPay) goToPayment('SMART_TRADE')
    else if (index === 2 && canPay) goToPayment('MASTER_MODE')
  }
  const openSheet = (index: number) => { lastFocus.current = document.activeElement as HTMLElement | null; setSheetPlan(index) }
  const closeSheet = () => { setSheetPlan(null); window.setTimeout(() => lastFocus.current?.focus?.(), 0) }
  useEffect(() => {
    if (sheetPlan === null) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) closeSheet() }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = previous; window.removeEventListener('keydown', onKey) }
  }, [sheetPlan])
  useEffect(() => {
    if (!checkoutOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) closeCheckout() }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = previous; window.removeEventListener('keydown', onKey) }
  }, [checkoutOpen])

  return <main className="subscriptionPage ktBill">


    <section className={`ktBillStatus tone-${badgeTone}${noPlan && !loading && !loadError ? ' isNoPlan' : ''}`} aria-label="Mevcut abonelik">
      <div className="ktBillStatusMain">
        <div className="ktBillStatusIcon">{hasAccess ? <ShieldCheck/> : <Lock/>}</div>
        <div>
          <small>MEVCUT PLAN</small>
          <h2>{planName}</h2>
        </div>
        {(loading || loadError || !noPlan) && <span className="ktBillBadge">{badge}</span>}
      </div>
      <dl className="ktBillStats">
        <div><dt>ERİŞİM</dt><dd>{subscription ? hasAccess ? 'Açık' : 'Kapalı' : '—'}</dd></div>
        <div><dt>BİTİŞ TARİHİ</dt><dd>{expiry ? trDate(expiry) : '—'}</dd></div>
        <div className={binancePaid || onTrial ? undefined : 'ktBillStatEmpty'}><dt>ÖDEME YÖNTEMİ</dt><dd>{binancePaid ? 'Binance' : onTrial ? 'Ödemesiz deneme' : '—'}</dd></div>
        <div className={binancePaid || onTrial ? undefined : 'ktBillStatEmpty'}><dt>YENİLEME</dt><dd>{binancePaid || onTrial ? 'Otomatik yenileme yok' : '—'}</dd></div>
      </dl>
      {hasAccess && <button type="button" className="ktBillGhost" onClick={onMasterTrade}><ShieldCheck/>Master Trade’e git<ArrowRight/></button>}
      {loadError && <p className="ktBillAlert" role="alert">Abonelik bilgileri yüklenemedi. <button type="button" onClick={onRetry}>Yeniden dene</button></p>}
    </section>

    <div className="ktBillTabs" role="tablist" aria-label="Plan seç" data-plan={PLAN_TABS[activePlan].key}>
      {PLAN_TABS.map((tab, index) => <button key={tab.key} type="button" role="tab" aria-selected={activePlan === index} className={activePlan === index ? 'isActive' : undefined} onClick={() => { setSwiped(true); scrollToPlan(index, 'smooth') }}>{tab.icon}<span>{tab.label}</span></button>)}
    </div>
    {!swiped && <p className="ktBillSwipeHint" aria-hidden="true"><ChevronLeft/>Diğer planlar için kaydır<ChevronRight/></p>}

    <section className="ktBillPlans" aria-label="Planlar" ref={plansRef} onScroll={onPlansScroll} onTouchStart={() => setSwiped(true)}>
      <article className={`ktBillPlan${activePlan === 0 ? ' isActive' : ''}`} onClick={event => onCardTap(0, event)}>
        <header>
          <div className="ktBillPlanIcon"><Sparkles/></div>
          <div><span className="ktBillTag">ÖDEMESİZ DENEME</span><h3>1 Hafta Ücretsiz Deneme</h3></div>
        </header>
        <p className="ktBillPlanLead">Kart veya ödeme bilgisi gerekmez. 7 gün boyunca çalışma alanını dene.</p>
        <div className="ktBillPrice"><strong>0</strong><span className="ktBillCurrency">USDT</span><span className="ktBillPer">/ 7 gün</span></div>
        <ul>{TRIAL_FEATURES.map(feature => <li key={feature.t}><Check/><span className="ktBillFeatText">{feature.t}</span>{feature.tag && <small className="ktBillFeatTag">{feature.tag}</small>}</li>)}</ul>
        <FeatureList items={TRIAL_FEATURES} locked="Canlı işlem · ücretli planlarda açılır"/>
        <button type="button" className="ktBillMore" onClick={() => openSheet(0)}>+{TRIAL_FEATURES.length + 1 - 4} özellik daha · Tümünü gör<ChevronRight aria-hidden="true"/></button>
        <p className="ktBillNote ktBillLockNote"><Lock/>Canlı işlem ücretli planlarla açılır.</p>
        {trialActive && <p className="ktBillInfo" role="status"><Clock3/>Kalan süre: <b>{trialDays} gün</b></p>}
        {trialUsed && !trialActive && <p className="ktBillInfo" role="status">Deneme sona erdi. Devam etmek için SMART TRADE ya da MASTER MODE’a geç.</p>}
        <button type="button" className="ktBillButton" onClick={() => void startTrial()} disabled={trialDisabled}>{trialLabel}</button>
        {trialError && <p className="ktBillAlert" role="alert">{trialError} <button type="button" onClick={() => setTrialAttempt(value => value + 1)}>Yeniden dene</button></p>}
        <small className="ktBillTerms">Hesap başına bir deneme. E-posta doğrulaması gerekir.</small>
      </article>

      <article className={`ktBillPlan isSmart${activePlan === 1 ? ' isActive' : ''}${canPay ? ' isPay' : ''}`} onClick={event => onCardTap(1, event)}>
        <header>
          <div className="ktBillPlanIcon"><img src="/smart-trade-icon.png" alt="" width={46} height={46}/><BarChart3 className="ktBillIconMobile" aria-hidden="true"/></div>
          <div><span className="ktBillTag">AKILLI MANUEL İŞLEMLER</span><h3>SMART TRADE</h3></div>
        </header>
        <p className="ktBillPlanLead">Kendi kararınla işlem aç. Analiz, Grid ve stop/hedef araçları dahil.</p>
        <div className="ktBillPrice"><strong>{PAID_PLAN_AMOUNT.SMART_TRADE}</strong><span className="ktBillCurrency">USDT</span><span className="ktBillPer">/ 30 gün</span></div>
        <ul>{SMART_FEATURES.map(feature => <li key={feature.t}><Check/><span className="ktBillFeatText">{feature.t}</span>{feature.tag && <small className="ktBillFeatTag">{feature.tag}</small>}</li>)}</ul>
        <FeatureList items={SMART_FEATURES} locked="Canlıda KAIS stratejileri ve Auto Trade bu planda yok"/>
        <button type="button" className="ktBillMore" onClick={() => openSheet(1)}>+{SMART_FEATURES.length + 1 - 4} özellik daha · Tümünü gör<ChevronRight aria-hidden="true"/></button>
        <p className="ktBillNoAuto"><Lock/> Kais otomatik stratejileri ve Auto Trade bu plana dahil değildir.</p>
        <p className="ktBillNote"><Wallet/>Binance Pay ile güvenle ödenir.</p>
        <button type="button" className="ktBillButton" onClick={() => goToPayment('SMART_TRADE')} disabled={!binanceEnabled || !authenticated || (binancePaid && hasAccess)}>{smartLabel}{binanceEnabled && !(binancePaid && hasAccess) && <ArrowRight/>}</button>
        <small className="ktBillTerms">Denemedeyken ödeme onaylanırsa 30 gün deneme bitişinden başlar. Otomatik yenileme yok.</small>
      </article>

      <article className={`ktBillPlan isFeatured${activePlan === 2 ? ' isActive' : ''}${canPay ? ' isPay' : ''}`} onClick={event => onCardTap(2, event)}>
        <span className="ktBillRibbon">ÖNERİLEN</span>
        <header>
          <div className="ktBillPlanIcon"><Crown/></div>
          <div><span className="ktBillTag">TAM ERİŞİM</span><h3>MASTER MODE</h3></div>
        </header>
        <p className="ktBillPlanLead">Yapay zekâ ve canlı işlem dahil tüm premium çalışma alanı.</p>
        <div className="ktBillPrice"><strong>{PLAN_BY_CODE.MASTER_MODE.monthlyPrice.toFixed(2)}</strong><span className="ktBillCurrency">USDT</span><span className="ktBillPer">/ 30 gün</span></div>
        <ul>{MASTER_FEATURES.map(feature => <li key={feature.t} className={feature.hot ? 'isHot' : undefined}><Check/><span className="ktBillFeatText">{feature.t}</span>{feature.tag && <small className="ktBillFeatTag">{feature.tag}</small>}</li>)}</ul>
        <FeatureList items={MASTER_FEATURES}/>
        <button type="button" className="ktBillMore" onClick={() => openSheet(2)}>+{MASTER_FEATURES.length - 5} özellik daha · Tümünü gör<ChevronRight aria-hidden="true"/></button>
        <p className="ktBillNote"><Wallet/>Binance Pay ile güvenle ödenir.</p>
        <button type="button" className="ktBillButton isPrimary" onClick={() => goToPayment('MASTER_MODE')} disabled={!binanceEnabled || !authenticated || (binancePaid && hasAccess)}>{masterLabel}{binanceEnabled && !(binancePaid && hasAccess) && <ArrowRight/>}</button>
        <small className="ktBillTerms">Denemedeyken ödeme onaylanırsa 30 gün deneme bitişinden başlar. Otomatik yenileme yok.</small>
      </article>
    </section>
    <div className="ktBillDots" aria-hidden="true" data-plan={PLAN_TABS[activePlan].key}>{PLAN_TABS.map((tab, index) => <i key={tab.key} className={activePlan === index ? 'isActive' : undefined}/>)}</div>

    {sheetPlan !== null && <div className="ktCheckout ktSheet" role="dialog" aria-modal="true" aria-labelledby="ktSheetTitle" data-plan={PLAN_TABS[sheetPlan].key}>
      <div className="ktCheckoutBackdrop" onClick={closeSheet}/>
      <div className="ktCheckoutPanel">
        <button type="button" className="ktCheckoutClose" onClick={closeSheet} aria-label="Kapat"><X/></button>
        <header className="ktCheckoutHead">
          <span className="ktBillEyebrow">NELER DAHİL</span>
          <h2 id="ktSheetTitle">{sheetPlan === 0 ? '1 Hafta Ücretsiz Deneme' : sheetPlan === 1 ? 'SMART TRADE' : 'MASTER MODE'}</h2>
          <div className="ktCheckoutPrice"><strong>{sheetPlan === 0 ? '0' : sheetPlan === 1 ? PAID_PLAN_AMOUNT.SMART_TRADE : PLAN_BY_CODE.MASTER_MODE.monthlyPrice.toFixed(2)}</strong><span>USDT</span><em>/ {sheetPlan === 0 ? '7' : '30'} gün</em></div>
        </header>
        {sheetPlan === 0 ? <FeatureList items={TRIAL_FEATURES} locked="Canlı işlem · ücretli planlarda açılır"/> : sheetPlan === 1 ? <FeatureList items={SMART_FEATURES} locked="Canlıda KAIS stratejileri ve Auto Trade bu planda yok"/> : <FeatureList items={MASTER_FEATURES}/>}
        {sheetPlan === 0
          ? <button type="button" className="ktBillButton ktSheetCta" onClick={() => { closeSheet(); void startTrial() }} disabled={trialDisabled}>{trialLabel}</button>
          : <button type="button" className="ktBillButton ktSheetCta" onClick={() => { const plan: PaidPlan = sheetPlan === 1 ? 'SMART_TRADE' : 'MASTER_MODE'; setSheetPlan(null); goToPayment(plan) }} disabled={!binanceEnabled || !authenticated || (binancePaid && hasAccess)}>{sheetPlan === 1 ? smartLabel : masterLabel}</button>}
      </div>
    </div>}
    {checkoutOpen && binanceEnabled && authenticated && <div className="ktCheckout" role="dialog" aria-modal="true" aria-labelledby="ktCheckoutTitle">
      <div className="ktCheckoutBackdrop" onClick={closeCheckout}/>
      <div className="ktCheckoutPanel">
        <button type="button" className="ktCheckoutClose" ref={closeRef} onClick={closeCheckout} aria-label="Ödeme ekranını kapat"><X/></button>
        <header className="ktCheckoutHead">
          <span className="ktBillEyebrow">GÜVENLİ ÖDEME</span>
          <h2 id="ktCheckoutTitle">{PAID_PLAN_NAME[selectedPlan]}</h2>
          <div className="ktCheckoutPrice"><strong>{PAID_PLAN_AMOUNT[selectedPlan]}</strong><span>USDT</span><em>/ 30 gün</em></div>
          <dl className="ktCheckoutMeta">
            <div><dt>Plan</dt><dd>{PAID_PLAN_NAME[selectedPlan]}</dd></div>
            <div><dt>Süre</dt><dd>30 gün</dd></div>
            <div><dt>Yöntem</dt><dd>Binance Pay</dd></div>
          </dl>
        </header>
        <ol className="ktCheckoutSteps">
          <li><b>1</b><div><strong>Ödeme talebi oluştur</strong><span>“Binance ile öde”ye bas; sana özel referans kodu çıkar.</span></div></li>
          <li><b>2</b><div><strong>Binance Pay ile gönder</strong><span>{PAID_PLAN_AMOUNT[selectedPlan]} USDT’yi Pay ID’ye gönder, ödeme notuna referans kodunu yaz.</span></div></li>
          <li><b>3</b><div><strong>İşlem numarasını gir</strong><span>48 saat içinde gönder. Onaylanınca erişimin açılır ve bildirim gelir.</span></div></li>
        </ol>
        <div className="ktBillPayBox"><BinanceManualPayment plan={selectedPlan}/></div>
        <button type="button" className="ktCheckoutAsk" onClick={() => askKais(`${PAID_PLAN_NAME[selectedPlan]} planı (${PAID_PLAN_AMOUNT[selectedPlan]} USDT / 30 gün) hakkında bilgi almak istiyorum. Neler dahil ve Binance Pay ile nasıl ödeme yaparım?`)}>
          <i aria-hidden="true"><MessageCircle/></i>
          <span><b>KAIS AI’a sor</b><small>Plan, fiyat veya ödeme adımları hakkında hemen sor</small></span>
          <ChevronRight aria-hidden="true"/>
        </button>
        <p className="ktCheckoutTip"><Clock3/><span>Ödemeler genelde birkaç saat içinde onaylanır. Takılırsan <b>Destek</b> bölümünden yazabilirsin.</span></p>
      </div>
    </div>}
    {methodsError && <p className="ktBillAlert" role="alert">{methodsError} <button type="button" onClick={onRetry}><RefreshCw/>Yeniden dene</button></p>}

    <footer className="ktBillTrust">
      <div><CreditCard/><span><b>Otomatik ücret yok</b><small>Kartın veya cüzdanın kayıtlı tutulmaz.</small></span></div>
      <div><ShieldCheck/><span><b>Manuel doğrulama</b><small>Her ödeme yönetici tarafından kontrol edilir.</small></span></div>
      <div><Clock3/><span><b>Net süre</b><small>30 gün biter, istersen yenilersin.</small></span></div>
    </footer>
    <div className="ktBillDock" role="region" aria-label="Seçili plan" data-plan={PLAN_TABS[activePlan].key}>
      <div>
        <small>{activePlan === 0 ? 'ÜCRETSİZ DENEME' : activePlan === 1 ? 'SMART TRADE' : 'MASTER MODE'}</small>
        <strong>{activePlan === 0 ? '0' : activePlan === 1 ? PAID_PLAN_AMOUNT.SMART_TRADE : PLAN_BY_CODE.MASTER_MODE.monthlyPrice.toFixed(2)} <span>USDT / {activePlan === 0 ? '7' : '30'} gün</span></strong>
      </div>
      {activePlan === 0
        ? <button type="button" className="ktBillButton" onClick={() => void startTrial()} disabled={trialDisabled}>{trialLabel}</button>
        : <button type="button" className={`ktBillButton${activePlan === 2 ? ' isPrimary' : ''}`} onClick={() => goToPayment(activePlan === 1 ? 'SMART_TRADE' : 'MASTER_MODE')} disabled={!binanceEnabled || !authenticated || (binancePaid && hasAccess)}>{activePlan === 1 ? smartLabel : masterLabel}</button>}
    </div>
  </main>
}

import {useEffect, useRef, useState} from 'react'
import {ArrowRight, Check, Clock3, CreditCard, Crown, Lock, RefreshCw, ShieldCheck, Sparkles, Wallet} from 'lucide-react'
import {userSessionToken} from './api'
import BinanceManualPayment from './BinanceManualPayment'
import {trialRequest, type Trial} from './FreeTrialPricing'
import {PLAN_BY_CODE} from './subscription'
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
const TRIAL_FEATURES = ['Master Trade analiz, grafik ve piyasa izleme', 'AI Analist · mevcut kredi sınırıyla', 'Kais AI asistanı · günlük sınırlı', 'Original V2, Demo Auto Trade ve pozisyon koruması']
const MASTER_FEATURES = ['Master Trade analiz ve canlı işlem', 'Kais AI asistanı ve AI Analist', 'Original V2, Auto Trade ve pozisyon koruması', 'Demo ve LIVE işlem · güvenlik kontrolleriyle']

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
  const paymentRef = useRef<HTMLElement>(null)

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
  const planName = onTrial ? '1 Hafta Ücretsiz Deneme' : subscription?.plan ? 'MASTER MODE' : 'Aktif plan yok'
  const badge = loading ? 'Yükleniyor' : loadError ? 'Bilgi alınamadı' : hasAccess ? (onTrial ? 'Deneme aktif' : 'Aktif') : STATUS_TR[status] || 'Kapalı'
  const badgeTone = loading || loadError ? 'neutral' : hasAccess ? 'positive' : 'warning'

  const trialUsed = trial?.enabled === true && trial.used
  const trialDays = trial?.enabled === true && trial.ends_at ? Math.max(0, Math.ceil((Date.parse(trial.ends_at) - now) / 86400000)) : 0
  const trialActive = trial?.enabled === true && trial.active && trialDays > 0
  const trialLabel = !trialEnabled ? 'Deneme yakında açılacak' : !authenticated ? 'Giriş yapın'
    : trialLoading ? 'Yükleniyor…' : trialBusy ? 'Başlatılıyor…' : trialActive ? `Deneme aktif · ${trialDays} gün kaldı` : trialUsed ? 'Deneme hakkı kullanıldı' : 'Ücretsiz denemeyi başlat'
  const trialDisabled = !trialEnabled || !authenticated || trialLoading || trialBusy || !trial || trialUsed

  const masterLabel = methodsLoading ? 'Yükleniyor…' : methodsError ? 'Ödeme bilgisi alınamadı' : !binanceEnabled ? 'Ödeme yakında açılacak'
    : binancePaid && hasAccess ? 'Erişimin aktif' : 'Ödemeye geç'
  const goToPayment = () => paymentRef.current?.scrollIntoView({behavior: 'smooth', block: 'start'})

  return <main className="subscriptionPage ktBill">
    <header className="ktBillHero">
      <div>
        <span className="ktBillEyebrow">ABONELİK</span>
        <h1>Planını seç, Master Trade’i aç.</h1>
        <p>Ödeme bilgisi vermeden 7 gün dene ya da MASTER MODE ile tüm özelliklere eriş. Otomatik ücret yok.</p>
      </div>
    </header>

    <section className={`ktBillStatus tone-${badgeTone}`} aria-label="Mevcut abonelik">
      <div className="ktBillStatusMain">
        <div className="ktBillStatusIcon">{hasAccess ? <ShieldCheck/> : <Lock/>}</div>
        <div>
          <small>MEVCUT PLAN</small>
          <h2>{planName}</h2>
        </div>
        <span className="ktBillBadge">{badge}</span>
      </div>
      <dl className="ktBillStats">
        <div><dt>ERİŞİM</dt><dd>{subscription ? hasAccess ? 'Açık' : 'Kapalı' : '—'}</dd></div>
        <div><dt>BİTİŞ TARİHİ</dt><dd>{expiry ? trDate(expiry) : '—'}</dd></div>
        <div><dt>ÖDEME YÖNTEMİ</dt><dd>{binancePaid ? 'Binance' : onTrial ? 'Ödemesiz deneme' : '—'}</dd></div>
        <div><dt>YENİLEME</dt><dd>{binancePaid || onTrial ? 'Elle · otomatik ücret yok' : '—'}</dd></div>
      </dl>
      {hasAccess && <button type="button" className="ktBillGhost" onClick={onMasterTrade}><ShieldCheck/>Master Trade’e git<ArrowRight/></button>}
      {loadError && <p className="ktBillAlert" role="alert">Abonelik bilgileri yüklenemedi. <button type="button" onClick={onRetry}>Yeniden dene</button></p>}
    </section>

    <section className="ktBillPlans" aria-label="Planlar">
      <article className="ktBillPlan">
        <header>
          <div className="ktBillPlanIcon"><Sparkles/></div>
          <div><span className="ktBillTag">ÖDEMESİZ DENEME</span><h3>1 Hafta Ücretsiz Deneme</h3></div>
        </header>
        <p className="ktBillPlanLead">Kart veya ödeme bilgisi gerekmez. 7 gün boyunca çalışma alanını dene.</p>
        <div className="ktBillPrice"><strong>0</strong><span className="ktBillCurrency">USD</span><span className="ktBillPer">/ 7 gün</span></div>
        <ul>{TRIAL_FEATURES.map(feature => <li key={feature}><Check/>{feature}</li>)}</ul>
        <p className="ktBillNote"><Lock/>LIVE işlem MASTER MODE ile açılır.</p>
        {trialActive && <p className="ktBillInfo" role="status"><Clock3/>Kalan süre: <b>{trialDays} gün</b></p>}
        {trialUsed && !trialActive && <p className="ktBillInfo" role="status">Deneme sona erdi. Devam etmek için MASTER MODE’a geç.</p>}
        <button type="button" className="ktBillButton" onClick={() => void startTrial()} disabled={trialDisabled}>{trialLabel}</button>
        {trialError && <p className="ktBillAlert" role="alert">{trialError} <button type="button" onClick={() => setTrialAttempt(value => value + 1)}>Yeniden dene</button></p>}
        <small className="ktBillTerms">Hesap başına bir deneme. E-posta doğrulaması gerekir.</small>
      </article>

      <article className="ktBillPlan isFeatured">
        <span className="ktBillRibbon">ÖNERİLEN</span>
        <header>
          <div className="ktBillPlanIcon"><Crown/></div>
          <div><span className="ktBillTag">TAM ERİŞİM</span><h3>MASTER MODE</h3></div>
        </header>
        <p className="ktBillPlanLead">Yapay zekâ ve canlı işlem dahil tüm premium çalışma alanı.</p>
        <div className="ktBillPrice"><strong>{PLAN_BY_CODE.MASTER_MODE.monthlyPrice.toFixed(2)}</strong><span className="ktBillCurrency">USDT</span><span className="ktBillPer">/ 30 gün</span></div>
        <ul>{MASTER_FEATURES.map(feature => <li key={feature}><Check/>{feature}</li>)}</ul>
        <p className="ktBillNote"><Wallet/>Binance Pay ile ödenir, elle onaylanır.</p>
        <button type="button" className="ktBillButton isPrimary" onClick={goToPayment} disabled={!binanceEnabled || !authenticated || (binancePaid && hasAccess)}>{masterLabel}{binanceEnabled && !(binancePaid && hasAccess) && <ArrowRight/>}</button>
        <small className="ktBillTerms">Denemedeyken ödeme onaylanırsa 30 gün deneme bitişinden başlar. Otomatik yenileme yok.</small>
      </article>
    </section>

    {binanceEnabled && authenticated && <section className="ktBillPay" ref={paymentRef} aria-label="Binance ile ödeme">
      <div className="ktBillSteps">
        <span className="ktBillEyebrow">NASIL ÖDENİR?</span>
        <h2>3 adımda MASTER MODE</h2>
        <ol>
          <li><b>1</b><div><strong>Ödeme talebi oluştur</strong><span>“Binance ile öde”ye bas; sana özel referans kodu çıkar.</span></div></li>
          <li><b>2</b><div><strong>Binance Pay ile gönder</strong><span>119.90 USDT’yi Pay ID’ye gönder, ödeme notuna referans kodunu yaz.</span></div></li>
          <li><b>3</b><div><strong>İşlem numarasını gir</strong><span>48 saat içinde gönder. Onaylanınca erişimin açılır ve bildirim gelir.</span></div></li>
        </ol>
        <p className="ktBillStepsTip"><Clock3/><span>Ödemeler genelde birkaç saat içinde onaylanır. Takılırsan <b>Destek</b> bölümünden yazabilirsin.</span></p>
      </div>
      <div className="ktBillPayBox"><BinanceManualPayment/></div>
    </section>}
    {methodsError && <p className="ktBillAlert" role="alert">{methodsError} <button type="button" onClick={onRetry}><RefreshCw/>Yeniden dene</button></p>}

    <footer className="ktBillTrust">
      <div><CreditCard/><span><b>Otomatik ücret yok</b><small>Kartın veya cüzdanın kayıtlı tutulmaz.</small></span></div>
      <div><ShieldCheck/><span><b>Elle doğrulanan ödeme</b><small>Her ödeme yönetici tarafından kontrol edilir.</small></span></div>
      <div><Clock3/><span><b>Net süre</b><small>30 gün biter, istersen yenilersin.</small></span></div>
    </footer>
  </main>
}

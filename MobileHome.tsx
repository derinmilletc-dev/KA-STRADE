import { type CSSProperties, useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { API_BASE } from './api'
import { useMemberAccess } from './premium-access'
import './mobile-home.css'

// Telefon ana sayfası (<=700px). Tek ekran, kaydırma yok: selam, canlı/demo bakiye, abonelik durumu,
// piyasa ve öne çıkan fırsat. Sayfalar arası geçiş alttaki menüden yapılır.

type Market = {symbol: string; display: string; price: number; change: number}
type Target = 'setup' | 'billing' | 'analyst' | 'trading'
type Account = {wallet_balance?: number | null; unrealized_pnl?: number | null; positions?: unknown[]}
type LiveStatus = {connected?: boolean; credentials?: {configured?: boolean}; account?: Account}
type DemoAccount = Account & {configured?: boolean}
type Subscription = {status?: string; plan?: string; current_period_end?: string | null; trial_end?: string | null; cancel_at_period_end?: boolean; master_trade_access?: boolean}
type UniverseRow = {symbol: string; display: string; price: number; change: number; direction: string; final_decision_score?: number; smart_score?: number}

const NAMES: Record<string, string> = {BTCUSDT: 'Bitcoin', ETHUSDT: 'Ethereum', BNBUSDT: 'BNB', SOLUSDT: 'Solana', XRPUSDT: 'XRP'}
const WATCH = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']
const money = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
const price = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: value < 10 ? 4 : 2})
const pct = (value: number) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`
const signed = (value: number) => `${value >= 0 ? '+' : '−'}${money(Math.abs(value))}`
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const greeting = (hour: number) => hour < 5 ? 'İyi geceler' : hour < 12 ? 'Günaydın' : hour < 18 ? 'İyi günler' : 'İyi akşamlar'
const daysUntil = (iso?: string | null) => { const at = iso ? Date.parse(iso) : NaN; return Number.isFinite(at) ? Math.max(0, Math.ceil((at - Date.now()) / 86_400_000)) : null }
const longDate = (iso?: string | null) => { const at = iso ? new Date(iso) : null; return at && Number.isFinite(at.getTime()) ? at.toLocaleDateString('tr-TR', {day: 'numeric', month: 'long', year: 'numeric'}) : '' }

async function getJson<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {signal})
    return response.ok ? await response.json() as T : null
  } catch { return null }
}

export default function MobileHome({markets, onNavigate}: {markets: Market[]; onNavigate: (target: Target) => void}) {
  const {premium, freeTrial} = useMemberAccess()
  const [name, setName] = useState('')
  const [subscription, setSubscription] = useState<Subscription | null>(null)
  const [live, setLive] = useState<LiveStatus | null>(null)
  const [demo, setDemo] = useState<DemoAccount | null>(null)
  const [universe, setUniverse] = useState<UniverseRow[]>([])
  const [tab, setTab] = useState<'live' | 'demo' | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getJson<{user?: {display_name?: string; email?: string}}>('/v22/session', controller.signal).then(payload => {
      const user = payload?.user
      setName((user?.display_name || user?.email?.split('@')[0] || '').trim().split(/\s+/)[0] || '')
    })
    void getJson<Subscription>('/v22/subscription', controller.signal).then(setSubscription)
    const balances = () => {
      void getJson<LiveStatus>('/v25/status', controller.signal).then(setLive)
      void getJson<DemoAccount>('/binance-demo/account', controller.signal).then(setDemo)
    }
    const scan = () => void getJson<{results?: UniverseRow[]}>('/analysis-universe?interval=15m&limit=40', controller.signal).then(payload => setUniverse(Array.isArray(payload?.results) ? payload.results : []))
    balances(); scan()
    const balanceTimer = window.setInterval(balances, 30_000)
    const scanTimer = window.setInterval(scan, 60_000)
    return () => { controller.abort(); window.clearInterval(balanceTimer); window.clearInterval(scanTimer) }
  }, [])

  const liveReady = Boolean(live && (live.connected || live.credentials?.configured) && finite(live.account?.wallet_balance))
  const demoReady = Boolean(demo && demo.configured !== false && finite(demo.wallet_balance))
  const active = tab ?? (liveReady || !demoReady ? 'live' : 'demo')
  const shown = active === 'live' ? live?.account : demo
  const shownReady = active === 'live' ? liveReady : demoReady
  const other = active === 'live' ? demo : live?.account
  const otherReady = active === 'live' ? demoReady : liveReady

  const watch = useMemo(() => {
    const picked = WATCH.map(symbol => markets.find(item => item.symbol === symbol)).filter((item): item is Market => Boolean(item))
    return (picked.length ? picked : markets).slice(0, 4)
  }, [markets])
  const longs = universe.filter(row => row.direction === 'LONG').length
  const shorts = universe.filter(row => row.direction === 'SHORT').length
  const featured = useMemo(() => [...universe].filter(row => row.direction === 'LONG' || row.direction === 'SHORT')
    .sort((left, right) => (right.final_decision_score ?? right.smart_score ?? 0) - (left.final_decision_score ?? left.smart_score ?? 0))[0], [universe])

  const plan = (() => {
    const status = subscription?.status
    if (status === 'TRIALING' || (!subscription && freeTrial)) {
      const days = daysUntil(subscription?.trial_end)
      return {kind: 'trial', title: 'Deneme', detail: subscription?.trial_end ? `${longDate(subscription.trial_end)}'de sona erer` : 'Deneme süresi devam ediyor', days, progress: days === null ? 0 : Math.min(1, days / 7)}
    }
    if ((subscription?.plan === 'MASTER_MODE' && (status === 'ACTIVE' || status === 'PAST_DUE')) || (!subscription && premium)) {
      const days = daysUntil(subscription?.current_period_end)
      const end = longDate(subscription?.current_period_end)
      return {kind: 'master', title: 'Master Mode', detail: end ? `${end}'de ${subscription?.cancel_at_period_end ? 'sona erer' : 'yenilenir'}` : 'Abonelik aktif', days, progress: days === null ? 1 : Math.min(1, days / 30)}
    }
    return {kind: 'free', title: 'Ücretsiz plan', detail: 'Master ile tüm sinyaller ve seviyeler', days: null, progress: 0}
  })()

  const direction = longs > shorts ? 'Yükseliş' : shorts > longs ? 'Düşüş' : universe.length ? 'Dengeli' : ''

  return <section className="mobileHome" aria-label="Ana sayfa">
    <p className="mhHello">{greeting(new Date().getHours())}{name ? <>, <b>{name}</b></> : null}</p>

    <div className="mhBalance">
      <div className="mhBalanceHead">
        <small>{active === 'live' ? 'Toplam bakiye · Binance Futures' : 'Demo hesap bakiyesi'}</small>
        <div className="mhTabs" role="tablist" aria-label="Hesap">
          <button type="button" role="tab" aria-selected={active === 'live'} className={active === 'live' ? 'on' : ''} onClick={() => setTab('live')}>Canlı</button>
          <button type="button" role="tab" aria-selected={active === 'demo'} className={active === 'demo' ? 'on' : ''} onClick={() => setTab('demo')}>Demo</button>
        </div>
      </div>
      {shownReady && shown ? <>
        <div className="mhAmount">{money(shown.wallet_balance as number)}<span>USDT</span></div>
        <div className="mhChange">
          {finite(shown.unrealized_pnl) && <span>Gerçekleşmemiş <b className={shown.unrealized_pnl >= 0 ? 'up' : 'down'}>{signed(shown.unrealized_pnl)}</b></span>}
          <span>{Array.isArray(shown.positions) ? shown.positions.length : 0} açık işlem</span>
        </div>
      </> : <div className="mhEmpty">
        <span>{active === 'live' ? 'Canlı hesap bağlı değil' : 'Demo hesap bağlı değil'}</span>
        <button type="button" onClick={() => onNavigate('setup')}>Bağla</button>
      </div>}
      <div className="mhOther">
        <span>{active === 'live' ? 'Demo hesap' : 'Canlı hesap'}</span>
        <span>{otherReady && other ? <><b>{money(other.wallet_balance as number)}</b> USDT</> : 'Bağlı değil'}</span>
      </div>
    </div>

    <button type="button" className={`mhPlan mhPlan-${plan.kind}`} onClick={() => onNavigate('billing')}>
      <span className="mhRing" style={{'--p': `${Math.round(plan.progress * 100)}%`} as CSSProperties}><span>{plan.days === null ? (plan.kind === 'free' ? '—' : '✓') : `${plan.days}g`}</span></span>
      <span className="mhPlanText"><b>{plan.title}</b><small>{plan.detail}</small></span>
      {plan.kind === 'master' ? <span className="mhPlanState">Aktif</span> : <span className="mhPlanCta">Yükselt</span>}
    </button>

    <div className="mhMarket">
      <div className="mhMarketHead"><h2>Piyasa</h2>{direction && <span>Genel yön: <b className={direction === 'Düşüş' ? 'down' : direction === 'Yükseliş' ? 'up' : ''}>{direction}</b></span>}</div>
      {watch.length ? watch.map(item => <div className="mhRow" key={item.symbol}>
        <i>{item.symbol.replace(/USDT$/, '')}</i>
        <b>{NAMES[item.symbol] || item.display}</b>
        <span>{price(item.price)}</span>
        <em className={item.change >= 0 ? 'up' : 'down'}>{pct(item.change)}</em>
      </div>) : <p className="mhMuted">Piyasa verisi bekleniyor…</p>}
    </div>

    {featured ? <button type="button" className="mhOpp" onClick={() => onNavigate('analyst')}>
      <span className="mhScore">{Math.round(featured.final_decision_score ?? featured.smart_score ?? 0)}</span>
      <span className="mhOppText"><small>Öne çıkan fırsat</small><b>{featured.display}<span className={featured.direction === 'SHORT' ? 'down' : 'up'}>{featured.direction === 'SHORT' ? 'Short' : 'Long'}</span></b><small>{price(featured.price)} · 24s {pct(featured.change)}</small></span>
      <ChevronRight aria-hidden="true"/>
    </button> : <div className="mhOpp mhOppEmpty"><span className="mhOppText"><small>Öne çıkan fırsat</small><b>Tarama yapılıyor…</b></span></div>}
  </section>
}

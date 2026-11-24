import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, BarChart3, ChevronRight, Sparkles, Wallet } from 'lucide-react'
import { API_BASE } from './api'
import { useMemberAccess } from './premium-access'
import { currentLang } from './i18n'
import { accountNetPnl } from './net-pnl'
import {useCoinbaseVisible} from './frontend/src/useCoinbaseVisible'
import {netOpenPnl} from './frontend/src/coinbase-position'
import {readNetBalance, todayText} from './frontend/src/coinbase-net-balance'
import {readExchange, writeExchange} from './frontend/src/exchange-pref'
import type {Exchange} from './frontend/src/CoinbaseExchangeSwitch'
import './mobile-home.css'

// Telefon ana sayfası (<=700px). Tek ekran, kaydırma yok: selam, canlı/demo bakiye, abonelik durumu,
// piyasa ve öne çıkan fırsat. Sayfalar arası geçiş alttaki menüden yapılır.

type Market = {symbol: string; display: string; price: number; change: number}
type Target = 'setup' | 'billing' | 'analyst' | 'trading'
type Account = {wallet_balance?: number | null; unrealized_pnl?: number | null; positions?: unknown[]; net?: boolean; today?: string | null}
type CbPnlRow = {product_id?: unknown; unrealized_pnl?: unknown}
type LiveStatus = {connected?: boolean; credentials?: {configured?: boolean}; account?: Account}
type DemoAccount = Account & {configured?: boolean}
type Subscription = {status?: string; plan?: string; current_period_end?: string | null; trial_end?: string | null; cancel_at_period_end?: boolean; master_trade_access?: boolean}
type UniverseRow = {symbol: string; display: string; price: number; change: number; direction: string; final_decision_score?: number; smart_score?: number}

// Metinler bileşende iki dilli tutulur: tarihli cümleler sözlük çevirisiyle doğru kurulamaz.
const COPY = {
  tr: {greet: (h: number) => h < 5 ? 'İyi geceler' : h < 12 ? 'Günaydın' : h < 18 ? 'İyi günler' : 'İyi akşamlar', home: 'Ana sayfa',
    liveTitle: 'Toplam bakiye · Binance Futures', coinbaseTitle: 'Bakiye · Coinbase (vadeli + spot)', exchange: 'Borsa', demoTitle: 'Demo hesap bakiyesi', account: 'Hesap', live: 'Canlı', demo: 'Demo',
    unrealized: 'Gerçekleşmemiş', open: (n: number) => `${n} açık işlem`, liveOff: 'Canlı hesap bağlı değil', demoOff: 'Demo hesap bağlı değil',
    connect: 'Bağla', portfolio: 'Portföyüm', setupSub: 'Bakiyeni ve işlemlerini takip et.', aiTag: 'AI FIRSATI', setupTitle: 'Hesabını bağla', setupText: 'Binance hesabını ya da demo hesabını bağla; bakiyen ve işlemlerin burada görünsün.', setupLive: 'Binance bağla', setupDemo: 'Demo hesap', notConnectedLink: 'Bağla', selectedOff: 'Bu hesap henüz bağlı değil.', demoAcc: 'Demo hesap', liveAcc: 'Canlı hesap', notConnected: 'Bağlı değil',
    trial: 'Deneme', trialEnds: (d: string) => `${d} tarihinde sona erer`, trialOn: 'Deneme süresi devam ediyor',
    master: 'Master Mode', renews: (d: string) => `${d} tarihinde yenilenir`, ends: (d: string) => `${d} tarihinde sona erer`, active: 'Aktif',
    free: 'Ücretsiz plan', freeDetail: 'AI sinyalleri ve piyasa analizi', upgrade: 'Yükselt', days: (n: number) => `${n}g`,
    market: 'Piyasa', direction: 'Genel yön', up: 'Yükseliş', down: 'Düşüş', flat: 'Dengeli', waiting: 'Piyasa verisi bekleniyor…',
    featured: 'Öne çıkan fırsat', scanning: 'Tarama yapılıyor…', change24: '24s', locale: 'tr-TR',
    quick: 'Kısayollar', qAnalyst: 'Analist', qDemo: 'Demo işlem', qAccount: 'Hesap', pulse: 'Tarama dağılımı'},
  en: {greet: (h: number) => h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening', home: 'Home',
    liveTitle: 'Total balance · Binance Futures', coinbaseTitle: 'Balance · Coinbase (futures + spot)', exchange: 'Exchange', demoTitle: 'Demo account balance', account: 'Account', live: 'Live', demo: 'Demo',
    unrealized: 'Unrealized', open: (n: number) => `${n} open ${n === 1 ? 'trade' : 'trades'}`, liveOff: 'Live account not connected', demoOff: 'Demo account not connected',
    connect: 'Connect', portfolio: 'Portfolio', setupSub: 'Track your balance and trades.', aiTag: 'AI SETUP', setupTitle: 'Connect your account', setupText: 'Connect your Binance or demo account to see your balance and trades here.', setupLive: 'Connect Binance', setupDemo: 'Demo account', notConnectedLink: 'Connect', selectedOff: 'This account is not connected yet.', demoAcc: 'Demo account', liveAcc: 'Live account', notConnected: 'Not connected',
    trial: 'Trial', trialEnds: (d: string) => `Ends on ${d}`, trialOn: 'Trial in progress',
    master: 'Master Mode', renews: (d: string) => `Renews on ${d}`, ends: (d: string) => `Ends on ${d}`, active: 'Active',
    free: 'Free plan', freeDetail: 'AI signals and market analysis', upgrade: 'Upgrade', days: (n: number) => `${n}d`,
    market: 'Market', direction: 'Trend', up: 'Bullish', down: 'Bearish', flat: 'Neutral', waiting: 'Waiting for market data…',
    featured: 'Top opportunity', scanning: 'Scanning…', change24: '24h', locale: 'en-US',
    quick: 'Shortcuts', qAnalyst: 'Analyst', qDemo: 'Demo trade', qAccount: 'Account', pulse: 'Scan breakdown'},
}
const NAMES: Record<string, string> = {BTCUSDT: 'Bitcoin', ETHUSDT: 'Ethereum', BNBUSDT: 'BNB', SOLUSDT: 'Solana', XRPUSDT: 'XRP'}
const WATCH = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']
const money = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
const price = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: value < 10 ? 4 : 2})
const pct = (value: number) => `${value >= 0 ? '+' : '−'}${Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`
const signed = (value: number) => `${value >= 0 ? '+' : '−'}${money(Math.abs(value))}`
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const daysUntil = (iso?: string | null) => { const at = iso ? Date.parse(iso) : NaN; return Number.isFinite(at) ? Math.max(0, Math.ceil((at - Date.now()) / 86_400_000)) : null }
const longDate = (iso: string | null | undefined, locale: string) => { const at = iso ? new Date(iso) : null; return at && Number.isFinite(at.getTime()) ? at.toLocaleDateString(locale, {day: 'numeric', month: 'long', year: 'numeric'}) : '' }

const BinanceMark = () => <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="m12 3.5 2.2 2.2L9.4 10.5 7.2 8.3 12 3.5Zm4.8 4.8 2.2 2.2-2.2 2.2-2.2-2.2 2.2-2.2ZM7.2 13.7l2.2 2.2-2.2 2.2L5 15.9l2.2-2.2Zm9.6 0 2.2 2.2-2.2 2.2-2.2-2.2 2.2-2.2ZM12 8.9l3.1 3.1-3.1 3.1L8.9 12 12 8.9Zm0 7.6 2.2 2.2L12 21l-2.2-2.3 2.2-2.2Z"/></svg>

function Coin({symbol}: {symbol: string}) {
  const base = symbol.replace(/USDT$/, '')
  const glyph = base === 'BTC' ? <text x="12" y="16.6" textAnchor="middle" fontSize="13" fontWeight="700" fill="#fff">₿</text>
    : base === 'ETH' ? <path fill="#fff" d="M12 4.5 7.3 12.2 12 15l4.7-2.8L12 4.5Zm0 11.6-4.7-2.8L12 19.5l4.7-6.2L12 16.1Z"/>
    : base === 'BNB' ? <path fill="#fff" d="m12 5 2 2-4.4 4.4-2-2L12 5Zm4.4 4.4 2 2-2 2-2-2 2-2Zm-8.8 4.2 2 2-2 2-2-2 2-2Zm8.8 0 2 2-2 2-2-2 2-2ZM12 10l2 2-2 2-2-2 2-2Zm0 7 2 2-2 2-2-2 2-2Z" transform="translate(0 -1.5)"/>
    : base === 'SOL' ? <g fill="#9be8c8"><path d="M7 8h10.5l-1.3 1.7H5.7zM7 11.2h10.5l-1.3 1.7H5.7zM5.7 14.4h10.5L17.5 16H7z"/></g>
    : <text x="12" y="15.4" textAnchor="middle" fontSize="8.5" fontWeight="700" fill="#cbd5e1">{base.slice(0, 3)}</text>
  return <svg className={`mhCoin mhCoin-${base}`} viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12"/>{glyph}</svg>
}

async function getJson<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {signal})
    return response.ok ? await response.json() as T : null
  } catch { return null }
}

export default function MobileHome({markets, onNavigate}: {markets: Market[]; onNavigate: (target: Target) => void}) {
  const {premium, freeTrial, ready} = useMemberAccess()
  const t = COPY[currentLang() === 'en' ? 'en' : 'tr']
  const [name, setName] = useState('')
  const [subscription, setSubscription] = useState<Subscription | null>(null)
  const [live, setLive] = useState<LiveStatus | null>(null)
  const [demo, setDemo] = useState<DemoAccount | null>(null)
  const [universe, setUniverse] = useState<UniverseRow[]>([])
  const [tab, setTab] = useState<'live' | 'demo' | null>(null)
  const coinbaseVisible = useCoinbaseVisible()
  const [coinbase, setCoinbase] = useState<Account | null>(null)
  const cbFees = useRef<Record<string, string | null>>({})
  const cbOpen = useRef(false)
  useEffect(() => { cbOpen.current = (coinbase?.positions?.length ?? 0) > 0 }, [coinbase])
  const [exchangePick, setExchangePick] = useState<Exchange | null>(() => readExchange())
  // İlk yanıtlar gelene kadar yanlış durum (ör. bağlı hesap için "Hesabını bağla", premium için "Ücretsiz plan") göstermeyiz.
  const [loaded, setLoaded] = useState({session: false, subscription: false, live: false, demo: false})
  const [timedOut, setTimedOut] = useState(false)
  const settled = timedOut || (ready && loaded.session && loaded.subscription && loaded.live && loaded.demo)
  const markLoaded = (key: keyof typeof loaded) => setLoaded(current => current[key] ? current : {...current, [key]: true})

  useEffect(() => {
    const controller = new AbortController()
    void getJson<{user?: {display_name?: string; email?: string}}>('/v22/session', controller.signal).then(payload => {
      const user = payload?.user
      setName((user?.display_name || user?.email?.split('@')[0] || '').trim().split(/\s+/)[0] || '')
      markLoaded('session')
    })
    void getJson<Subscription>('/v22/subscription', controller.signal).then(value => { setSubscription(value); markLoaded('subscription') })
    const balances = () => {
      void getJson<LiveStatus>('/v25/status', controller.signal).then(value => { setLive(value); markLoaded('live') })
      void getJson<DemoAccount>('/binance-demo/account', controller.signal).then(value => { setDemo(value); markLoaded('demo') })
    }
    const scan = () => void getJson<{results?: UniverseRow[]}>('/analysis-universe?interval=15m&limit=40', controller.signal).then(payload => setUniverse(Array.isArray(payload?.results) ? payload.results : []))
    // Coinbase (yalnız izinli hesaplarda): anahtar kayıtlı değilse ya da okunamazsa istek null döner, kart Binance'te kalır.
    const coinbaseBalance = () => void Promise.all([
      getJson<{balance?: {cfm_usd_balance?: {value?: string | null} | null; total_usd_balance?: {value?: string | null} | null; unrealized_pnl?: {value?: string | null} | null}}>('/coinbase/account', controller.signal),
      getJson<{positions?: unknown[]}>('/coinbase/positions', controller.signal),
      getJson<unknown>('/coinbase/net-balance', controller.signal),
    ]).then(([account, positions, netRaw]) => {
      const balance = account?.balance
      // Para çoğu zaman spot hesapta durur ve işlem açılınca vadeliye geçer: vadeli hesap (cfm) 0 görünse de toplam (cbi + cfm) doludur.
      const total = Number(balance?.total_usd_balance?.value)
      const futuresOnly = Number(balance?.cfm_usd_balance?.value)
      const settled = Number.isFinite(total) && total > 0 ? Math.max(total, Number.isFinite(futuresOnly) ? futuresOnly : 0) : futuresOnly
      // Tek Net bakiye (hesap kartıyla aynı sunucu rakamı); sunucu henüz güncel değilse eski bakiye.
      const netBal = readNetBalance(netRaw)
      const wallet = netBal ? netBal.netBalance : settled
      const pnl = Number(balance?.unrealized_pnl?.value)
      const rows = Array.isArray(positions?.positions) ? positions.positions as CbPnlRow[] : []
      // K/Z pozisyon ekranıyla aynı hesap: Coinbase pozisyon K/Z'si − ödenen komisyon (netOpenPnl).
      const net = netOpenPnl(rows, cbFees.current)
      setCoinbase(balance && Number.isFinite(wallet) ? {wallet_balance: wallet, unrealized_pnl: net ? net.value : Number.isFinite(pnl) ? pnl : null, positions: rows, net: net?.withFees ?? false, today: netBal ? todayText(netBal) : null} : null)
    })
    const coinbaseFees = () => void getJson<{fees?: Record<string, string | null>}>('/coinbase/positions/fees', controller.signal)
      .then(value => { if (value?.fees && typeof value.fees === 'object') cbFees.current = value.fees })
    // Açık pozisyon K/Z'si saniyede bir (pozisyon ekranıyla aynı hız ve aynı kaynak).
    const coinbasePnl = () => void getJson<{positions?: unknown[]}>('/coinbase/positions', controller.signal).then(value => {
      if (!Array.isArray(value?.positions)) return
      const rows = value.positions as CbPnlRow[]
      const net = netOpenPnl(rows, cbFees.current)
      setCoinbase(current => current ? {...current, positions: rows, unrealized_pnl: net ? net.value : rows.length === 0 ? 0 : current.unrealized_pnl, net: net?.withFees ?? false} : current)
    })
    if (coinbaseVisible) { coinbaseFees(); coinbaseBalance() }
    const pnlTimer = window.setInterval(() => { if (coinbaseVisible && document.visibilityState === 'visible' && cbOpen.current) coinbasePnl() }, 1000)
    const feeTimer = window.setInterval(() => { if (coinbaseVisible && cbOpen.current) coinbaseFees() }, 30_000)
    balances(); scan()
    const balanceTimer = window.setInterval(() => { balances(); if (coinbaseVisible) coinbaseBalance() }, 30_000)
    const scanTimer = window.setInterval(scan, 60_000)
    // Yavaş/asılı istekte sayfa sonsuza dek iskelette kalmasın.
    const settleTimer = window.setTimeout(() => setTimedOut(true), 4000)
    return () => { controller.abort(); window.clearInterval(pnlTimer); window.clearInterval(feeTimer); window.clearInterval(balanceTimer); window.clearInterval(scanTimer); window.clearTimeout(settleTimer) }
  }, [coinbaseVisible])

  const liveReady = Boolean(live && (live.connected || live.credentials?.configured) && finite(live.account?.wallet_balance))
  const demoReady = Boolean(demo && demo.configured !== false && finite(demo.wallet_balance))
  const coinbaseReady = coinbaseVisible && Boolean(coinbase && finite(coinbase.wallet_balance))
  // Seçili borsa: kayıtlı tercih; tercih yoksa Binance bağlı değil ama Coinbase bağlıysa Coinbase.
  const exchange: Exchange = !coinbaseVisible ? 'binance' : exchangePick ?? (!liveReady && coinbaseReady ? 'coinbase' : 'binance')
  const onCoinbase = exchange === 'coinbase'
  const pickExchange = (next: Exchange) => { writeExchange(next); setExchangePick(next) }
  const liveShownReady = onCoinbase ? coinbaseReady : liveReady
  const active = tab ?? (liveShownReady || !demoReady ? 'live' : 'demo')
  const shown = active === 'live' ? (onCoinbase ? coinbase : live?.account) : demo
  const shownReady = active === 'live' ? liveShownReady : demoReady
  const other = active === 'live' ? demo : (onCoinbase ? coinbase : live?.account)
  const otherReady = active === 'live' ? demoReady : liveShownReady

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
      return {kind: 'trial', title: t.trial, detail: subscription?.trial_end ? t.trialEnds(longDate(subscription.trial_end, t.locale)) : t.trialOn, days, progress: days === null ? 0 : Math.min(1, days / 7)}
    }
    if ((subscription?.plan === 'MASTER_MODE' && (status === 'ACTIVE' || status === 'PAST_DUE')) || premium) {
      const days = daysUntil(subscription?.current_period_end)
      const end = longDate(subscription?.current_period_end, t.locale)
      return {kind: 'master', title: t.master, detail: end ? (subscription?.cancel_at_period_end ? t.ends(end) : t.renews(end)) : t.active, days, progress: days === null ? 1 : Math.min(1, days / 30)}
    }
    return {kind: 'free', title: t.free, detail: t.freeDetail, days: null, progress: 0}
  })()

  const direction = longs > shorts ? t.up : shorts > longs ? t.down : universe.length ? t.flat : ''

  return <section className="mobileHome" aria-label={t.home} data-i18n-skip="">
    <p className="mhHello">{t.greet(new Date().getHours())}{name ? <>, <b>{name}</b></> : null}</p>

    {!settled ? <div className="mhCard mhSkel" aria-busy="true" aria-hidden="true"><i className="w40"/><i className="big"/><i className="w60"/></div>
    : !liveReady && !demoReady && !coinbaseReady ? <div className="mhCard mhSetup">
      <div className="mhCoinArt" aria-hidden="true"><Coin symbol="ETHUSDT"/><Coin symbol="BNBUSDT"/><Coin symbol="BTCUSDT"/></div>
      <div className="mhCardHead"><span className="mhIconTile"><Wallet aria-hidden="true"/></span><b>{t.portfolio}</b></div>
      <h2>{t.setupTitle}</h2>
      <strong>{t.setupSub}</strong>
      <p>{t.setupText}</p>
      <div className="mhSetupActions">
        <button type="button" className="primary" onClick={() => onNavigate('setup')}><BinanceMark/>{t.setupLive}</button>
        <button type="button" onClick={() => onNavigate('setup')}><BarChart3 aria-hidden="true"/>{t.setupDemo}</button>
      </div>
    </div> : <div className="mhCard mhBalance">
      <div className="mhCardHead">
        <span className="mhIconTile"><Wallet aria-hidden="true"/></span><b>{t.portfolio}</b>
        <div className="mhTabs" role="tablist" aria-label={t.account}>
          <button type="button" role="tab" aria-selected={active === 'live'} className={active === 'live' ? 'on' : ''} onClick={() => setTab('live')}>{t.live}</button>
          <button type="button" role="tab" aria-selected={active === 'demo'} className={active === 'demo' ? 'on' : ''} onClick={() => setTab('demo')}>{t.demo}</button>
        </div>
      </div>
      {coinbaseVisible && active === 'live' && <div className="mhTabs mhExchange" role="tablist" aria-label={t.exchange}>
        <button type="button" role="tab" aria-selected={!onCoinbase} className={!onCoinbase ? 'on' : ''} onClick={() => pickExchange('binance')}>Binance</button>
        <button type="button" role="tab" aria-selected={onCoinbase} className={onCoinbase ? 'on' : ''} onClick={() => pickExchange('coinbase')}>Coinbase</button>
      </div>}
      <small className="mhBalanceLabel">{active === 'live' ? (onCoinbase ? t.coinbaseTitle : t.liveTitle) : t.demoTitle}</small>
      {shownReady && shown ? <>
        <div className="mhAmount">{money(shown.wallet_balance as number)}<span>{active === 'live' && onCoinbase ? 'USD' : 'USDT'}</span></div>
        <div className="mhChange">
          {finite(shown.unrealized_pnl) && (() => { const open = active === 'live' && onCoinbase ? {value: null, net: Boolean(shown.net)} : accountNetPnl(shown.positions, shown.unrealized_pnl); const value = open.value ?? shown.unrealized_pnl; return <span>{t.unrealized}{open.net ? ' (net)' : ''} <b className={value >= 0 ? 'up' : 'down'}>{signed(value)}</b></span> })()}
          {active === 'live' && onCoinbase && (shown as Account).today ? <span>{(shown as Account).today}</span> : null}
          <span>{t.open(Array.isArray(shown.positions) ? shown.positions.length : 0)}</span>
        </div>
      </> : <div className="mhEmpty">
        <span>{t.selectedOff}</span>
        <button type="button" onClick={() => onNavigate('setup')}>{t.connect}</button>
      </div>}
      <div className="mhOther">
        <span>{active === 'live' ? t.demoAcc : t.liveAcc}</span>
        {otherReady && other ? <span><b>{money(other.wallet_balance as number)}</b> {active === 'demo' && onCoinbase ? 'USD' : 'USDT'}</span>
          : <button type="button" className="mhLink" onClick={() => onNavigate('setup')}>{t.notConnectedLink}<ChevronRight aria-hidden="true"/></button>}
      </div>
    </div>}

    <div className="mhQuick" role="group" aria-label={t.quick}>
      <button type="button" onClick={() => onNavigate('analyst')}><Sparkles aria-hidden="true"/><span>{t.qAnalyst}</span></button>
      <button type="button" onClick={() => onNavigate('trading')}><Activity aria-hidden="true"/><span>{t.qDemo}</span></button>
      <button type="button" onClick={() => onNavigate('setup')}><Wallet aria-hidden="true"/><span>{t.qAccount}</span></button>
    </div>

    {!settled ? <div className="mhPlan mhSkel mhSkelPlan" aria-hidden="true"><i className="big"/><i className="w60"/></div> : <button type="button" className={`mhPlan mhPlan-${plan.kind}`} onClick={() => onNavigate('billing')}>
      {plan.kind === 'free' ? <span className="mhIconTile mhPlanIcon"><Sparkles aria-hidden="true"/></span>
        : <span className="mhRing" style={{'--p': `${Math.round(plan.progress * 100)}%`} as CSSProperties}><span>{plan.days === null ? '✓' : t.days(plan.days)}</span></span>}
      <span className="mhPlanText"><b>{plan.title}</b><small>{plan.detail}</small></span>
      {plan.kind === 'master' ? <span className="mhPlanState">{t.active}</span> : <span className="mhPlanCta">{t.upgrade}<ChevronRight aria-hidden="true"/></span>}
    </button>}

    <div className="mhMarket">
      <div className="mhMarketHead"><h2>{t.market}</h2>{direction && <span>{t.direction}: <b className={direction === t.down ? 'down' : direction === t.up ? 'up' : ''}>{direction}</b></span>}</div>
      {universe.length > 0 && <div className="mhPulse" aria-label={t.pulse}>
        <div className="mhPulseBar" role="img" aria-label={`${longs} LONG, ${shorts} SHORT, ${universe.length - longs - shorts} ${t.flat}`}>
          <i className="up" style={{flexGrow: longs}}/><i className="flat" style={{flexGrow: universe.length - longs - shorts}}/><i className="down" style={{flexGrow: shorts}}/>
        </div>
        <div className="mhPulseLegend"><span className="up">{longs} LONG</span><span>{universe.length - longs - shorts} {t.flat}</span><span className="down">{shorts} SHORT</span></div>
      </div>}
      {watch.length ? watch.map(item => <div className="mhRow" key={item.symbol}>
        <Coin symbol={item.symbol}/>
        <b>{item.symbol.replace(/USDT$/, '')}<small>{NAMES[item.symbol] || item.display}</small></b>
        <span>{price(item.price)}</span>
        <em className={item.change >= 0 ? 'up' : 'down'}>{pct(item.change)}</em>
      </div>) : <p className="mhMuted">{t.waiting}</p>}
    </div>

    {featured ? <button type="button" className="mhOpp" onClick={() => onNavigate('analyst')}>
      <span className="mhScoreBox"><small>{t.aiTag}</small><span className="mhScore">{Math.round(featured.final_decision_score ?? featured.smart_score ?? 0)}</span>
        <i className="mhMeter"><i style={{width: `${Math.max(4, Math.min(100, Math.round(featured.final_decision_score ?? featured.smart_score ?? 0)))}%`}}/></i></span>
      <span className="mhOppText"><b><span className="mhPair">{featured.display}</span><span className={featured.direction === 'SHORT' ? 'mhDir down' : 'mhDir up'}>{featured.direction === 'SHORT' ? 'SHORT' : 'LONG'}</span></b><small>{price(featured.price)} · {t.change24} <em className={featured.change >= 0 ? 'up' : 'down'}>{pct(featured.change)}</em></small></span>
      <ChevronRight aria-hidden="true"/>
    </button> : <div className="mhOpp mhOppEmpty"><span className="mhOppText"><small>{t.featured}</small><b>{t.scanning}</b></span></div>}
  </section>
}

import {useEffect, useMemo, useRef, useState, type CSSProperties} from 'react'
import {ChevronDown, ChevronUp, Check, TriangleAlert, X} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang} from './i18n'
import {analysisPrice} from './master-trade-presentation'
import {requestPositionsRefresh} from './trade-intent'
import {liveBlockerMessage, type SharedLiveStatus} from './frontend/src/LiveTradingPanel'
import {
  DEMO_CLOSE_PHRASE, DEMO_PHRASE, DEMO_REDUCE_PHRASE, DEMO_STOP_PHRASE, DEMO_TRAIL_PHRASE, LIVE_TRAIL_PHRASE, TRAIL_CHOICES, JOURNAL_KEY, LIMIT_EXPIRY_CHOICES, LIVE_CLOSE_PHRASE, LIVE_PHRASE, LIVE_REDUCE_PHRASE, RISK_PCT_CHOICES,
  activePlan, addJournal, allowedLeverages, allowedMargins, breakEvenStop, isOpen, levelsFor, limitIssue, limitPresets, nearestChoice, newIntentId, parseJournal, planRights, postJson, riskSummary,
  setLanguageReader, sideOf, sizeByRisk, stopIssue, tightenStop, tx, netOfFees,
  type AnalysisLevels, type JournalEntry, type LimitExpiry, type Levels, type OrderKind, type PositionLike, type Side,
} from './studio-quick-trade'
import {JournalChip, JournalPanel} from './StudioJournal'
import {HoldButton, useHold} from './studio-hold'
import StudioGrid from './StudioGrid'
import {ConsentRenew} from './StudioConsent'
import type {Candle, ChartTools, GridChartLine} from './studio-grid'
import {bookState, freshClosePrice, type BookState, type FuturesBook} from './futures-book'
import './studio-quick-trade.css'
import './studio-modern.css'

setLanguageReader(() => currentLang() === 'en')

export type StudioPosition = PositionLike & {stop_loss?: number; tp1?: number; tp2?: number; tp3?: number; plan_source?: string | null; trail_pct?: number | null}
export type StudioDecision = {direction: string; confidenceScore: number | null; mtfScore: number | null; mtfConfirmed: number; mtfTotal: number; status: string}

const V25 = `${API_BASE}/v25`
const DEMO = `${API_BASE}/binance-demo`
const PREFS_KEY = 'kaistrade-studio-trade-prefs'
const HOLD_MS = {live: 1200, demo: 700}
/** Seviyeler dondurulduğunda fiyat bundan fazla oynarsa emir gönderilmez; seviyeler yenilenir. */
const MAX_DRIFT_PCT = .5

const locale = () => currentLang() === 'en' ? 'en-US' : 'tr-TR'
const fmt = (value: number, digits = 2) => value.toLocaleString(locale(), {minimumFractionDigits: digits, maximumFractionDigits: digits})
const signed = (value: number, digits = 2) => `${value >= 0 ? '+' : '−'}${fmt(Math.abs(value), digits)}`
const positive = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
const short = (symbol: string) => symbol.replace(/USDT$/, '')

type Prefs = {margin: number; leverage: number; sizing: 'fixed' | 'risk'; riskPct: number}
const DEFAULT_PREFS: Prefs = {margin: 25, leverage: 2, sizing: 'fixed', riskPct: 1}
const readPrefs = (): Prefs => {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')
    const value = typeof raw === 'object' && raw !== null ? raw as Record<string, unknown> : {}
    const riskPct = Number(value.riskPct)
    return {margin: Number(value.margin) || 25, leverage: Number(value.leverage) || 2, sizing: value.sizing === 'risk' ? 'risk' : 'fixed', riskPct: (RISK_PCT_CHOICES as readonly number[]).includes(riskPct) ? riskPct : 1}
  } catch { return DEFAULT_PREFS }
}
const savePrefs = (prefs: Prefs) => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)) } catch { /* tarayıcı kaydı kapalı olabilir */ } }

/** Anlık kâr/zarar: açık pozisyonun coini için canlı fiyattan, diğerleri için hesabın son değerinden. */
export function positionPnl(position: StudioPosition, livePrice: number | null): {pnl: number | null; pct: number | null; net: number | null} {
  const qty = Math.abs(Number(position.quantity) || 0)
  const entry = positive(position.entry_price)
  const now = positive(livePrice) ?? positive(position.mark_price)
  const sign = sideOf(position) === 'LONG' ? 1 : -1
  const pnl = entry && now && qty > 0 ? (now - entry) * qty * sign : typeof position.unrealized_pnl === 'number' && Number.isFinite(position.unrealized_pnl) ? position.unrealized_pnl : null
  const lev = positive(position.leverage ?? undefined)
  const margin = entry && qty > 0 && lev ? entry * qty / lev : null
  // `net`: giriş+çıkış komisyonu düşülmüş tahmin. `pnl` (brüt) değişmez; net ayrı gösterilir.
  return {pnl, pct: pnl !== null && margin ? pnl / margin * 100 : null, net: netOfFees(pnl, entry, now ?? entry, qty)}
}

/** Açık işlem varken sağ altta: pozisyonu kapatan tek tuş (LONG → SAT, SHORT → AL). Basılı tutarak çalışır; yeni işlem açmaz. */
function CloseDock({position, demo, disabled, onDone}: {position: StudioPosition; demo: boolean; disabled: boolean; onDone: () => void}) {
  const long = sideOf(position) === 'LONG'
  const ms = demo ? HOLD_MS.demo : HOLD_MS.live
  const {holding, bind} = useHold(ms, onDone, disabled) // yazı basılıyken değişmez: tuş boyutu sabit kalır
  const word = long ? tx('SAT', 'SELL') : tx('AL', 'BUY')
  const lev = positive(position.leverage ?? undefined)
  return <div className="sqtCloseDock" data-demo={demo || undefined} role="group" aria-label={tx('Pozisyonu kapat', 'Close position')}>
    <span className="sqtCloseInfo"><span className="sqtMode" data-demo={demo || undefined}>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span><b>{short(position.symbol)} · {sideOf(position)}{lev ? ` ${lev}x` : ''}</b></span>
    <button type="button" className="sqtSide sqtCloseBtn" data-side={long ? 'sell' : 'buy'} data-holding={holding} disabled={disabled} style={{'--sqt-hold': `${ms}ms`} as CSSProperties} {...bind}
      aria-label={tx(`${position.symbol} pozisyonunu kapatmak için ${word} tuşunu basılı tut`, `Press and hold ${word} to close the ${position.symbol} position`)}>
      <span className="sqtHoldFill" aria-hidden="true"/>
      <span className="sqtTx"><b>{word}</b><small>{tx('basılı tut · kapat', 'hold · close')}</small></span>
      <span className="sqtIc" aria-hidden="true">{long ? '▼' : '▲'}</span>
    </button>
  </div>
}

/** Fiyatın ne kadar taze olduğunu gösterir: yeşil = canlı, sarı = birkaç sn gecikme, kırmızı = fiyat eski. */
function LiveMark({state, age, compact = false}: {state: BookState; age: number; compact?: boolean}) {
  const secs = Math.max(1, Math.round(age / 1000))
  const label = state === 'live' ? tx('Canlı fiyat', 'Live price') : state === 'late' ? tx(`Fiyat ${secs} sn gecikti`, `Price ${secs}s late`) : tx(`Fiyat güncel değil (${secs > 99 ? '99+' : secs} sn)`, `Price is stale (${secs > 99 ? '99+' : secs}s)`)
  return <span className="sqtLive" data-state={state} role="status" aria-label={label} title={label}><i aria-hidden="true"/>{state !== 'live' && !compact && <small>{state === 'late' ? `${secs} sn` : tx('eski', 'stale')}</small>}</span>
}

type Toast = {kind: 'ok' | 'warn' | 'error'; title: string; detail?: string}

/** Üst çubuktaki (OHLC satırı) açık pozisyon sekmeleri: yalnız açık işlemleri gösterir, dokununca grafik o coine geçer. */
export function StudioPositionTabs({positions, symbol, price, book = null, onSelect}: {positions: readonly StudioPosition[]; symbol: string; price: number | null; book?: FuturesBook | null; onSelect: (symbol: string) => void}) {
  const open = positions.filter(isOpen)
  const viewing = open.some(position => position.symbol === symbol)
  // Tek işlem ve zaten onun grafiğindeyken sekme gereksizdir; başka coine bakarken ya da 2+ işlemde görünür.
  if (!open.length || (open.length === 1 && viewing)) return null
  return <div className="sqtTabs" role="tablist" aria-label={tx('Açık işlemler', 'Open trades')}>
    {open.map(position => {
      const {pnl} = positionPnl(position, position.symbol === symbol ? freshClosePrice(sideOf(position), book, symbol, Date.now()) ?? price : null)
      const selected = position.symbol === symbol
      return <button key={position.symbol} type="button" role="tab" aria-selected={selected} className="sqtTab" data-on={selected} data-side={sideOf(position)} title={`${position.symbol} · ${sideOf(position)}`} onClick={() => onSelect(position.symbol)}>
        <i aria-hidden="true"/><b>{short(position.symbol)}</b>{pnl !== null && <span data-tone={pnl >= 0 ? 'up' : 'down'}>{signed(pnl)}</span>}
      </button>
    })}
  </div>
}

type Props = {
  symbol: string; price: number | null; demo: boolean; premium: boolean; onUpgrade: () => void
  live: SharedLiveStatus | null; analysis: AnalysisLevels; decision: StudioDecision
  positions: readonly StudioPosition[]; onSelect: (symbol: string) => void; drawerOpen: boolean; dataReady: boolean
  candles?: readonly Candle[]; onGridLines?: (lines: GridChartLine[]) => void; onChartTools?: (tools: ChartTools) => void
  /** Gerçek hesapta açık coinin canlı en iyi alış/satış fiyatı; kâr/zarar kapanış fiyatından hesaplanır. */
  book?: FuturesBook | null
}

export default function StudioQuickTrade({symbol, price, demo, premium, onUpgrade, live, analysis, decision, positions, onSelect, drawerOpen, dataReady, candles, onGridLines, onChartTools, book = null}: Props) {
  const [prefs, setPrefs] = useState<Prefs>(readPrefs)
  const [side, setSide] = useState<Side | null>(null)
  const [frozen, setFrozen] = useState<{price: number; levels: Levels} | null>(null)
  const [intentId, setIntentId] = useState(newIntentId)
  const [busy, setBusy] = useState<string | null>(null)
  const [sheetError, setSheetError] = useState('')
  const [toast, setToast] = useState<Toast | null>(null)
  const [settings, setSettings] = useState(false)
  const [card, setCard] = useState(false)
  const [tray, setTray] = useState(false)
  const [action, setAction] = useState<'half' | 'close' | null>(null)
  const [actionError, setActionError] = useState('')
  const [lockedUntil, setLockedUntil] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [gridOpen, setGridOpen] = useState(false)
  const [journalOpen, setJournalOpen] = useState(false)
  const [picking, setPicking] = useState(false)
  const [kind, setKind] = useState<OrderKind>('MARKET')
  const [limitText, setLimitText] = useState('')
  const [expiry, setExpiry] = useState<LimitExpiry>(1440)
  const [demoBalance, setDemoBalance] = useState<number | null>(null)
  const [balanceFailed, setBalanceFailed] = useState(false)
  const [journal, setJournal] = useState<JournalEntry[]>(() => { try { return parseJournal(localStorage.getItem(JOURNAL_KEY)) } catch { return [] } })
  const [gridLocal, setGridLocal] = useState<GridChartLine[]>([])
  const [stopAsk, setStopAsk] = useState<{stop: number; label: string} | null>(null)
  const [stopError, setStopError] = useState('')
  const busyRef = useRef(false)

  const open = useMemo(() => positions.filter(isOpen), [positions])
  const current = open.find(position => position.symbol === symbol) ?? null
  const locked = demo ? false : live?.real_trading_locked !== false
  const armed = Boolean(live?.armed && !locked)
  const blocker = demo ? '' : liveBlockerMessage(live, armed)
  const liveBlocked = !demo && blocker !== 'Engel yok.'
  const policy = (live?.policy ?? {}) as Record<string, unknown>
  const margins = useMemo(() => allowedMargins(demo ? 100 : Number(policy.max_margin_per_trade)), [demo, policy.max_margin_per_trade])
  const leverages = useMemo(() => allowedLeverages(demo ? 3 : Number(policy.max_leverage)), [demo, policy.max_leverage])
  const margin = nearestChoice(prefs.margin, margins)
  const leverage = nearestChoice(prefs.leverage, leverages)
  const update = (change: Partial<Prefs>) => setPrefs(currentPrefs => { const next = {...currentPrefs, ...change}; savePrefs(next); return next })

  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id) }, [])
  // Coin değişince açık pencereler kapanır; yarım kalmış bir onay başka coine gitmesin.
  useEffect(() => { setSide(null); setFrozen(null); setSettings(false); setAction(null); setActionError(''); setSheetError(''); setTray(false); setKind('MARKET'); setLimitText(''); setStopAsk(null); setStopError(''); setPicking(false) }, [symbol])
  useEffect(() => { if (!side) setPicking(false) }, [side])
  useEffect(() => { if (!current) { setCard(false); setAction(null); setStopAsk(null); setStopError('') } }, [current])
  useEffect(() => { if (!toast) return; const id = window.setTimeout(() => setToast(null), toast.kind === 'ok' ? 4500 : 9000); return () => window.clearTimeout(id) }, [toast])
  // Esc: önce pencereyi kapatır (tam ekranı kapatmadan).
  useEffect(() => {
    if (!side && !settings && !tray) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || busyRef.current) return
      event.stopPropagation()
      if (picking) { setPicking(false); return }   // fiyat seçerken Esc yalnız seçimi bırakır; kart açık kalır
      setSide(null); setFrozen(null); setSettings(false); setTray(false)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [side, settings, tray, picking])

  const startOrder = (next: Side) => {
    if (!demo && !premium) { onUpgrade(); return }
    const fresh = levelsFor(next, price, analysis)
    const at = positive(price)
    if (at === null || !fresh) { setToast({kind: 'error', title: tx('Fiyat ya da analiz verisi hazır değil', 'Price or analysis data is not ready'), detail: tx('Birkaç saniye sonra tekrar dene.', 'Try again in a few seconds.')}); return }
    setSheetError(''); setIntentId(newIntentId()); setFrozen({price: at, levels: fresh}); setSide(next); setSettings(false); setKind('MARKET'); setLimitText('')
  }
  const closeSheet = () => { if (busyRef.current) return; setSide(null); setFrozen(null); setSheetError('') }
  const changeParam = (change: Partial<Prefs>) => { update(change); setIntentId(newIntentId()); setSheetError('') }

  const sending = busy !== null
  const lockLeft = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const price0 = positive(price)
  // Limit emirde giriş, yazılan limit fiyattır; stop/hedefler o fiyata göre yeniden hesaplanır.
  const limitValue = Number(limitText.replace(',', '.'))
  const limitProblem = side && kind === 'LIMIT' ? limitIssue(side, price0, limitValue) : null
  const limitLevels = side && kind === 'LIMIT' && !limitProblem ? levelsFor(side, limitValue, analysis) : null
  const limitError = limitProblem ?? (side && kind === 'LIMIT' && !limitLevels ? tx('Bu limit fiyat için stop ve hedefler hesaplanamadı.', 'Stop and targets could not be calculated for this limit price.') : null)
  const entryPrice = frozen && kind === 'LIMIT' && limitLevels ? limitValue : frozen?.price ?? 0
  const levels = frozen ? (kind === 'LIMIT' && limitLevels ? limitLevels : frozen.levels) : null
  const balance = demo ? demoBalance : positive(live?.account?.wallet_balance ?? undefined)
  const maxMargin = demo ? 100 : Math.min(Number(policy.max_margin_per_trade) || 100, 300)
  const sized = side && levels && prefs.sizing === 'risk' ? sizeByRisk({balance, riskPct: prefs.riskPct, entry: entryPrice, stop: levels.stop, leverage, maxMargin}) : null
  const sizingBlock = prefs.sizing === 'risk' && (!sized || sized.tooSmall)
  const effMargin = prefs.sizing === 'risk' && sized && !sized.tooSmall ? sized.margin : margin
  const risk = frozen && side && levels ? riskSummary(entryPrice, levels, effMargin, leverage) : null
  const presets = side ? limitPresets(side, price0, analysis?.entry) : []

  // Demo bakiyesi yalnız "Risk %" seçiliyken okunur (hesap ucu ağırdır).
  useEffect(() => {
    if (!demo || !side || prefs.sizing !== 'risk') return
    let cancelled = false
    setBalanceFailed(false)
    void fetch(`${DEMO}/account`).then(response => response.ok ? response.json() : Promise.reject(new Error('account'))).then((data: {wallet_balance?: unknown}) => {
      if (cancelled) return
      const value = Number(data?.wallet_balance)
      if (Number.isFinite(value) && value > 0) setDemoBalance(value); else setBalanceFailed(true)
    }).catch(() => { if (!cancelled) setBalanceFailed(true) })
    return () => { cancelled = true }
  }, [demo, side, prefs.sizing])

  const pickKind = (next: OrderKind) => {
    if (next === kind) return
    setKind(next); setIntentId(newIntentId()); setSheetError('')
    if (next === 'LIMIT' && !limitText && side) { const first = presets[0]; if (first) setLimitText(String(first)) }
  }
  const writeJournal = (next: JournalEntry[]) => { setJournal(next); try { localStorage.setItem(JOURNAL_KEY, JSON.stringify(next)) } catch { /* tarayıcı kaydı kapalı olabilir */ } }

  const placeOrder = async () => {
    if (!side || !frozen || !levels || !risk || busyRef.current) return
    const livePrice = positive(price)
    if (kind === 'LIMIT') {
      const issue = limitIssue(side, livePrice, limitValue)
      if (issue) { setSheetError(issue); return }
    } else if (!livePrice || Math.abs(livePrice - frozen.price) / frozen.price * 100 > MAX_DRIFT_PCT) {
      const fresh = livePrice ? levelsFor(side, livePrice, analysis) : null
      if (livePrice && fresh) setFrozen({price: livePrice, levels: fresh})
      setIntentId(newIntentId())
      setSheetError(tx(`Fiyat %${MAX_DRIFT_PCT} üzerinde oynadı; seviyeler güncellendi. Kontrol edip tekrar basılı tut.`, `The price moved by more than ${MAX_DRIFT_PCT}%; levels were updated. Review them and hold again.`))
      return
    }
    if (sizingBlock) { setSheetError(tx('Risk yüzdesine göre teminat hesaplanamadı; Sabit teminat seç ya da riski/stopu değiştir.', 'The margin could not be sized from the risk percentage; choose Fixed margin or change the risk/stop.')); return }
    busyRef.current = true
    const base = {symbol, direction: side, order_type: kind, margin_usdt: effMargin, leverage, limit_price: kind === 'LIMIT' ? limitValue : null, stop_loss: levels.stop, tp1: levels.tp1, tp2: levels.tp2, tp3: levels.tp3}
    const entry: JournalEntry = {
      id: intentId, ts: Date.now(), account: demo ? 'DEMO' : 'LIVE', symbol, side, kind, entry: entryPrice, stop: levels.stop, tp1: levels.tp1, tp2: levels.tp2, tp3: levels.tp3,
      margin: effMargin, leverage, riskUsdt: risk.lossAtStop, rewardRisk: risk.rewardRisk, analysis: decision.direction,
      followedAnalysis: decision.direction === 'LONG' || decision.direction === 'SHORT' ? decision.direction === side : null, note: '',
    }
    try {
      if (demo) {
        setBusy(tx('Demo emri gönderiliyor…', 'Sending demo order…'))
        const result = await postJson<{risk_adjusted?: boolean}>(`${DEMO}/order`, {...base, confirmation: DEMO_PHRASE}, 60000)
        finish(result, kind === 'LIMIT' ? tx('Demo limit emri kuruldu', 'Demo limit order placed') : tx('Demo emri gönderildi', 'Demo order sent'), entry)
      } else {
        setBusy(tx('Binance doğrulaması…', 'Checking with Binance…'))
        const payload = {...base, tp_split: '60-20-20', limit_expiry_minutes: kind === 'LIMIT' ? expiry : null, intent_id: intentId}
        const test = await postJson<unknown>(`${V25}/order/test`, payload, 45000)
        if (!test.ok) { setSheetError(test.message); return }
        setBusy(tx('Emir gönderiliyor…', 'Sending order…'))
        const result = await postJson<{risk_adjusted?: boolean}>(`${V25}/order`, {...payload, confirmation: LIVE_PHRASE}, 60000)
        finish(result, kind === 'LIMIT' ? tx('Canlı limit emri kuruldu', 'Live limit order placed') : tx('Canlı emir gönderildi', 'Live order sent'), entry)
      }
    } finally {
      busyRef.current = false
      setBusy(null)
    }
  }
  const finish = (result: Awaited<ReturnType<typeof postJson<{risk_adjusted?: boolean}>>>, title: string, entry: JournalEntry) => {
    if (result.ok) {
      setSide(null); setFrozen(null); setSheetError('')
      writeJournal(addJournal(journal, entry))
      setToast({kind: 'ok', title: `${title} · ${symbol}`, detail: entry.kind === 'LIMIT' ? tx('Fiyat limite gelince dolar; stop ve hedefler dolumdan sonra kurulur. Günlüğe kaydedildi.', 'It fills when the price reaches the limit; stop and targets are placed after the fill. Saved to the journal.')
        : result.data?.risk_adjusted ? tx('Risk limiti nedeniyle büyüklük otomatik azaltıldı. Stop ve hedefler emirle birlikte kuruldu.', 'The size was reduced automatically because of the risk limit. Stop and targets are placed with the order.') : tx('Stop ve hedefler emirle birlikte kuruluyor.', 'Stop and targets are being placed with the order.')})
      requestPositionsRefresh()
    } else if (result.uncertain) {
      // Emir gitmiş olabilir: aynı emri tekrar göndermeyiz; pozisyonlar yenilenir ve kısa süre yeni emir kapalı kalır.
      setSide(null); setFrozen(null)
      setLockedUntil(Date.now() + 20000)
      writeJournal(addJournal(journal, {...entry, note: tx('Sonuç doğrulanamadı; borsayı kontrol et.', 'Result not confirmed; check the exchange.')}))
      setToast({kind: 'warn', title: tx('Emir sonucu doğrulanamadı', 'Order result could not be confirmed'), detail: `${result.message} ${tx('Pozisyonların yenilenmesini bekle; açıldıysa sekmede görünür. Emri tekrar göndermeden önce kontrol et.', 'Wait for positions to refresh; if it opened it will appear as a tab. Check before sending the order again.')}`})
      requestPositionsRefresh()
    } else {
      setSheetError(result.message); setIntentId(newIntentId())
    }
  }

  // ---- Açık pozisyon: yarısını kapat / tamamen kapat ----
  const plan = !demo && current ? activePlan(live?.plans as Array<Record<string, unknown>> | undefined, current.symbol) : undefined
  const rights = demo ? {id: '', close: true, reduce: true} : planRights(plan)
  const runAction = async (kind: 'half' | 'close') => {
    if (!current || busyRef.current) return
    busyRef.current = true
    setBusy(kind === 'half' ? tx('Yarısı kapatılıyor…', 'Closing half…') : tx('Pozisyon kapatılıyor…', 'Closing position…')); setActionError('')
    try {
      let result
      if (demo) {
        const body = kind === 'half'
          ? {symbol: current.symbol, quantity: Math.abs(Number(current.quantity)) / 2, position_side: current.position_side || 'BOTH', confirmation: DEMO_REDUCE_PHRASE}
          : {symbol: current.symbol, position_side: current.position_side || 'BOTH', confirmation: DEMO_CLOSE_PHRASE}
        result = await postJson<unknown>(`${DEMO}/position/${kind === 'half' ? 'reduce' : 'close'}`, body, 60000)
      } else {
        const body = kind === 'half' ? {plan_id: rights.id, percent: 50, confirmation: LIVE_REDUCE_PHRASE} : {plan_id: rights.id, confirmation: LIVE_CLOSE_PHRASE}
        result = await postJson<unknown>(`${V25}/position/${kind === 'half' ? 'reduce' : 'close'}`, body, 60000)
      }
      if (result.ok) {
        setAction(null)
        setToast({kind: 'ok', title: kind === 'half' ? tx(`${current.symbol} pozisyonunun yarısı kapatıldı`, `Half of the ${current.symbol} position was closed`) : tx(`${current.symbol} kapatma emri gönderildi`, `${current.symbol} close order sent`), detail: kind === 'close' ? tx('Pozisyon kapanınca stop ve hedef emirleri otomatik temizlenir.', 'Stop and target orders are cleared automatically once the position is closed.') : undefined})
        requestPositionsRefresh()
      } else if (result.uncertain) {
        setAction(null)
        setToast({kind: 'warn', title: tx('Sonuç doğrulanamadı', 'Result could not be confirmed'), detail: `${result.message} ${tx('Pozisyonların yenilenmesini bekle.', 'Wait for positions to refresh.')}`})
        requestPositionsRefresh()
      } else setActionError(result.message)
    } finally { busyRef.current = false; setBusy(null) }
  }

  // ---- Açık pozisyon: stop yönetimi (başa baş / sıkılaştır / elle). Yalnız MANUEL planlarda (Canlı ve Demo); sunucu eski stopu doğrulayarak değiştirir. ----
  const canManage = Boolean(current) && (demo ? current?.plan_source === 'MANUAL' : rights.reduce)
  const applyStop = async (stop: number) => {
    if (!current || !canManage || busyRef.current) return
    const problem = stopIssue(sideOf(current), positive(price) ?? positive(current.mark_price), positive(current.stop_loss), stop)
    if (problem) { setStopError(problem); setStopAsk(null); return }
    busyRef.current = true
    setBusy(tx('Stop güncelleniyor…', 'Updating the stop…')); setStopError('')
    try {
      const result = demo
        ? await postJson<unknown>(`${DEMO}/position/protection`, {symbol: current.symbol, stop_loss: stop, confirmation: DEMO_STOP_PHRASE}, 60000)
        : await postJson<unknown>(`${V25}/position/protection`, {plan_id: rights.id, stop_loss: stop, confirmation: 'KORUMAYI GÜNCELLE'}, 60000)
      if (result.ok) {
        setStopAsk(null)
        setToast({kind: 'ok', title: tx(`${current.symbol} stopu güncellendi`, `${current.symbol} stop updated`), detail: tx('Eski stop silinip yenisi borsadan doğrulanarak kuruldu.', 'The old stop was removed and the new one was confirmed on the exchange.')})
        requestPositionsRefresh()
      } else if (result.uncertain) {
        setStopAsk(null)
        setToast({kind: 'warn', title: tx('Stop sonucu doğrulanamadı', 'Stop result could not be confirmed'), detail: `${result.message} ${tx('Pozisyonu ve stopu kontrol et; tekrar denemeden önce yenilenmesini bekle.', 'Check the position and its stop; wait for a refresh before trying again.')}`})
        requestPositionsRefresh()
      } else setStopError(result.message)
    } finally { busyRef.current = false; setBusy(null) }
  }

  // ---- İz süren stop: sunucu pozisyonu izler, stopu yalnız sıkılaştırır; kapatınca mevcut stop yerinde kalır ----
  const [trailError, setTrailError] = useState('')
  const activeTrail = demo ? positive(current?.trail_pct ?? undefined) : positive((plan as {trail?: {pct?: number}} | undefined)?.trail?.pct)
  const setTrail = async (pct: number | null) => {
    if (!current || !canManage || busyRef.current) return
    busyRef.current = true
    setBusy(pct === null ? tx('İz süren kapatılıyor…', 'Turning trailing off…') : tx('İz süren açılıyor…', 'Turning trailing on…')); setTrailError('')
    try {
      const result = demo
        ? await postJson<unknown>(`${DEMO}/position/trail`, {symbol: current.symbol, pct, confirmation: DEMO_TRAIL_PHRASE}, 30000)
        : await postJson<unknown>(`${V25}/position/trail`, {plan_id: rights.id, pct, confirmation: LIVE_TRAIL_PHRASE}, 30000)
      if (result.ok) {
        setToast({kind: 'ok', title: pct === null ? tx(`${current.symbol} iz süren stop kapatıldı`, `${current.symbol} trailing stop turned off`) : tx(`${current.symbol} iz süren stop açık · %${String(pct).replace('.', ',')}`, `${current.symbol} trailing stop on · ${pct}%`), detail: pct === null ? tx('Mevcut stop yerinde duruyor.', 'The current stop stays in place.') : tx('Sunucu izler; stop yalnız sıkılaşır, gevşemez.', 'The server tracks it; the stop only tightens, never loosens.')})
        requestPositionsRefresh()
      } else setTrailError(result.message)
    } finally { busyRef.current = false; setBusy(null) }
  }

  // ---- Grafik çizgileri: onay kartındaki plan ve açık pozisyonun seviyeleri (grid çizgileriyle birlikte) ----
  const tradeLines = useMemo<GridChartLine[]>(() => {
    const out: GridChartLine[] = []
    const add = (value: unknown, kind: GridChartLine['kind'], label: string, extra?: Partial<GridChartLine>) => { const n = positive(value); if (n) out.push({price: n, kind, label: `${label} ${analysisPrice(n)}`, ...extra}) }
    if (side && levels && entryPrice > 0) {
      add(entryPrice, 'entry', kind === 'LIMIT' ? tx('Limit', 'Limit') : tx('Giriş', 'Entry')); add(levels.stop, 'stop', 'Stop'); add(levels.tp1, 'target', 'TP1'); add(levels.tp2, 'target', 'TP2'); add(levels.tp3, 'target', 'TP3')
    } else if (current) {
      add(current.entry_price, 'entry', tx('Giriş', 'Entry')); add(current.stop_loss, 'stop', 'Stop', canManage ? {id: 'pos-stop', draggable: true} : undefined); add(current.tp1, 'target', 'TP1'); add(current.tp2, 'target', 'TP2'); add(current.tp3, 'target', 'TP3')
    }
    return out
  }, [side, levels, entryPrice, kind, current, canManage])
  useEffect(() => { onGridLines?.([...gridLocal, ...tradeLines]) }, [gridLocal, tradeLines, onGridLines])
  useEffect(() => () => onGridLines?.([]), [onGridLines])

  // Grafik etkileşimleri: limit fiyatı grafikten seç ve stop çizgisini sürükle. İşleyiciler ref'ten okunur; efekt gereksiz yere yenilenmez.
  const toolsRef = useRef({pick: (_price: number) => {}, drop: (_id: string, _price: number) => {}})
  toolsRef.current = {
    pick: (value: number) => { setLimitText(String(Number(value.toPrecision(6)))); setIntentId(newIntentId()); setSheetError(''); setPicking(false) },
    drop: (id: string, value: number) => {
      if (id !== 'pos-stop' || !current) return
      const rounded = Number(value.toPrecision(6))
      const problem = stopIssue(sideOf(current), positive(price) ?? positive(current.mark_price), positive(current.stop_loss), rounded)
      setCard(true)
      if (problem) { setStopAsk(null); setStopError(problem); return }
      setStopError(''); setStopAsk({stop: rounded, label: tx('Grafikten sürüklendi', 'Dragged on the chart')})
    },
  }
  const toolsReady = picking || canManage
  useEffect(() => {
    onChartTools?.(toolsReady ? {pick: picking ? {onPick: value => toolsRef.current.pick(value)} : undefined, onLineDrop: canManage ? (id, value) => toolsRef.current.drop(id, value) : undefined} : {})
  }, [onChartTools, picking, canManage, toolsReady])
  useEffect(() => () => onChartTools?.({}), [onChartTools])

  const dockDisabled = !dataReady || sending || lockLeft > 0
  const aligned = (target: Side) => decision.direction === target ? 'with' : decision.direction === 'LONG' || decision.direction === 'SHORT' ? 'against' : 'none'
  const confidenceText = tx(`${aligned(side ?? 'LONG') === 'with' ? 'Analiz seninle' : 'Analiz ters'} · ${decision.direction}${decision.confidenceScore !== null ? ` · güven %${Math.round(decision.confidenceScore)}` : ''}`, `${aligned(side ?? 'LONG') === 'with' ? 'Analysis agrees' : 'Analysis is opposite'} · ${decision.direction}${decision.confidenceScore !== null ? ` · confidence ${Math.round(decision.confidenceScore)}%` : ''}`)
  // Gerçek hesapta: kapatınca dolacak fiyat (LONG → en iyi alış, SHORT → en iyi satış). Yayın yoksa eski kaynağa düşer.
  const closeNow = (position: StudioPosition) => position.symbol === symbol ? freshClosePrice(sideOf(position), book, symbol, Date.now()) ?? price : null
  const liveState: BookState | null = book || (!demo && current) ? bookState(book, Math.max(now, book?.at ?? 0)) : null
  const liveAge = book ? Math.max(0, now - book.at) : 0
  const currentClose = current ? closeNow(current) : null
  const pnl = current ? positionPnl(current, currentClose) : null

  return <div className="sqtLayer" data-drawer={drawerOpen || undefined} data-grid={gridOpen || journalOpen || undefined}>
    {toast && <div className="sqtToast" role="status" data-kind={toast.kind}><span aria-hidden="true">{toast.kind === 'ok' ? <Check/> : <TriangleAlert/>}</span><div><b>{toast.title}</b>{toast.detail && <small>{toast.detail}</small>}</div><button type="button" aria-label={tx('Kapat', 'Close')} onClick={() => setToast(null)}><X/></button></div>}

    <StudioGrid symbol={symbol} price={price} demo={demo} premium={premium} onUpgrade={onUpgrade} analysis={analysis} decision={decision.direction} liveBlocker={liveBlocked ? blocker : ''}
      policy={policy} dataReady={dataReady} hasPosition={Boolean(current)} hidden={Boolean(side)} onSelect={onSelect} onToast={setToast} onOpenChange={setGridOpen} candles={candles} onLines={setGridLocal}
      extra={<JournalChip count={journal.length} onOpen={() => setJournalOpen(true)}/>}/>
    {journalOpen && !side && <JournalPanel entries={journal} demo={demo} onNote={(id, note) => writeJournal(journal.map(item => item.id === id ? {...item, note: note.slice(0, 280)} : item))} onClear={() => writeJournal([])} onClose={() => setJournalOpen(false)}/>}

    {!current && !side && <div className="sqtDock" data-demo={demo || undefined} role="group" aria-label={tx('Hızlı alım satım', 'Quick buy / sell')}>
      <button type="button" className="sqtMid" aria-expanded={settings} aria-label={tx('Teminat ve kaldıraç', 'Margin and leverage')} onClick={() => setSettings(open => !open)}>
        <span className="sqtMode" data-demo={demo || undefined}>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
        <b>{margin} USDT <em>· {leverage}x</em></b>
      </button>
      <button type="button" className="sqtSide" data-side="buy" disabled={dockDisabled} onClick={() => startOrder('LONG')} aria-label={`${symbol} ${tx('AL', 'BUY')} (LONG)`}><span className="sqtTx"><b>{tx('AL', 'BUY')}</b><small>{price0 ? analysisPrice(price0) : '—'}</small></span><span className="sqtIc" aria-hidden="true">▲</span></button>
      {settings && <div className="sqtPop" role="dialog" aria-label={tx('Teminat ve kaldıraç', 'Margin and leverage')}>
        <ParamPicker label={tx('Teminat (USDT)', 'Margin (USDT)')} choices={margins} value={margin} onPick={value => update({margin: value})}/>
        <ParamPicker label={tx('Kaldıraç', 'Leverage')} choices={leverages} value={leverage} suffix="x" onPick={value => update({leverage: value})}/>
        {liveBlocked && <p className="sqtPopWarn"><TriangleAlert aria-hidden="true"/>{blocker}<ConsentRenew blocker={blocker} onDone={hours => setToast({kind: 'ok', title: tx('Risk izni yenilendi', 'Risk permission renewed'), detail: tx(`${hours} saat geçerli. Canlı işlem için ARM gerekebilir.`, `Valid for ${hours} h. Live trading may need ARM.`)})}/></p>}
      </div>}
      {lockLeft > 0 && <p className="sqtLockNote" role="status">{tx(`Sonuç kontrol ediliyor · ${lockLeft} sn`, `Checking the result · ${lockLeft} s`)}</p>}
    </div>}

    {current && pnl && <div className="sqtPos" data-open={card} data-tray={tray || undefined} data-tone={pnl.pnl === null ? undefined : pnl.pnl >= 0 ? 'up' : 'down'}>
      {!card
        ? <button type="button" className="sqtChip" onClick={() => setCard(true)} aria-expanded="false" aria-label={tx(`${current.symbol} açık pozisyon detayı`, `${current.symbol} open position details`)}>
            <i aria-hidden="true"/><b>{sideOf(current)}{positive(current.leverage ?? undefined) ? ` ${current.leverage}x` : ''}</b><span className="sqtSep" aria-hidden="true"/>
            <strong>{pnl.pnl === null ? '—' : `${signed(pnl.pnl, Math.abs(pnl.pnl) < 1 ? 4 : 2)} USDT`}</strong>{pnl.pct !== null && <em>{signed(pnl.pct)}%</em>}{!demo && pnl.net !== null && <small className="sqtNet" data-tone={pnl.net >= 0 ? 'up' : 'down'}>{tx('net', 'net')} {signed(pnl.net, Math.abs(pnl.net) < 1 ? 3 : 2)}</small>}{liveState && <LiveMark state={liveState} age={liveAge} compact/>}<ChevronUp aria-hidden="true"/>
          </button>
        : <PositionCard position={current} price={currentClose} pnl={pnl} live={liveState ? {state: liveState, age: liveAge} : undefined} rights={rights} demo={demo} busy={busy} action={action} actionError={actionError} onCollapse={() => { setCard(false); setAction(null); setActionError('') }}
            onAsk={kind => { setActionError(''); setAction(kind) }} onCancelAsk={() => { setActionError(''); setAction(null) }} onRun={runAction} onTray={open.length > 1 ? () => setTray(value => !value) : undefined}
            manage={canManage ? {mark: positive(currentClose) ?? positive(current.mark_price), ask: stopAsk, error: stopError, onAsk: (stop, label) => { setStopError(''); setStopAsk({stop, label}) }, onCancel: () => { setStopAsk(null); setStopError('') }, onApply: stop => void applyStop(stop), trail: {pct: activeTrail, error: trailError, onSet: pct => void setTrail(pct)}} : undefined} demoManageNote={demo && !canManage}/>}
      {rights.close && !card && !tray && !side && <CloseDock position={current} demo={demo} disabled={sending || lockLeft > 0} onDone={() => void runAction('close')}/>}
      {tray && <div className="sqtTray" role="dialog" aria-label={tx('Açık pozisyonlar', 'Open positions')}>
        <header><b>{tx('Açık pozisyonlar', 'Open positions')}</b><button type="button" className="sqtRound" aria-label={tx('Listeyi kapat', 'Close list')} onClick={() => setTray(false)}><X aria-hidden="true"/></button><span>{tx(`${open.length} · toplam`, `${open.length} · total`)} <strong data-tone={open.reduce((sum, item) => sum + (positionPnl(item, closeNow(item)).pnl ?? 0), 0) >= 0 ? 'up' : 'down'}>{signed(open.reduce((sum, item) => sum + (positionPnl(item, closeNow(item)).pnl ?? 0), 0))} USDT</strong></span></header>
        {open.map(item => { const value = positionPnl(item, closeNow(item)); return <button key={item.symbol} type="button" className="sqtTrayRow" data-on={item.symbol === symbol} onClick={() => { onSelect(item.symbol); setTray(false) }}>
          <i data-tone={(value.pnl ?? 0) >= 0 ? 'up' : 'down'} aria-hidden="true"/><b>{item.symbol}</b><em data-side={sideOf(item)}>{sideOf(item)}{positive(item.leverage ?? undefined) ? ` ${item.leverage}x` : ''}</em>
          <span data-tone={(value.pnl ?? 0) >= 0 ? 'up' : 'down'}>{value.pnl === null ? '—' : signed(value.pnl)}</span><small>{value.pct === null ? '' : `${signed(value.pct)}%`}</small>
        </button> })}
      </div>}
    </div>}

    {picking && <div className="sqtPickHint" role="status"><span>{tx('Grafikte limit fiyatı için bir yere dokun', 'Tap the chart where you want the limit price')}</span><button type="button" className="sqtBtn" onClick={() => setPicking(false)}>{tx('Vazgeç', 'Cancel')}</button></div>}

    {side && frozen && risk && !picking && <>
      <div className="sqtScrim" onClick={closeSheet} aria-hidden="true"/>
      <div className="sqtSheet" data-demo={demo || undefined} role="dialog" aria-modal="true" aria-label={tx(`${symbol} ${side === 'LONG' ? 'AL' : 'SAT'} onayı`, `${symbol} ${side === 'LONG' ? 'BUY' : 'SELL'} confirmation`)} data-side={side}>
        <header className="sqtHead">
          <span className="sqtTag" data-side={side}>{side === 'LONG' ? tx('AL · LONG', 'BUY · LONG') : tx('SAT · SHORT', 'SELL · SHORT')}</span><b>{symbol}</b>
          <span className="sqtMkt">{kind === 'LIMIT' ? tx('Limit emri', 'Limit order') : tx('Piyasa emri', 'Market order')} · {analysisPrice(entryPrice)}</span>
          <span className="sqtAcc" data-demo={demo || undefined}><i aria-hidden="true"/>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
          <button type="button" className="sqtX" aria-label={tx('Vazgeç', 'Cancel')} onClick={closeSheet} disabled={sending}><X/></button>
        </header>
        <div className="sqtBody">
          <div className="sqtLeft">
            <div className="sqtChips">
              {aligned(side) !== 'none' && <span data-tone={aligned(side) === 'with' ? 'ok' : 'bad'}>{confidenceText}</span>}
              {aligned(side) === 'none' && <span data-tone="warn">{tx('Analiz: net yön yok', 'Analysis: no clear direction')}</span>}
              {decision.mtfScore !== null && aligned(side) !== 'none' && <span data-tone={aligned(side) === 'with' ? 'ok' : 'bad'}>{tx(`MTF ${decision.mtfConfirmed}/${decision.mtfTotal} ${aligned(side) === 'with' ? 'uyumlu' : 'ters yönde'}`, `MTF ${decision.mtfConfirmed}/${decision.mtfTotal} ${aligned(side) === 'with' ? 'aligned' : 'opposite'}`)}</span>}
              {risk.rewardRisk !== null && <span data-tone={risk.rewardRisk >= 1 ? 'ok' : 'warn'}>{tx('R/Ö', 'R/R')} 1 : {fmt(risk.rewardRisk, 1)}</span>}
            </div>
            {aligned(side) === 'against' && <p className="sqtWarn"><b>{tx(`Analiz ${decision.direction} diyor.`, `The analysis says ${decision.direction}.`)}</b> {tx(`${side === 'LONG' ? 'AL' : 'SAT'} analizin tersine bir işlemdir.`, `${side === 'LONG' ? 'BUY' : 'SELL'} is a trade against the analysis.`)}</p>}
            {levels!.source === 'mirror' && <p className="sqtNote">{tx('Analiz seviyeleri bu yöne ya da fiyata uymadığı için stop ve hedefler analizin mesafeleriyle yeniden hesaplandı.', 'The analysis levels do not fit this direction or price, so stop and targets were recalculated using the analysis distances.')}</p>}
            {levels!.source === 'default' && <p className="sqtNote">{tx('Analiz seviyesi bulunamadı; standart mesafeler kullanıldı (stop %1,5).', 'No analysis levels found; standard distances were used (1.5% stop).')}</p>}
            <Ladder side={side} entry={entryPrice} levels={levels!}/>
            <div className="sqtStats">
              <div data-tone="loss"><small>{tx('Stop olursa', 'If stopped out')}</small><b>−{fmt(risk.lossAtStop)} USDT</b></div>
              <div data-tone="gain"><small>{tx("TP1'de", 'At TP1')}</small><b>+{fmt(risk.gainAtTp1)} USDT</b></div>
              <div><small>{tx('Pozisyon büyüklüğü', 'Position size')}</small><b>{fmt(risk.notional, 0)} USDT</b></div>
            </div>
            <ul className="sqtChecks">
              <li data-ok="true"><Check aria-hidden="true"/>{kind === 'LIMIT' ? tx('Limit dolunca stop ve 3 hedef kurulur; dolmazsa pozisyon açılmaz', 'Stop and 3 targets are placed when the limit fills; no position opens if it does not') : tx('Stop ve 3 hedef emirle birlikte gider', 'Stop and 3 targets are sent with the order')}</li>
              {sizingBlock && <li data-ok="false"><TriangleAlert aria-hidden="true"/>{sized?.tooSmall ? tx('Risk yüzdesine göre teminat 5 USDT altında kalıyor; riski artır ya da stopu yaklaştır.', 'Sized margin falls below 5 USDT; raise the risk or tighten the stop.') : balance === null && !(demo && !balanceFailed) ? tx('Bakiye okunamadı; Sabit teminat seç.', 'Balance could not be read; choose Fixed margin.') : tx('Bakiye okunuyor…', 'Reading balance…')}</li>}
              {!demo && <li data-ok={!liveBlocked}>{liveBlocked ? <TriangleAlert aria-hidden="true"/> : <Check aria-hidden="true"/>}{liveBlocked ? <>{blocker}<ConsentRenew blocker={blocker} onDone={hours => setToast({kind: 'ok', title: tx('Risk izni yenilendi', 'Risk permission renewed'), detail: tx(`${hours} saat geçerli. Canlı işlem için ARM gerekebilir.`, `Valid for ${hours} h. Live trading may need ARM.`)})}/></> : tx('ARM açık · canlı kapılar geçti', 'ARM is on · live gates passed')}</li>}
              <li data-ok={risk.stopBeforeLiquidation}>{risk.stopBeforeLiquidation ? <Check aria-hidden="true"/> : <TriangleAlert aria-hidden="true"/>}{risk.stopBeforeLiquidation ? tx('Stop, likidasyondan önce çalışır', 'The stop triggers before liquidation') : tx('Stop likidasyona çok yakın; kaldıracı düşür', 'The stop is too close to liquidation; lower the leverage')}</li>
            </ul>
          </div>
          <div className="sqtRight">
            <div className="sqtOrd">
            <div className="sqtParam"><small>{tx('Emir tipi', 'Order type')}</small><div role="radiogroup" aria-label={tx('Emir tipi', 'Order type')}>
              <button type="button" role="radio" aria-checked={kind === 'MARKET'} data-on={kind === 'MARKET'} disabled={sending} onClick={() => pickKind('MARKET')}>{tx('Piyasa', 'Market')}</button>
              <button type="button" role="radio" aria-checked={kind === 'LIMIT'} data-on={kind === 'LIMIT'} disabled={sending} onClick={() => pickKind('LIMIT')}>Limit</button></div></div>
            {kind === 'LIMIT' && <div className="sqtLimit">
              <label className="sqtField" htmlFor="sqt-limit"><small>{tx('Limit fiyat', 'Limit price')}</small>
                <input id="sqt-limit" inputMode="decimal" autoComplete="off" spellCheck={false} value={limitText} disabled={sending} onChange={event => { setLimitText(event.target.value); setIntentId(newIntentId()); setSheetError('') }}/></label>
              <button type="button" className="sqtPickBtn" disabled={sending} onClick={() => setPicking(true)}>{tx('Grafikten seç', 'Pick on chart')}</button>
              <div className="sqtPresets" role="group" aria-label={tx('Limit fiyat önerileri', 'Limit price suggestions')}>{presets.map((value, index) => <button key={value} type="button" data-on={Number(limitText.replace(',', '.')) === value} disabled={sending} title={analysisPrice(value)}
                onClick={() => { setLimitText(String(value)); setIntentId(newIntentId()); setSheetError('') }}>{index === 0 && analysis?.entry && Math.abs(value - analysis.entry) < 1e-9 ? tx('Analiz', 'Analysis') : `${value < (price0 ?? value) ? '−' : '+'}${fmt(Math.abs(value / (price0 ?? value) - 1) * 100, 2).replace(/[.,]?0+$/, '')}%`}</button>)}</div>
              {!demo && <div className="sqtParam"><small>{tx('Geçerlilik', 'Expires')}</small><div role="radiogroup" aria-label={tx('Limit emir geçerlilik süresi', 'Limit order expiry')}>
                {[...LIMIT_EXPIRY_CHOICES, null].map(choice => <button key={String(choice)} type="button" role="radio" aria-checked={expiry === choice} data-on={expiry === choice} disabled={sending} onClick={() => setExpiry(choice)}>{choice === null ? tx('Süresiz', 'None') : choice === 1440 ? tx('24 sa', '24 h') : tx(`${choice / 60} sa`, `${choice / 60} h`)}</button>)}</div></div>}
              {limitError && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{limitError}</p>}
            </div>}
            </div>
            <div className="sqtSizing">
            <div className="sqtParam"><small>{tx('Büyüklük', 'Sizing')}</small><div role="radiogroup" aria-label={tx('Büyüklük yöntemi', 'Sizing method')}>
              <button type="button" role="radio" aria-checked={prefs.sizing === 'fixed'} data-on={prefs.sizing === 'fixed'} disabled={sending} onClick={() => changeParam({sizing: 'fixed'})}>{tx('Sabit teminat', 'Fixed margin')}</button>
              <button type="button" role="radio" aria-checked={prefs.sizing === 'risk'} data-on={prefs.sizing === 'risk'} disabled={sending} onClick={() => changeParam({sizing: 'risk'})}>{tx('Risk %', 'Risk %')}</button></div></div>
            {prefs.sizing === 'fixed'
              ? <ParamPicker label={tx('Teminat (USDT)', 'Margin (USDT)')} choices={margins} value={margin} onPick={value => changeParam({margin: value})} disabled={sending}/>
              : <div className="sqtParam"><small>{tx('Hesap riski (%)', 'Account risk (%)')}</small><div role="radiogroup" aria-label={tx('Risk yüzdesi', 'Risk percentage')}>
                  {RISK_PCT_CHOICES.map(value => <button key={value} type="button" role="radio" aria-checked={prefs.riskPct === value} data-on={prefs.riskPct === value} disabled={sending} onClick={() => changeParam({riskPct: value})}>%{String(value).replace('.', ',')}</button>)}</div>
                  <p className="sqtSized" role="status">{sized && !sized.tooSmall ? tx(`→ ${fmt(sized.margin)} USDT teminat · risk ≈ ${fmt(sized.actualRisk)} USDT${sized.capped ? ' (üst sınır)' : ''}`, `→ ${fmt(sized.margin)} USDT margin · risk ≈ ${fmt(sized.actualRisk)} USDT${sized.capped ? ' (capped)' : ''}`) : balance === null && (!demo || balanceFailed) ? tx('Bakiye okunamadı', 'Balance unavailable') : sized?.tooSmall ? tx('Teminat çok küçük kalıyor', 'Margin would be too small') : tx('Hesaplanıyor…', 'Calculating…')}</p></div>}
            <ParamPicker label={tx('Kaldıraç', 'Leverage')} choices={leverages} value={leverage} suffix="x" onPick={value => changeParam({leverage: value})} disabled={sending}/>
            </div>
            <div className="sqtActions">
            {sheetError && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{sheetError}</p>}
            <HoldButton ms={demo ? HOLD_MS.demo : HOLD_MS.live} tone={side === 'LONG' ? 'buy' : 'sell'} disabled={sending || liveBlocked || !risk.stopBeforeLiquidation || Boolean(limitError) || sizingBlock}
              label={sending ? busy ?? '' : kind === 'LIMIT' ? tx(`Basılı tut · ${side === 'LONG' ? 'AL' : 'SAT'} limit`, `Hold · ${side === 'LONG' ? 'BUY' : 'SELL'} limit`) : tx(`Basılı tut · ${side === 'LONG' ? 'AL' : 'SAT'}`, `Hold · ${side === 'LONG' ? 'BUY' : 'SELL'}`)} onDone={() => void placeOrder()}
              hint={tx(`${symbol} ${side === 'LONG' ? 'AL' : 'SAT'} emrini göndermek için basılı tut`, `Press and hold to send the ${symbol} ${side === 'LONG' ? 'buy' : 'sell'} order`)}/>
            <button type="button" className="sqtCancel" onClick={closeSheet} disabled={sending}>{tx('Vazgeç', 'Cancel')}</button>
            </div>
          </div>
        </div>
        <p className="sqtFine">{demo ? tx('Yalnız Demo hesaba gider · gerçek para yok.', 'Goes to the Demo account only · no real money.') : tx('Gerçek para kullanılır.', 'Real money is used.')} {tx('Analiz kazanç garantisi değildir.', 'The analysis is not a guarantee of profit.')}</p>
      </div>
    </>}
  </div>
}

function ParamPicker({label, choices, value, onPick, suffix = '', disabled = false}: {label: string; choices: readonly number[]; value: number; onPick: (value: number) => void; suffix?: string; disabled?: boolean}) {
  return <div className="sqtParam"><small>{label}</small><div role="radiogroup" aria-label={label}>{choices.map(choice => <button key={choice} type="button" role="radio" aria-checked={choice === value} data-on={choice === value} disabled={disabled} onClick={() => onPick(choice)}>{choice}{suffix}</button>)}</div></div>
}

/** Stop – giriş – hedefler: tek bakışta okunan seviye şeridi. SHORT'ta fiyat sırası tersine çevrilir. */
function Ladder({side, entry, levels}: {side: Side; entry: number; levels: Levels}) {
  const stopFirst = side === 'LONG'
  const rows = stopFirst
    ? [[tx('Stop', 'Stop'), levels.stop, 'loss'], [tx('Giriş', 'Entry'), entry, 'entry'], ['TP1', levels.tp1, 'gain'], ['TP2', levels.tp2, 'gain'], ['TP3', levels.tp3, 'gain']] as const
    : [['TP3', levels.tp3, 'gain'], ['TP2', levels.tp2, 'gain'], ['TP1', levels.tp1, 'gain'], [tx('Giriş', 'Entry'), entry, 'entry'], [tx('Stop', 'Stop'), levels.stop, 'loss']] as const
  const loss = Math.abs(entry - levels.stop), gain = Math.abs(levels.tp3 - entry), total = loss + gain || 1
  const lossW = Math.max(6, loss / total * 100), gainW = Math.max(6, gain / total * 100)
  return <div className="sqtLadder" aria-label={tx('Stop ve hedef seviyeleri', 'Stop and target levels')}>
    <div className="sqtBar" aria-hidden="true">{stopFirst ? <><i data-tone="loss" style={{flexBasis: `${lossW}%`}}/><i data-tone="entry"/><i data-tone="gain" style={{flexBasis: `${gainW}%`}}/></> : <><i data-tone="gain" style={{flexBasis: `${gainW}%`}}/><i data-tone="entry"/><i data-tone="loss" style={{flexBasis: `${lossW}%`}}/></>}</div>
    <div className="sqtLevels">{rows.map(([name, value, tone]) => <span key={`${name}-${tone}`} data-tone={tone}><small>{name}</small>{analysisPrice(value)}</span>)}</div>
  </div>
}

type Manage = {mark: number | null; ask: {stop: number; label: string} | null; error: string; onAsk: (stop: number, label: string) => void; onCancel: () => void; onApply: (stop: number) => void; trail: {pct: number | null; error: string; onSet: (pct: number | null) => void}}

/** Stop yönetimi: başa baş, fiyatı izleyerek sıkılaştırma (tek seferlik), elle stop. Riski büyütecek hareket kabul edilmez. */
function StopManager({position, manage, sending, busy}: {position: StudioPosition; manage: Manage; sending: boolean; busy: string | null}) {
  const side = sideOf(position)
  const [text, setText] = useState('')
  const stop = positive(position.stop_loss), entry = positive(position.entry_price)
  const be = breakEvenStop(side, entry, manage.mark)
  const tight = [.5, 1].map(pct => ({pct, value: tightenStop(side, manage.mark, stop, pct)}))
  const typed = Number(text.replace(',', '.'))
  const typedProblem = text.trim() ? stopIssue(side, manage.mark, stop, typed) : null
  const [trailAsk, setTrailAsk] = useState<number | null>(null)
  const fmtPct = (value: number) => String(value).replace('.', ',')
  return <div className="sqtManage" role="group" aria-label={tx('Stop yönetimi', 'Stop management')}>
    <small>{tx('Stop yönetimi', 'Stop management')}</small>
    <div className="sqtManageRow">
      <button type="button" className="sqtBtn" disabled={!be || sending} title={be ? analysisPrice(be) : tx('Fiyat henüz başa baş seviyesini geçmedi', 'Price has not passed break-even yet')} onClick={() => be && manage.onAsk(be, tx('Başa baş', 'Break-even'))}>{tx('Başa baş', 'Break-even')}</button>
      {tight.map(({pct, value}) => <button key={pct} type="button" className="sqtBtn" disabled={!value || sending} title={value ? analysisPrice(value) : tx('Şu an stopu bu kadar yaklaştıramaz', 'Cannot tighten the stop this much right now')} aria-label={tx(`Stopu fiyatın %${String(pct).replace('.', ',')} gerisine sıkılaştır`, `Tighten the stop to ${pct}% behind price`)} onClick={() => value && manage.onAsk(value, tx(`Fiyatın %${String(pct).replace('.', ',')} gerisi`, `${pct}% behind price`))}>{tx('Sıkı', 'Tight')} %{String(pct).replace('.', ',')}</button>)}
    </div>
    <div className="sqtManageRow">
      <input id="sqt-stop-new" inputMode="decimal" autoComplete="off" spellCheck={false} aria-label={tx('Yeni stop fiyatı', 'New stop price')} placeholder={stop ? analysisPrice(stop) : tx('Yeni stop', 'New stop')} value={text} disabled={sending} onChange={event => setText(event.target.value)}/>
      <button type="button" className="sqtBtn" disabled={sending || !text.trim() || Boolean(typedProblem)} onClick={() => manage.onAsk(typed, tx('Elle stop', 'Manual stop'))}>{tx('Uygula', 'Apply')}</button>
    </div>
    {typedProblem && <p className="sqtNoteSmall" data-tone="warn">{typedProblem}</p>}
    <div className="sqtManageRow sqtTrailRow" role="group" aria-label={tx('İz süren stop', 'Trailing stop')}>
      <span className="sqtTrailLabel">{tx('İz süren', 'Trailing')}</span>
      {TRAIL_CHOICES.map(pct => <button key={pct} type="button" className="sqtBtn sqtTrailChip" data-on={manage.trail.pct === pct || undefined} aria-pressed={manage.trail.pct === pct} disabled={sending || manage.trail.pct === pct} onClick={() => setTrailAsk(pct)}>%{fmtPct(pct)}</button>)}
      {manage.trail.pct && <button type="button" className="sqtBtn" disabled={sending} onClick={() => { setTrailAsk(null); manage.trail.onSet(null) }}>{tx('Kapat', 'Off')}</button>}
    </div>
    {trailAsk !== null && <div className="sqtAsk" role="alertdialog" aria-label={tx('İz süren stop onayı', 'Trailing stop confirmation')}><span>{tx(`Stop, fiyatın %${fmtPct(trailAsk)} gerisinden otomatik izlensin mi? Yalnız sıkılaşır; hata olursa kendini kapatır, stop yerinde kalır.`, `Trail the stop automatically ${trailAsk}% behind price? It only tightens; if it fails it switches itself off and the stop stays.`)}</span>
      <div><button type="button" className="sqtBtn" data-tone="primary" disabled={sending} onClick={() => { const value = trailAsk; setTrailAsk(null); manage.trail.onSet(value) }}>{tx('Evet, aç', 'Yes, turn on')}</button><button type="button" className="sqtBtn" disabled={sending} onClick={() => setTrailAsk(null)}>{tx('Vazgeç', 'Cancel')}</button></div></div>}
    {manage.trail.error && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{manage.trail.error}</p>}
    {manage.ask && <div className="sqtAsk" role="alertdialog" aria-label={tx('Stop güncelleme onayı', 'Stop update confirmation')}><span>{tx(`Stop ${analysisPrice(manage.ask.stop)} olarak güncellensin mi? (${manage.ask.label})`, `Update the stop to ${analysisPrice(manage.ask.stop)}? (${manage.ask.label})`)}</span>
      <div><button type="button" className="sqtBtn" data-tone="primary" disabled={sending} onClick={() => manage.onApply(manage.ask!.stop)}>{sending ? busy : tx('Evet, güncelle', 'Yes, update')}</button><button type="button" className="sqtBtn" disabled={sending} onClick={manage.onCancel}>{tx('Vazgeç', 'Cancel')}</button></div></div>}
    {manage.error && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{manage.error}</p>}
  </div>
}

function PositionCard({position, price, pnl, live, rights, demo, busy, action, actionError, onCollapse, onAsk, onCancelAsk, onRun, onTray, manage, demoManageNote}: {
  position: StudioPosition; price: number | null; pnl: {pnl: number | null; pct: number | null; net?: number | null}; live?: {state: BookState; age: number}
  rights: {id: string; close: boolean; reduce: boolean}; demo: boolean; busy: string | null; action: 'half' | 'close' | null; actionError: string
  onCollapse: () => void; onAsk: (kind: 'half' | 'close') => void; onCancelAsk: () => void; onRun: (kind: 'half' | 'close') => void; onTray?: () => void; manage?: Manage; demoManageNote?: boolean
}) {
  const side = sideOf(position)
  const entry = positive(position.entry_price), stop = positive(position.stop_loss), tp1 = positive(position.tp1)
  const sending = busy !== null
  return <div className="sqtCard" data-demo={demo || undefined} aria-label={tx(`${position.symbol} açık pozisyon`, `${position.symbol} open position`)}>
    <div className="sqtCardTop"><i aria-hidden="true"/><b>{position.symbol}</b><span data-side={side}>{side}{positive(position.leverage ?? undefined) ? ` ${position.leverage}x` : ''}</span>
      {onTray && <button type="button" className="sqtLink" onClick={onTray}>{tx('Tümü', 'All')}</button>}
      <button type="button" className="sqtRound" aria-label={tx('Küçült', 'Collapse')} onClick={onCollapse}><ChevronDown/></button></div>
    <div className="sqtPnl"><strong>{pnl.pnl === null ? '—' : signed(pnl.pnl, Math.abs(pnl.pnl) < 1 ? 4 : 2)} <u>USDT</u></strong>{pnl.pct !== null && <em>{signed(pnl.pct)}%</em>}{live && <LiveMark state={live.state} age={live.age}/>}</div>
    {!demo && typeof pnl.net === 'number' && <p className="sqtNetLine" data-tone={pnl.net >= 0 ? 'up' : 'down'}>{tx('Komisyon dahil net (tahmin)', 'Net after fees (estimate)')} <b>{signed(pnl.net, Math.abs(pnl.net) < 1 ? 4 : 2)} USDT</b></p>}
    {live && positive(price) && <p className="sqtCloseAt">{tx('Şimdi kapatırsan yaklaşık', 'If you close now, about')} <b>{analysisPrice(positive(price))}</b>{live.state === 'stale' && <em>{tx(' · fiyat güncel değil, dikkat', ' · price is stale, careful')}</em>}</p>}
    <div className="sqtMeta">{entry && <span>{tx('Giriş', 'Entry')} <b>{analysisPrice(entry)}</b></span>}{stop && <span>Stop <b data-tone="down">{analysisPrice(stop)}</b></span>}{tp1 && <span>TP1 <b data-tone="up">{analysisPrice(tp1)}</b></span>}{!stop && !tp1 && <span>{tx('Anlık', 'Now')} <b>{analysisPrice(positive(price) ?? positive(position.mark_price))}</b></span>}</div>
    {action === null && <div className="sqtActs">
      {rights.reduce && <button type="button" className="sqtBtn" disabled={sending} onClick={() => onAsk('half')}>{tx('Yarısını kapat', 'Close half')}</button>}
      {rights.close && <HoldButton ms={demo ? HOLD_MS.demo : HOLD_MS.live} tone="danger" disabled={sending} label={tx('Kapat · basılı tut', 'Close · hold')} onDone={() => onRun('close')} hint={tx(`${position.symbol} pozisyonunu kapatmak için basılı tut`, `Press and hold to close the ${position.symbol} position`)}/>}
      {!rights.close && <p className="sqtNoteSmall">{tx('Bu pozisyon bot ya da dış kaynaklı; kapatma Pozisyonlar sekmesinden.', 'This position is bot-managed or external; close it from the Positions tab.')}</p>}
    </div>}
    {action === null && manage && <StopManager position={position} manage={manage} sending={sending} busy={busy}/>}
    {action === null && demoManageNote && <p className="sqtNoteSmall">{tx('Stop taşıma yalnız elle açtığın işlemlerde; bot ya da dış pozisyonlara dokunulmaz.', 'Stop management is for trades you opened by hand; bot and external positions are never touched.')}</p>}
    {action === 'half' && <div className="sqtAsk" role="alertdialog" aria-label={tx('Yarısını kapat onayı', 'Close half confirmation')}><span>{tx('Pozisyonun yarısı piyasa fiyatından kapatılsın mı?', 'Close half of the position at market price?')}</span><div><button type="button" className="sqtBtn" data-tone="primary" disabled={sending} onClick={() => onRun('half')}>{sending ? busy : tx('Evet, kapat', 'Yes, close')}</button><button type="button" className="sqtBtn" disabled={sending} onClick={onCancelAsk}>{tx('Vazgeç', 'Cancel')}</button></div></div>}
    {actionError && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{actionError}</p>}
    {sending && action === null && <p className="sqtNoteSmall" role="status">{busy}</p>}
  </div>
}

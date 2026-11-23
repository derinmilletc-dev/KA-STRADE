// Coinbase işlem ekranı: Binance'in manuel emir sihirbazının AYNISI (aynı işaretleme, aynı bileşenler, aynı stil dosyaları).
// Binance ekranındaki ama Coinbase'de yapılamayan parçalar çıkarıldı: hedef (TP) emirleri (yalnız-azalt emri olmadığı için güvensiz),
// limit emir, risk limiti düzenleme, 24 saatlik izin/ARM. Ürün listesi, canlı fiyat ve Kais AI analizi VAR (analiz, ürünün dayanak varlığı üzerinden). Arka planda Coinbase mantığı çalışır: ön kontrol ("dry-run"),
// piyasa girişi, zorunlu stop, stop doğrulama. Binance dosyalarına dokunulmaz; yalnızca içe aktarılır.
import type React from 'react'
import {useCallback, useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {BarChart3, ChevronDown, ChevronLeft, ChevronRight, CircleDollarSign, RefreshCw, Search, Send, ShieldAlert, TriangleAlert, X, Zap} from 'lucide-react'
import {CoinIcon} from '../../src/components/CoinIcon'
import CoinLiveChart from './CoinLiveChart'
import {computeGrid, GRID_MAX_COUNT, GRID_MIN_COUNT, type GridFilters, type GridSide} from './gridMath'
import {buildGridAlerts} from './gridAlerts'
import {expiryInfo, levelsFromAnalysis, levelsFromVolatility, minOrder, type AnalysisLevels} from './coinbase-levels'
import {suggestVolatilityStop, type StopCandle} from './volatility-stop'
import CoinbaseActivity from './CoinbaseActivity'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {askKais} from '../../kais-ask'
import {fetchWithTimeout} from '../../master-trade-request'
import '../../live-ws.css'
import '../../manual-order.css'
import '../../coin-picker.css'
import '../../manual-mobile.css'
import '../../live-cockpit.css'
import '../../manual-analysis-sheet.css'
import {API_BASE} from './api'
import {AI_DISCLAIMER, STRONG_CONFIDENCE, alignment, buildSignalMap, filterBySignal, leanText, sortCoins, strengthLabel, type CoinFilter, type CoinSignal, type CoinSort, type SignalMap} from './coin-ranking'
import {analysisSymbol, changeText, expiryText, groupRows, isPerp, matchRows, variantLabel, priceText, signalsForRows, toRows, type CbProduct, type CbQuote, type CbRow} from './coinbase-market'
import {CONFIRM, emptyTicket, isPositiveDecimal, isProductId, newRequestId, ticketPayload, ticketProblems, type Ticket} from './coinbase-manual'
import {leverageBand, marginChips, priceString, riskPreview, sizeFromLeverage, stopFromPct, targetFromRR} from './coinbase-sizing'
import {sameConfirm} from './confirm-text'
import {CoinbaseApiError, coinbaseCall, postJson} from './coinbase-manual-api'
import CoinbaseAccountHero from './CoinbaseAccountHero'
import CoinbaseLimits from './CoinbaseLimits'
import {SuggestionButtons, SuggestionNote, useCoinbaseSuggestions} from './CoinbaseSuggestions'
import type {SuggestItem} from './coinbase-suggest'
import CoinbaseManualNotice, {noticeFromError, type Notice} from './CoinbaseManualNotice'
import CoinbaseManualPending from './CoinbaseManualPending'
import CoinbaseManualPositions, {type CbPosition, type CbProtection} from './CoinbaseManualPositions'
import {overlayLive} from './coinbase-position'
import {useCoinbaseTicker} from './useCoinbaseTicker'
import {CheckList, DirectionCards, ElapsedSeconds, SummaryList, type SummaryItem} from './ManualTerminalParts'
import type {CheckRow} from './manual-order-state'
import './coinbase-manual.css'

type Status = {orders_enabled?: boolean; resting_enabled?: boolean; key_saved: boolean; clock?: {ok: boolean | null; skew_seconds: number | null}}
type Amount = {value: string | null; currency: string} | null
type Account = {balance: {futures_buying_power: Amount; available_margin: Amount; unrealized_pnl: Amount; daily_realized_pnl: Amount; worst_margin_level: string | null; worst_margin_window: string | null}}
type Plan = Record<string, string | number>
type Preflight = {ok: boolean; reasons: string[]; plan: Plan | null}
type WizNotice = {kind: 'error' | 'ok' | 'info'; text: string}
type DoneInfo = {product: string; side: string; contracts: string; leverage: string; margin: string; entry: string; stop: string}

// Kais AI analiz evreni (Binance'in açık piyasa verisinden). Binance ekranıyla aynı uç; sayfa boyunca bir kez yüklenir.
let universeCache: SignalMap | null = null

const duo = (signal: CoinSignal | undefined) => signal
  ? <span className="cpkDuo" data-lean={signal.lean} role="img" aria-label={`LONG yüzde ${signal.long ?? 'bilinmiyor'}, SHORT yüzde ${signal.short ?? 'bilinmiyor'}`}>
    <span className="cpkSide cpkLong" data-lead={signal.lean === 'LONG'}><span className="cpkSideHead"><em>LONG</em><strong>{signal.long === null ? '–' : `%${signal.long}`}</strong></span><span className="cpkMeter"><i style={{width: `${signal.long ?? 0}%`}}/></span></span>
    <span className="cpkSide cpkShort" data-lead={signal.lean === 'SHORT'}><span className="cpkSideHead"><em>SHORT</em><strong>{signal.short === null ? '–' : `%${signal.short}`}</strong></span><span className="cpkMeter"><i style={{width: `${signal.short ?? 0}%`}}/></span></span>
  </span>
  : <span className="cpkDuo cpkNone" aria-hidden="true"/>

// Kısa satır: LONG/SHORT yüzdeleri yan yana, güçlü taraf vurgulu. Listeler için; büyük çift çubuk seçili ürün kartında kalır.
const miniDuo = (signal: CoinSignal | undefined) => signal
  ? <span className="cbMini" data-lean={signal.lean} role="img" aria-label={`LONG yüzde ${signal.long ?? 'bilinmiyor'}, SHORT yüzde ${signal.short ?? 'bilinmiyor'}`}>
    <span className="cbMiniSide cbMiniL" data-lead={signal.lean === 'LONG'}>LONG <b>{signal.long === null ? '–' : `%${signal.long}`}</b></span>
    <span className="cbMiniSide cbMiniS" data-lead={signal.lean === 'SHORT'}>SHORT <b>{signal.short === null ? '–' : `%${signal.short}`}</b></span>
    <span className="cbMiniLean">{leanText(signal)}</span>
  </span>
  : null

const FILTER_CHIPS: [CoinFilter, string][] = [['all', 'Tümü'], ['long', 'LONG ağır'], ['short', 'SHORT ağır'], ['strong', 'Güçlü sinyal']]
const SORT_CHIPS: [CoinSort, string][] = [['confidence', 'Güvene göre'], ['volume', 'Hacme göre'], ['change', 'Değişime göre']]
const PICK_LIMIT = 30

const STEP_NAMES = ['Ürün', 'Yön', 'Tutar', 'Stop ve hedef', 'Kontrol']
const STOP_PCTS = [1, 2, 3, 5]
const TARGET_RRS = [1, 2, 3]
const usd = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})

export default function CoinbaseManualTerminal({onGoSetup}: {onGoSetup: () => void}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [futuresMissing, setFuturesMissing] = useState(false)
  const [positions, setPositions] = useState<CbPosition[]>([])
  const [protection, setProtection] = useState<CbProtection | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [wizNotice, setWizNotice] = useState<WizNotice>({kind: 'info', text: ''})
  const [loading, setLoading] = useState(false)
  const [step, setStep] = useState(0)
  const [ticket, setTicket] = useState<Ticket>(emptyTicket())
  const [check, setCheck] = useState<Preflight | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [busy, setBusy] = useState<'' | 'review' | 'send'>('')
  const [tpRR, setTpRR] = useState<number | null>(null)
  const [kaisAna, setKaisAna] = useState<Record<string, AnalysisLevels | 'none' | 'error'>>({})
  const [levelPick, setLevelPick] = useState<{kind: 'kais' | 'vol'; stop: string; note: string; tone: 'ok' | 'warn'} | null>(null)
  const [volBusy, setVolBusy] = useState(false)
  const [activityKey, setActivityKey] = useState(0)
  const autoCheckRef = useRef(false)
  const [done, setDone] = useState<DoneInfo | null>(null)
  const [manualOpen, setManualOpen] = useState(false)
  const [lastCheck, setLastCheck] = useState<number | null>(null)
  const [anaSym, setAnaSym] = useState<string | null>(null)
  const [manualMode, setManualMode] = useState<'single' | 'grid'>('single')
  const [grid, setGrid] = useState({lower: '', upper: '', count: '5', margin: '50', leverage: '2', stop: ''})
  const [recentIds, setRecentIds] = useState<string[]>(() => { try { const raw = JSON.parse(window.localStorage.getItem('kt-cb-recent') || '[]'); return Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string').slice(0, 5) : [] } catch { return [] } })
  const [products, setProducts] = useState<CbProduct[] | null>(null)
  const [productsError, setProductsError] = useState('')
  const [universe, setUniverse] = useState<SignalMap | null>(() => universeCache)
  const [signalsError, setSignalsError] = useState(false)
  const [coinOpen, setCoinOpen] = useState(false)
  const [coinQuery, setCoinQuery] = useState('')
  const [coinFilter, setCoinFilter] = useState<CoinFilter>('all')
  const [coinSort, setCoinSort] = useState<CoinSort>('confidence')
  const [quotes, setQuotes] = useState<Record<string, CbQuote>>({})
  const [quoteError, setQuoteError] = useState(false)
  const loadingRef = useRef(false)
  const positionsRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async (quiet = false) => {
    if (loadingRef.current) return
    loadingRef.current = true
    setLoading(true)
    try {
      const next = await coinbaseCall<Status>('/status')
      setStatus(next)
      if (!next.key_saved) { setAccount(null); setFuturesMissing(false); setPositions([]); setProtection(null); return }
      // Her okuma ayrı: biri bozulursa diğerleri gösterilir ve hata açıkça yazılır.
      const results = await Promise.allSettled([
        coinbaseCall<Account>('/account'),
        coinbaseCall<{positions: CbPosition[]}>('/positions'),
        next.orders_enabled ? coinbaseCall<CbProtection>('/orders/protection') : Promise.resolve(null),
        coinbaseCall<{products: CbProduct[]}>('/products'),
      ])
      const [accountResult, positionResult, protectionResult, productsResult] = results
      if (productsResult.status === 'fulfilled') { setProducts(productsResult.value.products); setProductsError('') }
      else setProductsError(noticeFromError(productsResult.reason).text)
      // Vadeli hesap yoksa/boşsa Coinbase balance_summary vermez: bu bir arıza değil, kullanıcıya sade yönlendirme gösterilir.
      const noFutures = accountResult.status === 'rejected' && /balance_summary yok/.test(noticeFromError(accountResult.reason).text)
      if (accountResult.status === 'fulfilled') { setAccount(accountResult.value); setFuturesMissing(false) }
      else if (noFutures) { setAccount(null); setFuturesMissing(true) }
      if (positionResult.status === 'fulfilled') setPositions(positionResult.value.positions)
      if (protectionResult.status === 'fulfilled') setProtection(protectionResult.value)
      const failed = results.slice(0, 3).find((result, index): result is PromiseRejectedResult => result.status === 'rejected' && !(index === 0 && noFutures))
      if (failed) setNotice(noticeFromError(failed.reason))
      else if (!quiet) setNotice({kind: 'ok', text: 'Coinbase bilgileri yenilendi.'})
    } catch (error) {
      setNotice(noticeFromError(error))
    } finally {
      loadingRef.current = false
      setLastCheck(Date.now())
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh(true)
    const timer = visibleInterval(() => void refresh(true), 30000)
    return () => clearVisibleInterval(timer)
  }, [refresh])

  const patch = (change: Partial<Ticket>) => { setTicket(current => ({...current, ...change})); setCheck(null); setWizNotice({kind: 'info', text: ''}) }
  const goStep = (index: number) => { setWizNotice({kind: 'info', text: ''}); setStep(index) }
  const afterAction = (text: string) => { setNotice({kind: 'ok', text}); setActivityKey(key => key + 1); void refresh(true) }
  const afterError = (error: CoinbaseApiError) => { setNotice(noticeFromError(error)); void refresh(true) }

  /** Emir akışı hatası: ACİL olanlar üstte büyük bildirimde, diğerleri Binance'teki gibi kartın içinde. */
  const orderFailure = (error: unknown) => {
    if (error instanceof CoinbaseApiError && !error.failure.critical) {
      setWizNotice({kind: 'error', text: error.failure.message})
    } else setNotice(noticeFromError(error))
  }

  const reviewOrder = async () => {
    const problems = ticketProblems(ticket)
    if (problems.length) { setCheck({ok: false, reasons: problems, plan: null}); return }
    setBusy('review')
    try { setCheck(await postJson<Preflight>('/orders/preflight', ticketPayload(ticket))) }
    catch (error) { setCheck(null); orderFailure(error) }
    finally { setBusy('') }
  }

  const sendOrder = async () => {
    const plan = check?.plan
    if (!check?.ok || !plan) return
    setConfirmOpen(false)
    setBusy('send')
    try {
      await postJson<{message: string}>('/orders/open', {...ticketPayload(ticket), confirm: CONFIRM.open, request_id: newRequestId()})
      setDone({
        product: String(plan.product_id), side: String(plan.side), contracts: String(plan.contracts), leverage: String(plan.leverage),
        margin: String(plan.margin_usd), entry: String(plan.entry_reference), stop: String(plan.stop_price),
      })
      setTicket(emptyTicket()); setCheck(null); setStep(0); setWizNotice({kind: 'info', text: ''}); setLevelPick(null); setActivityKey(key => key + 1)
    } catch (error) {
      setCheck(null)
      orderFailure(error)
    } finally { setConfirmText(''); setBusy(''); void refresh(true) }
  }

  const ordersOn = status?.orders_enabled === true
  const [limitsRev, setLimitsRev] = useState(0)
  const suggestions = useCoinbaseSuggestions(ticket, ordersOn && step === 3, limitsRev)
  const applySuggestion = (item: SuggestItem) => { setLevelPick(null); setTpRR(item.reward_r); patch({stopPrice: item.stop_price, takeProfit: item.take_profit_price}) }
  const keySaved = status?.key_saved === true
  const selectedId = ticket.productId.trim()

  useEffect(() => {
    if (universeCache !== null) return
    let cancelled = false
    const controller = new AbortController()
    void fetchWithTimeout(`${API_BASE}/analysis-universe?interval=15m&limit=100`, {signal: controller.signal}, 60000).then(async response => {
      if (!response.ok) throw new Error('analysis-universe')
      const map = buildSignalMap(await response.json())
      if (cancelled) return
      if (Object.keys(map).length === 0) throw new Error('empty')
      universeCache = map
      setUniverse(map); setSignalsError(false)
    }).catch(error => {
      if (cancelled || error?.name === 'AbortError') return
      setSignalsError(true)
    })
    return () => { cancelled = true; controller.abort() }
  }, [])

  const allRows = products ? toRows(products) : []
  // Her coin tek satır (perp varsa o); tarihli ürünler seçilen coinin altında "Vade" olarak seçilir.
  const grouped = groupRows(allRows)
  const rows = grouped.rows
  const signals = signalsForRows(allRows, universe)
  const rankedRows = sortCoins(filterBySignal(rows, signals, coinFilter), signals, coinSort)
  const matches = matchRows(rankedRows, coinQuery)
  const listed = matches.slice(0, 80)
  const selectedRow = allRows.find(row => row.symbol === selectedId)
  const selectedVariants = selectedRow ? grouped.variants[selectedRow.root || selectedRow.symbol] ?? [] : []
  const isSelectedCoin = (row: CbRow) => selectedRow !== undefined && (row.root || row.symbol) === (selectedRow.root || selectedRow.symbol)
  // Seçilen coinin TÜM vadelerinin fiyatı tek istekte: vade değiştirmek anında olur, ekstra bekleme yok.
  const quoteIds = (selectedVariants.length > 0 ? selectedVariants.map(row => row.symbol) : isProductId(selectedId) ? [selectedId] : []).slice(0, 12).join(',')
  useEffect(() => {
    if (!keySaved || !quoteIds) return
    let alive = true
    const load = () => {
      void coinbaseCall<{quotes: Record<string, CbQuote>}>(`/quotes?ids=${encodeURIComponent(quoteIds)}`)
        .then(next => { if (alive) { setQuotes(current => ({...current, ...next.quotes})); setQuoteError(false) } })
        .catch(() => { if (alive) setQuoteError(true) })
    }
    load()
    const timer = visibleInterval(load, 5000)
    return () => { alive = false; clearVisibleInterval(timer) }
  }, [keySaved, quoteIds])
  // Açık pozisyonların fiyatı: 3 sn'de bir (sekme görünürken) alış/satış ortası; K/Z buna göre anında hesaplanır.
  const positionIds = positions.map(item => item.product_id).filter(isProductId).slice(0, 12).join(',')
  const [liveQuotes, setLiveQuotes] = useState<Record<string, CbQuote>>({})
  const [liveAt, setLiveAt] = useState<number | null>(null)
  useEffect(() => {
    if (!keySaved || !positionIds) { setLiveAt(null); return }
    let alive = true
    const load = () => {
      void coinbaseCall<{quotes: Record<string, CbQuote>}>(`/quotes?ids=${encodeURIComponent(positionIds)}`)
        .then(next => { if (alive) { setLiveQuotes(current => ({...current, ...next.quotes})); setLiveAt(Date.now()) } })
        .catch(() => { /* bir sonraki turda yeniden denenir; 15 sn sonuç gelmezse rozet "Bağlanıyor…" olur */ })
    }
    load()
    const timer = visibleInterval(load, 2000)
    return () => { alive = false; clearVisibleInterval(timer) }
  }, [keySaved, positionIds])
  // WebSocket (saniyelik) varken onu, yoksa/bayatsa 3 sn'lik sorgulamayı kullan.
  const ticker = useCoinbaseTicker(positionIds === '' ? [] : positionIds.split(','), keySaved && positionIds !== '')
  const livePositions = positions.map(item => {
    const size = Number(products?.find(row => row.product_id === item.product_id)?.contract_size)
    const tick = ticker.quotes[item.product_id]
    // En yeni kaynak kazanır: WS defter/işlem fiyatı ya da 2 sn'lik REST alış/satış ortası. Biri durursa diğeri fiyatı yürütür.
    const fresh = tick !== undefined && Date.now() - tick.at < 10000 && (liveAt === null || tick.at >= liveAt)
    return overlayLive(item, fresh ? {mid: String(tick.price)} : liveQuotes[item.product_id], Number.isFinite(size) && size > 0 ? size : null)
  })
  const liveStamp = Math.max(liveAt ?? 0, ticker.at ?? 0) || null
  const liveSource: 'ws' | 'poll' | null = ticker.at !== null && Date.now() - ticker.at < 10000 ? 'ws' : liveAt !== null ? 'poll' : null
  // Kais analizi (aynı coinin Binance analizi): stop adımında "Kais analizi" önerisi için. Coin başına bir kez alınır.
  const anaKey = analysisSymbol(selectedRow?.root ?? null)
  useEffect(() => {
    if (!anaKey || kaisAna[anaKey] !== undefined) return
    let alive = true
    void fetchWithTimeout(`${API_BASE}/analysis/${encodeURIComponent(anaKey)}?interval=15m`, {}, 30000)
      .then(async response => { const body = response.ok ? await response.json() : null; if (alive) setKaisAna(current => ({...current, [anaKey]: body && typeof body === 'object' ? body as AnalysisLevels : 'none'})) })
      .catch(() => { if (alive) setKaisAna(current => ({...current, [anaKey]: 'error'})) })
    return () => { alive = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anaKey])
  const quoteRaw = quotes[selectedId]
  const quote = quoteRaw && quoteRaw.mid !== null && Number(quoteRaw.mid) > 0 ? quoteRaw : null
  const quoteNote = quoteRaw?.source === 'last' || quoteRaw?.source === 'none' ? quoteRaw.reason ?? '' : ''
  const shownPrice = quote ? Number(quote.mid) : selectedRow ? selectedRow.price : NaN
  const selectedSignal = signals?.[selectedId]
  const signalCount = (filter: CoinFilter) => signals ? filterBySignal(rows, signals, filter).length : 0
  const chooseCoin = (value: string) => { setRecentIds(current => { const next = [value, ...current.filter(item => item !== value)].slice(0, 5); try { window.localStorage.setItem('kt-cb-recent', JSON.stringify(next)) } catch { /* depolama kapalı */ } return next }); setTicket(current => ({...current, productId: value})); setCheck(null); setWizNotice({kind: 'info', text: ''}); setCoinOpen(false); setCoinQuery('') }
  useEffect(() => {
    if (!manualOpen) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !confirmOpen) setManualOpen(false) }
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow }
  }, [manualOpen, confirmOpen])
  useEffect(() => {
    if (step !== 4) { autoCheckRef.current = false; return }
    if (autoCheckRef.current || check !== null || busy !== '' || !ordersOn || futuresMissing || ticketProblems(ticket).length > 0) return
    autoCheckRef.current = true
    void reviewOrder()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, check, busy, ordersOn])
  const stepOk = [
    isProductId(ticket.productId),
    true,
    isPositiveDecimal(ticket.marginUsd) && (ticket.contracts.trim() === '' || /^[1-9]\d{0,5}$/.test(ticket.contracts.trim())),
    isPositiveDecimal(ticket.stopPrice),
    true,
  ]
  const plan = check?.ok ? check.plan : null
  const dir: 'LONG' | 'SHORT' = ticket.side === 'BUY' ? 'LONG' : 'SHORT'
  const align = alignment(selectedSignal, dir)

  // --- Akıllı boyutlandırma ve risk özeti (tahmin; asıl kuralları sunucu uygular) ---
  const contractSize = Number(products?.find(item => item.product_id === selectedId)?.contract_size)
  const limitNum = isPositiveDecimal(ticket.limitPrice) ? Number(ticket.limitPrice) : NaN
  const entryRef = Number.isFinite(limitNum) ? limitNum : shownPrice
  const marginNum = Number(ticket.marginUsd)
  const levNum = Number(ticket.leverage)
  const manualContracts = ticket.contracts.trim() !== '' && /^[1-9]\d{0,5}$/.test(ticket.contracts.trim()) ? Number(ticket.contracts.trim()) : null
  const sizing = manualContracts === null ? sizeFromLeverage({margin: marginNum, leverage: levNum, entry: entryRef, contractSize}) : null
  const contractsNow = manualContracts ?? sizing?.contracts ?? null
  const marginAvail = Number(account?.balance.available_margin?.value)
  const chips = marginChips(Number.isFinite(marginAvail) ? marginAvail : null)
  const band = leverageBand(levNum)
  const stopNum = isPositiveDecimal(ticket.stopPrice) ? Number(ticket.stopPrice) : NaN
  const tpNum = isPositiveDecimal(ticket.takeProfit) ? Number(ticket.takeProfit) : null
  const risk = contractsNow !== null && Number.isFinite(stopNum)
    ? riskPreview({side: ticket.side, entry: entryRef, stop: stopNum, target: tpNum, contracts: contractsNow, contractSize}) : null
  const setStop = (value: string) => {
    const stop = Number(value)
    const target = tpRR !== null && Number.isFinite(stop) && Number.isFinite(entryRef) ? targetFromRR(ticket.side, entryRef, stop, tpRR) : null
    patch(target !== null ? {stopPrice: value, takeProfit: priceString(target)} : {stopPrice: value})
  }
  const pickStopPct = (pct: number) => { const stop = stopFromPct(ticket.side, entryRef, pct); if (stop !== null) setStop(priceString(stop)) }
  const pickTargetRR = (rr: number) => {
    const target = targetFromRR(ticket.side, entryRef, stopNum, rr)
    if (target === null) { setWizNotice({kind: 'error', text: 'Önce geçerli bir stop koy (LONG\'da girişin altı, SHORT\'ta üstü).'}); return }
    setTpRR(rr); patch({takeProfit: priceString(target)})
  }


  // --- Telefon: tutar ve stop/hedef adımları (Binance'teki mxtM bloklarının aynısı; Coinbase alanlarıyla) ---
  const FEE_RATE = 0.001
  const isLimit = ticket.limitPrice.trim() !== '' || ticket.rest
  const marginMax = Number.isFinite(marginAvail) && marginAvail >= 1 ? Math.floor(marginAvail) : 1_000_000
  const maxPick = Math.max(5, Math.min(marginMax, Number.isFinite(marginAvail) && marginAvail >= 5 ? Math.floor(marginAvail) : marginMax))
  const marginPicks = Array.from(new Set([10, 25, 50, 100].filter(v => v < maxPick).concat(maxPick)))
  const walletShare = Number.isFinite(marginAvail) && marginAvail > 0 && marginNum > 0 ? marginNum / marginAvail * 100 : null
  const notionalNow = contractsNow !== null && Number.isFinite(contractSize) && Number.isFinite(entryRef) ? contractsNow * contractSize * entryRef : null
  const roundTripFee = notionalNow !== null ? notionalNow * FEE_RATE * 2 : null
  const levBandM = !(levNum >= 1) ? null : levNum <= 3 ? ['Düşük risk', 'low'] : levNum <= 10 ? ['Orta risk', 'mid'] : ['Yüksek risk', 'high']
  const minO = minOrder(contractSize, entryRef, manualContracts === null && levNum >= 1 ? levNum : 1)
  const amountMobile = <div className="mxtM mxtMAmt">
    <div className="mxtMSeg" role="group" aria-label="Emir türü"><button type="button" aria-pressed={!isLimit} data-on={!isLimit || undefined} onClick={() => patch({limitPrice: '', rest: false})}>Piyasa fiyatından</button><button type="button" aria-pressed={isLimit} data-on={isLimit || undefined} onClick={() => patch({limitPrice: ticket.limitPrice.trim() || (Number.isFinite(shownPrice) ? priceString(shownPrice) : '')})}>Limit fiyattan</button></div>
    {isLimit && <div className="mxtMCard"><label className="mxtMField">Limit fiyatı<input type="number" inputMode="decimal" value={ticket.limitPrice} onChange={event => patch({limitPrice: event.target.value})}/></label>
      {status?.resting_enabled === true
        ? <div className="mxtMField">Dolmazsa<div className="mxtMSeg" role="group" aria-label="Limit emir davranışı"><button type="button" aria-pressed={!ticket.rest} data-on={!ticket.rest || undefined} onClick={() => patch({rest: false})}>Hemen iptal</button><button type="button" aria-pressed={ticket.rest} data-on={ticket.rest || undefined} onClick={() => patch({rest: true})}>Dolana kadar beklesin</button></div></div>
        : <p className="mxtMNote">Limit emir anında dolmazsa iptal edilir; işlem açılmaz.</p>}</div>}
    <div className="mxtMCard">
      <div className="mxtMLbl"><span>TEMİNAT</span>{walletShare !== null && <span>Hesabının <b>%{walletShare.toFixed(1)}</b>’ü</span>}</div>
      <div className="mxtMBig"><input type="number" inputMode="decimal" min="1" max={marginMax} style={{width: `${Math.max(2, String(ticket.marginUsd).length + 0.6)}ch`}} value={ticket.marginUsd} aria-label="Teminat (USD)" onChange={event => patch({marginUsd: event.target.value})}/><span>USD</span></div>
      <p className="mxtMUnder">Kullanılabilir <b>{Number.isFinite(marginAvail) ? `${usd(marginAvail)} USD` : 'okunamadı'}</b> · sınır: kasan</p>
      {minO && <p className="cbMinOrder" data-low={Number.isFinite(marginNum) && marginNum > 0 && marginNum < minO.margin || undefined}><span>En küçük emir <b>1 sözleşme ≈ {usd(minO.notional)} USD</b>{manualContracts === null && levNum >= 1 ? <> · {levNum}x ile ≈ <b>{usd(minO.margin)} USD</b> teminat</> : null}</span>{manualContracts === null && levNum >= 1 && <button type="button" onClick={() => patch({marginUsd: String(Math.ceil(minO.margin * 1.02 * 100) / 100)})}>En azını yaz</button>}</p>}
      <div className="mxtMChips">{marginPicks.map(v => <button type="button" key={v} aria-pressed={ticket.marginUsd.trim() === String(v)} data-on={ticket.marginUsd.trim() === String(v) || undefined} onClick={() => patch({marginUsd: String(v)})}>{v === maxPick && marginPicks.length > 1 ? `Maks ${v}` : v}</button>)}</div>
      <input type="range" className="mxtMRange" min="1" max={marginMax} step="1" value={Number.isFinite(marginNum) && marginNum > 0 ? Math.min(marginNum, marginMax) : Math.min(10, marginMax)} onChange={event => patch({marginUsd: event.target.value})} aria-label="Teminat kaydırıcı"/>
    </div>
    <div className="mxtMCard">
      <div className="mxtMLbl"><span>KALDIRAÇ</span><span>Pozisyon = teminat × kaldıraç</span></div>
      <div className="mxtMLev"><input type="number" inputMode="numeric" min="1" max="20" style={{width: `${Math.max(1.6, String(ticket.leverage).length + 0.6)}ch`}} value={ticket.leverage} aria-label="Kaldıraç (x)" disabled={manualContracts !== null} onChange={event => patch({leverage: event.target.value})}/><i>x</i>{levBandM && <span className="mxtMBand" data-band={levBandM[1]}>{levBandM[0]}</span>}</div>
      <div className="mxtMChips">{[1, 2, 3, 5, 10].map(v => <button type="button" key={v} disabled={manualContracts !== null} aria-pressed={ticket.leverage === String(v)} data-on={ticket.leverage === String(v) || undefined} onClick={() => patch({leverage: String(v)})}>{v}x</button>)}</div>
      <input type="range" className="mxtMRange" min="1" max="20" step="1" disabled={manualContracts !== null} value={Number.isFinite(levNum) && levNum > 0 ? Math.min(20, levNum) : 1} onChange={event => patch({leverage: event.target.value})} aria-label="Kaldıraç kaydırıcı"/>
    </div>
    <div className="mxtMTiles">
      <div className="mxtMTileW"><span><small>Pozisyon büyüklüğü</small><b>{notionalNow !== null ? `${usd(notionalNow)} USD · ${contractsNow} sözleşme` : '—'}</b></span></div>
      <div><small>Toplam komisyon</small><b>{roundTripFee === null ? '—' : `${usd(roundTripFee)} USD`}</b></div>
      <div><small>Stopta risk</small><b data-muted={!risk || risk.wrongSide || undefined}>{risk && !risk.wrongSide ? `${usd(risk.lossUsd)} USD` : 'Stop girilince'}</b></div>
    </div>
    {contractsNow === null && marginNum > 0 && Number.isFinite(entryRef) && Number.isFinite(contractSize) && <p className="mxtWarn" role="alert">Bu teminat ve kaldıraçla 1 sözleşme bile alınamıyor. Teminatı ya da kaldıracı artır.</p>}
    <details className="mxtMMore"><summary><span>Sözleşme sayısını elle yaz</span><em>{manualContracts !== null ? `${manualContracts} sözleşme` : 'otomatik'}</em></summary>
      <label className="mxtMField">Sözleşme sayısı<input type="number" inputMode="numeric" value={ticket.contracts} placeholder="boşsa kaldıraçtan hesaplanır" onChange={event => patch({contracts: event.target.value})}/></label>
    </details>
    <p className="mxtMNote">Giriş ve çıkış komisyonu dahil (taraf başı yaklaşık %{(FEE_RATE * 100).toFixed(2)}). Kayma ve fonlama dahil değil.</p>
  </div>
  const stopSide = ticket.side === 'SELL' ? -1 : 1
  const nudge = (key: 'stopPrice' | 'takeProfit', direction: 1 | -1) => {
    if (!(entryRef > 0)) return
    const stepPx = Math.max(entryRef * 0.001, entryRef * 1e-6)
    const current = Number(ticket[key])
    const next = (current > 0 ? current : entryRef) + direction * stepPx
    if (!(next > 0)) return
    if (key === 'stopPrice') setStop(priceString(next)); else { setTpRR(null); patch({takeProfit: priceString(next)}) }
  }
  const stopDistPct = Number.isFinite(stopNum) && entryRef > 0 ? Math.abs(entryRef - stopNum) / entryRef * 100 : null
  const stopSideOk = Number.isFinite(stopNum) && entryRef > 0 ? (stopSide === 1 ? stopNum < entryRef : stopNum > entryRef) : true
  const stopPickPct = stopDistPct !== null && stopSideOk ? [1, 2, 3, 5].find(pct => Math.abs(stopDistPct - pct) < 0.01) ?? null : null
  const anaData = anaKey ? kaisAna[anaKey] : undefined
  const kaisSuggest = anaData && typeof anaData === 'object' ? levelsFromAnalysis(anaData, entryRef, levNum) : null
  const kaisSub = anaData === undefined ? 'Yükleniyor…' : !kaisSuggest ? 'Şu an net yön yok' : `${kaisSuggest.direction} · stop %${kaisSuggest.stopPct.toFixed(1)}`
  const applyLevels = (kind: 'kais' | 'vol', suggestion: NonNullable<ReturnType<typeof levelsFromVolatility>>, note: string) => {
    const stop = priceString(suggestion.stop)
    const nextSide = suggestion.direction === 'LONG' ? 'BUY' : 'SELL'
    setTpRR(null)
    patch({...(nextSide !== ticket.side ? {side: nextSide} : {}), stopPrice: stop, takeProfit: suggestion.target !== null ? priceString(suggestion.target) : ''})
    const clampNote = suggestion.clamped && suggestion.maxPct !== null ? ` ${levNum}x kaldıraçta stop en fazla %${suggestion.maxPct.toFixed(1)} uzakta olabilir; ona çekildi.` : ''
    setLevelPick({kind, stop, tone: suggestion.clamped || nextSide !== ticket.side ? 'warn' : 'ok', note: note + (nextSide !== ticket.side ? ` Yön analize göre ${suggestion.direction} yapıldı.` : '') + clampNote})
  }
  const applyKais = () => {
    if (!kaisSuggest) { setWizNotice({kind: 'error', text: anaData === undefined ? 'Kais analizi yükleniyor, birkaç saniye sonra tekrar dene.' : 'Bu coin için şu an net yönlü Kais analizi yok. Dalgaya göre ya da yüzde ile stop seç.'}); return }
    applyLevels('kais', kaisSuggest, `Kais analizinin stop ve hedefi Coinbase fiyatına uyarlandı (%${kaisSuggest.stopPct.toFixed(2)} stop${kaisSuggest.targetPct !== null ? `, %${kaisSuggest.targetPct.toFixed(2)} hedef` : ''}).`)
  }
  const applyVol = async () => {
    if (!anaKey || !(entryRef > 0) || volBusy) return
    const sideNow: 'LONG' | 'SHORT' = dir
    setVolBusy(true)
    try {
      const response = await fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(anaKey)}?interval=15m&limit=60`, {cache: 'no-store'}, 20000)
      if (!response.ok) throw new Error('klines')
      const candles: unknown = await response.json()
      const list = Array.isArray(candles) ? candles as StopCandle[] : []
      const last = Number(list[list.length - 1]?.close)
      const result = list.length && last > 0 ? suggestVolatilityStop(list, last, sideNow) : null
      const suggestion = result ? levelsFromVolatility(sideNow, entryRef, result.distancePct, levNum) : null
      if (!suggestion) { setLevelPick(null); setWizNotice({kind: 'error', text: 'Bu coin için yeterli mum verisi yok; stopu elle gir.'}); return }
      applyLevels('vol', suggestion, `Stop, coinin son 15 dk hareketine göre %${suggestion.stopPct.toFixed(2)} uzağa kondu; hedef bunun 2 katı.`)
    } catch {
      setWizNotice({kind: 'error', text: 'Mum verisi alınamadı; tekrar dene ya da stopu elle gir.'})
    } finally {
      setVolBusy(false)
    }
  }
  const pickOn = (kind: 'kais' | 'vol') => levelPick?.kind === kind && levelPick.stop === ticket.stopPrice.trim()
  const pickNote = levelPick && levelPick.stop === ticket.stopPrice.trim() ? <p className="mxtHint cbPickNote" role="status" data-tone={levelPick.tone}>{levelPick.note}</p> : null
  const expiry = selectedRow && !isPerp(selectedRow) ? expiryInfo(selectedRow.expiry) : null
  const expiryEl = expiry ? <p className="cbExpiry" role={expiry.tone === 'info' ? 'status' : 'alert'} data-tone={expiry.tone}><b>{expiry.tone === 'info' ? 'Vadeli ürün' : 'Vade yaklaşıyor'}</b>{expiry.text}</p> : null
  const stopMobile = <div className="mxtM mxtMStop">
    <div className="mxtMPicks" role="group" aria-label="Hazır stop ve hedef önerileri">
      <button type="button" className="mxtMPk" data-rec={kaisSuggest ? '1' : undefined} data-on={pickOn('kais') || undefined} aria-pressed={pickOn('kais')} disabled={!(entryRef > 0) || !anaKey} onClick={applyKais}><b>Kais analizi</b><small>{kaisSuggest ? `Önerilen · ${kaisSub}` : kaisSub}</small></button>
      <button type="button" className="mxtMPk" data-rec={!kaisSuggest ? '1' : undefined} data-on={pickOn('vol') || undefined} aria-pressed={pickOn('vol')} disabled={!(entryRef > 0) || !anaKey || volBusy} onClick={() => void applyVol()}><b>{volBusy ? 'Hesaplanıyor…' : 'Dalgaya göre'}</b><small>Coinin normal hareketine göre</small></button>
      {ordersOn ? <SuggestionButtons suggestions={suggestions} ticket={ticket} onApply={applySuggestion} variant="card"/> : [1, 2, 3, 5].map(pct => <button type="button" key={pct} className="mxtMPk" data-on={!pickOn('kais') && !pickOn('vol') && stopPickPct === pct || undefined} aria-pressed={stopPickPct === pct} disabled={!(entryRef > 0)} onClick={() => { pickStopPct(pct); if (tpRR === null) { const st = stopFromPct(ticket.side, entryRef, pct); const tg = st !== null ? targetFromRR(ticket.side, entryRef, st, 2) : null; if (tg !== null) { setTpRR(2); patch({stopPrice: priceString(st as number), takeProfit: priceString(tg)}) } } }}><b>Stop %{pct}</b><small>{entryRef > 0 ? `Stop ${priceString(stopFromPct(ticket.side, entryRef, pct) ?? 0)}` : 'Fiyat bekleniyor'}</small></button>)}
    </div>
    {pickNote}
    {ordersOn && <SuggestionNote suggestions={suggestions}/>}
    <div className="mxtMCard" data-tone="stop">
      <div className="mxtMLbl"><span>STOP</span><span>{stopDistPct !== null ? `Girişe %${stopDistPct.toFixed(2)}` : 'Henüz girilmedi'}</span></div>
      <div className="mxtMBig mxtMStep"><button type="button" aria-label="Stopu düşür" disabled={!(entryRef > 0)} onClick={() => nudge('stopPrice', -1)}>−</button><input type="number" inputMode="decimal" placeholder="Stop fiyatı" aria-label="Stop fiyatı" value={ticket.stopPrice} onChange={event => setStop(event.target.value)}/><button type="button" aria-label="Stopu yükselt" disabled={!(entryRef > 0)} onClick={() => nudge('stopPrice', 1)}>+</button></div>
      <div className="mxtMChips" role="group" aria-label="Stop uzaklığı (girişe göre yüzde)">{[0.5, 1, 2, 3, 5].map(pct => <button type="button" key={pct} disabled={!(entryRef > 0)} data-on={stopDistPct !== null && stopSideOk && Math.abs(stopDistPct - pct) < 0.005 || undefined} onClick={() => pickStopPct(pct)}>%{String(pct).replace('.', ',')}</button>)}</div>
      <p className="mxtMSum" aria-label="Stop özeti">
        <span>Uzaklık <b data-muted={stopDistPct === null || undefined}>{stopDistPct !== null ? `%${stopDistPct.toFixed(2)}` : '—'}</b></span>
        <span>Zarar <b data-muted={!risk || risk.wrongSide || undefined}>{risk && !risk.wrongSide ? `${usd(risk.lossUsd)} USD` : '—'}</b></span>
        <span>Risk <b data-tone={risk && !risk.wrongSide ? 'up' : risk?.wrongSide ? 'down' : undefined} data-muted={!risk || undefined}>{risk?.wrongSide ? 'Yanlış taraf' : risk ? 'Uygun ✓' : '—'}</b></span>
      </p>
      {!stopSideOk && <p className="mxtWarn" role="alert">Stop, {dir} için yanlış tarafta: {dir === 'LONG' ? 'girişin altında' : 'girişin üstünde'} olmalı.</p>}
    </div>
    <div className="mxtMCard" data-tone="tp">
      <div className="mxtMLbl"><span>HEDEF</span><span>Coinbase’de hedef ve stop tek emirde</span></div>
      <div className="mxtMTp"><i>TP</i><span className="mxtMStep"><button type="button" aria-label="Hedefi düşür" disabled={!(entryRef > 0)} onClick={() => nudge('takeProfit', -1)}>−</button><input type="number" inputMode="decimal" aria-label="Hedef fiyatı" placeholder="isteğe bağlı" value={ticket.takeProfit} onChange={event => { setTpRR(null); patch({takeProfit: event.target.value}) }}/><button type="button" aria-label="Hedefi yükselt" disabled={!(entryRef > 0)} onClick={() => nudge('takeProfit', 1)}>+</button></span><em>{risk?.targetPct != null ? `%${risk.targetPct.toFixed(2)}` : '—'}</em></div>
      <div className="mxtMLbl"><span>Riske göre</span><span>Hedef = stop mesafesi × R</span></div>
      <div className="mxtMChips" role="group" aria-label="Hedefi risk katına göre ayarla">{[1, 1.5, 2, 3].map(multiple => <button type="button" key={multiple} data-on={tpRR === multiple || undefined} disabled={!(entryRef > 0) || !Number.isFinite(stopNum)} onClick={() => pickTargetRR(multiple)}>R {String(multiple).replace('.', ',')}</button>)}<button type="button" disabled={ticket.takeProfit.trim() === ''} onClick={() => { setTpRR(null); patch({takeProfit: ''}) }}>Hedefsiz</button></div>
      <p className="mxtMSum"><span>Hedefte <b data-tone={risk?.gainUsd != null ? 'up' : undefined} data-muted={risk?.gainUsd == null || undefined}>{risk?.gainUsd != null ? `+${usd(risk.gainUsd)} USD` : '—'}</b></span><span>Kâr/zarar <b data-muted={risk?.rr == null || undefined}>{risk?.rr != null ? `1 : ${risk.rr.toFixed(1)}` : '—'}</b></span></p>
      {risk?.rr != null && risk.rr < 1 && <p className="mxtWarn" role="status">Hedef, stoptan yakın: kazanabileceğin, kaybedebileceğinden az.</p>}
    </div>
  </div>

  // --- Grid (Binance'teki Grid sekmesinin aynısı; Coinbase'de canlı grid motoru olmadığı için ÖNİZLEME, emir göndermez) ---
  const gridSide: GridSide = dir
  const gridPrice = Number.isFinite(shownPrice) ? shownPrice : 0
  const setGridField = (key: keyof typeof grid, value: string) => setGrid(current => ({...current, [key]: value}))
  const gridAuto = (pct: number) => {
    if (!(gridPrice > 0)) return
    const sgn = gridSide === 'LONG' ? -1 : 1
    const near = gridPrice * (1 + sgn * 0.005), far = gridPrice * (1 + sgn * pct / 100), stopAt = gridPrice * (1 + sgn * (pct + 1.5) / 100)
    setGrid(current => ({...current, lower: priceString(Math.min(near, far)), upper: priceString(Math.max(near, far)), stop: priceString(stopAt)}))
  }
  const selProduct = products?.find(item => item.product_id === selectedId)
  const tick = Number(selProduct?.price_increment)
  const gridFilters: GridFilters | undefined = Number.isFinite(contractSize) && contractSize > 0 && gridPrice > 0
    ? {tickSize: Number.isFinite(tick) && tick > 0 ? tick : Math.pow(10, Math.floor(Math.log10(gridPrice)) - 4), stepSize: contractSize, minNotional: contractSize * gridPrice} : undefined
  const gridResult = computeGrid({side: gridSide, price: gridPrice, lower: Number(grid.lower), upper: Number(grid.upper), count: Number(grid.count), totalMargin: Number(grid.margin), leverage: Number(grid.leverage), stop: Number(grid.stop), filters: gridFilters}, {
    maxPositions: 1000, maxLossPerTrade: 1_000_000, maxTotalExposure: 100_000_000, currentExposure: 0, openPositions: positions.length, dailyLossBudgetLeft: 1_000_000, openPositionRisk: positions.length === 0 ? 0 : null,
  })
  const gridAlerts = buildGridAlerts({result: gridResult, filters: gridFilters ? 'ok' : selectedId ? 'loading' : 'idle', openRisk: positions.length === 0 ? 0 : null, hasOpenPositions: positions.length > 0})
  const gridLevelsSorted = [...gridResult.levels].sort((x, y) => Math.abs(x.entry - gridPrice) - Math.abs(y.entry - gridPrice))
  const gridChecksOk = gridResult.checks.filter(item => item.ok).length
  const gridTooTight = gridResult.levels.length > 1 && gridResult.spacingPct > 0 && gridResult.spacingPct < 0.4
  const gridScore = gridResult.levels.length === 0 ? 0 : Math.max(0, Math.round(100 * gridChecksOk / Math.max(1, gridResult.checks.length)) - (gridTooTight ? 12 : 0) - (gridAlerts.some(item => item.level === 'kritik') ? 25 : 0))
  const gridScoreName = gridScore >= 80 ? 'İyi' : gridScore >= 55 ? 'Orta' : 'Zayıf'
  const gridRangePct = gridResult.spacingPct * Math.max(1, gridResult.levels.length - 1)
  const gridFitCount = Math.max(GRID_MIN_COUNT, Math.min(GRID_MAX_COUNT, Math.floor(gridRangePct / 0.5) + 1))
  const gridMapPrices = gridResult.levels.length ? [gridPrice, Number(grid.stop), ...gridResult.levels.map(level => level.entry)].filter(value => value > 0) : []
  const gridMapHi = gridMapPrices.length ? Math.max(...gridMapPrices) : 0, gridMapLo = gridMapPrices.length ? Math.min(...gridMapPrices) : 0
  const gridMapY = (value: number) => gridMapHi > gridMapLo ? ((gridMapHi - value) / (gridMapHi - gridMapLo)) * 100 : 50
  const gusd = (value: number) => `${usd(value)} USD`
  const gridMap = gridResult.levels.length > 0 && gridMapHi > gridMapLo ? <div className="mgpMap" role="img" aria-label="Kademe haritası">
    <div className="mgpMapZone" style={{top: `${gridMapY(Math.max(...gridResult.levels.map(level => level.entry)))}%`, bottom: `${100 - gridMapY(Math.min(...gridResult.levels.map(level => level.entry)))}%`}}/>
    {gridResult.levels.map(level => <div className="mgpMapLv" key={level.index} style={{top: `${gridMapY(level.entry)}%`}}><i>{level.index}</i><span>{priceText(level.entry)}</span><em>{level.notional.toFixed(0)} USD</em></div>)}
    {gridPrice > 0 && <div className="mgpMapPx" style={{top: `${gridMapY(gridPrice)}%`}}><span>Fiyat {priceText(gridPrice)}</span></div>}
    {Number(grid.stop) > 0 && <div className="mgpMapStop" style={{top: `${gridMapY(Number(grid.stop))}%`}}><span>STOP {priceText(Number(grid.stop))}</span></div>}
  </div> : null
  const gridWhatIf = gridLevelsSorted.length > 0 ? <ul className="mgpWhat" aria-label="Ne olur?">
    {gridLevelsSorted.slice(0, 3).map((level, index) => <li key={level.index}>Fiyat <b>{priceText(level.entry)}</b> seviyesine gelirse <b>{index + 1}</b> kademe dolar, toplam büyüklük <b>{gusd(gridLevelsSorted.slice(0, index + 1).reduce((sum, item) => sum + item.notional, 0))}</b>.</li>)}
    <li>Stop <b>{grid.stop || '—'}</b> olursa toplam zarar <b className="down">{gusd(gridResult.worstLoss)}</b> (hepsi dolmuşsa).</li>
    <li>Fiyat ilk kademeye hiç gelmezse emir dolmaz, kâr da zarar da olmaz.</li>
  </ul> : null
  const gridSmart = gridResult.levels.length > 0 ? <div className="mgpSmart">
    <div className="mgpScore" data-level={gridScoreName}><span className="mgpRing" style={{background: `conic-gradient(var(--mgp-c) 0 ${gridScore}%, #1a283a 0)`}}><b>{gridScore}</b></span><span><b>Plan puanı: {gridScoreName}</b><small>{gridChecksOk}/{gridResult.checks.length} kontrol geçti</small></span></div>
    {gridTooTight && <div className="mgpFix"><span><b>Kademeler çok sık</b><small>Aralık %{gridResult.spacingPct.toFixed(2)}; ücretler kârı yer.</small></span>{gridFitCount < Number(grid.count) && <button type="button" onClick={() => setGridField('count', String(gridFitCount))}>{gridFitCount} kademe yap</button>}</div>}
  </div> : null
  const gridBody = <div className="mwz mgp">
    <div className="mwzCard">
      <small className="mwzKicker">GRID · ÖNİZLEME</small>
      <h4>Kademeli alım planı</h4>
      <p>Coinbase’de canlı grid motoru yok; bu ekran planı hesaplar, <b>emir göndermez</b>. Coinbase’de aynı anda en fazla 5 açık emir olabilir.</p>
      {!selectedId && <p className="liveWsWarn" role="status">Önce Tek işlem sekmesinde ürün seç.</p>}
      <div className="mwzMini"><span>{selectedRow?.display || selectedId || 'Ürün seçilmedi'}</span><span className={gridSide === 'LONG' ? 'up' : 'down'}>{gridSide}</span><span>Fiyat {gridPrice > 0 ? priceText(gridPrice) : '—'}</span></div>
      <div className="mxtDirMini liveWsSeg" role="group" aria-label="Grid yönü"><button type="button" aria-pressed={gridSide === 'LONG'} className={gridSide === 'LONG' ? 'on' : ''} onClick={() => patch({side: 'BUY'})}>LONG</button><button type="button" aria-pressed={gridSide === 'SHORT'} className={gridSide === 'SHORT' ? 'on' : ''} onClick={() => patch({side: 'SELL'})}>SHORT</button></div>
      <div className="liveWsChips manualPresets" role="group" aria-label="Hazır aralık"><small className="liveWsHint">Hazır plan:</small>{([[3, 'Dar', 3], [5, 'Dengeli', 5], [8, 'Geniş', 5]] as const).map(([pct, name, cnt]) => <button type="button" key={pct} disabled={!(gridPrice > 0)} onClick={() => { gridAuto(pct); setGridField('count', String(cnt)) }}>{name} · %{pct}</button>)}</div>
      {gridSmart}{gridMap}
      <div className="liveWsTwo">
        <label className="liveWsField">Alt sınır<input type="number" value={grid.lower} onChange={event => setGridField('lower', event.target.value)}/></label>
        <label className="liveWsField">Üst sınır<input type="number" value={grid.upper} onChange={event => setGridField('upper', event.target.value)}/></label>
        <label className="liveWsField">Kademe sayısı ({GRID_MIN_COUNT}-{GRID_MAX_COUNT})<input type="number" min={GRID_MIN_COUNT} max={GRID_MAX_COUNT} value={grid.count} onChange={event => setGridField('count', event.target.value)}/></label>
        <label className="liveWsField">Toplam teminat (USD)<input type="number" min="5" value={grid.margin} onChange={event => setGridField('margin', event.target.value)}/></label>
        <label className="liveWsField">Kaldıraç (x)<input type="number" min="1" max="20" value={grid.leverage} onChange={event => setGridField('leverage', event.target.value)}/></label>
        <label className="liveWsField">Ortak stop<input type="number" value={grid.stop} onChange={event => setGridField('stop', event.target.value)}/></label>
      </div>
      <p className="liveWsHint mgpRules">{gridFilters ? `Coinbase kuralları: fiyat adımı ${priceString(gridFilters.tickSize)} · sözleşme ${priceString(contractSize)} · en küçük emir 1 sözleşme ≈ ${usd(gridFilters.minNotional)} USD` : 'Ürün seçilince Coinbase kuralları burada görünür.'}</p>
      {Number(grid.count) > 5 && <p className="liveWsWarn" role="status">Coinbase’de aynı anda en fazla 5 açık emir olabilir; {grid.count} kademe tek seferde kurulamaz.</p>}
      {gridAlerts.length > 0 && <ul className="mgpAlerts" role="alert" aria-label="Grid uyarıları">{gridAlerts.map((item, index) => <li key={index} data-level={item.level}><b>{item.level === 'kritik' ? 'KRİTİK' : item.level === 'uyari' ? 'UYARI' : 'BİLGİ'}</b> {item.text}</li>)}</ul>}
      {gridResult.levels.length > 0 && <>
        <div className="liveWsSumGrid mgpSum">
          <span><small>Kademe aralığı</small><b>%{gridResult.spacingPct.toFixed(2)}</b></span>
          <span><small>Tüm kademeler dolarsa büyüklük</small><b>{gusd(gridResult.totalNotional)}</b></span>
          <span><small>En kötü zarar (hepsi dolar, stop)</small><b className="down">{gusd(gridResult.worstLoss)}</b></span>
          <span><small>Tahmini kâr (hepsi hedefe)</small><b className="up">{gusd(gridResult.bestGain)}</b></span>
          <span><small>Giriş+hedef ücreti (tahmini)</small><b>{gusd(gridResult.entryFees)}</b></span>
          <span><small>Stop ücreti (tahmini)</small><b>{gusd(gridResult.exitFees)}</b></span>
          <span><small>Tahmini likidasyon (hepsi dolarsa)</small><b>{gridResult.liquidation ? priceText(gridResult.liquidation) : '—'}</b></span>
        </div>
        <ul className="mgpChecks" aria-label="Limit kontrolleri">{gridResult.checks.map(item => <li key={item.id} data-ok={item.ok ? '1' : '0'}><i aria-hidden="true">{item.ok ? '✓' : '!'}</i><span><b>{item.label}</b><small>{item.detail}</small></span></li>)}</ul>
        {gridWhatIf}
        <div className="mgpTable" role="table" aria-label="Kademeler (USD)"><div role="row" className="mgpHead"><span>#</span><span>Giriş</span><span>Hedef</span><span>Boyut</span><span>Zarar</span></div>
          {gridResult.levels.map(level => <div role="row" key={level.index}><span>{level.index}</span><span>{priceText(level.entry)}</span><span>{priceText(level.target)}</span><span>{level.notional.toFixed(1)}</span><span className="down">{level.lossAtStop.toFixed(2)}</span></div>)}</div>
        <p className="liveWsHint" role="status">Coinbase’de canlı grid kapalı olduğu için emir gönderilmez.</p>
      </>}
      {gridResult.errors.length > 0 && (grid.lower || grid.upper || grid.stop) && <ul className="mgpAlerts" aria-label="Eksikler">{gridResult.errors.map(item => <li key={item} data-level="uyari"><b>EKSİK</b> {item}</li>)}</ul>}
      <p className="liveWsHint">Hepsi tahmindir: fonlama ücreti, kayma ve borsanın gerçek teminat tablosu dahil değil; gerçek sonuç farklı olabilir. Zarar, her kademenin ayrı stopu değil, <b>hepsinin toplamı</b> olarak hesaplanır.</p>
    </div>
  </div>

  const summaryRows: SummaryItem[] = [
    {label: 'Ürün', value: ticket.productId.trim() || null},
    {label: 'Yön', value: dir},
    {label: 'Teminat', value: ticket.marginUsd.trim() ? `${ticket.marginUsd.trim()} USD` : null},
    {label: 'Hedef (TP)', value: ticket.takeProfit.trim() ? `${ticket.takeProfit.trim()} (Coinbase'de hedef+stop tek emir)` : 'Yok (yalnızca stop)'},
    {label: 'Giriş türü', value: ticket.rest ? `Bekleyen limit · ${ticket.limitPrice.trim() || '—'} (dolana kadar emir kalır)` : ticket.limitPrice.trim() ? `Limit · en kötü ${ticket.limitPrice.trim()} (dolmazsa işlem açılmaz)` : 'Piyasa'},
    {label: 'Stop (zorunlu)', value: ticket.stopPrice.trim() || null, tone: 'down'},
    ...(plan ? [
      {label: 'Sözleşme sayısı', value: String(plan.contracts)},
      {label: 'Tahmini giriş', value: String(plan.entry_reference)},
      {label: 'Stop-limit (en kötü çıkış)', value: String(plan.stop_limit_price)},
      {label: 'Kaldıraç', value: `${plan.leverage}x`},
      {label: 'Pozisyon değeri', value: `${plan.notional_usd} USD`},
      {label: 'Stopta en kötü kayıp', value: `${plan.worst_case_loss_usd} USD`, tone: 'down' as const, big: true},
    ] : []),
  ]
  const checkRows: CheckRow[] = check === null
    ? [{key: 'pre', label: 'Ön kontrol', state: 'pending', detail: 'Henüz yapılmadı. "Ön kontrol yap" ile sınanır; gerçek emir gitmez.'}]
    : check.ok
      ? [{key: 'pre', label: 'Ön kontrol', state: 'ok', detail: 'Kayıp, kaldıraç, alım gücü, marjin ve sınırlar uygun.'}]
      : check.reasons.map((reason, index) => ({key: `r${index}`, label: 'Düzeltilmeli', state: 'bad' as const, detail: reason}))
  const reviewed = check?.ok === true
  const sendBlock = futuresMissing ? 'Vadeli cüzdanında bakiye yok. Coinbase\'de vadeli cüzdana USD ekle, sonra tekrar dene.' : !ordersOn ? 'Emir gönderme sunucuda kapalı (COINBASE_ORDERS_ENABLED).' : !reviewed ? 'Önce "Ön kontrol yap" ile sına; geçerse gönderebilirsin.' : ''

  const noticeEl = wizNotice.kind !== 'info' && wizNotice.text
    ? <p className={`mxtNotice ${wizNotice.kind}`} role={wizNotice.kind === 'error' ? 'alert' : 'status'}>{wizNotice.text}</p> : null

  const progressEl = busy ? <div className="mxtProgress" role="status" aria-live="polite" key={busy}>
    <RefreshCw className="spin" aria-hidden="true"/>
    <span><b>{busy === 'review' ? 'Ön kontrol yapılıyor…' : 'Emir Coinbase\'e gönderiliyor, stop doğrulanıyor…'}</b>
      <small>{busy === 'review' ? 'Gerçek emir gitmez; Coinbase kurallarına ve sınırlara göre sınanıyor.' : 'Ekranı kapatma, tekrar basma; sonuç gelince burada görünür.'} <ElapsedSeconds/></small></span>
  </div> : null

  // Hesap/bakiye hazır değilse düğme sakin görünür; yine de açılır (inceleme serbest, gönderimde bakiye hatası verir).
  const waitFunds = futuresMissing || (account?.balance != null && !(Number(account.balance.futures_buying_power?.value) > 0))
  const statusStrip = <CoinbaseAccountHero balance={account?.balance ?? null} futuresMissing={futuresMissing} ordersOn={ordersOn}
    clockSkew={status?.clock?.ok === false ? status.clock.skew_seconds : null} openCount={positions.length} loading={loading} onRefresh={() => void refresh()}
    openSummary={positions.length === 1 ? `${(positions[0].product_id || '').split('-')[0]} · ${String(positions[0].side ?? '').toUpperCase().endsWith('SHORT') ? 'SHORT' : 'LONG'}` : positions.length > 1 ? `${positions.length} açık` : null}
    openOrders={protection?.open_order_count ?? null} lastCheck={lastCheck}/>

  const reviewStep = <>
    <h4>Son kontrol</h4>
    {expiry && expiry.tone !== 'info' && expiryEl}
    <p className="mxtLead">Önce ön kontrolle sına (gerçek emir gitmez), sonra gönder. Gönderirken yazı onayı istenir.</p>
    {futuresMissing && <div className="mxtLockCard" role="alert"><span><b>Bakiye ekle</b><small>Vadeli cüzdanında bakiye yok. Coinbase'de vadeli cüzdana USD ekle, buradan yenile ve tekrar dene.</small></span></div>}
    {!ordersOn && <div className="mxtLockCard" role="status"><span><b>Emir şu an kilitli</b><small>{sendBlock}</small></span></div>}
    <div className="mxtReviewGrid"><SummaryList rows={summaryRows.slice(0, 5)}/><SummaryList rows={summaryRows.slice(5)}/></div>
    <CheckList rows={checkRows}/>
    {plan && <p className="mxtHint">Stop'ta kaybın en çok {String(plan.worst_case_loss_usd)} USD olur.</p>}
    {progressEl}
    <div className="mxtActions">
      <button type="button" className="mxtBtn ghost" onClick={() => void reviewOrder()} disabled={busy !== '' || !ordersOn || futuresMissing}>{busy === 'review' ? <RefreshCw className="spin"/> : <Send/>} {busy === 'review' ? 'Kontrol ediliyor…' : 'Ön kontrol yap'}</button>
      <button type="button" className="mxtBtn danger mxtSendPop" data-sending={busy === 'send' || undefined} onClick={() => { setConfirmText(''); setConfirmOpen(true) }} disabled={busy !== '' || sendBlock !== ''} aria-busy={busy === 'send'}>
        {busy === 'send' ? 'Gönderiliyor…' : <><Send/> Gerçek emri gönder</>}
      </button>
    </div>
    {busy !== 'send' && noticeEl}
    {sendBlock && <p className="mxtHint">{sendBlock}</p>}
    <p className="mxtHint">Giriş piyasa emriyle yapılır. Stop kurulamaz veya doğrulanamazsa pozisyon hemen kapatılır. Hedef (TP) isteğe bağlıdır.</p>
  </>

  const summaryAside = <aside className="mxtSummary" data-open={summaryOpen} aria-label="Emir özeti">
    <button type="button" className="mxtSumToggle" aria-expanded={summaryOpen} onClick={() => setSummaryOpen(open => !open)}><span>Emir özeti · {ticket.productId.trim() || 'ürün seçilmedi'}</span><ChevronDown aria-hidden="true"/></button>
    <div className="mxtSumBody"><SummaryList rows={summaryRows}/></div>
  </aside>

  const prot = done ? protection?.positions.find(item => item.product_id === done.product) : undefined
  const hasPos = Boolean(prot)
  const hasStop = prot?.state === 'protected'
  const goPositions = () => { setDone(null); setManualOpen(false); window.setTimeout(() => positionsRef.current?.scrollIntoView?.({block: 'start', behavior: 'smooth'}), 60) }
  const doneView = done ? <div className="mxtDone" role="status">
    <span className="mxtDoneIco" aria-hidden="true">✓</span>
    <h4>Emir gönderildi</h4>
    <p>{done.product} · {done.side === 'BUY' ? 'LONG' : 'SHORT'} {done.leverage}x · {done.margin} USD teminat · {done.contracts} sözleşme</p>
    <div className="mxtDoneGrid">
      <div><small>Giriş (yaklaşık)</small><b>{done.entry}</b></div>
      <div data-k="stop"><small>Stop</small><b>{done.stop}</b></div>
    </div>
    <div className="mxtProt" aria-label="Koruma kontrolü">
      <div data-ok={hasPos || undefined}><span>Pozisyon</span><b>{hasPos ? 'Açık ✓' : 'Bekleniyor…'}</b></div>
      <div data-ok={hasStop || undefined} data-bad={hasPos && !hasStop ? '' : undefined}><span>Stop</span><b>{hasStop ? 'Coinbase’de duruyor ✓' : hasPos ? 'Bulunamadı ✕' : 'Bekleniyor…'}</b></div>
      {hasPos && !hasStop && <p className="mxtProtWarn">⚠ Coinbase’de stop emri görünmüyor. Aşağıdaki pozisyondan kontrol et.</p>}
    </div>
    <button type="button" className="mxtBtn ghost" onClick={goPositions}>Pozisyonlarıma git</button>
    <button type="button" className="mxtDoneNew" onClick={() => setDone(null)}>Yeni emir</button>
  </div> : null

  const askText = `Coinbase manuel emirde "${STEP_NAMES[step]}" adımındayım${ticket.productId.trim() ? `, ürün ${ticket.productId.trim()}, yön ${dir}` : ''}. Bu ayarlar mantıklı mı? Ne dikkat etmeliyim?`

  const confirmModal = confirmOpen && typeof document !== 'undefined' ? createPortal(
    <div className="liveConfirmBackdrop"><section className="liveConfirm" role="dialog" aria-modal="true" data-tone="danger">
      <span className="liveConfirmGrab" aria-hidden="true"/>
      <span className="liveConfirmIcon" aria-hidden="true"><ShieldAlert/></span>
      <h2>COINBASE MANUEL EMİR</h2>
      <p>GERÇEK PARA KULLANILACAK. {String(check?.plan?.product_id ?? '')} · {dir} · PİYASA · {String(check?.plan?.contracts ?? '')} sözleşme · stop {String(check?.plan?.stop_price ?? '')} · stopta en kötü kayıp {String(check?.plan?.worst_case_loss_usd ?? '')} USD. Stop doğrulanamazsa pozisyon hemen kapatılır.</p>
      <label>TYPE TO CONFIRM<input autoFocus value={confirmText} onChange={event => setConfirmText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && sameConfirm(confirmText, CONFIRM.open)) void sendOrder() }} placeholder={CONFIRM.open} autoComplete="off"/></label>
      <div className="liveConfirmBtns"><button type="button" className="liveConfirmNo" onClick={() => { setConfirmOpen(false); setConfirmText('') }}>CANCEL</button>
        <button type="button" className="liveConfirmYes" disabled={!sameConfirm(confirmText, CONFIRM.open)} onClick={() => void sendOrder()}>CONFIRM</button></div>
    </section></div>,
    document.body,
  ) : null

  const vadePicker = selectedVariants.length > 1 ? <div className="cbVade" role="group" aria-label="Vade"><span>Vade:</span>{selectedVariants.map(row => <button type="button" key={row.symbol} aria-pressed={row.symbol === selectedId} data-on={row.symbol === selectedId || undefined} onClick={() => chooseCoin(row.symbol)}>{variantLabel(row)}</button>)}</div> : null
  const toolbar = signals !== null && <div className="cpkTools">
    <div className="cpkChips" role="group" aria-label="Filtre">{FILTER_CHIPS.map(([key, label]) => <button type="button" key={key} aria-pressed={coinFilter === key} className={coinFilter === key ? 'on' : ''} onClick={() => setCoinFilter(key)}>{label}<span>{key === 'all' ? rows.length : signalCount(key)}</span></button>)}</div>
    <div className="cpkChips cpkSorts" role="group" aria-label="Sıralama">{SORT_CHIPS.map(([key, label]) => <button type="button" key={key} aria-pressed={coinSort === key} className={coinSort === key ? 'on' : ''} onClick={() => setCoinSort(key)}>{label}</button>)}</div>
  </div>
  const pickAll = signals ? sortCoins(filterBySignal(rows, signals, coinFilter), signals, coinSort).filter(row => signals[row.symbol]) : []
  const pickRows = pickAll.slice(0, PICK_LIMIT)
  const sortLabel = (SORT_CHIPS.find(([key]) => key === coinSort) || [null, ''])[1]
  const signalNote = signals !== null && Object.keys(signals).length === 0 ? 'Bu ürünler için Kais AI analizi eşleşmedi (dayanak varlık bilinmiyor ya da analiz evreninde yok).' : ''
  const picksBlock = !coinOpen && <div className="cpk"><section className="cpkPicks" aria-label="Kais AI güçlü sinyaller">
    <header className="cpkPicksHead"><h5>Kais AI güçlü sinyaller</h5>{universe === null && !signalsError && <span className="cpkStatus">Analiz yükleniyor…</span>}{signalsError && <span className="cpkStatus warn">Analiz şu an alınamadı; liste hacme göre.</span>}</header>
    {toolbar}
    {universe === null && !signalsError && <ul className="cpkPickList" aria-hidden="true">{[0, 1, 2, 3].map(index => <li key={index} className="cpkSkeleton"/>)}</ul>}
    {signalNote && <p className="cpkEmpty">{signalNote}</p>}
    {signals !== null && !signalNote && pickRows.length === 0 && <p className="cpkEmpty">Bu filtrede ürün yok. Başka bir filtre seç.</p>}
    {pickRows.length > 0 && <p className="cpkPickCount" role="status"><b>{pickAll.length} ürün</b> · {sortLabel}{pickAll.length > 4 ? ' · kaydırarak hepsini gör' : ''}</p>}
    {pickRows.length > 0 && <ul className="cpkPickList cpkPickScroll" key={`${coinFilter}-${coinSort}`}>{pickRows.map(row => {
      const signal = signals![row.symbol]
      return <li key={row.symbol}><button type="button" aria-pressed={row.symbol === selectedId} data-lean={signal.lean} className={`cpkCard ${row.symbol === selectedId ? 'on' : ''}`} onClick={() => chooseCoin(row.symbol)}>
        <span className="cpkCardTop"><strong>{row.display}</strong><span className="cpkTick"><span>{priceText(row.price)}</span><em className={row.change >= 0 ? 'up' : 'down'}>{changeText(row.change)}</em></span></span>
        {miniDuo(signal)}
      </button></li>
    })}</ul>}
    <p className="cpkNote">{AI_DISCLAIMER}</p>
  </section></div>
  const coinBlock = <div className="liveWsBlock liveWsCoin cpk">
    <span className="liveWsLabel">1 · Ürün</span>
    <button type="button" className="liveWsCoinBtn cpkHero" data-lean={selectedSignal?.lean ?? 'NONE'} aria-haspopup="listbox" aria-expanded={coinOpen} onClick={() => setCoinOpen(value => !value)}>
      <span className="cpkHeroMain"><b>{selectedRow?.display || selectedId || 'Ürün seç'}</b>
        <span className="cpkTick">{selectedRow || quote ? <><span>{priceText(shownPrice)} USD</span>{selectedRow && <em className={selectedRow.change >= 0 ? 'up' : 'down'}>{changeText(selectedRow.change)}</em>}{quote && <em className="mxtMLive" title="Coinbase anlık fiyatı"><i aria-hidden="true"/>CANLI</em>}</> : <span>{productsError ? 'Liste alınamadı' : products === null ? 'Liste yükleniyor…' : 'Listeden seç'}</span>}</span>
        {selectedSignal && <span className="cpkLeanLine">{leanText(selectedSignal)}</span>}</span>
      {duo(selectedSignal)}
      <ChevronDown/>
    </button>
    {vadePicker}
    {expiryEl}
    {quoteError && selectedId && <small className="liveWsHint" role="alert">Canlı fiyat alınamadı; son liste fiyatı gösteriliyor.</small>}
    {coinOpen && <div className="cpkPop">
      <label className="cpkSearch"><Search/><input autoFocus value={coinQuery} placeholder="Ürün ara: BTC, ETH, SOL…" aria-label="Ürün ara" onChange={event => setCoinQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Escape') setCoinOpen(false)
          if (event.key === 'Enter') { if (listed[0]) chooseCoin(listed[0].symbol); else if (isProductId(coinQuery)) chooseCoin(coinQuery.trim()) }
        }}/></label>
      {toolbar}
      {products === null && !productsError && <p className="cpkEmpty">Ürün listesi yükleniyor…</p>}
      {productsError && <p className="cpkEmpty warn">Liste alınamadı: {productsError} Ürün kodunu yazıp Enter'a bas.</p>}
      {products !== null && <ul role="listbox" aria-label="Ürün listesi" className="cpkList">{listed.map(row => <li key={row.symbol}><button type="button" role="option" aria-selected={row.symbol === selectedId} data-lean={signals?.[row.symbol]?.lean ?? 'NONE'} className="cpkRow" onClick={() => chooseCoin(row.symbol)}>
        <strong>{row.display}{expiryText(row.expiry) ? ` · ${expiryText(row.expiry)}` : ''}</strong>{miniDuo(signals?.[row.symbol])}<span className="cpkRowPrice">{priceText(row.price)}</span><em className={row.change >= 0 ? 'up' : 'down'}>{changeText(row.change)}</em></button></li>)}{listed.length === 0 && <li className="cpkEmpty">Sonuç yok.</li>}</ul>}
      {signals !== null && <p className="cpkNote">{AI_DISCLAIMER}</p>}
      {products !== null && matches.length > listed.length && <p className="cpkNote">{matches.length} sonuçtan ilk {listed.length} gösteriliyor; aramayı daralt.</p>}
    </div>}
  </div>
  const coinSym = (row: CbRow | undefined) => (row && (analysisSymbol(row.root) || row.root)) || ''
  const leanBadge = (signal: CoinSignal | undefined) => !signal ? null : signal.lean === 'LONG' ? <span className="mxtMLean" data-lean="LONG">LONG{signal.long !== null ? ` %${signal.long}` : ''}</span> : signal.lean === 'SHORT' ? <span className="mxtMLean" data-lean="SHORT">SHORT{signal.short !== null ? ` %${signal.short}` : ''}</span> : <span className="mxtMLean">Nötr</span>
  const coinMark = (row: CbRow | undefined, fallback: string) => <span className="mxtMLogo" aria-hidden="true"><CoinIcon symbol={coinSym(row) || fallback} size={40}/></span>
  const recentRows = !coinQuery.trim() ? recentIds.map(id => allRows.find(row => row.symbol === id)).filter((row): row is CbRow => Boolean(row)).filter((row, index, list) => list.findIndex(item => (item.root || item.symbol) === (row.root || row.symbol)) === index) : []
  const mobileList = matches.slice(0, 120)
  // Analiz çekmecesi: Binance'teki ile aynı (listedeki sinyalden beslenir, yeni istek atmaz).
  const anaIdx = anaSym ? matches.findIndex(row => row.symbol === anaSym) : -1
  const anaRow = anaIdx >= 0 ? matches[anaIdx] : undefined
  const anaSig = anaRow ? signals?.[anaRow.symbol] : undefined
  const anaStep = (delta: number) => { const next = matches[anaIdx + delta]; if (next && signals?.[next.symbol]) setAnaSym(next.symbol) }
  const anaPick = () => { if (!anaRow) return; chooseCoin(anaRow.symbol); setAnaSym(null) }
  const anaLeanTitle = !anaSig ? '' : anaSig.lean === 'LONG' ? 'Yükseliş yönü ağır basıyor' : anaSig.lean === 'SHORT' ? 'Düşüş yönü ağır basıyor' : 'Belirgin yön yok'
  const anaPos = anaSig && anaSig.long !== null && anaSig.short !== null && anaSig.long + anaSig.short > 0 ? anaSig.long / (anaSig.long + anaSig.short) * 100 : 50
  const anaRead = !anaSig ? '' : anaSig.lean === 'NEUTRAL' ? 'LONG ve SHORT tarafları birbirine yakın. Net bir yön yok.'
    : `${anaSig.lean === 'SHORT' ? 'Düşüş' : 'Yükseliş'} tarafı ${strengthLabel(anaSig.confidence) === 'Güçlü' ? 'belirgin şekilde' : strengthLabel(anaSig.confidence) === 'Orta' ? 'kısmen' : 'hafifçe'} ağır basıyor.${strengthLabel(anaSig.confidence) === 'Zayıf' ? ' Tek başına yön için yeterli değil.' : ''}`
  const anaSheet = anaRow && anaSig && typeof document !== 'undefined' ? createPortal(<div className="mxsaRoot" data-open="1">
    <button type="button" className="mxsaScrim" aria-label="Analizi kapat" onClick={() => setAnaSym(null)}/>
    <section className="mxsaSheet" role="dialog" aria-modal="true" aria-label={`${anaRow.display} analizi`} data-lean={anaSig.lean}
      onKeyDown={event => { if (event.key === 'Escape') setAnaSym(null); if (event.key === 'ArrowRight') anaStep(1); if (event.key === 'ArrowLeft') anaStep(-1) }}>
      <span className="mxsaGrab" aria-hidden="true"/>
      <header className="mxsaHead">
        <button type="button" className="mxsaIcon" aria-label="Önceki coin" disabled={anaIdx <= 0} onClick={() => anaStep(-1)}><ChevronLeft aria-hidden="true"/></button>
        <span className="mxsaTitle"><small>ANALİZ SONUCU · {anaIdx + 1} / {matches.length}</small><b>{anaRow.display}</b></span>
        <button type="button" className="mxsaIcon" aria-label="Sonraki coin" disabled={anaIdx >= matches.length - 1 || !signals?.[matches[anaIdx + 1]?.symbol]} onClick={() => anaStep(1)}><ChevronRight aria-hidden="true"/></button>
        <button type="button" className="mxsaIcon" aria-label="Analizi kapat" onClick={() => setAnaSym(null)}><X aria-hidden="true"/></button>
      </header>
      <div className="mxsaBody">
        <section className="mxsaHero" aria-label="Analiz özeti">
          <div className="mxsaHeroTxt">
            <span className="mxsaPrice">{priceText(anaRow.price)}{Number.isFinite(anaRow.change) && <em data-tone={anaRow.change >= 0 ? 'up' : 'down'}>{changeText(anaRow.change)}</em>}</span>
            <b className="mxsaVerdictT" data-lean={anaSig.lean}>{anaLeanTitle}</b>
            <p className="mxsaRead">{anaRead}</p>
          </div>
          <div className="mxsaRing" data-lean={anaSig.lean} style={{'--mxsa-p': `${anaSig.confidence}%`} as React.CSSProperties} role="img" aria-label={`Güven yüzde ${anaSig.confidence}, ${strengthLabel(anaSig.confidence)}`}>
            <span><strong>%{anaSig.confidence}</strong><small>{strengthLabel(anaSig.confidence)}</small></span>
          </div>
        </section>
        {coinSym(anaRow) && <CoinLiveChart symbol={coinSym(anaRow)}/>}
        <section className="mxsaGauge" aria-label="LONG ve SHORT dengesi">
          <div className="mxsaTrack" data-lean={anaSig.lean}><i style={{left: `${anaPos}%`}}/></div>
          <div className="mxsaEnds"><span data-side="SHORT">SHORT</span><span data-side="BEKLE">NÖTR</span><span data-side="LONG">LONG</span></div>
        </section>
        <div className="mxsaPair">
          <div className="mxsaTile" data-side="SHORT" data-lead={anaSig.lean === 'SHORT' || undefined}><span>SHORT</span><strong>{anaSig.short === null ? '–' : `%${anaSig.short}`}</strong><span className="mxsaMeter"><i style={{width: `${anaSig.short ?? 0}%`}}/></span></div>
          <div className="mxsaTile" data-side="LONG" data-lead={anaSig.lean === 'LONG' || undefined}><span>LONG</span><strong>{anaSig.long === null ? '–' : `%${anaSig.long}`}</strong><span className="mxsaMeter"><i style={{width: `${anaSig.long ?? 0}%`}}/></span></div>
        </div>
        <p className="mxsaNote">{AI_DISCLAIMER} Analiz, ürünün dayanak coininin (Binance verisi) durumunu gösterir.</p>
      </div>
      <footer className="mxsaFoot">
        {anaSig.lean === 'NEUTRAL'
          ? <button type="button" className="mxsaCta" data-lean="NEUTRAL" onClick={anaPick}>{anaRow.display} seç</button>
          : <button type="button" className="mxsaCta" data-lean={anaSig.lean} onClick={anaPick}>{anaRow.display} seç · analiz {anaSig.lean}</button>}
        <button type="button" className="mxsaBack" onClick={() => setAnaSym(null)}>Listeye dön</button>
      </footer>
    </section>
  </div>, document.body) : null
  const coinMobile = <div className="mxtM mxtMCoin">
    {selectedId && <div className="mxtMSel">{coinMark(selectedRow, selectedId)}<span className="mxtMSelN"><b>{selectedRow?.display || selectedId}</b><small>{Number.isFinite(shownPrice) ? <>{priceText(shownPrice)}{selectedRow && Number.isFinite(selectedRow.change) && <> · <em data-tone={selectedRow.change >= 0 ? 'up' : 'down'}>{changeText(selectedRow.change)}</em></>}</> : (quoteRaw?.source === 'none' ? 'Fiyat yok' : 'Fiyat alınıyor…')}</small></span>{leanBadge(selectedSignal)}</div>}
    {vadePicker}
    {expiryEl}
    {selectedId && (quoteNote || (!quoteRaw && !Number.isFinite(shownPrice) && quoteError)) && <p className="mxtMSigNote" role="status">{quoteNote || 'Fiyat alınamadı; birkaç saniye içinde yeniden deneniyor.'}</p>}
    {selectedId && coinSym(selectedRow) && <CoinLiveChart symbol={coinSym(selectedRow)} compact/>}
    <label className="mxtMSearch"><Search aria-hidden="true"/><input value={coinQuery} placeholder="Coin ara: BTC, ETH, SOL…" aria-label="Coin ara" onChange={event => setCoinQuery(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter') { if (matches[0]) chooseCoin(matches[0].symbol); else if (isProductId(coinQuery)) chooseCoin(coinQuery.trim()) } }}/></label>
    {recentRows.length > 0 && <div className="mxtMRecent"><span>Son:</span>{recentRows.slice(0, 5).map(row => <button type="button" key={row.symbol} onClick={() => chooseCoin(row.symbol)}>{row.root || row.display}</button>)}</div>}
    <div className="mxtMTabs" role="group" aria-label="Filtre" aria-busy={signals === null || undefined} data-loading={signals === null || undefined}>{FILTER_CHIPS.map(([key, label]) => <button type="button" key={key} disabled={signals === null && key !== 'all'} aria-pressed={coinFilter === key} data-on={coinFilter === key || undefined} onClick={() => setCoinFilter(key)}>{label.replace(' ağır', '').replace(' sinyal', '')}<em>{key === 'all' ? rows.length : signals === null ? <i className="mxtMSpin" aria-hidden="true"/> : signalCount(key)}</em></button>)}</div>
    {signals === null && signalsError && <p className="mxtMSigNote" role="status">Kais AI sinyalleri alınamadı; liste hacme göre gösteriliyor.</p>}
    <div className="mxtMMeta"><span><b>{matches.length} ürün</b>{matches.length > 5 ? ' · kaydır' : ''}</span>{signals !== null && <label className="mxtMSort">Sırala:<select value={coinSort} aria-label="Sıralama" onChange={event => setCoinSort(event.target.value as CoinSort)}>{SORT_CHIPS.map(([key, label]) => <option key={key} value={key}>{label.replace(' göre', '').replace('Güvene', 'Güven').replace('Hacme', 'Hacim').replace('Değişime', 'Değişim')}</option>)}</select></label>}</div>
    {products === null && !productsError && <p className="mxtMEmpty">Ürün listesi yükleniyor…</p>}
    {productsError && <p className="mxtMEmpty">Liste alınamadı: {productsError} Ürün kodunu yazıp Enter'a bas.</p>}
    {products !== null && matches.length === 0 && <p className="mxtMEmpty">Bu aramada ya da filtrede ürün yok.</p>}
    {mobileList.length > 0 && <ul className="mxtMList" key={`${coinFilter}-${coinSort}-${coinQuery}`} role="listbox" aria-label="Ürün listesi">{mobileList.map(row => <li key={row.symbol} className="mxsaLi" role="none"><button type="button" role="option" aria-selected={isSelectedCoin(row)} data-on={isSelectedCoin(row) || undefined} onClick={() => chooseCoin(isSelectedCoin(row) ? selectedId : row.symbol)}>
      {coinMark(row, row.symbol)}<span className="mxtMRowN"><b>{row.root || row.display}{(grouped.variants[row.root || row.symbol]?.length ?? 0) > 1 ? <em className="cbVadeN"> · {grouped.variants[row.root || row.symbol].length} vade</em> : expiryText(row.expiry) ? ` · ${variantLabel(row)}` : ''}</b><small>{priceText(row.price)}</small></span>
      <span className="mxtMRowR">{Number.isFinite(row.change) && <em data-tone={row.change >= 0 ? 'up' : 'down'}>{changeText(row.change)}</em>}{leanBadge(signals?.[row.symbol])}</span>
      {isSelectedCoin(row) && <i className="mxtMTick" aria-hidden="true">✓</i>}
    </button>{signals?.[row.symbol] && <button type="button" className="mxsaBtn" aria-label={`${row.display} analizini aç`} onClick={() => setAnaSym(row.symbol)}><BarChart3 aria-hidden="true"/></button>}</li>)}</ul>}
    {anaSheet}
    {matches.length > mobileList.length && <p className="mxtMEmpty">{matches.length} üründen ilk {mobileList.length} gösteriliyor; aramayla daralt.</p>}
    {signals !== null && <p className="mxtMNote">{AI_DISCLAIMER}</p>}
  </div>
  const aiCard = selectedSignal ? <div className="cpk mxtAi" data-align={align}>
    <h5>Kais AI analizi · {selectedRow?.display || selectedId}</h5>
    {duo(selectedSignal)}
    <div className="mxtFacts">
      <div><span>Sinyal yönü</span><b data-tone={selectedSignal.lean === 'LONG' ? 'up' : selectedSignal.lean === 'SHORT' ? 'down' : undefined}>{selectedSignal.lean === 'NEUTRAL' ? 'Net yön yok' : selectedSignal.lean}</b></div>
      <div><span>Sinyal gücü</span><b>{strengthLabel(selectedSignal.confidence)} · %{selectedSignal.confidence}</b></div>
      <div><span>Seçtiğinle uyum</span><b data-tone={align === 'aligned' ? 'up' : align === 'opposed' ? 'warn' : undefined}>{align === 'aligned' ? 'Uyumlu' : align === 'opposed' ? 'Ters yönde' : 'Karşılaştırılamaz'}</b></div>
    </div>
    {align === 'opposed' && <div className="mxsaAlert" role="alert" data-strong={selectedSignal.confidence >= STRONG_CONFIDENCE || undefined}>
      <TriangleAlert aria-hidden="true"/>
      <div>
        <b>{selectedSignal.confidence >= STRONG_CONFIDENCE ? 'Güçlü analizin tersine gidiyorsun' : 'Yön analizle uyuşmuyor'}</b>
        <p>Kais AI bu üründe <strong>{selectedSignal.lean} %{selectedSignal.confidence}</strong> diyor, sen <strong>{dir}</strong> seçtin. Analiz, ürünün dayanak varlığının (Binance verisi) durumunu gösterir; bu yalnızca bilgidir, kararı sen verirsin.</p>
        <div className="mxsaAlertActs"><button type="button" onClick={() => patch({side: selectedSignal.lean === 'LONG' ? 'BUY' : 'SELL'})}>{selectedSignal.lean} yap</button></div>
      </div>
    </div>}
    <p className="mxtHint">{AI_DISCLAIMER}</p>
  </div> : <p className="mxtHint">{universe === null && !signalsError ? 'Kais AI analizi yükleniyor…' : 'Bu ürün için Kais AI analizi yok (dayanak varlık eşleşmedi). Yön seçimi tamamen sana ait.'}</p>

  const wizardBody = <div className="mxt" data-step={step} data-done={done ? 'yes' : undefined}>
    {doneView}
    <div className="mxtM mxtMHead">
      <div className="mxtMLabel"><span>ADIM {step + 1} / {STEP_NAMES.length} · <em>{STEP_NAMES[step]}</em></span><span className="mxtMTools"><button type="button" className="mxtMAsk" onClick={() => askKais(askText)}>✦ Kais’e sor</button></span></div>
      {(selectedRow || quote) && Number.isFinite(shownPrice) && <div className="mxtMPrice"><i aria-hidden="true"/><b>{selectedRow?.display || selectedId}</b><span>{priceText(shownPrice)}</span>{selectedRow && Number.isFinite(selectedRow.change) && <span className="mxtMChg" data-tone={selectedRow.change >= 0 ? 'up' : 'down'}>{changeText(selectedRow.change)}</span>}{quote && <em className="mxtMLive" title="Coinbase anlık fiyatı"><i aria-hidden="true"/>CANLI</em>}</div>}
    </div>
    <ol className="mxtSteps" aria-label="Manuel emir adımları">{STEP_NAMES.map((name, index) => <li key={name} data-state={index === step ? 'current' : index < step ? 'done' : 'todo'}>
      <button type="button" disabled={index > step && !stepOk.slice(0, index).every(Boolean)} aria-current={index === step ? 'step' : undefined} onClick={() => goStep(index)}><i>{index < step ? '✓' : index + 1}</i><span>{name}</span></button></li>)}</ol>
    <div className="mxtLayout">
      <div className="mxtCard" key={step}>
        <span className="mxtKicker">Adım {step + 1} / {STEP_NAMES.length}</span>
        {step !== 4 && noticeEl}
        {step === 0 && <><h4>Hangi coin?</h4><p className="mxtLead">Listeden seç ya da ürün kodunu yaz.</p>{coinMobile}<div className="mxtDesk">{picksBlock}</div>{coinBlock}</>}
        {step === 1 && <><h4>Hangi yön?</h4><p className="mxtLead">LONG: fiyat yükselirse kazanırsın. SHORT: fiyat düşerse kazanırsın.</p>
          <DirectionCards value={dir} onChange={side => { const next = side === 'LONG' ? 'BUY' : 'SELL'; if (next !== ticket.side) { setTpRR(null); patch({side: next, stopPrice: '', takeProfit: ''}) } }}/>
          {aiCard}</>}
        {step === 2 && <><h4>Ne kadar ve nasıl girelim?</h4><p className="mxtLead">Teminat, bu işe ayırdığın para. Kaldıraç arttıkça kazanç da zarar da büyür.</p>
          {amountMobile}<div className="mxtDesk">
          <div className="cbAmt">
            <label className="cbAmtMain"><span>Teminat (USD)</span>
              <input className="cbAmtInput" aria-label="Teminat (USD)" value={ticket.marginUsd} onChange={event => patch({marginUsd: event.target.value})} inputMode="decimal" autoComplete="off" placeholder="0"/></label>
            {chips.length > 0 && <div className="cbChips" role="group" aria-label="Teminat kısayolları">{chips.map(chip => <button type="button" key={chip.label} aria-pressed={ticket.marginUsd.trim() === chip.value} onClick={() => patch({marginUsd: chip.value})}>{chip.label}</button>)}</div>}
            <small className="cbAmtNote">{Number.isFinite(marginAvail) ? `Kullanılabilir marjin ${usd(marginAvail)} USD` : 'Kullanılabilir marjin okunamadı; teminatı kendin yaz.'}{minO ? ` · En küçük emir 1 sözleşme ≈ ${usd(minO.notional)} USD (${levNum >= 1 ? levNum : 1}x ile ≈ ${usd(minO.margin)} USD teminat)` : ''}</small>
            <div className="cbLev" data-tone={band.tone}>
              <div className="cbLevHead"><span>Kaldıraç</span><b>{manualContracts === null ? `${Number.isFinite(levNum) ? levNum : '—'}x` : 'elle'}</b><em>{manualContracts === null ? band.label : 'sözleşme elle yazıldı'}</em></div>
              <input type="range" min="1" max="20" step="1" aria-label="Kaldıraç" disabled={manualContracts !== null} value={Number.isFinite(levNum) && levNum >= 1 ? Math.min(20, levNum) : 5} onChange={event => patch({leverage: event.target.value})}/>
              <div className="cbLevScale" aria-hidden="true"><span>1x</span><span>5x</span><span>10x</span><span>20x</span></div>
            </div>
            <div className="cbAmtPreview" role="status" data-ok={contractsNow !== null || undefined}>
              {contractsNow !== null && Number.isFinite(entryRef) && Number.isFinite(contractSize)
                ? <><b>≈ {contractsNow} sözleşme</b><span>pozisyon ≈ {usd(contractsNow * contractSize * entryRef)} USD{Number.isFinite(marginNum) && marginNum > 0 ? ` · gerçek kaldıraç ≈ ${(contractsNow * contractSize * entryRef / marginNum).toFixed(1)}x` : ''}</span></>
                : Number.isFinite(marginNum) && marginNum > 0 && Number.isFinite(entryRef) && Number.isFinite(contractSize)
                  ? <span>Bu teminat ve kaldıraçla 1 sözleşme bile alınamıyor. Teminatı ya da kaldıracı artır.</span>
                  : <span>Teminat yazınca sözleşme sayısı burada hesaplanır.</span>}
            </div>
          </div>
          <details className="cbAdv"><summary>Gelişmiş: sözleşme sayısı, limit giriş</summary>
            <input className="cbMxtInput" aria-label="Sözleşme sayısı (isteğe bağlı)" value={ticket.contracts} onChange={event => patch({contracts: event.target.value})} inputMode="numeric" autoComplete="off" placeholder="sözleşme sayısı (boşsa kaldıraçtan hesaplanır)"/>
            <input className="cbMxtInput" aria-label="Limit giriş fiyatı (isteğe bağlı)" value={ticket.limitPrice} onChange={event => patch({limitPrice: event.target.value})} inputMode="decimal" autoComplete="off" placeholder="limit giriş fiyatı (boşsa piyasa)"/>
            {status?.resting_enabled === true && <label className="mxtHint"><input type="checkbox" checked={ticket.rest} onChange={event => patch({rest: event.target.checked})}/> Bekleyen limit: emir dolana kadar Coinbase'de bekler (limit giriş ve hedef zorunlu; stop+hedef emre ekli gider ve ekran dolumu izler).</label>}
            <p className="mxtHint">Limit fiyatı yazarsan emir yalnızca bu fiyattan ya da daha iyisinden anında dolar; dolmazsa hiçbir işlem açılmaz. Bekleyen emir bırakılmaz, çünkü dolduğunda stop'suz kalabilirdi. Sözleşme sayısını elle yazarsan kaldıraç ona göre kendiliğinden belirlenir.</p>
          </details></div></>}
        {step === 3 && <><h4>Stop ve hedefleri belirle</h4><p className="mxtLead">Stop, zararı sınırlar; hedef kârı alır. Hazır önerilerden birine dokun, sonra değiştirebilirsin.</p>
          {stopMobile}<div className="mxtDesk">
          <div className="cbLv">
            <label className="cbLvField"><span>Stop fiyatı</span><input className="cbAmtInput small" aria-label="Stop fiyatı" value={ticket.stopPrice} onChange={event => setStop(event.target.value)} inputMode="decimal" autoComplete="off" placeholder="stop fiyatı"/></label>
            <div className="cbChips" role="group" aria-label="Akıllı stop önerileri"><em>Öneri</em><button type="button" aria-pressed={pickOn('kais')} disabled={!(entryRef > 0) || !anaKey} onClick={applyKais}>Kais analizi{kaisSuggest ? ` · ${kaisSuggest.direction}` : ''}</button><button type="button" aria-pressed={pickOn('vol')} disabled={!(entryRef > 0) || !anaKey || volBusy} onClick={() => void applyVol()}>{volBusy ? 'Hesaplanıyor…' : 'Dalgaya göre'}</button></div>
            {pickNote}
            <div className="cbChips" role="group" aria-label="Stop kısayolları"><em>Girişten</em>{ordersOn ? <SuggestionButtons suggestions={suggestions} ticket={ticket} onApply={applySuggestion} variant="chip"/> : STOP_PCTS.map(pct => <button type="button" key={pct} disabled={!Number.isFinite(entryRef)} onClick={() => pickStopPct(pct)}>%{pct}</button>)}</div>
            {ordersOn && <SuggestionNote suggestions={suggestions}/>}
            <label className="cbLvField"><span>Hedef fiyatı (isteğe bağlı)</span><input className="cbAmtInput small" aria-label="Hedef (TP) fiyatı (isteğe bağlı)" value={ticket.takeProfit} onChange={event => { setTpRR(null); patch({takeProfit: event.target.value}) }} inputMode="decimal" autoComplete="off" placeholder="hedef fiyatı"/></label>
            <div className="cbChips" role="group" aria-label="Hedef kısayolları"><em>Stop mesafesinin</em>{TARGET_RRS.map(rr => <button type="button" key={rr} aria-pressed={tpRR === rr} disabled={!Number.isFinite(stopNum)} onClick={() => pickTargetRR(rr)}>{rr}R</button>)}<button type="button" disabled={ticket.takeProfit.trim() === ''} onClick={() => { setTpRR(null); patch({takeProfit: ''}) }}>Hedefsiz</button></div>
          </div>
          {risk && risk.wrongSide && <p className="mxtNotice error" role="alert">Stop yanlış tarafta: {dir === 'LONG' ? 'LONG\'da girişin altında' : 'SHORT\'ta girişin üstünde'} olmalı.</p>}
          {risk && !risk.wrongSide && <div className="cbRisk" aria-label="Risk özeti">
            <div className="cbRiskRow" data-k="loss"><span>Stopta yaklaşık</span><b>−{usd(risk.lossUsd)} USD</b><small>%{risk.stopPct.toFixed(2)} uzakta{Number.isFinite(marginNum) && marginNum > 0 ? ` · teminatının %${(risk.lossUsd / marginNum * 100).toFixed(0)}'i` : ''}</small></div>
            {risk.gainUsd !== null && risk.rr !== null
              ? <div className="cbRiskRow" data-k="gain"><span>Hedefte yaklaşık</span><b>+{usd(risk.gainUsd)} USD</b><small>%{(risk.targetPct ?? 0).toFixed(2)} uzakta · kâr/zarar 1:{risk.rr.toFixed(1)}</small></div>
              : <div className="cbRiskRow" data-k="none"><span>Hedef yok</span><small>Yalnızca stop kurulur</small></div>}
            {risk.gainUsd !== null && risk.rr !== null && <div className="cbRiskBar" aria-hidden="true"><i data-k="loss" style={{flexGrow: Math.max(0.05, risk.lossUsd)}}/><i data-k="gain" style={{flexGrow: Math.max(0.05, risk.gainUsd)}}/></div>}
            {risk.rr !== null && risk.rr < 1 && <p className="cbRiskWarn">Hedef, stoptan yakın: kazanabileceğin, kaybedebileceğinden az.</p>}
            <p className="cbRiskNote">Komisyon dahil tahmindir; gerçek rakamı son adımdaki ön kontrol verir.</p>
          </div>}
          {!risk && <p className="mxtHint">Teminat, kaldıraç ve stop girilince risk özeti burada görünür.</p>}</div></>}
        {step === 4 && reviewStep}
        <div className="mxtNav">
          <button type="button" className="mxtBtn ghost" disabled={step === 0} onClick={() => goStep(Math.max(0, step - 1))}>← Geri</button>
          {step < STEP_NAMES.length - 1 && <button type="button" className="mxtBtn primary" disabled={!stepOk[step]} onClick={() => goStep(step + 1)}>Devam →</button>}
        </div>
        {ticket.productId.trim() && <p className="mxtM mxtMDraft"><i aria-hidden="true">●</i> Ürün, yön ve tutar bu ekranda tutulur. Stop güncel fiyata göre yeniden kontrol edilir.</p>}
      </div>
      {summaryAside}
    </div>
  </div>

  return <div className="cbTerminal">
    {notice && <div className="cbPanel"><CoinbaseManualNotice notice={notice}/></div>}
    {status && !keySaved && <div className="cbPanel"><p className="cbNote warn">Coinbase anahtarı yok. Önce <b>Borsa Bağlantıları</b> sayfasında Coinbase’i seçip anahtarı ekle.
      <button type="button" onClick={onGoSetup}>Borsa Bağlantıları’na git</button></p></div>}
    {keySaved && <div className="mxt cbHome">{statusStrip}
      <button type="button" className="cbOpenManual" data-testid="cb-open-manual" data-wait={waitFunds || undefined} onClick={() => setManualOpen(true)}>
        <span className="cbOpenIcon" aria-hidden="true"><Zap/></span>
        <span className="cbOpenText"><b>Manuel işlem aç</b><small>{waitFunds ? 'İnceleyebilirsin · göndermek için bakiye gerekir' : 'Ürün seç · Long/Short · Stop · TP'}</small></span>
        <span className="cbOpenGo" aria-hidden="true">→</span>
      </button></div>}
    {keySaved && manualOpen && <div className="lcRoot" data-manual-modal="">
      <div className="lcManualBackdrop" role="presentation" onClick={() => setManualOpen(false)}/>
      <button type="button" className="lcManualClose" aria-label="Manuel işlem panelini kapat" onClick={() => setManualOpen(false)}><X aria-hidden="true"/></button>
      <details open className="originalSurface originalManualOrder advRow" role="dialog" aria-modal="true" aria-label="Manuel işlem">
        <summary className="advHead" onClick={event => event.preventDefault()}>
          <span className="advHeadIcon amber"><CircleDollarSign aria-hidden="true"/></span>
          <span className="advHeadText"><small>MANUEL</small><b>Manuel işlem</b></span>
          <span className="advChip warn">GERÇEK PARA</span>
        </summary>
        <div className="liveWs cbWizWrap">
          <div className="liveWsSeg mgpSwitch" role="group" aria-label="Emir türü"><button type="button" aria-pressed={manualMode === 'single'} className={manualMode === 'single' ? 'on' : ''} onClick={() => setManualMode('single')}>Tek işlem</button><button type="button" aria-pressed={manualMode === 'grid'} className={manualMode === 'grid' ? 'on' : ''} onClick={() => setManualMode('grid')}>Grid <em>ÖNİZLEME</em></button></div>
          {manualMode === 'grid' ? gridBody : wizardBody}</div>
      </details></div>}
    {confirmModal}
    {keySaved && <div className="cbPanel" ref={positionsRef}>
      {ordersOn && <CoinbaseLimits openOrders={protection?.open_order_count ?? null} buyingPower={Number.isFinite(Number(account?.balance?.futures_buying_power?.value)) ? Number(account?.balance?.futures_buying_power?.value) : null} onChanged={() => { setLimitsRev(value => value + 1); void refresh(true) }}/>}
      {ordersOn && status?.resting_enabled === true && <CoinbaseManualPending onDone={afterAction} onError={afterError}/>}
      <CoinbaseManualPositions positions={livePositions} liveAt={liveStamp} liveSource={liveSource} protection={protection} ordersEnabled={ordersOn} onDone={afterAction} onError={afterError}/>
      <CoinbaseActivity refreshKey={activityKey} dailyPnl={account?.balance?.daily_realized_pnl?.value ?? null}/>
    </div>}
  </div>
}

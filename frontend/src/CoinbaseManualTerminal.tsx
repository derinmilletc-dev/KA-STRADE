// Coinbase işlem ekranı: Binance'in manuel emir sihirbazının AYNISI (aynı işaretleme, aynı bileşenler, aynı stil dosyaları).
// Binance ekranındaki ama Coinbase'de yapılamayan parçalar çıkarıldı: hedef (TP) emirleri (yalnız-azalt emri olmadığı için güvensiz),
// limit emir, risk limiti düzenleme, 24 saatlik izin/ARM. Ürün listesi, canlı fiyat ve Kais AI analizi VAR (analiz, ürünün dayanak varlığı üzerinden). Arka planda Coinbase mantığı çalışır: ön kontrol ("dry-run"),
// piyasa girişi, zorunlu stop, stop doğrulama. Binance dosyalarına dokunulmaz; yalnızca içe aktarılır.
import {useCallback, useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ChevronDown, RefreshCw, Search, Send, ShieldAlert, TriangleAlert, X, Zap} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {askKais} from '../../kais-ask'
import {fetchWithTimeout} from '../../master-trade-request'
import '../../live-ws.css'
import '../../manual-order.css'
import '../../coin-picker.css'
import '../../manual-mobile.css'
import {API_BASE} from './api'
import {AI_DISCLAIMER, STRONG_CONFIDENCE, alignment, buildSignalMap, filterBySignal, leanText, sortCoins, strengthLabel, type CoinFilter, type CoinSignal, type CoinSort, type SignalMap} from './coin-ranking'
import {changeText, expiryText, matchRows, priceText, signalsForRows, toRows, type CbProduct, type CbQuote} from './coinbase-market'
import {CONFIRM, emptyTicket, isPositiveDecimal, isProductId, newRequestId, ticketPayload, ticketProblems, type Ticket} from './coinbase-manual'
import {leverageBand, marginChips, priceString, riskPreview, sizeFromLeverage, stopFromPct, targetFromRR} from './coinbase-sizing'
import {sameConfirm} from './confirm-text'
import {CoinbaseApiError, coinbaseCall, postJson} from './coinbase-manual-api'
import CoinbaseAccountHero from './CoinbaseAccountHero'
import CoinbaseManualNotice, {noticeFromError, type Notice} from './CoinbaseManualNotice'
import CoinbaseManualPending from './CoinbaseManualPending'
import CoinbaseManualPositions, {type CbPosition, type CbProtection} from './CoinbaseManualPositions'
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
  const autoCheckRef = useRef(false)
  const [done, setDone] = useState<DoneInfo | null>(null)
  const [manualOpen, setManualOpen] = useState(false)
  const [products, setProducts] = useState<CbProduct[] | null>(null)
  const [productsError, setProductsError] = useState('')
  const [universe, setUniverse] = useState<SignalMap | null>(() => universeCache)
  const [signalsError, setSignalsError] = useState(false)
  const [coinOpen, setCoinOpen] = useState(false)
  const [coinQuery, setCoinQuery] = useState('')
  const [coinFilter, setCoinFilter] = useState<CoinFilter>('all')
  const [coinSort, setCoinSort] = useState<CoinSort>('confidence')
  const [quote, setQuote] = useState<CbQuote | null>(null)
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
  const afterAction = (text: string) => { setNotice({kind: 'ok', text}); void refresh(true) }
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
      setTicket(emptyTicket()); setCheck(null); setStep(0); setWizNotice({kind: 'info', text: ''})
    } catch (error) {
      setCheck(null)
      orderFailure(error)
    } finally { setConfirmText(''); setBusy(''); void refresh(true) }
  }

  const ordersOn = status?.orders_enabled === true
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

  useEffect(() => {
    setQuote(null); setQuoteError(false)
    if (!keySaved || !isProductId(selectedId)) return
    let alive = true
    const load = () => {
      void coinbaseCall<CbQuote>(`/quote/${encodeURIComponent(selectedId)}`)
        .then(next => { if (alive) { setQuote(next); setQuoteError(false) } })
        .catch(() => { if (alive) { setQuote(null); setQuoteError(true) } })
    }
    load()
    const timer = visibleInterval(load, 5000)
    return () => { alive = false; clearVisibleInterval(timer) }
  }, [keySaved, selectedId])

  const rows = products ? toRows(products) : []
  const signals = signalsForRows(rows, universe)
  const rankedRows = sortCoins(filterBySignal(rows, signals, coinFilter), signals, coinSort)
  const matches = matchRows(rankedRows, coinQuery)
  const listed = matches.slice(0, 80)
  const selectedRow = rows.find(row => row.symbol === selectedId)
  const selectedSignal = signals?.[selectedId]
  const shownPrice = quote ? Number(quote.mid) : selectedRow ? selectedRow.price : NaN
  const signalCount = (filter: CoinFilter) => signals ? filterBySignal(rows, signals, filter).length : 0
  const chooseCoin = (value: string) => { setTicket(current => ({...current, productId: value})); setCheck(null); setWizNotice({kind: 'info', text: ''}); setCoinOpen(false); setCoinQuery('') }
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

  const statusStrip = <CoinbaseAccountHero balance={account?.balance ?? null} futuresMissing={futuresMissing} ordersOn={ordersOn}
    clockSkew={status?.clock?.ok === false ? status.clock.skew_seconds : null} openCount={positions.length} loading={loading} onRefresh={() => void refresh()}/>

  const reviewStep = <>
    <h4>Son kontrol</h4>
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
        {step === 0 && <><h4>Hangi ürün?</h4><p className="mxtLead">Listeden seç ya da ürün kodunu yaz.</p>{picksBlock}{coinBlock}</>}
        {step === 1 && <><h4>Hangi yön?</h4><p className="mxtLead">LONG: fiyat yükselirse kazanırsın. SHORT: fiyat düşerse kazanırsın.</p>
          <DirectionCards value={dir} onChange={side => { const next = side === 'LONG' ? 'BUY' : 'SELL'; if (next !== ticket.side) { setTpRR(null); patch({side: next, stopPrice: '', takeProfit: ''}) } }}/>
          {aiCard}</>}
        {step === 2 && <><h4>Ne kadar?</h4><p className="mxtLead">Teminatını ve kaldıracını seç, sözleşme sayısını biz hesaplarız.</p>
          <div className="cbAmt">
            <label className="cbAmtMain"><span>Teminat (USD)</span>
              <input className="cbAmtInput" aria-label="Teminat (USD)" value={ticket.marginUsd} onChange={event => patch({marginUsd: event.target.value})} inputMode="decimal" autoComplete="off" placeholder="0"/></label>
            {chips.length > 0 && <div className="cbChips" role="group" aria-label="Teminat kısayolları">{chips.map(chip => <button type="button" key={chip.label} aria-pressed={ticket.marginUsd.trim() === chip.value} onClick={() => patch({marginUsd: chip.value})}>{chip.label}</button>)}</div>}
            <small className="cbAmtNote">{Number.isFinite(marginAvail) ? `Kullanılabilir marjin ${usd(marginAvail)} USD` : 'Kullanılabilir marjin okunamadı; teminatı kendin yaz.'}</small>
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
          </details></>}
        {step === 3 && <><h4>Stop ve hedef</h4><p className="mxtLead">Stop zorunludur; stop'suz işlem açılmaz. LONG'da girişin altına, SHORT'ta üstüne koy. Hedef isteğe bağlıdır.</p>
          <div className="cbLv">
            <label className="cbLvField"><span>Stop fiyatı</span><input className="cbAmtInput small" aria-label="Stop fiyatı" value={ticket.stopPrice} onChange={event => setStop(event.target.value)} inputMode="decimal" autoComplete="off" placeholder="stop fiyatı"/></label>
            <div className="cbChips" role="group" aria-label="Stop kısayolları"><em>Girişten</em>{STOP_PCTS.map(pct => <button type="button" key={pct} disabled={!Number.isFinite(entryRef)} onClick={() => pickStopPct(pct)}>%{pct}</button>)}</div>
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
          {!risk && <p className="mxtHint">Teminat, kaldıraç ve stop girilince risk özeti burada görünür.</p>}</>}
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
      <button type="button" className="cbOpenManual" data-testid="cb-open-manual" onClick={() => setManualOpen(true)}>
        <span className="cbOpenIcon" aria-hidden="true"><Zap/></span>
        <span className="cbOpenText"><b>Manuel işlem aç</b><small>Ürün seç, yönü belirle, stop ve hedefle gönder</small></span>
        <span className="cbOpenGo" aria-hidden="true">→</span>
      </button></div>}
    {keySaved && manualOpen && <><div className="cbModalBackdrop" role="presentation" onClick={() => setManualOpen(false)}/>
      <div className="cbModal" role="dialog" aria-modal="true" aria-label="Manuel işlem">
        <button type="button" className="cbModalClose" aria-label="Manuel işlem panelini kapat" onClick={() => setManualOpen(false)}><X aria-hidden="true"/></button>
        {wizardBody}
      </div></>}
    {confirmModal}
    {keySaved && <div className="cbPanel" ref={positionsRef}>
      {ordersOn && status?.resting_enabled === true && <CoinbaseManualPending onDone={afterAction} onError={afterError}/>}
      <CoinbaseManualPositions positions={positions} protection={protection} ordersEnabled={ordersOn} onDone={afterAction} onError={afterError}/>
    </div>}
  </div>
}

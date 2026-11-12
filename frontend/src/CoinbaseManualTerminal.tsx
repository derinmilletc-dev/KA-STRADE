// Coinbase işlem ekranı: Binance'in manuel emir sihirbazının AYNISI (aynı işaretleme, aynı bileşenler, aynı stil dosyaları).
// Binance ekranındaki ama Coinbase'de yapılamayan parçalar çıkarıldı: hedef (TP) emirleri (yalnız-azalt emri olmadığı için güvensiz),
// limit emir, risk limiti düzenleme, 24 saatlik izin/ARM. Ürün listesi, canlı fiyat ve Kais AI analizi VAR (analiz, ürünün dayanak varlığı üzerinden). Arka planda Coinbase mantığı çalışır: ön kontrol ("dry-run"),
// piyasa girişi, zorunlu stop, stop doğrulama. Binance dosyalarına dokunulmaz; yalnızca içe aktarılır.
import {useCallback, useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ChevronDown, RefreshCw, Search, Send, ShieldAlert, TriangleAlert} from 'lucide-react'
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
import {CONFIRM, confirmMatches, emptyTicket, isPositiveDecimal, isProductId, newRequestId, ticketPayload, ticketProblems, type Ticket} from './coinbase-manual'
import {CoinbaseApiError, coinbaseCall, postJson} from './coinbase-manual-api'
import CoinbaseManualNotice, {noticeFromError, type Notice} from './CoinbaseManualNotice'
import CoinbaseManualPositions, {type CbPosition, type CbProtection} from './CoinbaseManualPositions'
import {CheckList, DirectionCards, ElapsedSeconds, StatusPill, SummaryList, type SummaryItem} from './ManualTerminalParts'
import type {CheckRow} from './manual-order-state'
import './coinbase-manual.css'

type Status = {orders_enabled?: boolean; key_saved: boolean; clock?: {ok: boolean | null; skew_seconds: number | null}}
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

const FILTER_CHIPS: [CoinFilter, string][] = [['all', 'Tümü'], ['long', 'LONG ağır'], ['short', 'SHORT ağır'], ['strong', 'Güçlü sinyal']]
const SORT_CHIPS: [CoinSort, string][] = [['confidence', 'Güvene göre'], ['volume', 'Hacme göre'], ['change', 'Değişime göre']]
const PICK_LIMIT = 30

const STEP_NAMES = ['Ürün', 'Yön', 'Tutar', 'Stop', 'Kontrol']
const amount = (value: Amount | undefined) => value?.value == null ? null : `${value.value} ${value.currency}`

export default function CoinbaseManualTerminal({onGoSetup}: {onGoSetup: () => void}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
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
  const [done, setDone] = useState<DoneInfo | null>(null)
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
      if (!next.key_saved) { setAccount(null); setPositions([]); setProtection(null); return }
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
      if (accountResult.status === 'fulfilled') setAccount(accountResult.value)
      if (positionResult.status === 'fulfilled') setPositions(positionResult.value.positions)
      if (protectionResult.status === 'fulfilled') setProtection(protectionResult.value)
      const failed = results.slice(0, 3).find((result): result is PromiseRejectedResult => result.status === 'rejected')
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
      await postJson<{message: string}>('/orders/open', {...ticketPayload(ticket), confirm: confirmText, request_id: newRequestId()})
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

  const summaryRows: SummaryItem[] = [
    {label: 'Ürün', value: ticket.productId.trim() || null},
    {label: 'Yön', value: dir},
    {label: 'Teminat', value: ticket.marginUsd.trim() ? `${ticket.marginUsd.trim()} USD` : null},
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
  const sendBlock = !ordersOn ? 'Emir gönderme sunucuda kapalı (COINBASE_ORDERS_ENABLED).' : !reviewed ? 'Önce "Ön kontrol yap" ile sına; geçerse gönderebilirsin.' : ''

  const noticeEl = wizNotice.kind !== 'info' && wizNotice.text
    ? <p className={`mxtNotice ${wizNotice.kind}`} role={wizNotice.kind === 'error' ? 'alert' : 'status'}>{wizNotice.text}</p> : null

  const progressEl = busy ? <div className="mxtProgress" role="status" aria-live="polite" key={busy}>
    <RefreshCw className="spin" aria-hidden="true"/>
    <span><b>{busy === 'review' ? 'Ön kontrol yapılıyor…' : 'Emir Coinbase\'e gönderiliyor, stop doğrulanıyor…'}</b>
      <small>{busy === 'review' ? 'Gerçek emir gitmez; Coinbase kurallarına ve sınırlara göre sınanıyor.' : 'Ekranı kapatma, tekrar basma; sonuç gelince burada görünür.'} <ElapsedSeconds/></small></span>
  </div> : null

  const statusStrip = <div className="mxtStatus" aria-label="Bağlantı ve güvenlik durumu">
    <span className="mxtBrand">KAISTRADE MANUAL EXECUTION<small>Manuel emir · gerçek para</small></span>
    <StatusPill tone={keySaved ? 'ok' : 'bad'}>Coinbase {keySaved ? 'bağlı' : 'bağlı değil'}</StatusPill>
    <StatusPill tone={keySaved ? 'ok' : 'warn'}>Hesap / API {keySaved ? 'tanımlı' : 'eksik'}</StatusPill>
    <StatusPill tone={ordersOn ? 'ok' : 'warn'}>Güvenlik: {ordersOn ? 'emir kilidi açık' : 'emir kilitli'}</StatusPill>
    {status?.clock?.ok === false && <StatusPill tone="bad">Saat farkı {status.clock.skew_seconds} sn</StatusPill>}
    <StatusPill tone="warn">Gerçek para</StatusPill>
  </div>

  const reviewStep = <>
    <h4>Son kontrol</h4>
    <p className="mxtLead">Önce ön kontrolle sına (gerçek emir gitmez), sonra gönder. Gönderirken yazı onayı istenir.</p>
    {!ordersOn && <div className="mxtLockCard" role="status"><span><b>Emir şu an kilitli</b><small>{sendBlock}</small></span></div>}
    <div className="mxtReviewGrid"><SummaryList rows={summaryRows.slice(0, 5)}/><SummaryList rows={summaryRows.slice(5)}/></div>
    <CheckList rows={checkRows}/>
    {plan && <p className="mxtHint">Stop'ta kaybın en çok {String(plan.worst_case_loss_usd)} USD olur.</p>}
    {progressEl}
    <div className="mxtActions">
      <button type="button" className="mxtBtn ghost" onClick={() => void reviewOrder()} disabled={busy !== '' || !ordersOn}>{busy === 'review' ? <RefreshCw className="spin"/> : <Send/>} {busy === 'review' ? 'Kontrol ediliyor…' : 'Ön kontrol yap'}</button>
      <button type="button" className="mxtBtn danger mxtSendPop" data-sending={busy === 'send' || undefined} onClick={() => { setConfirmText(''); setConfirmOpen(true) }} disabled={busy !== '' || sendBlock !== ''} aria-busy={busy === 'send'}>
        {busy === 'send' ? 'Gönderiliyor…' : <><Send/> Gerçek emri gönder</>}
      </button>
    </div>
    {busy !== 'send' && noticeEl}
    {sendBlock && <p className="mxtHint">{sendBlock}</p>}
    <p className="mxtHint">Giriş piyasa emriyle yapılır. Stop kurulamaz veya doğrulanamazsa pozisyon hemen kapatılır. Bu sürümde hedef (TP) emri yoktur.</p>
  </>

  const summaryAside = <aside className="mxtSummary" data-open={summaryOpen} aria-label="Emir özeti">
    <button type="button" className="mxtSumToggle" aria-expanded={summaryOpen} onClick={() => setSummaryOpen(open => !open)}><span>Emir özeti · {ticket.productId.trim() || 'ürün seçilmedi'}</span><ChevronDown aria-hidden="true"/></button>
    <div className="mxtSumBody"><SummaryList rows={summaryRows}/></div>
  </aside>

  const prot = done ? protection?.positions.find(item => item.product_id === done.product) : undefined
  const hasPos = Boolean(prot)
  const hasStop = prot?.state === 'protected'
  const goPositions = () => { setDone(null); window.setTimeout(() => positionsRef.current?.scrollIntoView?.({block: 'start', behavior: 'smooth'}), 60) }
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
      <label>TYPE TO CONFIRM<input autoFocus value={confirmText} onChange={event => setConfirmText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && confirmMatches(confirmText, CONFIRM.open)) void sendOrder() }} placeholder={CONFIRM.open} autoComplete="off"/></label>
      <div className="liveConfirmBtns"><button type="button" className="liveConfirmNo" onClick={() => { setConfirmOpen(false); setConfirmText('') }}>CANCEL</button>
        <button type="button" className="liveConfirmYes" disabled={!confirmMatches(confirmText, CONFIRM.open)} onClick={() => void sendOrder()}>CONFIRM</button></div>
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
  const picksBlock = !coinOpen && <section className="cpkPicks" aria-label="Kais AI güçlü sinyaller">
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
        {duo(signal)}
        <span className="cpkLeanLine">{leanText(signal)}</span>
      </button></li>
    })}</ul>}
    <p className="cpkNote">{AI_DISCLAIMER}</p>
  </section>
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
        <strong>{row.display}{expiryText(row.expiry) ? ` · ${expiryText(row.expiry)}` : ''}</strong>{duo(signals?.[row.symbol])}<span className="cpkRowPrice">{priceText(row.price)}</span><em className={row.change >= 0 ? 'up' : 'down'}>{changeText(row.change)}</em></button></li>)}{listed.length === 0 && <li className="cpkEmpty">Sonuç yok.</li>}</ul>}
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
    {statusStrip}
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
          <DirectionCards value={dir} onChange={side => patch({side: side === 'LONG' ? 'BUY' : 'SELL'})}/>
          {aiCard}</>}
        {step === 2 && <><h4>Ne kadar?</h4><p className="mxtLead">Kullanacağın teminatı yaz. Sözleşme sayısını boş bırakırsan 5 USD kayıp ve 3–5x kaldıraç kurallarına uyan en büyük miktar seçilir.</p>
          <input className="cbMxtInput" aria-label="Teminat (USD)" value={ticket.marginUsd} onChange={event => patch({marginUsd: event.target.value})} inputMode="decimal" autoComplete="off" placeholder="teminat (USD)"/>
          <input className="cbMxtInput" aria-label="Sözleşme sayısı (isteğe bağlı)" value={ticket.contracts} onChange={event => patch({contracts: event.target.value})} inputMode="numeric" autoComplete="off" placeholder="sözleşme sayısı (isteğe bağlı)"/></>}
        {step === 3 && <><h4>Stop</h4><p className="mxtLead">Stop zorunludur; stop'suz işlem açılmaz. LONG'da giriş fiyatının altına, SHORT'ta üstüne koy.</p>
          <input className="cbMxtInput" aria-label="Stop fiyatı" value={ticket.stopPrice} onChange={event => patch({stopPrice: event.target.value})} inputMode="decimal" autoComplete="off" placeholder="stop fiyatı"/></>}
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

  const accountRows: SummaryItem[] = account?.balance ? [
    {label: 'Alım gücü', value: amount(account.balance.futures_buying_power)},
    {label: 'Kullanılabilir marjin', value: amount(account.balance.available_margin)},
    {label: 'Gerçekleşmemiş K/Z', value: amount(account.balance.unrealized_pnl)},
    {label: 'Bugünkü gerçekleşen K/Z', value: amount(account.balance.daily_realized_pnl)},
    {label: 'Marjin seviyesi (kötü pencere)', value: account.balance.worst_margin_level ? `${account.balance.worst_margin_level}${account.balance.worst_margin_window ? ` · ${account.balance.worst_margin_window === 'overnight' ? 'gece' : 'gün içi'}` : ''}` : null},
  ] : []

  return <div className="cbTerminal">
    {(notice || loading) && <div className="cbPanel">
      <div className="cbTop"><span/><button onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''}/>Yenile</button></div>
      {notice && <CoinbaseManualNotice notice={notice}/>}
    </div>}
    {status && !keySaved && <div className="cbPanel"><p className="cbNote warn">Coinbase anahtarı yok. Önce <b>Borsa Bağlantıları</b> sayfasında Coinbase’i seçip anahtarı ekle.
      <button type="button" onClick={onGoSetup}>Borsa Bağlantıları’na git</button></p></div>}
    {keySaved && wizardBody}
    {confirmModal}
    {keySaved && <div className="cbPanel" ref={positionsRef}>
      {accountRows.length > 0 && <section className="cbCard" aria-label="Hesap"><h3>Vadeli hesap</h3><SummaryList rows={accountRows}/></section>}
      <CoinbaseManualPositions positions={positions} protection={protection} ordersEnabled={ordersOn} onDone={afterAction} onError={afterError}/>
    </div>}
  </div>
}

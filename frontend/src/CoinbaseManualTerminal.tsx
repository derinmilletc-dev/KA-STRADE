// Coinbase işlem ekranı: Binance'in manuel emir sihirbazının AYNISI (aynı işaretleme, aynı bileşenler, aynı stil dosyaları).
// Binance ekranındaki ama Coinbase'de yapılamayan parçalar çıkarıldı: coin listesi/canlı fiyat, Kais analizi, hedef (TP) emirleri,
// limit emir, risk limiti düzenleme, 24 saatlik izin/ARM. Arka planda Coinbase mantığı çalışır: ön kontrol ("dry-run"),
// piyasa girişi, zorunlu stop, stop doğrulama. Binance dosyalarına dokunulmaz; yalnızca içe aktarılır.
import {useCallback, useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {ChevronDown, RefreshCw, Send, ShieldAlert} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {askKais} from '../../kais-ask'
import '../../live-ws.css'
import '../../manual-order.css'
import '../../coin-picker.css'
import '../../manual-mobile.css'
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
      ])
      const [accountResult, positionResult, protectionResult] = results
      if (accountResult.status === 'fulfilled') setAccount(accountResult.value)
      if (positionResult.status === 'fulfilled') setPositions(positionResult.value.positions)
      if (protectionResult.status === 'fulfilled') setProtection(protectionResult.value)
      const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
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
  const stepOk = [
    isProductId(ticket.productId),
    true,
    isPositiveDecimal(ticket.marginUsd) && (ticket.contracts.trim() === '' || /^[1-9]\d{0,5}$/.test(ticket.contracts.trim())),
    isPositiveDecimal(ticket.stopPrice),
    true,
  ]
  const plan = check?.ok ? check.plan : null
  const dir: 'LONG' | 'SHORT' = ticket.side === 'BUY' ? 'LONG' : 'SHORT'

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

  const wizardBody = <div className="mxt" data-step={step} data-done={done ? 'yes' : undefined}>
    {doneView}
    {statusStrip}
    <div className="mxtM mxtMHead">
      <div className="mxtMLabel"><span>ADIM {step + 1} / {STEP_NAMES.length} · <em>{STEP_NAMES[step]}</em></span><span className="mxtMTools"><button type="button" className="mxtMAsk" onClick={() => askKais(askText)}>✦ Kais’e sor</button></span></div>
    </div>
    <ol className="mxtSteps" aria-label="Manuel emir adımları">{STEP_NAMES.map((name, index) => <li key={name} data-state={index === step ? 'current' : index < step ? 'done' : 'todo'}>
      <button type="button" disabled={index > step && !stepOk.slice(0, index).every(Boolean)} aria-current={index === step ? 'step' : undefined} onClick={() => goStep(index)}><i>{index < step ? '✓' : index + 1}</i><span>{name}</span></button></li>)}</ol>
    <div className="mxtLayout">
      <div className="mxtCard" key={step}>
        <span className="mxtKicker">Adım {step + 1} / {STEP_NAMES.length}</span>
        {step !== 4 && noticeEl}
        {step === 0 && <><h4>Hangi ürün?</h4><p className="mxtLead">Coinbase vadeli ürün kodunu yaz.</p>
          <input className="cbMxtInput" aria-label="Ürün kodu" value={ticket.productId} onChange={event => patch({productId: event.target.value})} autoComplete="off" placeholder="ürün kodu"/></>}
        {step === 1 && <><h4>Hangi yön?</h4><p className="mxtLead">LONG: fiyat yükselirse kazanırsın. SHORT: fiyat düşerse kazanırsın.</p>
          <DirectionCards value={dir} onChange={side => patch({side: side === 'LONG' ? 'BUY' : 'SELL'})}/></>}
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

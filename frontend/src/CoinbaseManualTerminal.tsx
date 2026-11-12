// Coinbase işlem ekranı: Binance'in manuel emir sihirbazıyla AYNI görünüm (aynı bileşenler ve stiller),
// arka planda Coinbase mantığı çalışır: ön kontrol ("dry-run"), piyasa girişi, zorunlu stop ve stop doğrulama.
import {useCallback, useEffect, useRef, useState} from 'react'
import {RefreshCw, Send} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import '../../manual-order.css'
import '../../manual-mobile.css'
import {CONFIRM, confirmMatches, emptyTicket, isPositiveDecimal, isProductId, newRequestId, ticketPayload, ticketProblems, type Ticket} from './coinbase-manual'
import {CoinbaseApiError, coinbaseCall, postJson} from './coinbase-manual-api'
import CoinbaseManualNotice, {noticeFromError, type Notice} from './CoinbaseManualNotice'
import CoinbaseManualPositions, {type CbPosition, type CbProtection} from './CoinbaseManualPositions'
import {CheckList, DirectionCards, StatusPill, SummaryList, type SummaryItem} from './ManualTerminalParts'
import type {CheckRow} from './manual-order-state'
import './coinbase-manual.css'

type Status = {orders_enabled?: boolean; key_saved: boolean; clock?: {ok: boolean | null; skew_seconds: number | null}}
type Amount = {value: string | null; currency: string} | null
type Account = {balance: {futures_buying_power: Amount; available_margin: Amount; unrealized_pnl: Amount; daily_realized_pnl: Amount; worst_margin_level: string | null; worst_margin_window: string | null; liquidation_buffer_percentage: string | null}}
type Plan = Record<string, string | number>
type Preflight = {ok: boolean; reasons: string[]; plan: Plan | null}

const STEPS = ['Ürün', 'Yön', 'Tutar', 'Stop', 'Kontrol']
const amount = (value: Amount | undefined) => value?.value == null ? null : `${value.value} ${value.currency}`

export default function CoinbaseManualTerminal({onGoSetup}: {onGoSetup: () => void}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [positions, setPositions] = useState<CbPosition[]>([])
  const [protection, setProtection] = useState<CbProtection | null>(null)
  const [notice, setNotice] = useState<Notice>({kind: 'warn', text: 'Coinbase durumu kontrol ediliyor…'})
  const [loading, setLoading] = useState(false)
  const [step, setStep] = useState(0)
  const [ticket, setTicket] = useState<Ticket>(emptyTicket())
  const [check, setCheck] = useState<Preflight | null>(null)
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState<'' | 'review' | 'send'>('')
  const [done, setDone] = useState<string | null>(null)
  const loadingRef = useRef(false)

  const refresh = useCallback(async (quiet = false) => {
    if (loadingRef.current) return
    loadingRef.current = true
    setLoading(true)
    try {
      const next = await coinbaseCall<Status>('/status')
      setStatus(next)
      if (!next.key_saved) { setAccount(null); setPositions([]); setProtection(null); if (!quiet) setNotice({kind: 'warn', text: 'Coinbase anahtarı kayıtlı değil.'}); return }
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

  const patch = (change: Partial<Ticket>) => { setTicket(current => ({...current, ...change})); setCheck(null); setConfirm('') }
  const afterAction = (text: string) => { setNotice({kind: 'ok', text}); void refresh(true) }
  const afterError = (error: CoinbaseApiError) => { setNotice(noticeFromError(error)); void refresh(true) }

  const reviewOrder = async () => {
    const problems = ticketProblems(ticket)
    if (problems.length) { setCheck({ok: false, reasons: problems, plan: null}); return }
    setBusy('review')
    try { setCheck(await postJson<Preflight>('/orders/preflight', ticketPayload(ticket))) }
    catch (error) { setCheck(null); setNotice(noticeFromError(error)) }
    finally { setBusy('') }
  }

  const sendOrder = async () => {
    setBusy('send')
    try {
      const result = await postJson<{message: string}>('/orders/open', {...ticketPayload(ticket), confirm, request_id: newRequestId()})
      setDone(result.message || 'Pozisyon açıldı ve stop doğrulandı.')
      setNotice({kind: 'ok', text: result.message || 'Pozisyon açıldı ve stop doğrulandı.'})
      setTicket(emptyTicket()); setCheck(null); setConfirm(''); setStep(0)
    } catch (error) {
      setCheck(null); setConfirm('')
      setNotice(noticeFromError(error))
    } finally { setBusy(''); void refresh(true) }
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
  const canSend = busy === '' && check?.ok === true && confirmMatches(confirm, CONFIRM.open)

  const statusStrip = <div className="mxtStatus" aria-label="Bağlantı ve güvenlik durumu">
    <span className="mxtBrand">KAISTRADE MANUAL EXECUTION<small>Coinbase · ABD vadeli · gerçek para</small></span>
    <StatusPill tone={keySaved ? 'ok' : 'bad'}>Coinbase {keySaved ? 'bağlı' : 'bağlı değil'}</StatusPill>
    <StatusPill tone={ordersOn ? 'ok' : 'warn'}>Güvenlik: {ordersOn ? 'emir kilidi açık' : 'emir kilitli (salt okunur)'}</StatusPill>
    {status?.clock?.ok === false && <StatusPill tone="bad">Saat farkı {status.clock.skew_seconds} sn</StatusPill>}
    <StatusPill tone="warn">Gerçek para</StatusPill>
  </div>

  const bal = account?.balance
  const accountRows: SummaryItem[] = bal ? [
    {label: 'Alım gücü', value: amount(bal.futures_buying_power)},
    {label: 'Kullanılabilir marjin', value: amount(bal.available_margin)},
    {label: 'Gerçekleşmemiş K/Z', value: amount(bal.unrealized_pnl)},
    {label: 'Bugünkü gerçekleşen K/Z', value: amount(bal.daily_realized_pnl)},
    {label: 'Marjin seviyesi (kötü pencere)', value: bal.worst_margin_level ? `${bal.worst_margin_level}${bal.worst_margin_window ? ` · ${bal.worst_margin_window === 'overnight' ? 'gece' : 'gün içi'}` : ''}` : null},
  ] : []

  const reviewStep = <>
    <h4>Son kontrol</h4>
    <p className="mxtLead">Önce ön kontrolle sına (gerçek emir gitmez), sonra gönder. Gönderirken yazı onayı istenir.</p>
    <div className="mxtReviewGrid"><SummaryList rows={summaryRows}/></div>
    <CheckList rows={checkRows}/>
    <p className="mxtHint">Stop kurulamaz veya doğrulanamazsa pozisyon hemen kapatılır. Bu sürümde hedef (TP) emri yoktur.</p>
    {check?.ok && <label className="cbConfirm">Onay metni<input value={confirm} onChange={event => setConfirm(event.target.value)} placeholder={`Şunu yaz: ${CONFIRM.open}`} autoComplete="off"/></label>}
    <div className="mxtActions">
      <button type="button" className="mxtBtn ghost" onClick={() => void reviewOrder()} disabled={busy !== '' || !ordersOn}>{busy === 'review' ? <RefreshCw className="spin"/> : <Send/>} {busy === 'review' ? 'Kontrol ediliyor…' : 'Ön kontrol yap'}</button>
      <button type="button" className="mxtBtn danger" onClick={() => void sendOrder()} disabled={!canSend} aria-busy={busy === 'send'}>{busy === 'send' ? 'Gönderiliyor, bekle…' : <><Send/> Gerçek emri gönder</>}</button>
    </div>
    {busy === 'send' && <p className="mxtHint" role="status">Emir Coinbase'e gönderiliyor ve stop doğrulanıyor. Ekranı kapatma, tekrar basma.</p>}
  </>

  const body = <div className="mxt" data-step={step} data-done={done ? 'yes' : undefined}>
    {done && <div className="mxtDone" role="status"><span className="mxtDoneIco" aria-hidden="true">✓</span><h4>Emir gönderildi</h4><p>{done}</p>
      <div className="mxtActions"><button type="button" className="mxtBtn primary" onClick={() => setDone(null)}>Yeni emir</button></div></div>}
    {statusStrip}
    {!done && <>
      <div className="mxtM mxtMHead"><div className="mxtMLabel"><span>ADIM {step + 1} / {STEPS.length} · <em>{STEPS[step]}</em></span></div></div>
      <ol className="mxtSteps" aria-label="Coinbase emir adımları">{STEPS.map((name, index) => <li key={name} data-state={index === step ? 'current' : index < step ? 'done' : 'todo'}>
        <button type="button" disabled={index > step && !stepOk.slice(0, index).every(Boolean)} aria-current={index === step ? 'step' : undefined} onClick={() => setStep(index)}><i>{index < step ? '✓' : index + 1}</i><span>{name}</span></button></li>)}</ol>
      <div className="mxtLayout">
        <div className="mxtCard" key={step}>
          <span className="mxtKicker">Adım {step + 1} / {STEPS.length}</span>
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
            <button type="button" className="mxtBtn ghost" disabled={step === 0} onClick={() => setStep(Math.max(0, step - 1))}>← Geri</button>
            {step < STEPS.length - 1 && <button type="button" className="mxtBtn primary" disabled={!stepOk[step]} onClick={() => setStep(step + 1)}>Devam →</button>}
          </div>
        </div>
        <aside className="mxtSummary" data-open="true" aria-label="Emir özeti"><div className="mxtSumBody"><SummaryList rows={summaryRows}/></div></aside>
      </div>
    </>}
  </div>

  return <div className="cbTerminal">
    <div className="cbPanel">
      <div className="cbTop"><span/><button onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''}/>Yenile</button></div>
      <CoinbaseManualNotice notice={notice}/>
    </div>
    {status && !keySaved && <div className="cbPanel"><p className="cbNote warn">Coinbase anahtarı yok. Önce <b>Borsa Bağlantıları</b> sayfasında Coinbase'i seçip anahtarı ekle.
      <button type="button" onClick={onGoSetup}>Borsa Bağlantıları'na git</button></p></div>}
    {keySaved && ordersOn && body}
    {keySaved && !ordersOn && <div className="cbPanel"><p className="cbNote warn">Emir gönderme sunucuda kapalı; bu ekran yalnızca izleme yapar. Emir vermek için sunucuda <b>COINBASE_ORDERS_ENABLED</b> açılmalı.</p></div>}
    {keySaved && <div className="cbPanel">
      {accountRows.length > 0 && <section className="cbCard" aria-label="Hesap"><h3>Vadeli hesap</h3><SummaryList rows={accountRows}/></section>}
      <CoinbaseManualPositions positions={positions} protection={protection} ordersEnabled={ordersOn} onDone={afterAction} onError={afterError}/>
    </div>}
  </div>
}

import {useEffect, useRef, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {API_BASE, userSessionToken} from './api'
import {PAID_PLAN_AMOUNT, PAID_PLAN_NAME, type PaidPlan} from './smart-trade-plans'

type Current = {status:string; plan:string}
const isRecord = (value:unknown):value is Record<string,unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

// Ödeme sayfası yalnız nowpayments.io alan adlarına açılır; sunucu yanıtı bozulsa bile başka adrese gidilmez.
function safeInvoiceUrl(value:unknown):string|null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    return url.protocol === 'https:' && (host === 'nowpayments.io' || host.endsWith('.nowpayments.io')) ? url.toString() : null
  } catch {return null}
}

async function request(path:string, options:RequestInit = {}):Promise<unknown> {
  const headers = new Headers(options.headers)
  const token = userSessionToken()
  if (token) headers.set('Authorization',`Bearer ${token}`)
  if (options.body) headers.set('Content-Type','application/json')
  const response = await fetch(`${API_BASE}/v22/payments/nowpayments${path}`,{...options,headers,cache:'no-store'})
  const value:unknown = await response.json().catch(() => null)
  if (!response.ok) throw new Error(isRecord(value) && typeof value.detail === 'string' ? value.detail : 'Kripto ödeme işlemi tamamlanamadı.')
  return value
}

export default function NowPaymentsPay({plan = 'MASTER_MODE'}: {plan?: PaidPlan} = {}) {
  const [enabled,setEnabled] = useState(false)
  const [current,setCurrent] = useState<Current|null>(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const inFlight = useRef(false)

  const load = async (signal?:AbortSignal) => {
    const config = await request('/config',{signal})
    if (!isRecord(config) || config.enabled !== true) {setEnabled(false);return}
    setEnabled(true)
    const value = await request('/current',{signal})
    const payment = isRecord(value) && isRecord(value.payment) ? value.payment : null
    if (payment && typeof payment.status === 'string' && typeof payment.plan === 'string') {
      setCurrent({status:payment.status,plan:payment.plan})
      if (payment.status === 'finished') window.dispatchEvent(new Event('protrebot-access-refresh'))
    } else setCurrent(null)
  }
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal).catch(() => {if (!controller.signal.aborted) setEnabled(false)})
    return () => controller.abort()
  },[])

  const pay = async () => {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true);setError('');setNotice('')
    try {
      const value = await request('/invoices',{method:'POST',body:JSON.stringify({plan})})
      const url = isRecord(value) ? safeInvoiceUrl(value.invoice_url) : null
      if (!url) throw new Error('Kripto ödeme sayfası doğrulanamadı; biraz sonra tekrar deneyin.')
      window.location.assign(url)
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : 'Kripto ödeme işlemi tamamlanamadı.')
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const refresh = async () => {
    setBusy(true);setError('');setNotice('')
    try {await load();setNotice('Durum güncellendi.')}
    catch (exception) {setError(exception instanceof Error ? exception.message : 'Durum alınamadı.')}
    finally {setBusy(false)}
  }

  if (!enabled) return null
  const waiting = current && ['waiting','confirming','confirmed','sending'].includes(current.status)
  return <section className="binanceManualPayment" aria-label="Kripto ile ödeme">
    <header><div><small>OTOMATİK ÖDEME</small><h3>Kripto ile öde</h3></div><span>Otomatik onay</span></header>
    <p>30 günlük {PAID_PLAN_NAME[plan]} · {PAID_PLAN_AMOUNT[plan]} USD karşılığı kripto. Ödeme ağda onaylanınca üyeliğin kendiliğinden açılır; işlem numarası girmen gerekmez. Otomatik yenileme yoktur.</p>
    {error && <div className="binanceManualError" role="alert">{error}</div>}
    {current?.status === 'finished' && <div role="status"><strong>Ödeme alındı</strong><p>Üyeliğin açıldı. Görünmüyorsa “Durumu yenile”ye bas.</p></div>}
    {waiting && <div role="status"><strong>Ödeme bekleniyor</strong><p>Ödemeyi yaptıysan ağ onayı birkaç dakika sürebilir. Sayfayı yenileyip durumu kontrol et.</p></div>}
    {current?.status === 'partially_paid' && <div className="binanceManualError" role="alert">Eksik tutar gönderilmiş görünüyor. Destek bölümünden bize yaz.</div>}
    <button type="button" className="binanceManualPrimary" disabled={busy} onClick={() => void pay()}>{busy ? 'Hazırlanıyor…' : 'Kripto ile öde'}</button>
    <button type="button" className="binanceManualRefresh" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14}/> Durumu yenile</button>
    {notice && <p role="status">{notice}</p>}
  </section>
}

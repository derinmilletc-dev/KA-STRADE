import {useEffect, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {API_BASE, userSessionToken} from './api'
import {PAID_PLAN_NAME, type PaidPlan} from './smart-trade-plans'
import './admin-nowpayments.css'

type Item = {id:string; plan:string; amount:string; status:string; pay_currency:string|null; note:string|null; email:string|null;
  created_at:string; completed_at:string|null; period_end:string|null}
type Page = {items:Item[]; total:number}

const isRecord = (value:unknown):value is Record<string,unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const text = (value:unknown):value is string => typeof value === 'string'
const nullableText = (value:unknown):value is string|null => value === null || typeof value === 'string'

const STATUS: Record<string,{label:string; tone:'ok'|'wait'|'bad'}> = {
  finished:{label:'Ödendi · üyelik açıldı',tone:'ok'},
  created:{label:'Ödeme sayfası açıldı',tone:'wait'}, waiting:{label:'Ödeme bekleniyor',tone:'wait'},
  confirming:{label:'Ağda onaylanıyor',tone:'wait'}, confirmed:{label:'Ağda onaylandı',tone:'wait'}, sending:{label:'İşleniyor',tone:'wait'},
  partially_paid:{label:'Eksik tutar',tone:'bad'}, mismatch:{label:'İncelenmeli',tone:'bad'},
  failed:{label:'Başarısız',tone:'bad'}, expired:{label:'Süresi doldu',tone:'bad'}, refunded:{label:'İade edildi',tone:'bad'},
}
const date = (value:string|null) => value ? new Date(value).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—'

function parsePage(value:unknown):Page {
  if (!isRecord(value) || !Array.isArray(value.items) || typeof value.total !== 'number') throw new Error('Kripto ödeme listesi doğrulanamadı.')
  const items = value.items.map((row:unknown):Item => {
    if (!isRecord(row) || !text(row.id) || !text(row.plan) || !text(row.amount) || !text(row.status) || !nullableText(row.pay_currency)
      || !nullableText(row.note) || !nullableText(row.email) || !text(row.created_at) || !nullableText(row.completed_at) || !nullableText(row.period_end)) {
      throw new Error('Kripto ödeme kaydı doğrulanamadı.')
    }
    return {id:row.id,plan:row.plan,amount:row.amount,status:row.status,pay_currency:row.pay_currency,note:row.note,email:row.email,
      created_at:row.created_at,completed_at:row.completed_at,period_end:row.period_end}
  })
  return {items,total:value.total}
}

export default function AdminNowPayments() {
  const [page,setPage] = useState<Page|null>(null)
  const [error,setError] = useState('')
  const [busy,setBusy] = useState(false)
  const [attempt,setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setBusy(true)
    void (async () => {
      try {
        const token = userSessionToken()
        const response = await fetch(`${API_BASE}/v22/admin/nowpayments?limit=25`,{headers:token ? {Authorization:`Bearer ${token}`} : {},cache:'no-store',signal:controller.signal})
        const value:unknown = await response.json().catch(() => null)
        if (!response.ok) throw new Error(isRecord(value) && text(value.detail) ? value.detail : 'Kripto ödeme listesi alınamadı.')
        setPage(parsePage(value));setError('')
      } catch (exception) {
        if (!controller.signal.aborted) setError(exception instanceof Error ? exception.message : 'Kripto ödeme listesi alınamadı.')
      } finally {if (!controller.signal.aborted) setBusy(false)}
    })()
    return () => controller.abort()
  },[attempt])

  const paid = page?.items.filter(item => item.status === 'finished').length ?? 0
  const attention = page?.items.filter(item => STATUS[item.status]?.tone === 'bad' && item.status !== 'expired' && item.status !== 'failed').length ?? 0
  return <section className="adminCrypto" aria-label="Kripto ödemeler">
    <header>
      <div><small>OTOMATİK ONAYLI</small><h2>Kripto ödemeler</h2><p>Ödeme ağda tamamlanınca üyelik kendiliğinden açılır; burada yalnız izlersin.</p></div>
      <button type="button" onClick={() => setAttempt(value => value + 1)} disabled={busy}><RefreshCw size={14}/> Yenile</button>
    </header>
    {error && <div className="adminCryptoError" role="alert">{error}</div>}
    {page && <>
      <div className="adminCryptoStats">
        <div><span>Son kayıtlar</span><strong>{page.items.length}</strong></div>
        <div><span>Ödenen</span><strong>{paid}</strong></div>
        <div><span>İncelenmesi gereken</span><strong>{attention}</strong></div>
      </div>
      {page.items.length === 0 ? <p>Henüz kripto ödeme kaydı yok.</p> : <div className="adminCryptoList">
        {page.items.map(item => {
          const status = STATUS[item.status] ?? {label:item.status,tone:'wait' as const}
          return <article key={item.id}>
            <div><strong>{item.email ?? 'Bilinmeyen hesap'}</strong><span>{PAID_PLAN_NAME[item.plan as PaidPlan] ?? item.plan} · {item.amount} USD{item.pay_currency ? ` · ${item.pay_currency.toUpperCase()}` : ''}</span></div>
            <div><em className={`adminCryptoStatus is-${status.tone}`}>{status.label}</em><span>{date(item.completed_at ?? item.created_at)}</span></div>
            {item.period_end && <small>Üyelik bitişi: {date(item.period_end)}</small>}
            {item.note && <small className="adminCryptoNote">{item.note}</small>}
          </article>
        })}
      </div>}
    </>}
  </section>
}

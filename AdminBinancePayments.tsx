import {useEffect, useRef, useState} from 'react'
import {Copy, RefreshCw, Search} from 'lucide-react'
import {API_BASE, userSessionToken} from './api'
import {withRequestDeadline} from './browser-request'
import {isRecord, parseBinancePayment, type BinancePayment} from './binance-payment-model'
import {subscriptionLabels} from './moderator-model'
import './admin-binance-payments.css'

type Status = Exclude<BinancePayment['status'], 'created'>
type Customer = {role:string|null; active:boolean; email_verified:boolean; mfa_enabled:boolean; registered_at:string|null;
  plan:string|null; subscription_status:string|null; plan_period_end:string|null; membership_end:string|null;
  approved_count:number; rejected_count:number; expired_count:number}
type Recommendation = {at:string; by:string|null; note:string|null}
type Item = BinancePayment & {user_id:string; email:string|null; display_name:string|null; created_at:string; decision_note:string|null; customer:Customer|null; recommendation:Recommendation|null}
type PaymentPage = {items:Item[]; total:number; limit:number; offset:number; summary:{pending:number; approved_today:number; rejected:number}}
type DisabledPayments = {enabled:false; message:string}
function parseDisabled(value:unknown):DisabledPayments|null {
  if (!isRecord(value) || value.enabled !== false) return null
  if (typeof value.message !== 'string' || !value.message.trim()) throw new Error('Binance ödeme durumu doğrulanamadı.')
  return {enabled:false,message:value.message}
}
function parseDecision(value:unknown):BinancePayment|DisabledPayments {
  return parseDisabled(value) ?? parseBinancePayment(value)
}
const tabs: {status:Status; label:string}[] = [
  {status:'pending',label:'Bekleyen'}, {status:'approved',label:'Onaylanan'},
  {status:'rejected',label:'Reddedilen'}, {status:'expired',label:'Süresi dolan'},
]
const count = (value:unknown):value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const nullableDate = (value:unknown):value is string|null => value === null || (typeof value === 'string' && Number.isFinite(Date.parse(value)))
const nullableText = (value:unknown):value is string|null => value === null || typeof value === 'string'
function parseRecommendation(value:unknown):Recommendation|null {
  if (value === undefined || value === null) return null
  if (!isRecord(value) || typeof value.at !== 'string' || !Number.isFinite(Date.parse(value.at)) || !nullableText(value.by) || !nullableText(value.note)) throw new Error('Moderatör önerisi doğrulanamadı.')
  return {at:value.at,by:value.by,note:value.note}
}
function parseCustomer(value:unknown):Customer|null {
  if (value === undefined) return null
  if (!isRecord(value) || !nullableText(value.role) || typeof value.active !== 'boolean' || typeof value.email_verified !== 'boolean'
    || typeof value.mfa_enabled !== 'boolean' || !nullableDate(value.registered_at) || !nullableText(value.plan)
    || !nullableText(value.subscription_status) || !nullableDate(value.plan_period_end) || !nullableDate(value.membership_end)
    || !count(value.approved_count) || !count(value.rejected_count) || !count(value.expired_count)) throw new Error('Müşteri bilgisi doğrulanamadı.')
  return {role:value.role,active:value.active,email_verified:value.email_verified,mfa_enabled:value.mfa_enabled,registered_at:value.registered_at,
    plan:value.plan,subscription_status:value.subscription_status,plan_period_end:value.plan_period_end,membership_end:value.membership_end,
    approved_count:value.approved_count,rejected_count:value.rejected_count,expired_count:value.expired_count}
}
function parsePage(value:unknown):PaymentPage|DisabledPayments {
  const disabled = parseDisabled(value)
  if (disabled) return disabled
  if (!isRecord(value) || !Array.isArray(value.items) || !count(value.total) || !count(value.limit)
    || value.limit === 0 || !count(value.offset) || !isRecord(value.summary)
    || !count(value.summary.pending) || !count(value.summary.approved_today) || !count(value.summary.rejected)) {
    throw new Error('Binance ödeme listesi doğrulanamadı.')
  }
  const items = value.items.map((row:unknown):Item => {
    const payment = parseBinancePayment(row)
    if (!isRecord(row) || typeof row.user_id !== 'string'
      || !(row.email === null || typeof row.email === 'string') || !(row.display_name === null || typeof row.display_name === 'string')
      || typeof row.created_at !== 'string' || !Number.isFinite(Date.parse(row.created_at))
      || !(row.decision_note === null || typeof row.decision_note === 'string')
      || payment.amount !== '119.90') throw new Error('Binance ödeme kartı doğrulanamadı.')
    return {...payment,user_id:row.user_id,email:row.email,display_name:row.display_name,created_at:row.created_at,decision_note:row.decision_note,customer:parseCustomer(row.customer),recommendation:parseRecommendation(row.recommendation)}
  })
  return {items,total:value.total,limit:value.limit,offset:value.offset,
    summary:{pending:value.summary.pending,approved_today:value.summary.approved_today,rejected:value.summary.rejected}}
}
async function request<T>(path:string, parse:(value:unknown) => T, signal:AbortSignal, options:RequestInit = {}):Promise<T> {
  return withRequestDeadline(async transportSignal => {
    const headers = new Headers(options.headers)
    const token = userSessionToken()
    if (token) headers.set('Authorization',`Bearer ${token}`)
    headers.set('X-Requested-With','XMLHttpRequest')
    if (options.body) headers.set('Content-Type','application/json')
    const response = await fetch(`${API_BASE}/v22/admin/binance-payments${path}`,
      {...options,headers,signal:transportSignal,credentials:'same-origin',cache:'no-store'})
    const value:unknown = await response.json()
    if (!response.ok) throw new Error(isRecord(value) && typeof value.detail === 'string' ? value.detail
      : response.status === 403 ? 'Bu bölüm yalnız OWNER hesabına açıktır.' : 'Binance ödeme işlemi tamamlanamadı.')
    return parse(value)
  },{signal})
}
const message = (failure:unknown) => failure instanceof Error ? failure.message : 'İşlem tamamlanamadı. Yeniden deneyin.'
const date = (value:string) => new Date(value).toLocaleString('tr-TR',{dateStyle:'medium',timeStyle:'short'})
const optionalDate = (value:string|null) => value ? date(value) : '—'
const planName = (plan:string|null) => plan === 'MASTER_MODE' ? 'Master Mode' : plan === 'TRIAL' ? 'Deneme' : 'Üyelik yok'
const statusName = (status:string|null) => status && status in subscriptionLabels ? subscriptionLabels[status as keyof typeof subscriptionLabels] : null
const initialOf = (row:Item) => ((row.display_name || row.email || row.user_id).match(/[\p{L}\p{N}]/u)?.[0] ?? '?').toLocaleUpperCase('tr-TR')

export default function AdminBinancePayments() {
  const [query,setQuery] = useState<{status:Status;offset:number;version:number;paymentId:string}>(() => {
    const id = new URLSearchParams(window.location.search).get('payment') || ''
    return {status:'pending',offset:0,version:0,paymentId:/^[a-f0-9]{32}$/.test(id) ? id : ''}
  })
  const [page,setPage] = useState<PaymentPage|null>(null)
  const [disabled,setDisabled] = useState<DisabledPayments|null>(null)
  const [loading,setLoading] = useState(true)
  const [busy,setBusy] = useState('')
  const [error,setError] = useState('')
  const [refreshFailed,setRefreshFailed] = useState(false)
  const [notice,setNotice] = useState('')
  const [checks,setChecks] = useState<Record<string,boolean>>({})
  const [reasons,setReasons] = useState<Record<string,string>>({})
  const [search,setSearch] = useState('')
  const inFlight = useRef(false)
  const operation = useRef<AbortController|null>(null)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false;operation.current?.abort()}
  },[])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true);setError('');setRefreshFailed(false)
    const params = new URLSearchParams({limit:'25',offset:String(query.offset)})
    if (query.paymentId) params.set('request_id',query.paymentId)
    else params.set('status',query.status)
    void request(`?${params}`,parsePage,controller.signal)
      .then(value => {
        if ('enabled' in value) {
          if (!controller.signal.aborted) {setDisabled(value);setPage(null)}
          return
        }
        if (value.items.some(row => query.paymentId ? row.id !== query.paymentId : row.status !== query.status)) throw new Error('Ödeme listesi durum filtresi doğrulanamadı.')
        if (!controller.signal.aborted) {setDisabled(null);setPage(value)}
      })
      .catch(failure => {if (!controller.signal.aborted) {setError(message(failure));setRefreshFailed(true)}})
      .finally(() => {if (!controller.signal.aborted) setLoading(false)})
    return () => controller.abort()
  },[query])
  useEffect(() => {
    if (!page || !query.paymentId) return
    const target = document.getElementById(`binance-payment-${query.paymentId}`)
    target?.scrollIntoView({block:'center'})
    target?.focus({preventScroll:true})
  },[page,query.paymentId])
  const refresh = () => setQuery(value => ({...value,version:value.version+1}))
  const decide = async (row:Item, action:'approve'|'reject') => {
    if (inFlight.current) return
    if (action === 'approve' && !checks[row.id]) {setError('Önce Binance hesabınızda ödemeyi kontrol edip onay kutusunu işaretleyin.');return}
    if (action === 'reject' && !reasons[row.id]?.trim()) {setError('Red gerekçesi gerekli.');return}
    const controller = new AbortController()
    operation.current = controller;inFlight.current = true;setBusy(row.id);setError('');setRefreshFailed(false);setNotice('')
    try {
      const result = await request(`/${row.id}/${action}`,parseDecision,controller.signal,{
        method:'POST',body:JSON.stringify(action === 'approve' ? {checked_in_binance:true} : {reason:reasons[row.id].trim()}),
      })
      if ('enabled' in result) {
        if (mounted.current) {setDisabled(result);setPage(null)}
        return
      }
      if (result.id !== row.id || result.status !== (action === 'approve' ? 'approved' : 'rejected')) throw new Error('Ödeme kararı doğrulanamadı. Listeyi yenileyin.')
      if (mounted.current) {
        setPage(value => value ? {...value,items:value.items.filter(item => item.id !== row.id)} : value)
        setNotice(action === 'approve' ? 'Ödeme onaylandı. Üyelik dönemi sunucu tarafından kaydedildi.' : 'Ödeme gerekçesiyle reddedildi.')
        setChecks(value => ({...value,[row.id]:false}));setReasons(value => ({...value,[row.id]:''}))
        refresh()
      }
    } catch (failure) {if (mounted.current) setError(`${message(failure)} Kararı doğrulamak için listeyi yenileyin.`)}
    finally {inFlight.current = false;if (mounted.current) setBusy('')}
  }
  const term = search.trim().toLocaleLowerCase('tr-TR')
  const visible = (page?.items ?? []).filter(row => !term || [row.display_name,row.email,row.user_id,row.reference_code,row.transaction_number]
    .some(text => text?.toLocaleLowerCase('tr-TR').includes(term)))
  const copy = async (text:string) => {
    try {await navigator.clipboard.writeText(text);if (mounted.current) setNotice('Kopyalandı.')}
    catch {if (mounted.current) setError('Kopyalanamadı; metni seçip elle kopyalayın.')}
  }
  if (disabled) return <section className="adminBinance" aria-label="Binance ödemeleri">
    <header className="adminBinanceIntro"><h2>Ödeme talepleri</h2>
      <button type="button" onClick={refresh} disabled={loading}><RefreshCw size={16}/>Yenile</button></header>
    <div className="adminBinanceDisabled" role="status"><h3>Kapalı</h3><p>{disabled.message}</p></div>
    {error && <p className="adminBinanceError" role="alert">{error}</p>}
  </section>
  return <section className="adminBinance" aria-label="Binance ödemeleri">
    <header className="adminBinanceIntro"><div><h2>Ödeme talepleri</h2><p>Müşterilerin Binance ödeme talepleri burada toplanır. Ödemeyi Binance hesabınızda doğrulayın; kararlar yalnız OWNER tarafından kaydedilir.</p></div>
      <button type="button" onClick={refresh} disabled={loading || Boolean(busy)}><RefreshCw size={16}/>Yenile</button></header>
    <div className="adminBinanceSummary">{[
      ['Bekleyen',page?.summary.pending],['Bugün onaylanan',page?.summary.approved_today],['Reddedilen',page?.summary.rejected],
    ].map(([label,value]) => <div key={label}><small>{label}</small><strong>{value ?? '—'}</strong></div>)}</div>
    <div role="tablist" aria-label="Ödeme durumu" className="adminBinanceTabs">{tabs.map(tab =>
      <button role="tab" type="button" key={tab.status} aria-selected={query.status === tab.status} disabled={Boolean(busy)}
        onClick={() => {setPage(null);setNotice('');setSearch('');setChecks({});setReasons({});setQuery({status:tab.status,offset:0,version:query.version+1,paymentId:''})}}>{tab.label}</button>)}</div>
    <label className="adminBinanceSearch"><Search size={16} aria-hidden="true"/><span className="adminBinanceSr">Bu sayfada ara</span>
      <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Ad, e-posta, kullanıcı no, referans veya işlem numarası" autoComplete="off" spellCheck={false}/></label>
    {loading && <p role="status">Ödemeler yükleniyor…</p>}
    {error && <p role="alert" className="adminBinanceError">{error} {page && refreshFailed && 'Mevcut liste güncellenemedi.'}</p>}
    {notice && <p role="status" className="adminBinanceNotice">{notice}</p>}
    {!loading && !error && page?.items.length === 0 && <p>Bu durumda ödeme talebi yok.</p>}
    {!loading && !error && Boolean(page?.items.length) && visible.length === 0 && <p>Aramanızla eşleşen talep yok.</p>}
    <div className="adminBinanceCards">{visible.map(row => <article id={`binance-payment-${row.id}`} tabIndex={-1} className={row.recommendation && row.status === 'pending' ? 'isRecommended' : undefined} key={row.id} aria-label={`Ödeme ${row.reference_code}`}>
      <header><div className="adminBinanceIdentity"><span className="adminBinanceAvatar" aria-hidden="true">{initialOf(row)}</span>
        <div><h3>{row.display_name || row.email || row.user_id}</h3><span>{row.email || row.user_id}</span></div></div><strong>{row.amount} {row.currency}</strong></header>
      {row.recommendation && <p className="adminBinanceRecommended" role="note"><b>Moderatör onaya gönderdi</b> · {row.recommendation.by || 'Moderatör'} · {date(row.recommendation.at)}{row.recommendation.note ? <> — “{row.recommendation.note}”</> : null}<br/>Üyelik, siz <b>Onayla</b>ya basana kadar açılmaz.</p>}
      {row.customer && <ul className="adminBinanceBadges" aria-label="Hesap durumu">
        <li className={row.customer.active ? 'good' : 'warn'}>{row.customer.active ? 'Hesap aktif' : 'Hesap devre dışı'}</li>
        <li className={row.customer.email_verified ? 'good' : 'warn'}>{row.customer.email_verified ? 'E-posta doğrulandı' : 'E-posta doğrulanmadı'}</li>
        <li className={row.customer.mfa_enabled ? 'good' : ''}>{row.customer.mfa_enabled ? '2FA açık' : '2FA kapalı'}</li></ul>}
      <div className="adminBinanceColumns">
        <section aria-label="Ödeme bilgileri"><h4>Ödeme</h4>
          <dl><div><dt>Referans kodu</dt><dd>{row.reference_code}<button type="button" aria-label="Referans kodunu kopyala" onClick={() => void copy(row.reference_code)}><Copy size={16}/></button></dd></div>
            <div><dt>İşlem / sipariş numarası</dt><dd>{row.transaction_number || '—'}{row.transaction_number && <button type="button" aria-label="İşlem numarasını kopyala" onClick={() => {if (row.transaction_number) void copy(row.transaction_number)}}><Copy size={16}/></button>}</dd></div>
            <div><dt>Talep zamanı</dt><dd><time dateTime={row.created_at}>{date(row.created_at)}</time></dd></div>
            <div><dt>Kalan süre</dt><dd>{row.status === 'pending' ? 'İnceleme bekliyor · süre sınırı yok' : row.status === 'expired' ? 'Süresi doldu' : 'Tamamlandı'}</dd></div></dl></section>
        <section aria-label="Müşteri bilgileri"><h4>Müşteri</h4>
          <dl><div><dt>Kullanıcı numarası</dt><dd>{row.user_id}<button type="button" aria-label="Kullanıcı numarasını kopyala" onClick={() => void copy(row.user_id)}><Copy size={16}/></button></dd></div>
            {row.customer ? <>
              <div><dt>Kayıt tarihi</dt><dd>{optionalDate(row.customer.registered_at)}</dd></div>
              <div><dt>Plan</dt><dd>{planName(row.customer.plan)}{statusName(row.customer.subscription_status) && !(row.customer.plan === 'TRIAL' && row.customer.subscription_status === 'TRIALING') ? ` · ${statusName(row.customer.subscription_status)}` : ''}</dd></div>
              <div><dt>Üyelik bitişi</dt><dd>{optionalDate(row.customer.membership_end ?? row.customer.plan_period_end)}</dd></div>
              <div><dt>Önceki talepler</dt><dd>{row.customer.approved_count} onaylı · {row.customer.rejected_count} red · {row.customer.expired_count} süresi dolan</dd></div>
            </> : <div><dt>Hesap detayı</dt><dd>Alınamadı</dd></div>}</dl></section>
      </div>
      {row.decision_note && <p className="adminBinanceReason"><b>Red gerekçesi:</b> {row.decision_note}</p>}
      {row.status === 'pending' && <div className="adminBinanceDecision">
        <label className="adminBinanceCheck"><input type="checkbox" checked={checks[row.id] || false} disabled={Boolean(busy) || loading}
          onChange={event => setChecks(value => ({...value,[row.id]:event.target.checked}))}/>Binance hesabımda kontrol ettim</label>
        <div className="adminBinanceRejectLabel"><label htmlFor={`binance-reason-${row.id}`}>Red gerekçesi</label><textarea id={`binance-reason-${row.id}`} value={reasons[row.id] || ''} maxLength={500} disabled={Boolean(busy) || loading}
          onChange={event => setReasons(value => ({...value,[row.id]:event.target.value}))} placeholder="Ödeme neden reddediliyor?"/></div>
        <div className="adminBinanceActions"><button type="button" className="adminBinanceApprove" disabled={loading || Boolean(busy) || !checks[row.id]} onClick={() => void decide(row,'approve')}>Onayla</button>
          <button type="button" className="adminBinanceReject" disabled={loading || Boolean(busy) || !reasons[row.id]?.trim()} onClick={() => void decide(row,'reject')}>Reddet</button></div>
        {busy === row.id && <p role="status">Karar kaydediliyor…</p>}
      </div>}
    </article>)}</div>
    {page && <div className="adminBinancePagination"><span>{page.total} talep</span>
      <button type="button" disabled={loading || Boolean(busy) || page.offset === 0} onClick={() => {setPage(null);setQuery({...query,offset:Math.max(0,page.offset-page.limit)})}}>Önceki</button>
      <button type="button" disabled={loading || Boolean(busy) || page.offset+page.limit >= page.total} onClick={() => {setPage(null);setQuery({...query,offset:page.offset+page.limit})}}>Sonraki</button></div>}
  </section>
}

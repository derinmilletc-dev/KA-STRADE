import {useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode} from 'react'
import {Check, Copy, Monitor, ShieldCheck, X} from 'lucide-react'
import {accountDate, accountInitials, accountRequest, type AccountOverview} from './account-settings-api'
import AdminModeratorAccess from './AdminModeratorAccess'
import {accountRoleLabel} from './account-role'
import './admin-account-drawer.css'

const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Kullanıcı bilgileri alınamadı.'

export const TABS = [
  ['overview', 'Genel Bakış'], ['trading', 'Trading'], ['accounts', 'Hesaplar'],
  ['subscription', 'Abonelik'], ['security', 'Güvenlik'], ['activity', 'Aktiviteler'],
] as const
type TabKey = typeof TABS[number][0]

export const shortId = (id: string) => id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id

/** Copies the exact value; falls back to a hidden textarea when the async Clipboard API is unavailable or denied. */
export async function copyText(value: string, host: HTMLElement | null): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {await navigator.clipboard.writeText(value); return true}
  } catch { /* fall through to the legacy path */ }
  const area = document.createElement('textarea')
  area.value = value; area.setAttribute('readonly', ''); area.setAttribute('aria-hidden', 'true')
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none'
  ;(host ?? document.body).appendChild(area)
  try {area.select(); return document.execCommand('copy')}
  catch {return false}
  finally {area.remove()}
}

function CopyButton({value, label, onResult}: {value: string; label: string; onResult: (message: string) => void}) {
  const [done, setDone] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(timer.current), [])
  return <button type="button" className="adContentCopy" aria-label={`${label} kopyala`} title={`${label} kopyala`} onClick={event => {
    const host = event.currentTarget.closest('dialog')
    void copyText(value, host).then(ok => {
      onResult(ok ? `${label} kopyalandı.` : `${label} kopyalanamadı. Değeri elle seçip kopyalayın.`)
      setDone(ok); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setDone(false), 1800)
    })
  }}>{done ? <Check/> : <Copy/>}</button>
}

type Remote<T> = {data: T | null; error: string; loading: boolean; reload: () => void}
function useRemote<T>(path: string | null): Remote<T> {
  const [state, setState] = useState<{data: T | null; error: string; loading: boolean}>({data: null, error: '', loading: path !== null})
  const [nonce, setNonce] = useState(0)
  useEffect(() => {
    if (path === null) return
    const controller = new AbortController()
    setState(current => ({...current, error: '', loading: true}))
    accountRequest<T>(path, {signal: controller.signal})
      .then(data => {if (!controller.signal.aborted) setState({data, error: '', loading: false})})
      .catch(failure => {if (!controller.signal.aborted) setState({data: null, error: errorMessage(failure), loading: false})})
    return () => controller.abort()
  }, [path, nonce])
  return {...state, reload: useCallback(() => setNonce(value => value + 1), [])}
}

type Paged<T> = {items: T[]; total: number}
/** Pages of 25 appended on demand — history is never loaded in one request. */
function usePaged<T>(base: string | null, itemsKey: string) {
  const [items, setItems] = useState<T[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(base !== null)
  const [nonce, setNonce] = useState(0)
  const offset = useRef(0)
  const generation = useRef(0)
  const load = useCallback(async (reset: boolean) => {
    if (base === null) return
    const mine = ++generation.current
    if (reset) offset.current = 0
    setLoading(true); setError('')
    try {
      const joiner = base.includes('?') ? '&' : '?'
      const page = await accountRequest<Record<string, unknown>>(`${base}${joiner}limit=25&offset=${offset.current}`)
      if (mine !== generation.current) return
      const next = (page[itemsKey] as T[] | undefined) ?? []
      offset.current += next.length
      setItems(current => reset ? next : [...current, ...next]); setTotal(Number(page.total) || 0)
    } catch (failure) {if (mine === generation.current) setError(errorMessage(failure))}
    finally {if (mine === generation.current) setLoading(false)}
  }, [base, itemsKey])
  useEffect(() => {void load(true); return () => {generation.current += 1}}, [load, nonce])
  return {items, total, error, loading, more: () => void load(false), reload: () => setNonce(value => value + 1), hasMore: items.length < total}
}

function State({loading, error, empty, onRetry, children, emptyText}: {loading: boolean; error: string; empty: boolean; onRetry?: () => void; children: ReactNode; emptyText: string}) {
  if (error) return <div className="adState adError"><p role="alert">{error}</p>{onRetry && <button type="button" onClick={onRetry}>Yeniden dene</button>}</div>
  if (loading && empty) return <p className="adState" role="status">Yükleniyor...</p>
  if (empty) return <p className="adState">{emptyText}</p>
  return <>{children}</>
}

const Fact = ({label, children}: {label: string; children: ReactNode}) => <div><dt>{label}</dt><dd>{children}</dd></div>
const Tone = ({ok, yes, no}: {ok: boolean; yes: string; no: string}) => <b data-tone={ok ? 'positive' : 'negative'}>{ok ? yes : no}</b>

type TradingAccount = {id: string; provider: string; environment: string; account_reference: string; status: string; created_at: string; updated_at: string}
type CustomerSubscription = {plan: string | null; subscription_status: string | null; current_period_end: string | null; cancel_at_period_end: boolean | null}
type CustomerPayments = {payment_status: string; last_failed_payment_at: string | null}
type EventItem = {kind: string; code: string; feature: string; count: number; first_at: string; last_at: string; severity: string; source: string; http_status: number | null}
type AuditItem = {id: string | number; created_at: string; actor_role: string | null; action: string; reason: string | null}

const environmentLabel = (value: string) => /demo|test/i.test(value) ? 'Demo / Testnet' : /live|real|prod/i.test(value) ? 'Gerçek' : value
const eventLabels: Record<string, string> = {
  'auth.login_failed': 'Başarısız giriş', 'auth.login_succeeded': 'Başarılı giriş', 'auth.mfa_failed': '2FA doğrulaması başarısız',
  'auth.password_reset_requested': 'Parola yenileme istendi', 'auth.password_reset_completed': 'Parola yenilendi',
  'auth.email_verification_sent': 'Doğrulama e-postası gönderildi', 'auth.email_verification_failed': 'E-posta doğrulaması başarısız',
  'auth.email_verified': 'E-posta doğrulandı', 'account.session_revoked': 'Oturum sonlandırıldı', 'api.error': 'API hatası',
}

function TradingTab({data}: {data: AccountOverview}) {
  const p = data.preferences
  return <div className="adStack">
    <div className="adNotice" role="note"><b>İşlem verisi kullanılamıyor</b><p>Yönetici API'sinde kullanıcı bazlı işlem, pozisyon ve kâr/zarar verisi sunan bir kaynak bulunmuyor. Bu nedenle toplam işlem, açık pozisyon, gerçekleşmiş/gerçekleşmemiş K/Z ve başarı oranı gösterilmiyor; tahmini ya da örnek değer üretilmez.</p></div>
    <section aria-label="İşlem tercihleri"><h3>Kayıtlı işlem tercihleri</h3><p className="adHint">Kullanıcının kendi ayarlarıdır; gerçekleşmiş işlem verisi ya da canlı risk politikası değildir.</p>
      <dl className="adFacts"><Fact label="Mod">{p.trading_mode === 'AUTO' ? 'Otomatik' : 'Manuel'} <small>({p.trading_mode})</small></Fact><Fact label="Zaman dilimi">{p.timeframe}</Fact><Fact label="Borsa">{p.exchange}</Fact><Fact label="Tercih edilen risk">{p.risk_per_trade}%</Fact><Fact label="Semboller">{p.symbols.join(', ') || '—'}</Fact></dl></section>
  </div>
}

function AccountsTab({id}: {id: string}) {
  const {data, error, loading, reload} = useRemote<{accounts: TradingAccount[]}>(`/admin/users/${encodeURIComponent(id)}/trading-accounts`)
  const rows = data?.accounts ?? []
  return <div className="adStack"><section aria-label="Bağlı borsa hesapları"><h3>Bağlı borsa hesapları</h3>
    <p className="adHint">Yalnızca hesap eşlemesi gösterilir. API anahtarı, secret ve token bilgileri hiçbir zaman gösterilmez.</p>
    <State loading={loading} error={error} empty={!rows.length} onRetry={reload} emptyText="Bu kullanıcıya eşlenmiş borsa hesabı yok.">
      <ul className="adRecords">{rows.map(row => <li key={row.id}><div><b>{row.provider}</b> · <span data-env={/demo|test/i.test(row.environment) ? 'demo' : 'real'}>{environmentLabel(row.environment)}</span><small>Referans: {row.account_reference}</small></div><div className="adRight"><b>{row.status}</b><small>Güncellendi: {accountDate(row.updated_at)}</small></div></li>)}</ul>
    </State>
    <p className="adHint">Bot ilişkisi bilgisi yönetici API'sinde mevcut olmadığı için gösterilmiyor.</p></section></div>
}

function SubscriptionTab({id, data, busy, onGrant, onRevoke}: {id: string; data: AccountOverview; busy: boolean; onGrant: (plan: 'MASTER_MODE' | 'TRIAL', days: number) => void; onRevoke: () => void}) {
  const sub = data.subscription
  const isCustomer = data.user.role === 'CUSTOMER'
  const enc = encodeURIComponent(id)
  const detail = useRemote<CustomerSubscription>(isCustomer ? `/api/mod/customers/${enc}/subscription` : null)
  const payments = useRemote<CustomerPayments>(isCustomer ? `/api/mod/customers/${enc}/payments` : null)
  const [days, setDays] = useState(30)
  const [plan, setPlan] = useState<'MASTER_MODE' | 'TRIAL'>('MASTER_MODE')
  const isOwner = data.user.role === 'OWNER'
  return <div className="adStack">
    <section aria-label="Abonelik özeti"><h3>Abonelik</h3>
      <dl className="adFacts"><Fact label="Paket">{sub.plan}</Fact><Fact label="Durum">{sub.status}</Fact><Fact label="Premium erişimi">{sub.is_premium ? 'Var' : 'Yok'}{isOwner && ' · Yönetici yetkisi ücretli paket değildir'}</Fact><Fact label="Bitiş">{accountDate(sub.expires_at)}</Fact></dl>
      {sub.features.length > 0 && <ul className="adChips" aria-label="Paket özellikleri">{sub.features.map(feature => <li key={feature.key} data-included={feature.included}>{feature.included ? <Check/> : <X/>}{feature.label}</li>)}</ul>}
    </section>
    <section aria-label="Abonelik kaynak kayıtları"><h3>Kayıtlı abonelik ve son ödeme durumu</h3>
      {!isCustomer ? <p className="adState">Bu kayıtlar yalnızca müşteri rolündeki hesaplar için sunulur.</p> : <>
        <State loading={detail.loading} error={detail.error} empty={!detail.data} onRetry={detail.reload} emptyText="Abonelik kaydı bulunamadı.">
          <dl className="adFacts"><Fact label="Kayıtlı paket">{detail.data?.plan ?? '—'}</Fact><Fact label="Abonelik durumu">{detail.data?.subscription_status ?? '—'}</Fact><Fact label="Dönem sonu">{accountDate(detail.data?.current_period_end)}</Fact><Fact label="Dönem sonunda iptal">{detail.data?.cancel_at_period_end == null ? '—' : detail.data.cancel_at_period_end ? 'Evet' : 'Hayır'}</Fact></dl></State>
        <State loading={payments.loading} error={payments.error} empty={!payments.data} onRetry={payments.reload} emptyText="Ödeme durumu bulunamadı.">
          <dl className="adFacts"><Fact label="Son ödeme durumu">{payments.data?.payment_status}</Fact><Fact label="Son başarısız ödeme">{accountDate(payments.data?.last_failed_payment_at)}</Fact></dl></State>
        <p className="adHint">Ödeme geçmişi listesi yönetici API'sinde mevcut olmadığı için gösterilmiyor; yalnızca son durum görüntülenir.</p></>}
    </section>
    <section aria-label="Abonelik yönetimi"><h3>Abonelik yönetimi</h3>
      <p className="adHint">Elle verilen erişim gerçek premium erişimi sağlar. Bu işlem denetim kaydına yazılır; ücretli (Stripe/Binance) kayıtlar kaldırma işleminden etkilenmez.</p>
      <div className="adInline">
        <label>Erişim<select value={plan} disabled={busy || isOwner} onChange={event => setPlan(event.target.value as 'MASTER_MODE' | 'TRIAL')}><option value="MASTER_MODE">Master Mode (premium)</option><option value="TRIAL">Deneme</option></select></label>
        <label>Gün<input type="number" min={1} max={730} value={days} disabled={busy || isOwner} onChange={event => setDays(Math.min(730, Math.max(1, Number(event.target.value) || 1)))}/></label>
        <button type="button" disabled={busy || isOwner} onClick={() => onGrant(plan, days)}>Erişim ver</button>
        <button type="button" disabled={busy || isOwner} onClick={onRevoke}>Elle verilen erişimi kaldır</button>
      </div>
      {isOwner && <p className="adHint">Yönetici hesabı için abonelik yönetimi yapılamaz.</p>}
    </section></div>
}

function SecurityTab({data, id}: {data: AccountOverview; id: string}) {
  const enc = encodeURIComponent(id)
  const ok = usePaged<EventItem>(`/api/mod/customers/${enc}/events?kind=auth.login_succeeded`, 'items')
  const failed = usePaged<EventItem>(`/api/mod/customers/${enc}/events?kind=auth.login_failed`, 'items')
  const isCustomer = data.user.role === 'CUSTOMER'
  return <div className="adStack">
    <section aria-label="Güvenlik durumu"><h3>Güvenlik durumu</h3>
      <dl className="adFacts"><Fact label="2FA"><Tone ok={data.security.two_factor_enabled} yes="Etkin" no="Kapalı"/></Fact><Fact label="E-posta doğrulaması"><Tone ok={data.user.email_verified} yes="Doğrulandı" no="Doğrulanmadı"/></Fact><Fact label="Aktif oturumlar">{data.security.active_sessions}</Fact><Fact label="Son giriş">{accountDate(data.user.last_login)}</Fact><Fact label="Son parola değişikliği">{accountDate(data.user.password_changed_at)}</Fact><Fact label="Giriş yöntemleri">{data.user.auth_methods.join(' / ') || '—'}</Fact></dl></section>
    <section aria-label="Aktif oturumlar"><h3>Aktif oturumlar</h3>
      {data.sessions.length ? <ul className="adRecords">{data.sessions.map(session => <li key={session.id}><Monitor/><div><b>{session.device} · {session.browser}</b><small>Başlangıç: {accountDate(session.created_at)}</small></div><time className="adRight">{accountDate(session.last_seen_at)}</time></li>)}</ul> : <p className="adState">Aktif oturum yok.</p>}</section>
    <section aria-label="Giriş olayları"><h3>Son giriş olayları</h3><p className="adHint">Son 90 gün; sayfalı.</p>
      {!isCustomer ? <p className="adState">Teknik olay geçmişi yalnızca müşteri rolündeki hesaplar için sunulur.</p> : <div className="adColumns">
        <EventList title="Başarılı girişler" source={ok} empty="Kayıtlı başarılı giriş yok."/>
        <EventList title="Başarısız girişler" source={failed} empty="Kayıtlı başarısız giriş yok."/></div>}</section></div>
}

function EventList({title, source, empty}: {title: string; source: ReturnType<typeof usePaged<EventItem>>; empty: string}) {
  return <div><h4>{title}{source.total > 0 && <span> · {source.total}</span>}</h4>
    <State loading={source.loading} error={source.error} empty={!source.items.length} onRetry={source.reload} emptyText={empty}>
      <ul className="adRecords">{source.items.map((item, index) => <li key={`${item.kind}-${item.first_at}-${index}`}><div><b>{eventLabels[item.kind] ?? item.kind}{item.count > 1 && ` ×${item.count}`}</b><small>{item.feature} · {item.code}{item.http_status ? ` · HTTP ${item.http_status}` : ''}</small></div><time className="adRight">{accountDate(item.last_at)}</time></li>)}</ul>
      {source.hasMore && <button type="button" disabled={source.loading} onClick={source.more}>Daha fazla göster</button>}
    </State></div>
}

function ActivityTab({data, id}: {data: AccountOverview; id: string}) {
  const enc = encodeURIComponent(id)
  const isCustomer = data.user.role === 'CUSTOMER'
  const events = usePaged<EventItem>(isCustomer ? `/api/mod/customers/${enc}/events` : null, 'items')
  const audit = usePaged<AuditItem>(`/api/v22/admin/audit?target=${enc}`, 'items')
  return <div className="adStack">
    <section aria-label="Hesap aktiviteleri"><h3>Hesap aktiviteleri</h3>
      {data.activity.length ? <ul className="adRecords">{data.activity.map(item => <li key={item.id}><span>{item.message}</span><time className="adRight">{accountDate(item.created_at)}</time></li>)}</ul> : <p className="adState">Kayıtlı aktivite yok.</p>}</section>
    <section aria-label="Teknik olay kayıtları"><h3>Teknik olaylar ve hatalar</h3><p className="adHint">Son 90 gün; her seferinde 25 kayıt.</p>
      {!isCustomer ? <p className="adState">Teknik olay geçmişi yalnızca müşteri rolündeki hesaplar için sunulur.</p> : <EventList title="Olaylar" source={events} empty="Kayıtlı teknik olay yok."/>}</section>
    <section aria-label="Yönetici denetim kayıtları"><h3>Yönetici denetim kayıtları</h3>
      <State loading={audit.loading} error={audit.error} empty={!audit.items.length} onRetry={audit.reload} emptyText="Bu kullanıcı için denetim kaydı yok.">
        <ul className="adRecords">{audit.items.map(item => <li key={item.id}><div><b>{item.action}</b><small>{item.actor_role ?? '—'}{item.reason ? ` · ${item.reason}` : ''}</small></div><time className="adRight">{accountDate(item.created_at)}</time></li>)}</ul>
        {audit.hasMore && <button type="button" disabled={audit.loading} onClick={audit.more}>Daha fazla göster</button>}
      </State></section></div>
}

function OverviewTab({data, id, busy, setBusy, refresh, onChanged}: {data: AccountOverview; id: string; busy: boolean; setBusy: (value: boolean) => void; refresh: () => Promise<void>; onChanged: () => void}) {
  const {user, subscription, security} = data
  return <div className="adStack">
    <section aria-label="Profil ve kimlik"><h3>Profil ve kimlik</h3>
      <dl className="adFacts"><Fact label="Hesap rolü">{accountRoleLabel(user.role)}</Fact><Fact label="Hesap durumu"><Tone ok={user.active} yes="Aktif" no="Kapalı / devre dışı"/></Fact><Fact label="E-posta doğrulaması">{user.email_verified ? 'Doğrulandı' : 'Doğrulanmadı'}</Fact><Fact label="Bekleyen e-posta">{data.pending_email?.email || '—'}</Fact><Fact label="Giriş yöntemleri">{user.auth_methods.join(' / ') || '—'}</Fact><Fact label="Kayıt tarihi">{accountDate(user.created_at)}</Fact><Fact label="Son giriş">{accountDate(user.last_login)}</Fact><Fact label="Son parola değişikliği">{accountDate(user.password_changed_at)}</Fact></dl></section>
    <section aria-label="Temel göstergeler"><h3>Temel göstergeler</h3>
      <dl className="adFacts"><Fact label="Paket">{subscription.plan}</Fact><Fact label="Abonelik durumu">{subscription.status}</Fact><Fact label="Premium erişimi">{subscription.is_premium ? 'Var' : 'Yok'}{user.role === 'OWNER' && ' · Yönetici yetkisi ücretli paket değildir'}</Fact><Fact label="Paket bitişi">{accountDate(subscription.expires_at)}</Fact><Fact label="2FA">{security.two_factor_enabled ? 'Etkin' : 'Kapalı'}</Fact><Fact label="Aktif oturumlar">{security.active_sessions}</Fact><Fact label="Tercih edilen mod">{data.preferences.trading_mode}</Fact><Fact label="Zaman dilimi">{data.preferences.timeframe}</Fact><Fact label="Borsa">{data.preferences.exchange}</Fact><Fact label="Tercih edilen risk">{data.preferences.risk_per_trade}% · canlı politika değildir</Fact><Fact label="Semboller">{data.preferences.symbols.join(', ') || '—'}</Fact></dl></section>
    <AdminModeratorAccess id={id} role={user.role} disabled={busy} onBusyChange={setBusy} onChanged={async () => {await refresh(); onChanged()}}/>
    <p className="adHint adPrivacy"><ShieldCheck/>Şifreler, 2FA anahtarları, kurtarma kodları, oturum token'ları ve API anahtarları bu ekranda gösterilmez.</p>
  </div>
}

type Confirmation = 'status' | 'sessions' | 'reset' | 'grant' | 'revoke' | 'delete'

export default function AdminAccountDetail({id, onClose, onChanged}: {id: string; onClose: () => void; onChanged: () => void}) {
  const [data, setData] = useState<AccountOverview | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [copyNote, setCopyNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<TabKey>('overview')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [grant, setGrant] = useState<{plan: 'MASTER_MODE' | 'TRIAL'; days: number}>({plan: 'MASTER_MODE', days: 30})
  const [typedEmail, setTypedEmail] = useState('')
  const [typedPhrase, setTypedPhrase] = useState('')
  const inFlight = useRef(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const uid = useId()
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const result = await accountRequest<AccountOverview>(`/admin/accounts/${encodeURIComponent(id)}`, {signal})
    if (!signal?.aborted) {setData(result); setError('')}
  }, [id])
  useEffect(() => {
    const previous = document.activeElement
    const element = dialog.current
    element?.showModal()
    const controller = new AbortController()
    void refresh(controller.signal).catch(failure => {if (!controller.signal.aborted) setError(errorMessage(failure))})
    return () => {controller.abort(); element?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus()}
  }, [refresh])
  const copyTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(copyTimer.current), [])
  const reportCopy = (text: string) => {setCopyNote(text); window.clearTimeout(copyTimer.current); copyTimer.current = window.setTimeout(() => setCopyNote(''), 3500)}
  const choose = (value: Confirmation) => {setConfirmation(value); setTypedEmail(''); setTypedPhrase(''); setError(''); setMessage('')}

  const deleteReady = !!data && typedEmail.trim().toLowerCase() === data.user.email.trim().toLowerCase() && typedPhrase === 'DELETE USER'
  const act = async () => {
    if (!confirmation || !data || inFlight.current) return
    if (confirmation === 'delete' && !deleteReady) return
    inFlight.current = true; setBusy(true); setError(''); setMessage('')
    try {
      const enc = encodeURIComponent(id)
      let done = 'Kullanıcı işlemi tamamlandı.'
      if (confirmation === 'status') await accountRequest(`/customers/${enc}/status`, {method: 'POST', body: JSON.stringify({active: !data.user.active, reason: 'Admin kullanıcı yönetimi'})})
      else if (confirmation === 'sessions') await accountRequest(`/admin/users/${enc}/sessions/revoke`, {method: 'POST'})
      else if (confirmation === 'grant') {
        await accountRequest('/subscriptions/activate-demo', {method: 'POST', body: JSON.stringify({user_id: id, plan: grant.plan, days: grant.days})})
        done = `Erişim ${grant.days} gün için verildi.`
      } else if (confirmation === 'revoke') {
        await accountRequest('/subscriptions/activate-demo', {method: 'POST', body: JSON.stringify({user_id: id, plan: 'NONE', days: 1})})
        done = 'Elle verilen erişim kaldırıldı.'
      } else if (confirmation === 'delete') {
        await accountRequest(`/admin/users/${enc}`, {method: 'DELETE', body: JSON.stringify({email: data.user.email, confirmation: 'DELETE USER'})})
        onChanged(); onClose(); return
      } else {await accountRequest(`/admin/accounts/${enc}/password-reset`, {method: 'POST', body: '{}'}); done = 'Parola yenileme e-postası gönderildi.'}
      await refresh()
      onChanged()
      setMessage(done)
      setConfirmation(null)
    } catch (failure) {setError(errorMessage(failure))}
    finally {inFlight.current = false; setBusy(false)}
  }

  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const move = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : event.key === 'Home' ? -index : event.key === 'End' ? TABS.length - 1 - index : 0
    if (!move) return
    event.preventDefault()
    const next = TABS[(index + move + TABS.length) % TABS.length][0]
    setTab(next); tabRefs.current[next]?.focus()
  }

  const confirmText = (): string => {
    if (!data) return ''
    switch (confirmation) {
      case 'status': return `Bu hesabı ${data.user.active ? 'devre dışı bırakmak' : 'yeniden açmak'} istiyor musunuz? Kayıtlar silinmez.`
      case 'sessions': return 'Bu kullanıcının tüm oturumları sonlandırılacak.'
      case 'grant': return `Bu kullanıcıya ${grant.plan === 'TRIAL' ? 'deneme' : 'Master Mode (premium)'} erişimi ${grant.days} gün için verilecek.`
      case 'revoke': return 'Elle verilen erişim kaldırılacak. Ücretli abonelik kayıtları korunur.'
      case 'delete': return 'Hesap ve ilişkili veriler kalıcı olarak silinecek; bu işlem geri alınamaz. Açık pozisyon veya borsa bağlantısı varsa sunucu işlemi reddeder.'
      default: return 'Kullanıcıya gerçek parola yenileme e-postası gönderilecek.'
    }
  }

  const tabIds = (key: string) => ({tab: `${uid}-tab-${key}`, panel: `${uid}-panel-${key}`})
  return <dialog className="adminAccountDialog adDrawer" ref={dialog} aria-labelledby="admin-account-title" onCancel={event => {event.preventDefault(); if (!busy) onClose()}}
    onClick={event => {if (event.target === dialog.current && !busy) onClose()}}>
    <header className="adTop">
      <div><small>KULLANICI DETAYI</small><h2 id="admin-account-title">Kullanıcı detayları</h2></div>
      <button type="button" className="adClose" aria-label="Kullanıcı detayını kapat" disabled={busy} onClick={onClose}><X/></button>
    </header>
    <div className="adBody">
      {error && <p role="alert" className="adminAccountError">{error}</p>}
      {message && <p role="status" className="adSuccess">{message}</p>}
      <p role="status" className="adCopyNote" aria-live="polite">{copyNote}</p>
      {!data && !error && <p role="status" className="adState">Kullanıcı bilgileri yükleniyor...</p>}
      {!data && error && <button type="button" disabled={busy} onClick={() => void refresh().catch(failure => setError(errorMessage(failure)))}>Yeniden dene</button>}
      {data && <>
        <section className="adIdentity" aria-label="Kullanıcı kimliği">
          <i aria-hidden="true">{accountInitials(data.user.display_name)}</i>
          <div className="adIdentityMain">
            <h3>{data.user.display_name}<CopyButton value={data.user.display_name} label="Görünen ad" onResult={reportCopy}/></h3>
            <p className="adLine"><span>{data.user.email}</span><CopyButton value={data.user.email} label="E-posta" onResult={reportCopy}/></p>
            <p className="adLine adMono"><span title={data.user.id}>{shortId(data.user.id)}</span><CopyButton value={data.user.id} label="UUID" onResult={reportCopy}/></p>
          </div>
          <ul className="adBadges" aria-label="Hesap özeti">
            <li>{accountRoleLabel(data.user.role)}</li>
            <li data-tone={data.user.active ? 'positive' : 'negative'}>{data.user.active ? 'Aktif' : 'Kapalı / devre dışı'}</li>
            <li data-tone={data.user.email_verified ? 'positive' : 'warn'}>{data.user.email_verified ? 'E-posta doğrulandı' : 'E-posta doğrulanmadı'}</li>
            <li>Plan: {data.subscription.plan}</li>
          </ul>
          <p className="adDates">Kayıt: {accountDate(data.user.created_at)} · Son giriş: {accountDate(data.user.last_login)}</p>
        </section>
        <div className="adTabs" role="tablist" aria-label="Kullanıcı bölümleri">
          {TABS.map(([key, label], index) => <button key={key} type="button" role="tab" id={tabIds(key).tab} ref={node => {tabRefs.current[key] = node}} aria-selected={tab === key} aria-controls={tabIds(key).panel} tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={event => onTabKey(event, index)}>{label}</button>)}
        </div>
        <div role="tabpanel" id={tabIds(tab).panel} aria-labelledby={tabIds(tab).tab} className="adPanel" tabIndex={0}>
          {tab === 'overview' && <OverviewTab data={data} id={id} busy={busy} setBusy={setBusy} refresh={refresh} onChanged={onChanged}/>}
          {tab === 'trading' && <TradingTab data={data}/>}
          {tab === 'accounts' && <AccountsTab id={id}/>}
          {tab === 'subscription' && <SubscriptionTab id={id} data={data} busy={busy} onGrant={(plan, days) => {setGrant({plan, days}); choose('grant')}} onRevoke={() => choose('revoke')}/>}
          {tab === 'security' && <SecurityTab data={data} id={id}/>}
          {tab === 'activity' && <ActivityTab data={data} id={id}/>}
        </div>
      </>}
    </div>
    {data && <footer className="adFooter">
      {confirmation && <section className="adminAccountConfirmation" role="group" aria-label="Yönetici işlemi onayı">
        <p>{confirmText()}</p>
        {confirmation === 'delete' && <div className="adDeleteFields">
          <label>Onay için hesabın e-postasını yazın<input autoComplete="off" value={typedEmail} onChange={event => setTypedEmail(event.target.value)} placeholder={data.user.email}/></label>
          <label>Ardından DELETE USER yazın<input autoComplete="off" value={typedPhrase} onChange={event => setTypedPhrase(event.target.value)} placeholder="DELETE USER"/></label></div>}
        <div className="adConfirmButtons"><button type="button" className={confirmation === 'delete' ? 'adDanger' : ''} disabled={busy || (confirmation === 'delete' && !deleteReady)} onClick={() => void act()}>İşlemi onayla</button><button type="button" disabled={busy} onClick={() => setConfirmation(null)}>İptal</button></div></section>}
      <div className="adminAccountActions">
        <button type="button" disabled={busy || data.user.role === 'OWNER'} onClick={() => choose('status')}>{data.user.active ? 'Hesabı devre dışı bırak' : 'Hesabı yeniden aç'}</button>
        <button type="button" disabled={busy} onClick={() => choose('sessions')}>Oturumları sonlandır</button>
        <button type="button" disabled={busy || !data.security.email_delivery_available} onClick={() => choose('reset')}>Parola yenileme e-postası gönder</button>
        <button type="button" className="adDanger" disabled={busy || data.user.role === 'OWNER'} onClick={() => choose('delete')}>Hesabı sil</button>
      </div>
    </footer>}
  </dialog>
}

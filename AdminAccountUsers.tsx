import {useEffect, useState} from 'react'
import {Check, ChevronLeft, ChevronRight, Mail, RefreshCw, Search, ShieldCheck, UserRound} from 'lucide-react'
import {accountDate, accountInitials, accountRequest, type AdminAccount, type AdminAccounts} from './account-settings-api'
import './admin-account-users.css'
import AdminAccountDetail from './AdminAccountDetail'
import {accountRoleLabel} from './account-role'

type Props = {search: string; onSearch: (value: string) => void; page: number; onPage: (value: number) => void; filter: string; onFilter: (value: string) => void; pageSize: number}
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Kullanıcı bilgileri alınamadı.'

export default function AdminAccountUsers({search, onSearch, page, onPage, filter, onFilter, pageSize}: Props) {
  const [data, setData] = useState<AdminAccounts | null>(null)
  const [status, setStatus] = useState('all')
  const [verified, setVerified] = useState('all')
  const [query, setQuery] = useState(search)
  const [debounced, setDebounced] = useState(search)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  useEffect(() => {setQuery(search)}, [search])
  useEffect(() => {const timer = setTimeout(() => setDebounced(query), 200); return () => clearTimeout(timer)}, [query])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({search: debounced, premium: filter === 'PREMIUM' ? 'premium' : filter === 'FREE' ? 'free' : 'all', status, verified, page: String(page), page_size: String(pageSize)})
    const load = async () => {
      try {
        const result = await accountRequest<AdminAccounts>(`/admin/accounts?${params}`, {signal: controller.signal})
        if (!controller.signal.aborted) {setData(result); setError('')}
      } catch (failure) {if (!controller.signal.aborted) setError(errorMessage(failure))}
      finally {if (!controller.signal.aborted) setLoading(false)}
    }
    setLoading(true)
    void load()
    const timer = setInterval(() => {if (!document.hidden) void load()}, 15000)
    return () => {controller.abort(); clearInterval(timer)}
  }, [debounced, filter, status, verified, page, pageSize, nonce])
  const selectStatus = (value: string) => {setStatus(value); onPage(1)}
  return <section className="adminContent adminAccountUsers" aria-label="Kullanıcı hesapları" aria-busy={loading}>
    <header><div><small>AYNI HESAP KAYITLARI</small><h2>Kullanıcılar <span>{data?.total ?? '—'}</span></h2><p>Kimlik, üyelik, güvenlik ve hesap durumu.</p></div><button disabled={loading} onClick={() => setNonce(value => value + 1)}><RefreshCw/>Yenile</button></header>
    <div className="adminAccountFilters"><label><Search/><input aria-label="Kullanıcı ara" placeholder="Ad, e-posta veya hesap kimliği" value={query} onChange={event => {setQuery(event.target.value); onSearch(event.target.value); onPage(1)}}/></label><select aria-label="Premium filtresi" value={filter} onChange={event => {onFilter(event.target.value); onPage(1)}}><option value="ALL">Tüm üyelikler</option><option value="PREMIUM">Premium erişimi var</option><option value="FREE">Premium erişimi yok</option></select><select aria-label="Hesap durumu filtresi" value={status} onChange={event => selectStatus(event.target.value)}><option value="all">Tüm hesaplar</option><option value="active">Aktif</option><option value="inactive">Kapalı / devre dışı</option></select><select aria-label="E-posta doğrulama filtresi" value={verified} onChange={event => {setVerified(event.target.value); onPage(1)}}><option value="all">Tüm e-postalar</option><option value="verified">Doğrulandı</option><option value="unverified">Doğrulanmadı</option></select></div>
    {error && <p className="adminAccountError" role="alert">{error}</p>}{loading && <p role="status">Kullanıcılar yükleniyor...</p>}
    <div className="adminAccountTableScroll"><table><thead><tr><th>Kullanıcı</th><th>E-posta</th><th>Rol / giriş</th><th>Üyelik</th><th>Hesap</th><th>Güvenlik</th><th>Tarihler</th><th>Detay</th></tr></thead><tbody>{!loading && !error && data?.users.map((user: AdminAccount) => <tr key={user.id}><td><div className="adminAccountUser"><i>{accountInitials(user.display_name)}</i><span><b>{user.display_name}</b><small>{user.id}</small></span></div></td><td><span>{user.email}</span><small>{user.email_verified ? <><Check/>Doğrulandı</> : <><Mail/>Doğrulanmadı</>}</small></td><td>{accountRoleLabel(user.role)}<small>{user.auth_methods.join(' / ') || '—'}</small></td><td><b>{user.subscription.plan}</b><small>{user.subscription.is_premium ? 'Premium erişimi var' : 'Premium erişimi yok'} · {user.subscription.status}</small><small>Bitiş: {accountDate(user.subscription.expires_at)}</small></td><td><b data-tone={user.active ? 'positive' : 'negative'}>{user.active ? 'Aktif' : 'Kapalı / devre dışı'}</b>{user.closed_at && <small>Kapatılma: {accountDate(user.closed_at)}</small>}</td><td><small><ShieldCheck/>2FA {user.two_factor_enabled ? 'etkin' : 'kapalı'}</small><small>{user.active_sessions} oturum</small></td><td><small>Kayıt: {accountDate(user.created_at)}</small><small>Giriş: {accountDate(user.last_login)}</small></td><td><button onClick={() => setSelected(user.id)} aria-label={`${user.display_name} detayları`}><UserRound/>Görüntüle</button></td></tr>)}</tbody></table></div>
    {!loading && !error && data?.users.length === 0 && <p>Sonuç bulunamadı.</p>}
    <footer><span>{data ? `${data.total} kullanıcı · Sayfa ${page} / ${Math.max(1, Math.ceil(data.total / pageSize))}` : '—'}</span><button aria-label="Önceki kullanıcı sayfası" disabled={loading || page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft/></button><button aria-label="Sonraki kullanıcı sayfası" disabled={loading || !data || page * pageSize >= data.total} onClick={() => onPage(page + 1)}><ChevronRight/></button></footer>
    {selected && <AdminAccountDetail key={selected} id={selected} onClose={() => setSelected(null)} onChanged={() => setNonce(value => value + 1)}/>}
  </section>
}

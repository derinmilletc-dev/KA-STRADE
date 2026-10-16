import {useCallback, useEffect, useState} from 'react'
import {Monitor, Smartphone} from 'lucide-react'
import {accountDate, accountRequest, type AccountMutation, type AccountOverview, type AccountSession} from './account-settings-api'
import './sessions-card.css'

export default function ActiveSessionsCard() {
  const [sessions, setSessions] = useState<AccountSession[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const load = useCallback(async () => {
    try {
      const data = await accountRequest<AccountOverview>('/account/overview')
      setSessions(data.sessions || []); setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Oturumlar alınamadı.') }
  }, [])
  useEffect(() => { void load() }, [load])
  const act = async (key: string, path: string, body: object) => {
    setBusy(key); setError('')
    try {
      const result = await accountRequest<AccountMutation>(path, {method: 'POST', body: JSON.stringify(body)})
      if (result.reauthenticate) { window.location.reload(); return }
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : 'İşlem tamamlanamadı.') }
    finally { setBusy('') }
  }
  const others = (sessions || []).filter(s => !s.current).length
  return <section className="accountCard" id="sessions-card">
    <h2>Açık oturumlar</h2>
    <p className="scLead">Hesabına hangi cihazlardan girildiğini gör. Tanımadığın bir cihaz varsa tek tıkla çıkış yaptır.</p>
    {error && <p className="scError">{error}</p>}
    {sessions === null && !error && <p className="scLead">Yükleniyor…</p>}
    <div className="scList">
      {(sessions || []).map(s => {
        const mobile = /mobile|android|iphone|ipad/i.test(s.device)
        return <div className="scRow" key={s.id}>
          <i>{mobile ? <Smartphone/> : <Monitor/>}</i>
          <span><b>{s.device} · {s.browser}{s.current && <em>Bu cihaz</em>}</b><small>Son etkinlik: {accountDate(s.last_seen_at)}</small></span>
          {!s.current && <button type="button" disabled={!!busy} onClick={() => void act(s.id, '/account/sessions/revoke', {session_id: s.id})}>{busy === s.id ? '…' : 'Çıkış yaptır'}</button>}
        </div>
      })}
      {sessions && !sessions.length && <p className="scLead">Kayıtlı açık oturum yok.</p>}
    </div>
    {others > 0 && <div className="scFoot"><small>{others} başka cihaz açık.</small><button type="button" className="scDanger" disabled={!!busy} onClick={() => void act('others', '/account/sessions/revoke-others', {})}>{busy === 'others' ? '…' : 'Diğer hepsini çıkar'}</button></div>}
  </section>
}

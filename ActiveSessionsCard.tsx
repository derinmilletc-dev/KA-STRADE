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
  const act = async (key: string, path: string, bodies: object[]) => {
    setBusy(key); setError('')
    try {
      for (const body of bodies) {
        const result = await accountRequest<AccountMutation>(path, {method: 'POST', body: JSON.stringify(body)})
        if (result.reauthenticate) { window.location.reload(); return }
      }
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : 'İşlem tamamlanamadı.') }
    finally { setBusy('') }
  }
  const time = (v: string) => { const t = Date.parse(v); return Number.isFinite(t) ? t : 0 }
  const groups = (() => {
    const map = new Map<string, {key: string; device: string; browser: string; current: boolean; last: string; ids: string[]}>()
    for (const s of sessions || []) {
      const key = `${s.device}|${s.browser}`.toLowerCase()
      const g = map.get(key)
      if (!g) map.set(key, {key, device: s.device, browser: s.browser, current: s.current, last: s.last_seen_at, ids: s.current ? [] : [s.id]})
      else {
        g.current = g.current || s.current
        if (time(s.last_seen_at) > time(g.last)) g.last = s.last_seen_at
        if (!s.current) g.ids.push(s.id)
      }
    }
    return [...map.values()].sort((a, b) => time(b.last) - time(a.last))
  })()
  const others = groups.filter(g => !g.current).length
  return <section className="accountCard" id="sessions-card">
    <h2>Açık oturumlar</h2>
    <p className="scLead">Hesabına hangi cihazlardan girildiğini gör. Tanımadığın bir cihaz varsa tek tıkla çıkış yaptır.</p>
    {error && <p className="scError">{error}</p>}
    {sessions === null && !error && <p className="scLead">Yükleniyor…</p>}
    <div className="scList">
      {groups.map(s => {
        const mobile = /mobile|android|iphone|ipad/i.test(s.device)
        return <div className="scRow" key={s.key}>
          <i>{mobile ? <Smartphone/> : <Monitor/>}</i>
          <span><b>{s.device} · {s.browser}{s.current && <em>Bu cihaz</em>}</b><small>Son etkinlik: {accountDate(s.last)}</small></span>
          {!s.current && <button type="button" disabled={!!busy} onClick={() => void act(s.key, '/account/sessions/revoke', s.ids.map(id => ({session_id: id})))}>{busy === s.key ? '…' : 'Çıkış yaptır'}</button>}
        </div>
      })}
      {sessions && !groups.length && <p className="scLead">Kayıtlı açık oturum yok.</p>}
    </div>
    {others > 0 && <div className="scFoot"><small>{others} başka cihaz açık.</small><button type="button" className="scDanger" disabled={!!busy} onClick={() => void act('others', '/account/sessions/revoke-others', [{}])}>{busy === 'others' ? '…' : 'Diğer hepsini çıkar'}</button></div>}
  </section>
}

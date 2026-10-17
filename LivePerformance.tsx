import {useCallback, useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from './master-trade-request'
import './performance-live.css'

type Row = {id: string; symbol: string; direction?: string; created_at?: string; opened_at?: string; reason?: string; realized_pnl: number; r_multiple?: number | null; price?: number | null; quantity?: number | null}
type Period = 'all' | 'daily' | 'weekly' | 'monthly'
const PERIODS: {id: Period; label: string; days: number | null}[] = [
  {id: 'all', label: 'Tümü', days: null}, {id: 'daily', label: 'Günlük', days: 1}, {id: 'weekly', label: 'Haftalık', days: 7}, {id: 'monthly', label: 'Aylık', days: 31},
]
const fmt = (n: number | null | undefined, d = 2) => typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—'
const sign = (n: number) => `${n >= 0 ? '+' : ''}${fmt(n)}`
const when = (v?: string) => { const t = v ? new Date(v) : null; return t && !Number.isNaN(t.getTime()) ? t.toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—' }

export default function LivePerformance({active}: {active: boolean}) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState('')
  const [period, setPeriod] = useState<Period>('all')
  const [updated, setUpdated] = useState<Date | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(`${API_BASE}/v25/status`, {cache: 'no-store'})
      if (!res.ok) throw new Error(String(res.status))
      const body = await res.json() as {journal?: Row[]}
      const list = (body.journal ?? []).filter(item => typeof item.realized_pnl === 'number' && Number.isFinite(item.realized_pnl))
      setRows(list); setError(''); setUpdated(new Date())
    } catch {
      setError('Canlı işlem sonuçları şu an okunamadı. Birazdan otomatik olarak tekrar denenecek.')
    }
  }, [])
  useEffect(() => {
    if (!active) return
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    const onFocus = () => void load()
    window.addEventListener('focus', onFocus)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', onFocus) }
  }, [active, load])

  const view = useMemo(() => {
    const days = PERIODS.find(p => p.id === period)?.days ?? null
    const now = Date.now()
    const list = (rows ?? []).filter(r => days === null || now - new Date(r.created_at ?? 0).getTime() <= days * 86400000)
      .sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime())
    const pnl = list.map(r => r.realized_pnl)
    const wins = pnl.filter(v => v > 0), losses = pnl.filter(v => v < 0)
    const profit = wins.reduce((a, b) => a + b, 0), loss = losses.reduce((a, b) => a + b, 0)
    let eq = 0, peak = 0, dd = 0
    const curve = [...list].reverse().map(r => { eq += r.realized_pnl; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq); return r.realized_pnl })
    return {list, total: list.length, wins: wins.length, losses: losses.length, profit, loss, net: profit + loss, winRate: list.length ? wins.length / list.length * 100 : 0,
      pf: loss < 0 ? profit / Math.abs(loss) : null, avg: list.length ? (profit + loss) / list.length : 0, best: pnl.length ? Math.max(...pnl) : 0, worst: pnl.length ? Math.min(...pnl) : 0, dd, curve}
  }, [rows, period])

  const maxAbs = Math.max(...view.curve.map(Math.abs), 1)
  return <section className="lpWrap">
    <header className="lpHead">
      <div><span>GERÇEK BINANCE İŞLEMLERİ</span><h2>Canlı işlem performansı</h2><p>Kapanan her canlı işlem Binance’te doğrulandıktan sonra buraya otomatik düşer.</p></div>
      <div className="lpPeriods">{PERIODS.map(p => <button key={p.id} type="button" className={period === p.id ? 'on' : ''} onClick={() => setPeriod(p.id)}>{p.label}</button>)}</div>
    </header>
    {error && <div className="lpNote">{error}</div>}
    {rows === null && !error && <div className="lpNote">Canlı sonuçlar yükleniyor…</div>}
    {rows !== null && <>
      <div className="lpHero">
        <span><small>Net kâr / zarar</small><b className={view.net >= 0 ? 'win' : 'loss'}>{sign(view.net)} USDT</b><em>{view.total} kapanmış işlem</em></span>
        <span><small>Kazanma oranı</small><b>{fmt(view.winRate, 1)}%</b><em>{view.wins} kazanç · {view.losses} kayıp</em></span>
        <span><small>Kâr faktörü</small><b>{view.pf === null ? '—' : fmt(view.pf)}</b><em>Toplam kâr / toplam zarar</em></span>
        <span><small>En büyük düşüş</small><b className="loss">{fmt(view.dd)} USDT</b><em>Seçili dönem</em></span>
      </div>
      <div className="lpGrid">
        <span><small>Toplam kâr</small><b className="win">{sign(view.profit)}</b></span>
        <span><small>Toplam zarar</small><b className="loss">{fmt(view.loss)}</b></span>
        <span><small>Ortalama işlem</small><b className={view.avg >= 0 ? 'win' : 'loss'}>{sign(view.avg)}</b></span>
        <span><small>En iyi işlem</small><b className="win">{sign(view.best)}</b></span>
        <span><small>En kötü işlem</small><b className="loss">{fmt(view.worst)}</b></span>
      </div>
      {view.curve.length >= 2 && <div className="lpCurve" aria-label="İşlem sonuçları">{view.curve.map((v, i) => <i key={i} className={v >= 0 ? 'win' : 'loss'} style={{height: `${Math.max(8, Math.abs(v) / maxAbs * 100)}%`}} title={`${sign(v)} USDT`}/>)}</div>}
      <div className="lpList">
        <h3>Kapanan işlemler</h3>
        {view.list.length === 0 && <div className="lpEmpty"><b>Henüz kapanmış canlı işlem yok</b><p>Bir canlı işlem kapandığında sonucu burada görünür.</p></div>}
        {view.list.map(r => <article key={r.id}>
          <div><b>{r.symbol.replace('USDT', '/USDT')}</b><em className={String(r.direction).toUpperCase() === 'LONG' ? 'long' : 'short'}>{String(r.direction || '—').toUpperCase()}</em></div>
          <small>{when(r.created_at)}{r.reason ? ` · ${r.reason}` : ''}{typeof r.r_multiple === 'number' ? ` · ${fmt(r.r_multiple)}R` : ''}</small>
          <strong className={r.realized_pnl >= 0 ? 'win' : 'loss'}>{sign(r.realized_pnl)} USDT</strong>
        </article>)}
      </div>
      {updated && <p className="lpFoot">Son güncelleme {updated.toLocaleTimeString('tr-TR')} · 15 saniyede bir yenilenir</p>}
    </>}
  </section>
}

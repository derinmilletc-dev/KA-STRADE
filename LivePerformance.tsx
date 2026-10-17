import {useCallback, useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {currentLang} from './i18n'
import {fetchWithTimeout} from './master-trade-request'
import './performance-live.css'

type Row = {id: string; symbol: string; direction?: string; created_at?: string; opened_at?: string; reason?: string; realized_pnl: number; r_multiple?: number | null; price?: number | null; quantity?: number | null}
type Range = {kind: 'all' | 'today' | '7d' | '30d' | 'month' | 'custom'; month?: string; from?: string; to?: string}
type Dir = 'all' | 'LONG' | 'SHORT'
type Result = 'all' | 'win' | 'loss'
const PRESETS: {kind: Range['kind']; label: string}[] = [
  {kind: 'all', label: 'Tümü'}, {kind: 'today', label: 'Bugün'}, {kind: '7d', label: 'Son 7 gün'}, {kind: '30d', label: 'Son 30 gün'},
]
const monthKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
const dayKey = (d: Date) => `${monthKey(d)}-${String(d.getDate()).padStart(2, '0')}`
const monthLabel = (key: string) => { const [y, m] = key.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleDateString(currentLang() === 'en' ? 'en-US' : 'tr-TR', {month: 'long', year: 'numeric'}) }
const fmt = (n: number | null | undefined, d = 2) => typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—'
const sign = (n: number) => `${n >= 0 ? '+' : ''}${fmt(n)}`
const when = (v?: string) => { const t = v ? new Date(v) : null; return t && !Number.isNaN(t.getTime()) ? t.toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—' }

export default function LivePerformance({active, mode = 'live'}: {active: boolean; mode?: 'live' | 'demo'}) {
  const demo = mode === 'demo'
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState('')
  const [range, setRange] = useState<Range>({kind: 'all'})
  const [dir, setDir] = useState<Dir>('all')
  const [result, setResult] = useState<Result>('all')
  const [symbol, setSymbol] = useState('all')
  const [pop, setPop] = useState<'' | 'month' | 'custom'>('')
  const en = currentLang() === 'en'
  const [updated, setUpdated] = useState<Date | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(demo ? `${API_BASE}/v21/summary` : `${API_BASE}/v25/status`, {cache: 'no-store'})
      if (!res.ok) throw new Error(String(res.status))
      const body = await res.json() as {journal?: (Row & {side?: string; verified_realized?: boolean})[]}
      const list = (body.journal ?? [])
        .filter(item => (!demo || item.verified_realized === true) && typeof item.realized_pnl === 'number' && Number.isFinite(item.realized_pnl))
        .map(item => ({...item, direction: item.direction || (String(item.side).toUpperCase() === 'BUY' ? 'LONG' : String(item.side).toUpperCase() === 'SELL' ? 'SHORT' : undefined)}))
      setRows(list); setError(''); setUpdated(new Date())
    } catch {
      setError('İşlem sonuçları şu an okunamadı. Birazdan otomatik olarak tekrar denenecek.')
    }
  }, [demo])
  useEffect(() => {
    if (!active) return
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    const onFocus = () => void load()
    window.addEventListener('focus', onFocus)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', onFocus) }
  }, [active, load])

  const months = useMemo(() => {
    const map = new Map<string, {n: number; net: number}>()
    for (const r of rows ?? []) {
      const t = new Date(r.created_at ?? 0); if (Number.isNaN(t.getTime())) continue
      const k = monthKey(t); const cur = map.get(k) ?? {n: 0, net: 0}
      map.set(k, {n: cur.n + 1, net: cur.net + r.realized_pnl})
    }
    return [...map.entries()].sort((x, y) => y[0].localeCompare(x[0])).map(([key, v]) => ({key, ...v}))
  }, [rows])
  const symbols = useMemo(() => [...new Set((rows ?? []).map(r => r.symbol))].sort(), [rows])

  const view = useMemo(() => {
    const now = new Date()
    const list = (rows ?? []).filter(r => {
      const t = new Date(r.created_at ?? 0)
      if (Number.isNaN(t.getTime())) return range.kind === 'all'
      if (range.kind === 'today' && dayKey(t) !== dayKey(now)) return false
      if (range.kind === '7d' && now.getTime() - t.getTime() > 7 * 86400000) return false
      if (range.kind === '30d' && now.getTime() - t.getTime() > 30 * 86400000) return false
      if (range.kind === 'month' && monthKey(t) !== range.month) return false
      if (range.kind === 'custom') {
        const d = dayKey(t)
        if (range.from && d < range.from) return false
        if (range.to && d > range.to) return false
      }
      if (symbol !== 'all' && r.symbol !== symbol) return false
      if (dir !== 'all' && String(r.direction).toUpperCase() !== dir) return false
      if (result === 'win' && !(r.realized_pnl > 0)) return false
      if (result === 'loss' && !(r.realized_pnl < 0)) return false
      return true
    }).sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime())
    const pnl = list.map(r => r.realized_pnl)
    const wins = pnl.filter(v => v > 0), losses = pnl.filter(v => v < 0)
    const profit = wins.reduce((a, b) => a + b, 0), loss = losses.reduce((a, b) => a + b, 0)
    let eq = 0, peak = 0, dd = 0
    const curve = [...list].reverse().map(r => { eq += r.realized_pnl; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq); return r.realized_pnl })
    return {list, total: list.length, wins: wins.length, losses: losses.length, profit, loss, net: profit + loss, winRate: list.length ? wins.length / list.length * 100 : 0,
      pf: loss < 0 ? profit / Math.abs(loss) : null, avg: list.length ? (profit + loss) / list.length : 0, best: pnl.length ? Math.max(...pnl) : 0, worst: pnl.length ? Math.min(...pnl) : 0, dd, curve}
  }, [rows, range, dir, result, symbol])
  const filtered = range.kind !== 'all' || dir !== 'all' || result !== 'all' || symbol !== 'all'
  const reset = () => { setRange({kind: 'all'}); setDir('all'); setResult('all'); setSymbol('all'); setPop('') }
  const rangeText = range.kind === 'month' && range.month ? monthLabel(range.month)
    : range.kind === 'custom' ? `${range.from || '…'} → ${range.to || '…'}` : ''

  const maxAbs = Math.max(...view.curve.map(Math.abs), 1)
  return <section className="lpWrap">
    <header className="lpHead">
      <div><span>{demo ? 'BINANCE DEMO İŞLEMLERİ' : 'GERÇEK BINANCE İŞLEMLERİ'}</span><h2>{demo ? 'Demo işlem performansı' : 'Canlı işlem performansı'}</h2><p>{demo ? 'Kapanan her demo işlem doğrulandıktan sonra buraya otomatik düşer. Gerçek para kullanılmaz.' : 'Kapanan her canlı işlem Binance’te doğrulandıktan sonra buraya otomatik düşer.'}</p></div>
    </header>
    <div className="lpFilters">
      <div className="lpRow">
        {PRESETS.map(p => <button key={p.kind} type="button" className={range.kind === p.kind ? 'on' : ''} onClick={() => { setRange({kind: p.kind}); setPop('') }}>{p.label}</button>)}
        <button type="button" className={range.kind === 'month' ? 'on' : ''} aria-expanded={pop === 'month'} onClick={() => setPop(pop === 'month' ? '' : 'month')}>{range.kind === 'month' && range.month ? monthLabel(range.month) : 'Ay seç'} ▾</button>
        <button type="button" className={range.kind === 'custom' ? 'on' : ''} aria-expanded={pop === 'custom'} onClick={() => setPop(pop === 'custom' ? '' : 'custom')}>{range.kind === 'custom' ? rangeText : 'Tarih aralığı'} ▾</button>
      </div>
      {pop === 'month' && <div className="lpPop">
        {months.length === 0 && <p>Henüz kapanmış işlem olan bir ay yok.</p>}
        <div className="lpMonths">{months.map(m => <button key={m.key} type="button" className={range.kind === 'month' && range.month === m.key ? 'on' : ''} onClick={() => { setRange({kind: 'month', month: m.key}); setPop('') }}><b>{monthLabel(m.key)}</b><small>{en ? `${m.n} trades` : `${m.n} işlem`}</small><em className={m.net >= 0 ? 'win' : 'loss'}>{sign(m.net)}</em></button>)}</div>
      </div>}
      {pop === 'custom' && <div className="lpPop lpDates">
        <label><span>Başlangıç</span><input type="date" value={range.from ?? ''} max={range.to || undefined} onChange={e => setRange({kind: 'custom', from: e.target.value, to: range.to})}/></label>
        <label><span>Bitiş</span><input type="date" value={range.to ?? ''} min={range.from || undefined} onChange={e => setRange({kind: 'custom', from: range.from, to: e.target.value})}/></label>
        <button type="button" onClick={() => setPop('')}>Uygula</button>
      </div>}
      <div className="lpRow lpSub">
        <div className="lpSeg" role="group">{([['all', 'Hepsi'], ['LONG', 'Long'], ['SHORT', 'Short']] as [Dir, string][]).map(([id, label]) => <button key={id} type="button" className={dir === id ? 'on' : ''} onClick={() => setDir(id)}>{label}</button>)}</div>
        <div className="lpSeg" role="group">{([['all', 'Tüm sonuçlar'], ['win', 'Kazananlar'], ['loss', 'Kaybedenler']] as [Result, string][]).map(([id, label]) => <button key={id} type="button" className={result === id ? 'on' : ''} onClick={() => setResult(id)}>{label}</button>)}</div>
        <select value={symbol} onChange={e => setSymbol(e.target.value)} aria-label="Parite"><option value="all">Tüm pariteler</option>{symbols.map(sym => <option key={sym} value={sym}>{sym.replace('USDT', '/USDT')}</option>)}</select>
        {filtered && <button type="button" className="lpReset" onClick={reset}>Filtreleri temizle</button>}
      </div>
    </div>
    {error && <div className="lpNote">{error}</div>}
    {rows === null && !error && <div className="lpNote">Sonuçlar yükleniyor…</div>}
    {rows !== null && <>
      <div className="lpHero">
        <span><small>Net kâr / zarar</small><b className={view.net >= 0 ? 'win' : 'loss'}>{sign(view.net)} USDT</b><em>{en ? `${view.total} closed trades` : `${view.total} kapanmış işlem`}</em></span>
        <span><small>Kazanma oranı</small><b>{fmt(view.winRate, 1)}%</b><em>{en ? `${view.wins} wins · ${view.losses} losses` : `${view.wins} kazanç · ${view.losses} kayıp`}</em></span>
        <span><small>Kâr faktörü</small><b>{view.pf === null ? '—' : fmt(view.pf)}</b><em>Toplam kâr / toplam zarar</em></span>
        <span><small>En büyük düşüş</small><b className="loss">{fmt(view.dd)} USDT</b><em>Seçili filtre</em></span>
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
        {view.list.length === 0 && filtered && rows.length > 0 && <div className="lpEmpty"><b>Bu filtrede işlem yok</b><p>Filtreleri değiştir veya temizle.</p></div>}
        {view.list.length === 0 && !(filtered && rows.length > 0) && <div className="lpEmpty"><b>{demo ? 'Henüz kapanmış demo işlem yok' : 'Henüz kapanmış canlı işlem yok'}</b><p>{demo ? 'Bir demo işlem kapandığında sonucu burada görünür.' : 'Bir canlı işlem kapandığında sonucu burada görünür.'}</p></div>}
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

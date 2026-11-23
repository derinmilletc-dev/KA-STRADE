// Coinbase işlem geçmişi ve olay akışı: bugün özeti + son olaylar (giriş, stop, hedef, kapanış) + önemli uyarılar.
// Yalnızca gösterim; veri GET /api/coinbase/activity'den gelir (salt okunur). Ekran görünürken 30 sn'de bir yenilenir.
import {useCallback, useEffect, useState} from 'react'
import {Bell, ChevronDown, RefreshCw} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {coinbaseCall} from './coinbase-manual-api'
import {enablePush, pushState, type PushState} from '../../push-client'
import {loadNotifyPrefs} from '../../notification-prefs'
import {groupByDay} from './coinbase-activity-view'

type Item = {time: string; product_id: string; side: string; size: string; price: string; commission: string; pnl: string | null; kind: string; tone: string; title: string}
type Alert = {time: string; product_id: string | null; tone: string; title: string}
type Activity = {items: Item[]; alerts: Alert[]}

const ICON: Record<string, string> = {entry: '↗', target: '🎯', stop: '🛑', protect: '⚠', manual_close: '✓', other: '•'}
const hhmm = (iso: string) => { const d = new Date(iso); return Number.isFinite(d.getTime()) ? `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : '' }
const dayLabel = (iso: string) => {
  const d = new Date(iso), now = new Date()
  if (!Number.isFinite(d.getTime())) return ''
  if (d.toDateString() === now.toDateString()) return 'Bugün'
  const y = new Date(now); y.setDate(now.getDate() - 1)
  return d.toDateString() === y.toDateString() ? 'Dün' : d.toLocaleDateString('tr-TR', {day: 'numeric', month: 'short'})
}
/** Aynı uyarı (aynı başlık, ürün ve gün) tekrar ediyorsa tek satır: en yeni saat ve adet. Liste en yeni önce gelir. */
const groupAlerts = (alerts: Alert[]): Array<Alert & {count: number}> => {
  const seen = new Map<string, Alert & {count: number}>()
  for (const alert of alerts) {
    const key = `${alert.title}|${alert.product_id ?? ''}|${new Date(alert.time).toDateString()}`
    const found = seen.get(key)
    if (found) found.count += 1
    else seen.set(key, {...alert, count: 1})
  }
  return [...seen.values()]
}
const num = (text: string | null | undefined) => { if (text === null || text === undefined || text === '') return null; const n = Number(text); return Number.isFinite(n) ? n : null }
const money = (value: number) => `${value > 0 ? '+' : ''}${value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USD`

export default function CoinbaseActivity({refreshKey, dailyPnl, netToday = null}: {refreshKey: number; dailyPnl: string | null; netToday?: number | null}) {
  const [data, setData] = useState<Activity | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [period, setPeriod] = useState<'today' | 'week'>('today')
  const [push, setPush] = useState<PushState | 'unknown' | 'busy'>('unknown')
  const [pushError, setPushError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try { const next = await coinbaseCall<Partial<Activity>>('/activity'); setData({items: Array.isArray(next?.items) ? next.items : [], alerts: Array.isArray(next?.alerts) ? next.alerts : []}); setError('') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'İşlem geçmişi alınamadı.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    void load()
    const timer = visibleInterval(() => void load(), 30000)
    return () => clearVisibleInterval(timer)
  }, [load, refreshKey])

  useEffect(() => {
    let alive = true
    void pushState().then(state => { if (alive) setPush(state) }).catch(() => { if (alive) setPush('unsupported') })
    return () => { alive = false }
  }, [])

  const enablePushNow = async () => {
    setPush('busy')
    const problem = await enablePush(loadNotifyPrefs())
    setPushError(problem ?? '')
    setPush(problem ? await pushState() : 'on')
  }

  const today = new Date().toDateString()
  const weekAgo = Date.now() - 7 * 86_400_000
  const todayItems = (data?.items ?? []).filter(item => new Date(item.time).toDateString() === today)
  const weekItems = (data?.items ?? []).filter(item => new Date(item.time).getTime() >= weekAgo)
  const isClose = (item: Item) => ['target', 'stop', 'manual_close', 'protect'].includes(item.kind)
  const scope = period === 'today' ? todayItems : weekItems
  const closes = scope.filter(isClose)
  const known = closes.filter(item => num(item.pnl) !== null)
  const wins = known.filter(item => (num(item.pnl) ?? 0) > 0).length
  const weekTotal = known.reduce((sum, item) => sum + (num(item.pnl) ?? 0), 0)
  const best = known.length ? known.reduce((a, b) => (num(b.pnl) ?? 0) > (num(a.pnl) ?? 0) ? b : a) : null
  const worst = known.length ? known.reduce((a, b) => (num(b.pnl) ?? 0) < (num(a.pnl) ?? 0) ? b : a) : null
  const pnlToday = num(dailyPnl)
  // Bugün: komisyon düşülmüş net (hesap kartıyla aynı rakam); sunucu henüz vermediyse Coinbase'in brüt günlük K/Z'si.
  const todayNet = netToday !== null && Number.isFinite(netToday)
  const pnlShown = period === 'today' ? (todayNet ? netToday : pnlToday) : known.length ? weekTotal : null
  const days = groupByDay(data?.items ?? [])
  const groupedAlerts = groupAlerts(data?.alerts ?? [])

  return <section className="cbAct" aria-label="İşlem geçmişi">
    <div className="cbActHead">
      <h3>Son olaylar</h3>
      <button type="button" className="cbActRefresh" onClick={() => void load()} disabled={loading} aria-label="Yenile"><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/></button>
    </div>

    <div className="cbActSeg" role="group" aria-label="Dönem"><button type="button" aria-pressed={period === 'today'} data-on={period === 'today' || undefined} onClick={() => setPeriod('today')}>Bugün</button><button type="button" aria-pressed={period === 'week'} data-on={period === 'week' || undefined} onClick={() => setPeriod('week')}>Bu hafta</button></div>
    {(scope.length > 0 || closes.length > 0) && <dl className="cbActToday" aria-label={period === 'today' ? 'Bugün' : 'Son 7 gün'}>
      <div><dt>{period === 'today' ? (todayNet ? 'Bugün net' : 'Bugün K/Z') : '7 gün K/Z'}</dt><dd data-tone={pnlShown === null || pnlShown === 0 ? undefined : pnlShown > 0 ? 'up' : 'down'}>{pnlShown === null ? '—' : money(pnlShown)}</dd></div>
      <div><dt>İşlem</dt><dd>{scope.length}</dd></div>
      <div><dt>Kapanan</dt><dd>{closes.length ? `${wins} / ${known.length || closes.length} kârlı` : '0'}</dd></div>
    </dl>}
    {period === 'week' && best && worst && known.length > 1 && <p className="cbActBest"><span>En iyi <b data-tone="up">{best.product_id.split('-')[0]} {money(num(best.pnl) ?? 0)}</b></span><span>En kötü <b data-tone={(num(worst.pnl) ?? 0) < 0 ? 'down' : 'up'}>{worst.product_id.split('-')[0]} {money(num(worst.pnl) ?? 0)}</b></span></p>}
    {period === 'week' && <p className="cbActNote">Her pazartesi sabah bu özet telefona da gelir (bildirim açıksa).</p>}

    {push !== 'on' && push !== 'unknown' && push !== 'not-ready' && <div className="cbActPush">
      <Bell aria-hidden="true"/>
      <span><b>Stop ya da hedef çalışınca haber ver</b><small>{pushError || (push === 'ios-install' ? 'iPhone’da önce siteyi Ana Ekrana ekle, sonra oradan aç.' : push === 'denied' ? 'Bildirim izni kapalı. Telefon ayarlarından bu site için izin ver.' : push === 'unsupported' ? 'Bu tarayıcı telefon bildirimini desteklemiyor.' : 'Uygulama kapalıyken de telefona bildirim gelir.')}</small></span>
      {(push === 'off' || push === 'busy') && <button type="button" onClick={() => void enablePushNow()} disabled={push === 'busy'}>{push === 'busy' ? 'Açılıyor…' : 'Bildirimi aç'}</button>}
    </div>}

    {groupedAlerts.length > 0 && <div className="cbActAlertBox" data-open={alertsOpen || undefined}>
      <button type="button" className="cbActAlertBtn" aria-expanded={alertsOpen} onClick={() => setAlertsOpen(value => !value)}>
        <span>⚠ Uyarılar <em>{groupedAlerts.length}</em></span><small>{alertsOpen ? 'Gizle' : 'Göster'}</small><ChevronDown aria-hidden="true"/></button>
      {alertsOpen && <ul className="cbActAlerts" aria-label="Önemli uyarılar">{groupedAlerts.slice(0, 8).map((alert, index) => <li key={index} data-tone={alert.tone}>
        <b>{alert.title}{alert.count > 1 ? ` ×${alert.count}` : ''}</b><small>{alert.product_id ?? ''} · {dayLabel(alert.time)} {hhmm(alert.time)}</small></li>)}</ul>}
    </div>}

    {error && !data && <p className="cbNote warn" role="alert">{error}</p>}
    {data && data.items.length === 0 && <p className="cbActEmpty"><b>Henüz işlem yok</b><small>İlk işlemin, stop ve hedef olayları burada görünür.</small></p>}
    {days.length > 0 && <div className="cbActScroll" tabIndex={0} aria-label="Olay listesi, kaydırılır">{days.map(day => <section key={day.key} className="cbActDay">
      <header className="cbActDayHead"><b>{day.label}</b><small>{day.items.length} olay</small>{day.pnl !== null && <em data-tone={day.pnl > 0 ? 'up' : day.pnl < 0 ? 'down' : undefined}>{money(day.pnl)}</em>}</header>
      <ol className="cbActList">{day.items.map((item, index) => {
        const pnl = num(item.pnl)
        const root = item.product_id.split('-')[0]
        return <li key={`${item.time}-${index}`} data-tone={item.tone} data-kind={item.kind} title={item.product_id}>
          <i aria-hidden="true">{ICON[item.kind] ?? '•'}</i>
          <span className="cbActMain"><b>{item.title}</b><small>{root} · {item.size} sözleşme · {Number(item.price).toLocaleString('tr-TR', {maximumFractionDigits: 6})}</small></span>
          <span className="cbActSide">{pnl !== null ? <em data-tone={pnl > 0 ? 'up' : pnl < 0 ? 'down' : undefined}>{money(pnl)}</em> : <em/>}<small>{hhmm(item.time)}</small></span>
        </li>
      })}</ol>
    </section>)}</div>}
    {data && data.items.some(item => item.pnl !== null) && <p className="cbActNote">{todayNet && period === 'today' ? 'Satırlardaki kâr/zarar komisyonsuzdur; üstteki özet komisyon düşülmüş nettir.' : 'Kâr/zarar komisyon hariç yaklaşık değerdir.'}</p>}
  </section>
}

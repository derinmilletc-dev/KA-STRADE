import {useEffect, useRef} from 'react'
import {Target, TrendingDown, TrendingUp, Wallet} from 'lucide-react'
import {showToast} from '../../toast-center'
import {money, pnlView, type FarmerResultRow, type FarmerStatus} from './farmerView'

type LFn = (tr: string, en: string) => string

const REASON: Record<string, {tr: string; en: string; tone: 'gain' | 'loss' | 'flat'}> = {
  TARGET: {tr: 'Hedef', en: 'Target', tone: 'gain'},
  STOP: {tr: 'Stop', en: 'Stop', tone: 'loss'},
  MARKET_CLOSE: {tr: 'Kapatıldı', en: 'Closed', tone: 'flat'},
  DEAD_CLOSE: {tr: 'Ölü kapanış', en: 'Dead close', tone: 'flat'},
  EMERGENCY: {tr: 'Acil kapanış', en: 'Emergency', tone: 'loss'},
}

function ago(ts: number, L: LFn): string {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 90) return L('az önce', 'just now')
  if (s < 3600) return L(`${Math.round(s / 60)} dk önce`, `${Math.round(s / 60)} min ago`)
  if (s < 86400) return L(`${Math.round(s / 3600)} sa önce`, `${Math.round(s / 3600)} h ago`)
  return L(`${Math.round(s / 86400)} gün önce`, `${Math.round(s / 86400)} d ago`)
}

/** Sonuç özeti: yalnız borsada kanıtlanmış kapanışlar ve doğrulanmış net PnL. Doğrulanmamış tutar sayı gibi gösterilmez ("—"). */
export function ResultsCard({status, L}: {status: FarmerStatus; L: LFn}) {
  const sum = status.summary
  const rows = (status.recent_results ?? []).filter(r => r.closed_at > 0).slice(-5).reverse()
  if (!sum) return null
  const empty = sum.closed_count === 0
  const decided = sum.target_count + sum.stop_count
  const net = pnlView(sum.verified_count > 0 ? 'VERIFIED' : 'PENDING', sum.verified_net_pnl)
  const unverified = sum.closed_count - sum.verified_count
  return <section className="fmCard fmRes" data-testid="farmer-results" aria-label={L('Sonuçların', 'Your results')}>
    <header className="fmResHead"><h3>{L('Sonuçların', 'Your results')}</h3>{!empty && <span>{L(`${sum.closed_count} işlem`, `${sum.closed_count} trades`)}{sum.win_rate !== null && <> · <b>{L(`%${Math.round(sum.win_rate * 100)} hedefe`, `${Math.round(sum.win_rate * 100)}% target`)}</b></>}</span>}</header>
    {empty
      ? <p className="fmResEmpty">{L('Henüz kapanan işlem yok. İlk işlem kapanınca sonuçların burada görünecek.', 'No closed trades yet. Your results will appear here after the first trade closes.')}</p>
      : <>
        <div className="fmResNet" data-sign={net.sign ?? undefined}>
          <small>{L('Toplam net (doğrulanmış)', 'Total net (verified)')}</small>
          <b data-testid="farmer-results-net">{net.text === '—' ? '—' : money(sum.verified_net_pnl, true)}</b>
          {unverified > 0 && <em>{L(`${unverified} sonucun net tutarı doğrulanıyor`, `${unverified} result${unverified === 1 ? ' is' : 's are'} being verified`)}</em>}
        </div>
        {decided > 0 && <div className="fmResBalance" aria-label={L('Hedef ve stop dağılımı', 'Target versus stop split')}>
          <div className="fmResBar"><i data-k="gain" style={{flexGrow: sum.target_count}}/><i data-k="loss" style={{flexGrow: sum.stop_count}}/></div>
          <div className="fmResBarTx">
            <span data-k="gain"><Target aria-hidden="true"/>{L('Hedefe giden', 'Hit target')} <b>{sum.target_count}</b></span>
            <span data-k="loss"><TrendingDown aria-hidden="true"/>{L('Stop olan', 'Stopped')} <b>{sum.stop_count}</b></span>
          </div>
        </div>}
        <ul className="fmResTiles">
          <li data-k="fee"><Wallet aria-hidden="true"/><small>{L('Ücret', 'Fees')}</small><b>{sum.commission_total === null ? '—' : money(sum.commission_total)}</b></li>
          <li data-k="best"><TrendingUp aria-hidden="true"/><small>{L('En iyi', 'Best')}</small><b>{sum.best_pnl === null ? '—' : money(sum.best_pnl, true)}</b></li>
          <li data-k="worst"><TrendingDown aria-hidden="true"/><small>{L('En kötü', 'Worst')}</small><b>{sum.worst_pnl === null ? '—' : money(sum.worst_pnl, true)}</b></li>
        </ul>
        {rows.length > 0 && <ul className="fmResList" aria-label={L('Son işlemler', 'Recent trades')}>{rows.map(r => <ResultRow key={r.grid_id} r={r} L={L}/>)}</ul>}
      </>}
  </section>
}

function ResultRow({r, L}: {r: FarmerResultRow; L: LFn}) {
  const why = REASON[r.close_reason] ?? {tr: 'Kapandı', en: 'Closed', tone: 'flat' as const}
  const v = pnlView(r.pnl_state, r.verified_net_pnl)
  return <li data-tone={why.tone}>
    <span className="fmResSym"><b>{r.symbol.replace('USDT', '')}</b><i data-side={r.side}>{r.side === 'LONG' ? 'LONG' : 'SHORT'}</i></span>
    <span className="fmResWhy" data-tone={why.tone}>{L(why.tr, why.en)}</span>
    <span className="fmResAmt" data-sign={v.sign ?? undefined}><b>{v.text}</b><small>{v.kind === 'verified' ? ago(r.closed_at, L) : L('doğrulanıyor', 'verifying')}</small></span>
  </li>
}

/** Her işlem açılınca / kapanınca ve kritik durum değişince bildirim (üstte kayan toast). İlk yüklemede mevcut durum bildirilmez (tohumlanır). */
export function useFarmerNotifications(status: FarmerStatus | null, L: LFn): void {
  const seen = useRef<{ready: boolean; open: Set<string>; closed: Set<string>; state: string; events: Map<string, number>}>(
    {ready: false, open: new Set(), closed: new Set(), state: '', events: new Map()})
  useEffect(() => {
    if (!status || !status.available) return
    const s = seen.current
    const closedRows = (status.recent_results ?? []).filter(r => r.closed_at > 0)
    const now = Date.now()
    if (!s.ready) {
      status.positions.forEach(p => s.open.add(p.grid_id))
      closedRows.forEach(r => s.closed.add(r.grid_id))
      ;(status.events ?? []).forEach(e => s.events.set(e, now))
      s.state = status.state
      s.ready = true
      return
    }
    for (const p of status.positions) {
      if (s.open.has(p.grid_id)) continue
      s.open.add(p.grid_id)
      const name = p.symbol.replace('USDT', '')
      showToast({tone: 'info', title: L(`${name} ${p.side} açıldı`, `${name} ${p.side} opened`),
        body: L(`Hedef ${money(p.target_usdt, true)} · en fazla ${money(p.max_loss_usdt === null ? null : -Math.abs(p.max_loss_usdt))} · stop borsada.`,
          `Target ${money(p.target_usdt, true)} · max ${money(p.max_loss_usdt === null ? null : -Math.abs(p.max_loss_usdt))} · stop is on the exchange.`)})
    }
    for (const r of closedRows) {
      if (s.closed.has(r.grid_id)) continue
      s.closed.add(r.grid_id)
      const name = r.symbol.replace('USDT', '')
      const why = REASON[r.close_reason]
      const tone = why?.tone === 'gain' ? 'ok' : why?.tone === 'loss' ? 'bad' : 'warn'
      const title = r.close_reason === 'TARGET' ? L(`${name} hedefe ulaştı`, `${name} hit its target`)
        : r.close_reason === 'STOP' ? L(`${name} stop oldu`, `${name} was stopped out`)
        : L(`${name} kapandı`, `${name} closed`)
      showToast({tone, title, body: L('Net sonuç doğrulanınca “Sonuçların” kartında görünür.', 'The net result appears in “Your results” once verified.')})
    }
    if (status.state !== s.state) {
      const prev = s.state
      s.state = status.state
      if (status.state === 'INTERVENTION_REQUIRED') showToast({tone: 'bad', title: L('Müdahale gerekiyor', 'Attention required'), body: L('KAIS FARMER bir pozisyonu güvenle yönetemedi. Farmer ekranını aç.', 'KAIS FARMER could not manage a position safely. Open the Farmer screen.'), duration: 15000})
      else if (status.state === 'ENTRY_PAUSED_SAFETY' && prev !== 'ENTRY_PAUSED_SAFETY') showToast({tone: 'warn', title: L('Yeni işlem durdu', 'New entries paused'),
        body: status.why.code === 'DAILY_LOSS' ? L('Günlük zarar limiti doldu. Açık pozisyonların stopu borsada korunuyor.', 'The daily loss limit is reached. Open positions stay protected.') : L('Bir güvenlik kapısı yeni girişi durdurdu.', 'A safety gate paused new entries.')})
      else if (status.state === 'ENTRY_PAUSED_ARM' && prev !== 'ENTRY_PAUSED_ARM') showToast({tone: 'warn', title: L('İşlem oturumu bitti', 'Trading session ended'), body: L('Yeni işlem açılmaz. Oturumu yenile. Açık pozisyonlar korunur.', 'No new trades. Renew the session. Open positions stay protected.')})
    }
    for (const e of status.events ?? []) {
      const at = s.events.get(e)
      if (at !== undefined && now - at < 15 * 60_000) continue          // aynı uyarı 15 dk içinde tekrar bildirilmez
      s.events.set(e, now)
      showToast({tone: 'warn', title: L('KAIS FARMER uyarısı', 'KAIS FARMER notice'), body: e})
    }
  }, [status, L])
}

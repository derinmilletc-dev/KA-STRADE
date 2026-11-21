import {useState} from 'react'
import {AlertTriangle, ShieldAlert} from 'lucide-react'
import {CONFIRM, isPositiveDecimal, newRequestId, protectionTone} from './coinbase-manual'
import {positionView} from './coinbase-position'
import {CoinIcon} from '../../src/components/CoinIcon'
import {sameConfirm} from './confirm-text'
import {CoinbaseApiError, postJson} from './coinbase-manual-api'

export type CbPosition = {product_id: string; side: string | null; number_of_contracts: string | null; avg_entry_price: string | null; current_price: string | null; unrealized_pnl: string | null; expiration_time: string | null}
export type CbProtectionItem = {product_id: string; side: string; contracts: number; state: string; message: string; stop_price: string | null; take_profit_price?: string | null}
export type CbProtection = {positions: CbProtectionItem[]; orphan_stops: {order_id: string; product_id: string}[]; open_order_count: number; all_protected: boolean; has_problem: boolean}
type Props = {positions: CbPosition[]; protection: CbProtection | null; ordersEnabled: boolean; onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void}

const sideOf = (raw: string | null) => raw?.toUpperCase().endsWith('LONG') ? 'BUY' : raw?.toUpperCase().endsWith('SHORT') ? 'SELL' : raw || '—'

type Act = 'close' | 'half' | 'breakeven' | 'stop' | 'reduce'
const num = (value: string | null | undefined) => { const n = Number(value); return value != null && value !== '' && Number.isFinite(n) ? n : null }
const px = (value: number | null) => value === null ? '—' : value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2})
const plain = (value: number) => String(Number(value.toPrecision(8)))
const hoursLeft = (ms: number) => ms <= 0 ? 'vadesi doldu' : ms < 86_400_000 ? `${Math.max(1, Math.round(ms / 3_600_000))} saat sonra kapanıyor` : `${Math.floor(ms / 86_400_000)} gün sonra kapanıyor`

function PositionRow({position, item, ordersEnabled, onDone, onError}: {position: CbPosition; item?: CbProtectionItem; ordersEnabled: boolean; onDone: Props['onDone']; onError: Props['onError']}) {
  const [act, setAct] = useState<Act | null>(null)
  const [value, setValue] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false)
  const view = positionView(position, item)
  const tone = item ? protectionTone(item.state) : 'warn'
  const pnl = num(position.unrealized_pnl)
  const root = position.product_id.split('-')[0]
  const expected = act === 'close' ? CONFIRM.close : CONFIRM.change
  const valueOk = act === 'close' || act === 'half' || act === 'breakeven' || (act === 'reduce' ? /^[1-9]\d{0,5}$/.test(value.trim()) : act === 'stop' ? isPositiveDecimal(value) : false)
  const canSend = !busy && act !== null && valueOk && sameConfirm(confirm, expected)
  const open = (next: Act) => { setAct(current => current === next ? null : next); setValue(''); setConfirm('') }

  const send = async () => {
    if (!act) return
    setBusy(true)
    try {
      const base = {product_id: position.product_id, confirm: expected, request_id: newRequestId()}
      const path = act === 'half' || act === 'reduce' ? 'reduce' : act === 'breakeven' || act === 'stop' ? 'stop' : 'close'
      const body = act === 'half' ? {...base, reduce_by: view.half} : act === 'reduce' ? {...base, reduce_by: Number(value.trim())}
        : act === 'breakeven' ? {...base, stop_price: plain(view.breakeven as number)} : act === 'stop' ? {...base, stop_price: value.trim()} : base
      const result = await postJson<{message: string}>(`/orders/${path}`, body)
      setAct(null); setValue(''); setConfirm('')
      onDone(result.message)
    } catch (error) {
      setAct(null); setValue(''); setConfirm('')
      if (error instanceof CoinbaseApiError) onError(error); else throw error
    } finally { setBusy(false) }
  }

  const sheet: Record<Act, {title: string; text: string; button: string}> = {
    breakeven: {title: 'Stopu girişe çek', text: `Stop ${px(view.breakeven)} olur (giriş + komisyon payı). Fiyat geri dönse bile yaklaşık başa baş kapanır. Hedef aynı kalır.`, button: 'Stopu girişe çek'},
    half: {title: 'Yarısını kapat', text: `${view.half ?? 0} sözleşme piyasa fiyatından kapanır, kalan ${view.contracts - (view.half ?? 0)} sözleşme aynı stop ve hedefle korunur.`, button: 'Yarısını kapat'},
    close: {title: 'Pozisyonu kapat', text: `${view.contracts} sözleşmenin tamamı piyasa fiyatından kapanır, stop ve hedef emirleri silinir.`, button: 'Pozisyonu kapat'},
    stop: {title: 'Stopu taşı', text: 'Stop yalnızca pozisyona yaklaştırılabilir; kaybı büyütecek bir yere taşınamaz.', button: 'Stopu uygula'},
    reduce: {title: 'Azalt', text: `1 ile ${Math.max(1, view.contracts - 1)} arasında sözleşme kapatılır, kalan kısım korunur.`, button: 'Azalt'},
  }

  return <li className="cbPos2" data-tone={tone} data-dir={view.dir ?? undefined}>
    <div className="cbPos2Head">
      <span className="cbPos2Logo" aria-hidden="true"><CoinIcon symbol={`${root}USDT`} size={36}/></span>
      <span className="cbPos2Name"><b>{root} <em data-dir={view.dir ?? undefined}>{view.dir ?? '—'}</em></b><small>{position.product_id} · {view.contracts} sözleşme</small></span>
      <span className="cbPos2Pnl"><b data-tone={pnl === null || pnl === 0 ? undefined : pnl > 0 ? 'up' : 'down'}>{pnl === null ? '—' : `${pnl > 0 ? '+' : ''}${pnl.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USD`}</b>
        <small>{view.movePct === null ? '' : `${view.movePct >= 0 ? '+' : ''}${view.movePct.toFixed(2)}% girişten`}</small></span>
    </div>

    {view.bar && <div className="cbPos2Bar" role="img" aria-label={`Stop ${px(view.stop)}, giriş ${px(view.entry)}, güncel ${px(view.current)}, hedef ${px(view.target)}`}>
      <div className="cbPos2Track">
        {view.bar.stop !== null && view.bar.entry !== null && <i className="cbPos2Risk" style={{left: `${Math.min(view.bar.stop, view.bar.entry)}%`, width: `${Math.abs(view.bar.entry - view.bar.stop)}%`}}/>}
        {view.bar.target !== null && view.bar.entry !== null && <i className="cbPos2Gain" style={{left: `${Math.min(view.bar.target, view.bar.entry)}%`, width: `${Math.abs(view.bar.target - view.bar.entry)}%`}}/>}
        {view.bar.entry !== null && <i className="cbPos2Tick" data-k="entry" style={{left: `${view.bar.entry}%`}}/>}
        {view.bar.current !== null && <i className="cbPos2Now" style={{left: `${view.bar.current}%`}}/>}
      </div>
      <div className="cbPos2Labels">
        <span data-k="stop"><small>Stop</small><b>{px(view.stop)}</b></span>
        <span data-k="entry"><small>Giriş</small><b>{px(view.entry)}</b></span>
        <span data-k="now"><small>Güncel</small><b>{px(view.current)}</b></span>
        <span data-k="target"><small>Hedef</small><b>{px(view.target)}</b></span>
      </div>
    </div>}

    <div className={`cbPos2Guard ${tone}`}>{item ? (item.state === 'protected' ? <>🛡 Korumalı{view.stop !== null && view.entry !== null && view.dir && (view.stop - view.entry) * (view.dir === 'LONG' ? 1 : -1) >= 0 ? ' · stop kârda' : ''}</> : item.message) : ordersEnabled ? 'Koruma durumu okunamadı' : 'Emir gönderme kapalı; koruma bilgisi yok'}
      {view.expiryMs !== null && <span className="cbPos2Exp" data-soon={view.expiryMs < 86_400_000 || undefined}>⏳ {hoursLeft(view.expiryMs)}</span>}</div>

    {ordersEnabled && <div className="cbPos2Acts">
      {item?.state === 'UNPROTECTED'
        ? <button type="button" data-kind="primary" onClick={() => open('stop')}>Stop koy</button>
        : <button type="button" data-kind="primary" disabled={!view.canBreakeven} title={view.canBreakeven ? undefined : view.breakevenWhy} onClick={() => open('breakeven')}>Stopu girişe çek</button>}
      {view.half !== null && <button type="button" onClick={() => open('half')}>Yarısını kapat</button>}
      <button type="button" data-kind="danger" onClick={() => open('close')}>Kapat</button>
      <button type="button" data-kind="more" aria-expanded={more} onClick={() => setMore(value => !value)}>{more ? 'Daha az' : 'Diğer'}</button>
    </div>}
    {ordersEnabled && !view.canBreakeven && item?.state !== 'UNPROTECTED' && !act && <p className="cbPos2Why">{view.breakevenWhy}</p>}
    {ordersEnabled && more && <div className="cbPos2More">
      <button type="button" onClick={() => open('stop')}>Stopu elle taşı</button>
      {view.contracts > 1 && <button type="button" onClick={() => open('reduce')}>Belirli miktar azalt</button>}
    </div>}

    {act && <div className="cbPos2Sheet" data-danger={act === 'close' || undefined}>
      <b>{sheet[act].title}</b>
      <p>{sheet[act].text}</p>
      {(act === 'stop' || act === 'reduce') && <label>{act === 'reduce' ? 'Kaç sözleşme?' : 'Yeni stop fiyatı'}<input value={value} onChange={event => setValue(event.target.value)} inputMode="decimal" autoComplete="off" placeholder={act === 'stop' ? px(view.stop) : '1'}/></label>}
      <label>Onaylamak için yaz: <code>{expected}</code><input value={confirm} onChange={event => setConfirm(event.target.value)} autoComplete="off" placeholder={expected}/></label>
      <div className="cbPos2SheetBtns"><button type="button" onClick={() => setAct(null)} disabled={busy}>Vazgeç</button><button type="button" data-kind={act === 'close' ? 'danger' : 'primary'} disabled={!canSend} onClick={() => void send()}>{busy ? 'Gönderiliyor…' : sheet[act].button}</button></div>
    </div>}
  </li>
}

export default function CoinbaseManualPositions({positions, protection, ordersEnabled, onDone, onError}: Props) {
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const orphans = protection?.orphan_stops ?? []

  const cleanup = async () => {
    setBusy(true)
    try {
      const result = await postJson<{message: string}>('/orders/cleanup-orphans', {confirm: CONFIRM.change, request_id: newRequestId()})
      setConfirm(''); onDone(result.message)
    } catch (error) { setConfirm(''); if (error instanceof CoinbaseApiError) onError(error); else throw error } finally { setBusy(false) }
  }

  return <section className="cbCard" data-empty={positions.length === 0 && orphans.length === 0 && !protection?.has_problem || undefined} aria-label="Pozisyonlar ve koruma">
    {(positions.length > 0 || orphans.length > 0 || protection?.has_problem) && <h3><ShieldAlert/>Pozisyonlar ve koruma</h3>}
    {positions.length > 0 && <div className="cbPosSum" aria-label="Pozisyon özeti">
      <div><small>Açık</small><b>{positions.length}</b></div>
      <div><small>Long</small><b data-tone="up">{positions.filter(item => sideOf(item.side) === 'BUY').length}</b></div>
      <div><small>Short</small><b data-tone="down">{positions.filter(item => sideOf(item.side) === 'SELL').length}</b></div>
    </div>}
    {protection && positions.length > 0 && !protection.all_protected && <p className="cbNote warn" role="alert">Korumasız pozisyon var; aşağıdan düzelt.</p>}
    {positions.length === 0 && <p className="cbPosEmpty"><ShieldAlert aria-hidden="true"/><span>Açık pozisyon yok</span><small>Stop zorunlu · en fazla 5 emir</small></p>}
    {protection?.has_problem && <p className="cbNote bad" role="alert"><AlertTriangle/>Koruma sorunu var. Aşağıdaki kırmızı/sarı satırları düzelt.</p>}
    <ul className="cbPositions cbPos2List">{positions.map(position => <PositionRow key={position.product_id} position={position} ordersEnabled={ordersEnabled}
      item={protection?.positions.find(entry => entry.product_id === position.product_id)} onDone={onDone} onError={onError}/>)}</ul>
    {orphans.length > 0 && <div className="cbNote warn" role="alert">
      <b>Sahipsiz stop emri var ({orphans.length}).</b> Pozisyon yokken kalan stop, ters yönde pozisyon açabilir.
      <ul>{orphans.map(entry => <li key={entry.order_id}>{entry.product_id} · {entry.order_id}</li>)}</ul>
      <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.change}`} autoComplete="off"/></label>
      <button className="danger" disabled={busy || !sameConfirm(confirm, CONFIRM.change)} onClick={cleanup}>Sahipsiz stop'ları iptal et</button>
    </div>}
  </section>
}

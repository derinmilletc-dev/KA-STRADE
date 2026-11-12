import {useState} from 'react'
import {AlertTriangle, ShieldAlert} from 'lucide-react'
import {CONFIRM, confirmMatches, isPositiveDecimal, newRequestId, protectionTone, sideLabel} from './coinbase-manual'
import {CoinbaseApiError, postJson} from './coinbase-manual-api'

export type CbPosition = {product_id: string; side: string | null; number_of_contracts: string | null; avg_entry_price: string | null; current_price: string | null; unrealized_pnl: string | null; expiration_time: string | null}
export type CbProtectionItem = {product_id: string; side: string; contracts: number; state: string; message: string; stop_price: string | null}
export type CbProtection = {positions: CbProtectionItem[]; orphan_stops: {order_id: string; product_id: string}[]; open_order_count: number; all_protected: boolean; has_problem: boolean}
type Props = {positions: CbPosition[]; protection: CbProtection | null; ordersEnabled: boolean; onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void}
type Mode = 'close' | 'reduce' | 'stop' | null

const sideOf = (raw: string | null) => raw?.toUpperCase().endsWith('LONG') ? 'BUY' : raw?.toUpperCase().endsWith('SHORT') ? 'SELL' : raw || '—'

function PositionRow({position, item, ordersEnabled, onDone, onError}: {position: CbPosition; item?: CbProtectionItem; ordersEnabled: boolean; onDone: Props['onDone']; onError: Props['onError']}) {
  const [mode, setMode] = useState<Mode>(null)
  const [value, setValue] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const tone = item ? protectionTone(item.state) : 'warn'
  const expected = mode === 'close' ? CONFIRM.close : CONFIRM.change
  const valueOk = mode === 'close' || (mode === 'reduce' ? /^[1-9]\d{0,5}$/.test(value.trim()) : mode === 'stop' ? isPositiveDecimal(value) : false)
  const canSend = !busy && mode !== null && valueOk && confirmMatches(confirm, expected)

  const send = async () => {
    setBusy(true)
    try {
      const base = {product_id: position.product_id, confirm, request_id: newRequestId()}
      const result = await postJson<{message: string}>(`/orders/${mode}`,
        mode === 'reduce' ? {...base, reduce_by: Number(value.trim())} : mode === 'stop' ? {...base, stop_price: value.trim()} : base)
      setMode(null); setValue(''); setConfirm('')
      onDone(result.message)
    } catch (error) {
      setMode(null); setValue(''); setConfirm('')
      if (error instanceof CoinbaseApiError) onError(error); else throw error
    } finally { setBusy(false) }
  }

  return <li className={`cbPosition ${tone}`}>
    <div className="cbPositionHead">
      <b>{position.product_id}</b><span>{sideLabel(sideOf(position.side))}</span><span>{position.number_of_contracts ?? '—'} sözleşme</span>
      <span>Giriş {position.avg_entry_price ?? '—'}</span><span>Güncel {position.current_price ?? '—'}</span><span>K/Z {position.unrealized_pnl ?? '—'}</span>
    </div>
    <div className={`cbBadge ${tone}`}>{item ? (item.state === 'protected' ? `Stop var${item.stop_price ? ` (${item.stop_price})` : ''}` : item.message) : ordersEnabled ? 'Koruma durumu okunamadı' : 'Emir gönderme kapalı; koruma bilgisi yok'}</div>
    {ordersEnabled && <div className="cbButtons">
      <button onClick={() => setMode(mode === 'stop' ? null : 'stop')}>{item?.state === 'UNPROTECTED' ? 'Stop koy' : 'Stop taşı'}</button>
      <button onClick={() => setMode(mode === 'reduce' ? null : 'reduce')}>Azalt</button>
      <button className="danger" onClick={() => setMode(mode === 'close' ? null : 'close')}>Kapat</button>
    </div>}
    {mode && <div className="cbInline">
      {mode !== 'close' && <label>{mode === 'reduce' ? 'Kaç sözleşme azaltılsın?' : 'Yeni stop fiyatı (yalnızca pozisyona yaklaştırılır)'}<input value={value} onChange={e => setValue(e.target.value)} inputMode="decimal" autoComplete="off"/></label>}
      <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${expected}`} autoComplete="off"/></label>
      <button className={mode === 'close' ? 'danger' : 'primary'} disabled={!canSend} onClick={send}>{mode === 'close' ? 'Pozisyonu kapat' : mode === 'reduce' ? 'Azalt' : 'Stop\'u uygula'}</button>
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
      const result = await postJson<{message: string}>('/orders/cleanup-orphans', {confirm, request_id: newRequestId()})
      setConfirm(''); onDone(result.message)
    } catch (error) { setConfirm(''); if (error instanceof CoinbaseApiError) onError(error); else throw error } finally { setBusy(false) }
  }

  return <section className="cbCard" aria-label="Pozisyonlar ve koruma">
    <h3><ShieldAlert/>Pozisyonlar ve koruma</h3>
    {positions.length === 0 && <p className="cbNote">Açık pozisyon yok.</p>}
    {protection?.has_problem && <p className="cbNote bad" role="alert"><AlertTriangle/>Koruma sorunu var. Aşağıdaki kırmızı/sarı satırları düzelt.</p>}
    <ul className="cbPositions">{positions.map(position => <PositionRow key={position.product_id} position={position} ordersEnabled={ordersEnabled}
      item={protection?.positions.find(entry => entry.product_id === position.product_id)} onDone={onDone} onError={onError}/>)}</ul>
    {orphans.length > 0 && <div className="cbNote warn" role="alert">
      <b>Sahipsiz stop emri var ({orphans.length}).</b> Pozisyon yokken kalan stop, ters yönde pozisyon açabilir.
      <ul>{orphans.map(entry => <li key={entry.order_id}>{entry.product_id} · {entry.order_id}</li>)}</ul>
      <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.change}`} autoComplete="off"/></label>
      <button className="danger" disabled={busy || !confirmMatches(confirm, CONFIRM.change)} onClick={cleanup}>Sahipsiz stop'ları iptal et</button>
    </div>}
  </section>
}

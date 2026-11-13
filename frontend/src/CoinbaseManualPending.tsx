import {useCallback, useEffect, useRef, useState} from 'react'
import {Clock} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {CONFIRM, newRequestId} from './coinbase-manual'
import {sameConfirm} from './confirm-text'
import {CoinbaseApiError, postJson} from './coinbase-manual-api'

type Pending = {order_id: string; product_id: string; side: string; status: string; base_size?: string | null}
type Reconcile = {fixed: string[]; notes: string[]; pending: Pending[]}
type Props = {onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void}

// Bekleyen limit girişleri. Ekran açıkken 10 sn'de bir "reconcile" çağrılır: dolmuş ama korumasız pozisyon varsa
// korur ya da kapatır (sessiz kalmaz, sonuç mesajla görünür).
export default function CoinbaseManualPending({onDone, onError}: Props) {
  const [pending, setPending] = useState<Pending[]>([])
  const [target, setTarget] = useState<string | null>(null)
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const running = useRef(false)

  const reconcile = useCallback(async () => {
    if (running.current) return
    running.current = true
    try {
      const result = await postJson<Reconcile>('/orders/reconcile', {})
      setPending(result.pending)
      if (result.fixed.length) onDone(`Dolan emir korundu: ${result.fixed.join(', ')}. ${result.notes.join(' ')}`.trim())
      else if (result.notes.length) onDone(result.notes.join(' '))
    } catch (error) {
      if (error instanceof CoinbaseApiError) onError(error); else throw error
    } finally { running.current = false }
  }, [onDone, onError])

  useEffect(() => {
    void reconcile()
    const timer = visibleInterval(() => void reconcile(), 10000)
    return () => clearVisibleInterval(timer)
  }, [reconcile])

  const cancel = async () => {
    if (!target) return
    setBusy(true)
    try {
      const result = await postJson<{message: string}>('/orders/cancel-entry', {order_id: target, confirm: CONFIRM.change, request_id: newRequestId()})
      setTarget(null); setConfirm(''); onDone(result.message); void reconcile()
    } catch (error) {
      setTarget(null); setConfirm('')
      if (error instanceof CoinbaseApiError) onError(error); else throw error
    } finally { setBusy(false) }
  }

  if (pending.length === 0) return null
  return <section className="cbCard" aria-label="Bekleyen girişler">
    <h3><Clock/>Bekleyen limit girişler</h3>
    <ul className="cbPositions">{pending.map(order => <li key={order.order_id} className="cbPosition warn">
      <div className="cbPositionHead"><b>{order.product_id}</b><span>{order.side === 'BUY' ? 'LONG' : 'SHORT'}</span><span>{order.base_size ?? '—'} sözleşme</span><span>{order.status}</span></div>
      <div className="cbButtons"><button className="danger" onClick={() => setTarget(target === order.order_id ? null : order.order_id)}>İptal et</button></div>
      {target === order.order_id && <div className="cbInline">
        <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.change}`} autoComplete="off"/></label>
        <button className="danger" disabled={busy || !sameConfirm(confirm, CONFIRM.change)} onClick={cancel}>Bekleyen emri iptal et</button>
      </div>}
    </li>)}</ul>
  </section>
}

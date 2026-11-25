import {useCallback, useEffect, useRef, useState} from 'react'
import {Clock, Hourglass, Target} from 'lucide-react'
import {useCoinbaseTicker} from './useCoinbaseTicker'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import {CONFIRM, newRequestId} from './coinbase-manual'
import {sameConfirm} from './confirm-text'
import {CoinbaseApiError, postJson} from './coinbase-manual-api'

type PendingPlan = {entry_limit_price?: string | null; stop_price?: string | null; take_profit_price?: string | null; contracts?: string | null; leverage?: string | null}
type Pending = {order_id: string; product_id: string; side: string; status: string; base_size?: string | null; plan?: PendingPlan}
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
  const [editing, setEditing] = useState<string | null>(null)
  const [stop, setStop] = useState('')
  const [tp, setTp] = useState('')
  const [profit, setProfit] = useState('')
  const [editConfirm, setEditConfirm] = useState('')
  const ids = pending.map(order => order.product_id).join(',')
  const ticker = useCoinbaseTicker(ids === '' ? [] : ids.split(','), ids !== '')

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

  const startEdit = (order: Pending) => {
    setEditing(order.order_id); setTarget(null); setEditConfirm('')
    setStop(order.plan?.stop_price ?? ''); setTp(order.plan?.take_profit_price ?? ''); setProfit('')
  }
  const positive = (value: string) => { const n = Number(value.replace(',', '.')); return Number.isFinite(n) && n > 0 ? value.replace(',', '.').trim() : undefined }
  const save = async (order: Pending) => {
    const body: Record<string, string> = {}
    const s = positive(stop), t = positive(tp), p = positive(profit)
    if (s && s !== order.plan?.stop_price) body.stop_price = s
    if (p) body.profit_usd = p
    else if (t && t !== order.plan?.take_profit_price) body.take_profit_price = t
    if (Object.keys(body).length === 0) { onDone('Değişen bir seviye yok.'); return }
    setBusy(true)
    try {
      const result = await postJson<{message: string}>('/orders/replace-entry', {order_id: order.order_id, product_id: order.product_id, confirm: CONFIRM.change, request_id: newRequestId(), ...body})
      setEditing(null); setEditConfirm(''); onDone(result.message); void reconcile()
    } catch (error) {
      setEditing(null); setEditConfirm(''); void reconcile()
      if (error instanceof CoinbaseApiError) onError(error); else throw error
    } finally { setBusy(false) }
  }

  if (pending.length === 0) return null
  return <section className="cbCard" aria-label="Bekleyen girişler">
    <h3><Clock/>Bekleyen limit girişler</h3>
    <ul className="cbPositions">{pending.map(order => {
      const plan = order.plan
      const entry = Number(plan?.entry_limit_price), price = ticker.quotes[order.product_id]?.price
      const away = Number.isFinite(entry) && entry > 0 && typeof price === 'number' && price > 0 ? (entry - price) / price * 100 : null
      const open = editing === order.order_id
      return <li key={order.order_id} className="cbPosition warn" data-testid="cb-pending-entry">
        <div className="cbPositionHead"><b>{order.product_id}</b><span>{order.side === 'BUY' ? 'LONG' : 'SHORT'}</span><span>{order.base_size ?? plan?.contracts ?? '—'} sözleşme</span><span><Hourglass aria-hidden="true"/> Fiyata ulaşması bekleniyor</span></div>
        {plan && <div className="cbPendingLevels">
          <span><small>Emir fiyatı</small><b>{plan.entry_limit_price ?? '—'}</b></span>
          <span><small>Güncel</small><b>{typeof price === 'number' ? price.toLocaleString('tr-TR', {maximumFractionDigits: 6}) : '—'}</b></span>
          <span><small>Uzaklık</small><b>{away === null ? '—' : `${away > 0 ? '+' : ''}${away.toLocaleString('tr-TR', {maximumFractionDigits: 2})}%`}</b></span>
          <span><small>Stop</small><b>{plan.stop_price ?? '—'}</b></span>
          <span><small>Hedef</small><b>{plan.take_profit_price ?? '—'}</b></span>
        </div>}
        {!open && <div className="cbButtons">
          {plan && <button onClick={() => startEdit(order)}>Stop / kâr hedefi</button>}
          <button className="danger" onClick={() => { setEditing(null); setTarget(target === order.order_id ? null : order.order_id) }}>İptal et</button>
        </div>}
        {open && <div className="cbInline">
          <label>Stop fiyatı<input inputMode="decimal" value={stop} onChange={e => setStop(e.target.value)}/></label>
          <label><Target aria-hidden="true"/> Şu kadar kâr olunca çık (USD, net)<input inputMode="decimal" placeholder="boş bırak: hedef fiyatı kullan" value={profit} onChange={e => setProfit(e.target.value)}/></label>
          {positive(profit) === undefined && <label>Hedef fiyatı<input inputMode="decimal" value={tp} onChange={e => setTp(e.target.value)}/></label>}
          <p className="mxtHint">Coinbase'de stop/hedef emre eklidir: kaydedince emir iptal edilip (doğrulanır) aynı fiyatla, yeni seviyelerle yeniden verilir. Ön kontrol yeniden çalışır; geçmezse emir açık kalmaz.</p>
          <label>Onay metni<input value={editConfirm} onChange={e => setEditConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.change}`} autoComplete="off"/></label>
          <div className="cbButtons">
            <button disabled={busy || !sameConfirm(editConfirm, CONFIRM.change)} onClick={() => void save(order)}>Kaydet</button>
            <button onClick={() => setEditing(null)}>Vazgeç</button>
          </div>
        </div>}
        {target === order.order_id && <div className="cbInline">
          <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.change}`} autoComplete="off"/></label>
          <button className="danger" disabled={busy || !sameConfirm(confirm, CONFIRM.change)} onClick={cancel}>Bekleyen emri iptal et</button>
        </div>}
      </li>
    })}</ul>
  </section>
}

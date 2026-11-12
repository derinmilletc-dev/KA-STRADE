import {useState} from 'react'
import {ShieldCheck, Send} from 'lucide-react'
import {CONFIRM, confirmMatches, emptyTicket, newRequestId, sideLabel, ticketPayload, ticketProblems, type Ticket} from './coinbase-manual'
import {CoinbaseApiError, postJson} from './coinbase-manual-api'

type Plan = Record<string, string | number>
type Preflight = {ok: boolean; reasons: string[]; plan: Plan | null; state?: {bid: string; ask: string; open_positions: number; open_orders: number}}
type Props = {onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void}

const PLAN_ROWS: [string, string, string][] = [
  ['side', 'Yön', ''], ['contracts', 'Sözleşme sayısı', ''], ['entry_reference', 'Tahmini giriş fiyatı', ''], ['stop_price', 'Stop fiyatı', ''],
  ['stop_limit_price', 'Stop-limit fiyatı (en kötü çıkış)', ''], ['stop_distance_pct', 'Stop uzaklığı', '%'], ['notional_usd', 'Pozisyon değeri', '$'],
  ['margin_usd', 'Teminat', '$'], ['leverage', 'Kaldıraç', 'x'], ['worst_case_loss_usd', 'EN KÖTÜ KAYIP', '$'],
]

export default function CoinbaseManualTicket({onDone, onError}: Props) {
  const [ticket, setTicket] = useState<Ticket>(emptyTicket())
  const [check, setCheck] = useState<Preflight | null>(null)
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [localProblems, setLocalProblems] = useState<string[]>([])

  const patch = (change: Partial<Ticket>) => { setTicket(current => ({...current, ...change})); setCheck(null); setConfirm(''); setLocalProblems([]) }

  const fail = (error: unknown) => { if (error instanceof CoinbaseApiError) onError(error); else throw error }

  const runCheck = async () => {
    const problems = ticketProblems(ticket)
    setLocalProblems(problems)
    if (problems.length) return
    setBusy(true)
    try { setCheck(await postJson<Preflight>('/orders/preflight', ticketPayload(ticket))) } catch (error) { setCheck(null); fail(error) } finally { setBusy(false) }
  }

  const open = async () => {
    setBusy(true)
    try {
      const result = await postJson<{message: string}>('/orders/open', {...ticketPayload(ticket), confirm, request_id: newRequestId()})
      setCheck(null); setConfirm('')
      onDone(result.message || 'Pozisyon açıldı ve stop doğrulandı.')
    } catch (error) { setCheck(null); setConfirm(''); fail(error) } finally { setBusy(false) }
  }

  const canOpen = !busy && check?.ok === true && confirmMatches(confirm, CONFIRM.open)

  return <section className="cbCard" aria-label="Yeni emir">
    <h3><Send/>Yeni emir (piyasa girişi + zorunlu stop)</h3>
    <div className="cbGrid">
      <label>Ürün kodu<input value={ticket.productId} onChange={e => patch({productId: e.target.value})} placeholder="vadeli ürün kodu" autoComplete="off"/></label>
      <label>Yön<select value={ticket.side} onChange={e => patch({side: e.target.value as 'BUY' | 'SELL'})}><option value="BUY">ALIŞ (long)</option><option value="SELL">SATIŞ (short)</option></select></label>
      <label>Stop fiyatı (zorunlu)<input value={ticket.stopPrice} onChange={e => patch({stopPrice: e.target.value})} inputMode="decimal" autoComplete="off"/></label>
      <label>Teminat (USD)<input value={ticket.marginUsd} onChange={e => patch({marginUsd: e.target.value})} inputMode="decimal" autoComplete="off"/></label>
      <label>Sözleşme sayısı (boşsa güvenli en büyük)<input value={ticket.contracts} onChange={e => patch({contracts: e.target.value})} inputMode="numeric" autoComplete="off"/></label>
    </div>
    {localProblems.length > 0 && <ul className="cbNote bad" role="alert">{localProblems.map(text => <li key={text}>{text}</li>)}</ul>}
    <div className="cbButtons"><button disabled={busy} onClick={runCheck}><ShieldCheck/>Ön kontrol yap</button></div>

    {check && !check.ok && <div className="cbNote bad" role="alert"><b>Ön kontrol geçmedi. Emir verilmedi.</b><ul>{check.reasons.map(text => <li key={text}>{text}</li>)}</ul></div>}
    {check?.ok && check.plan && <div className="cbPlan">
      <b>Ön kontrol geçti. Plan:</b>
      <dl>{PLAN_ROWS.map(([key, label, unit]) => {
        const raw = check.plan?.[key]
        if (raw === undefined) return null
        const text = key === 'side' ? sideLabel(String(raw)) : unit === '$' ? `${raw} USD` : unit ? `${raw}${unit === '%' ? ' %' : unit}` : String(raw)
        return <div key={key} className={key === 'worst_case_loss_usd' ? 'strong' : ''}><dt>{label}</dt><dd>{text}</dd></div>
      })}</dl>
      <p className="cbNote warn">Bu gerçek paradır. Stop kurulamaz veya doğrulanamazsa pozisyon hemen kapatılır.</p>
      <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Şunu yaz: ${CONFIRM.open}`} autoComplete="off"/></label>
      <div className="cbButtons"><button className="primary" disabled={!canOpen} onClick={open}>Emri gönder</button></div>
    </div>}
  </section>
}

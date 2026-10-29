import {useState} from 'react'
import {createPortal} from 'react-dom'
import {X} from 'lucide-react'
import {demoRequest} from './demo-request'
import {API_BASE} from './api'
import {buildOrderBody, type OrderDraft} from './kais-order-intents'
import './kais-order-card.css'

const API = `${API_BASE}/binance-demo`
const WORD = 'ONAYLA'
const errorText = (detail: unknown) => typeof detail === 'string' && detail.trim() ? detail : Array.isArray(detail) ? 'Emir değerleri geçersiz; alanları kontrol et.' : 'Demo sunucusu emri kabul etmedi.'
type Stage = 'edit' | 'testing' | 'tested' | 'sending' | 'sent'
const FIELDS: [keyof OrderDraft, string][] = [['symbol', 'Parite'], ['margin', 'Marjin (USDT)'], ['leverage', 'Kaldıraç (x)'], ['stop', 'Stop'], ['tp1', 'TP1'], ['tp2', 'TP2'], ['tp3', 'TP3']]

/** Aşama 2–3: taslak → sunucu testi (emir oluşmaz) → yazılı ONAYLA → yalnızca Binance Futures Demo'ya gönderim. Sesle onay yoktur. */
export default function KaisOrderCard({initial, onClose, onResult}: {initial: OrderDraft; onClose: () => void; onResult: (text: string) => void}) {
  const [draft, setDraft] = useState(initial)
  const [stage, setStage] = useState<Stage>('edit')
  const [word, setWord] = useState('')
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)
  const built = buildOrderBody(draft)
  const busy = stage === 'testing' || stage === 'sending'
  const edit = (patch: Partial<OrderDraft>) => { if (busy || stage === 'sent') return; setDraft(current => ({...current, ...patch})); setStage('edit'); setWord(''); setMessage(''); setFailed(false) }
  const post = (path: string, body: object) => demoRequest<{ok?: boolean; message?: string}>(API, path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}, errorText)

  const test = async () => {
    if (!built.body) { setMessage(built.error); setFailed(true); return }
    setStage('testing'); setMessage(''); setFailed(false)
    try { const result = await post('/order/test', built.body); setStage('tested'); setMessage(result.message || 'Test başarılı; hiçbir emir oluşmadı.') }
    catch (error) { setStage('edit'); setFailed(true); setMessage(error instanceof Error ? error.message : 'Test başarısız.') }
  }
  const send = async () => {
    if (!built.body || stage !== 'tested' || word.trim() !== WORD) return
    setStage('sending'); setFailed(false)
    try { await post('/order', {...built.body, confirmation: 'DEMO'}); setStage('sent'); const text = `${built.body.symbol} ${built.body.direction} Demo emri gönderildi (yalnızca Binance Futures Demo).`; setMessage(text); onResult(text) }
    catch (error) { setStage('tested'); setFailed(true); setMessage(error instanceof Error ? error.message : 'Emir gönderilemedi.') }
  }

  return createPortal(<div className="kaisOrder" role="dialog" aria-modal="true" aria-label="Demo emir taslağı">
    <div className="kaisOrderCard">
      <header><b>Demo emir taslağı</b><button type="button" onClick={onClose} aria-label="Kapat"><X aria-hidden="true"/></button></header>
      <p className="kaisOrderNote">Otomatik gönderilmez. Yalnızca Binance Futures Demo; gerçek hesap kilitli.</p>
      <div className="kaisOrderGrid">
        {FIELDS.map(([key, label]) => <label key={key}>{label}<input value={draft[key]} inputMode={key === 'symbol' ? 'text' : 'decimal'} disabled={busy || stage === 'sent'} onChange={event => edit({[key]: event.target.value} as Partial<OrderDraft>)}/></label>)}
      </div>
      <div className="kaisOrderSide" role="group" aria-label="Yön">
        {(['LONG', 'SHORT'] as const).map(side => <button key={side} type="button" aria-pressed={draft.direction === side} data-side={side} onClick={() => edit({direction: side})}>{side === 'LONG' ? 'Long' : 'Short'}</button>)}
      </div>
      {message && <p className={failed ? 'kaisOrderMsg err' : 'kaisOrderMsg'} role="status">{message}</p>}
      {stage !== 'sent' && <button type="button" className="kaisOrderTest" disabled={busy} onClick={() => void test()}>{stage === 'testing' ? 'Test ediliyor…' : 'Sunucuda test et (emir oluşmaz)'}</button>}
      {(stage === 'tested' || stage === 'sending') && <div className="kaisOrderConfirm">
        <label>Göndermek için <b translate="no">{WORD}</b> yaz<input value={word} autoComplete="off" autoCapitalize="characters" disabled={stage === 'sending'} onChange={event => setWord(event.target.value)} placeholder={WORD}/></label>
        <button type="button" disabled={word.trim() !== WORD || stage === 'sending'} onClick={() => void send()}>{stage === 'sending' ? 'Gönderiliyor…' : 'Demo emri gönder'}</button>
      </div>}
      {stage === 'sent' && <button type="button" className="kaisOrderTest" onClick={onClose}>Kapat</button>}
    </div>
  </div>, document.body)
}

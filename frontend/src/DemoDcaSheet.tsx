import {useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {X, ShieldAlert} from 'lucide-react'
import DcaPanel, {type DcaPanelProps} from './DcaPanel'
import {buildSignalMap, type SignalMap} from './coin-ranking'
import {API_BASE} from '../../api'
import {fetchWithTimeout} from '../../master-trade-request'
import '../../live-ws.css'
import '../../manual-order.css'

type Row = {symbol: string; display: string; price: number; change: number; volume: number}
type Confirm = Parameters<DcaPanelProps['askConfirm']>[0]

const detail = (payload: unknown, fallback: string) => {
  const value = payload && typeof payload === 'object' ? (payload as {detail?: unknown}).detail : null
  return typeof value === 'string' && value.trim() ? value : fallback
}

/** Demo DCA: canlıdaki DCA ekranının aynısı; adresler demo motoruna gider, ARM/izin gerekmez. */
export default function DemoDcaSheet({onClose}: {onClose: () => void}) {
  const [rows, setRows] = useState<Row[]>([])
  const [signals, setSignals] = useState<SignalMap | null>(null)
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [side, setSide] = useState<'LONG' | 'SHORT'>('LONG')
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    void fetchWithTimeout(`${API_BASE}/markets?all=true`, {signal: controller.signal}).then(async response => {
      const data = await response.json() as Row[]
      if (Array.isArray(data)) setRows(data.filter(row => row && typeof row.symbol === 'string').sort((a, b) => Number(b.volume || 0) - Number(a.volume || 0)))
    }).catch(() => undefined)
    void fetchWithTimeout(`${API_BASE}/analysis-universe?interval=15m&limit=100`, {signal: controller.signal}, 60000)
      .then(async response => { if (response.ok) setSignals(buildSignalMap(await response.json())) }).catch(() => undefined)
    return () => controller.abort()
  }, [])

  const call: DcaPanelProps['call'] = async (base, path, options = {}) => {
    const {timeoutMs, ...rest} = options as RequestInit & {timeoutMs?: number}
    const headers = new Headers(rest.headers)
    if (rest.body) headers.set('Content-Type', 'application/json')
    const response = await fetchWithTimeout(`${base}${path}`, {...rest, headers}, timeoutMs)
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new Error(detail(payload, 'İşlem tamamlanamadı.'))
    return payload as never
  }

  const price = Number(rows.find(row => row.symbol === symbol)?.price)
  const same = (a: string, b?: string) => a.trim().toLocaleUpperCase('tr-TR') === (b ?? '').trim().toLocaleUpperCase('tr-TR')
  const run = async () => {
    if (!confirm) return
    setBusy(true)
    try { await confirm.action(); setConfirm(null); setText('') } catch { /* panel hatayı kendisi gösterir */ } finally { setBusy(false) }
  }

  return createPortal(<div className="liveConfirmBackdrop" style={{alignItems: 'stretch', overflowY: 'auto'}}>
    <section className="liveConfirm" role="dialog" aria-modal="true" aria-label="Demo DCA" style={{maxWidth: 560, width: '100%', margin: '0 auto', maxHeight: '100%', overflowY: 'auto'}}>
      <button type="button" aria-label="Kapat" onClick={onClose} style={{alignSelf: 'flex-end', background: 'none', border: 0, color: 'inherit'}}><X aria-hidden="true"/></button>
      <DcaPanel demo coins={rows} signals={signals} symbol={symbol} price={price} side={side} liveArmed v25Base={`${API_BASE}/v25`} demoBase={`${API_BASE}/binance-demo`}
        call={call} onPickSymbol={setSymbol} onPickSide={setSide} askConfirm={config => { setConfirm(config); setText('') }}/>
    </section>
    {confirm && <div className="liveConfirmBackdrop"><section className="liveConfirm" role="dialog" aria-modal="true" data-tone={confirm.simple ? 'warn' : 'danger'}>
      <span className="liveConfirmIcon" aria-hidden="true"><ShieldAlert/></span>
      <h2>{confirm.title}</h2><p>{confirm.message}</p>
      {!confirm.simple && <label>YAZARAK ONAYLA<input autoFocus value={text} onChange={event => setText(event.target.value)} placeholder={confirm.expected}/></label>}
      <div className="liveConfirmBtns"><button type="button" className="liveConfirmNo" onClick={() => { setConfirm(null); setText('') }}>{confirm.simple ? 'HAYIR' : 'VAZGEÇ'}</button>
        <button type="button" className="liveConfirmYes" disabled={busy || (!confirm.simple && !same(text, confirm.expected))} onClick={() => void run()}>{confirm.simple ? 'EVET' : 'ONAYLA'}</button></div>
    </section></div>}
  </div>, document.body)
}

// Analiz Stüdyosu · canlı risk izni yenileme: izin bittiğinde uyarının yanında "İzni yenile" düğmesi, tıklayınca süre seçtiren onay paneli.
// Sunucudaki /v25/consent ucu kullanılır; süre en fazla 24 saattir (1/4/8/12/24). Yenileme ARM ve otomasyonu sıfırlar (sunucu kuralı).
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Check, ShieldCheck, X} from 'lucide-react'
import {API_BASE} from './api'
import {requestPositionsRefresh} from './trade-intent'
import {CONSENT_HOURS, consentRenewable, postJson, tx} from './studio-quick-trade'
import './studio-consent.css'

const V25 = `${API_BASE}/v25`

export function ConsentRenew({blocker, onDone}: {blocker: string; onDone?: (hours: number) => void}) {
  const [open, setOpen] = useState(false)
  const [hours, setHours] = useState<number>(24)
  const [understood, setUnderstood] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const anchor = useRef<HTMLSpanElement>(null)
  const [host, setHost] = useState<Element | null>(null)
  useEffect(() => { if (open) setHost(anchor.current?.closest('.sqtLayer') ?? null) }, [open])
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) { event.stopPropagation(); setOpen(false) } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, busy])
  if (!consentRenewable(blocker)) return null

  const close = () => { if (busy) return; setOpen(false); setError(''); setUnderstood(false) }
  const renew = async () => {
    if (busy || !understood) return
    setBusy(true); setError('')
    const result = await postJson<unknown>(`${V25}/consent`, {understood: true, hours})
    setBusy(false)
    if (!result.ok) { setError(result.message || tx('İzin yenilenemedi.', 'Could not renew the permission.')); return }
    setOpen(false); setUnderstood(false)
    requestPositionsRefresh()
    onDone?.(hours)
  }
  const panel = open && <>
    <div className="sqcScrim" onClick={close} aria-hidden="true"/>
    <section className="sqcPanel" role="dialog" aria-modal="true" aria-label={tx('Canlı risk iznini yenile', 'Renew the live risk permission')}>
      <header>
        <ShieldCheck aria-hidden="true"/>
        <div><b>{tx('Risk iznini yenile', 'Renew risk permission')}</b><small>{tx('Gerçek parayla emir açmak için izin gerekir', 'Permission is needed to open real-money orders')}</small></div>
        <button type="button" className="sqcX" onClick={close} aria-label={tx('Kapat', 'Close')} disabled={busy}><X aria-hidden="true"/></button>
      </header>
      <div className="sqcBody">
        <div className="sqcCol">
          <small className="sqcLabel">{tx('İzin süresi', 'Permission length')}</small>
          <div className="sqcHours" role="radiogroup" aria-label={tx('İzin süresi (saat)', 'Permission length (hours)')}>
            {CONSENT_HOURS.map(value => <button key={value} type="button" role="radio" aria-checked={hours === value} data-on={hours === value || undefined} disabled={busy} onClick={() => setHours(value)}>{value}<em>{tx('saat', 'h')}</em></button>)}
          </div>
          <p className="sqcNote">{hours === 24 ? tx('24 saat: en uzun süre. Süre bitince yeniden onay istenir.', '24 hours: the longest. You will be asked again when it ends.') : tx(`${hours} saat sonra izin kendiliğinden biter; daha güvenli.`, `The permission ends by itself after ${hours} h; safer.`)}</p>
          <p className="sqcWarn">{tx('Yenileme canlı ARM’ı ve otomasyonu sıfırlar; açık pozisyonların stopları yerinde kalır.', 'Renewing resets live ARM and automation; stops on open positions stay in place.')}</p>
        </div>
        <div className="sqcCol">
          <label className="sqcCheck"><input type="checkbox" checked={understood} disabled={busy} onChange={event => setUnderstood(event.target.checked)}/><span aria-hidden="true">{understood && <Check/>}</span>
            <div><b>{tx('Anladım', 'I understand')}</b><small>{tx('Gerçek parayla işlem yaptığımı ve kaybedebileceğimi biliyorum.', 'I know I am trading real money and can lose it.')}</small></div></label>
          {error && <p className="sqcError" role="alert">{error}</p>}
          <div className="sqcActs">
            <button type="button" className="sqcGo" disabled={!understood || busy} onClick={() => void renew()}>{busy ? tx('Yenileniyor…', 'Renewing…') : tx(`${hours} saatlik izni ver`, `Grant ${hours} h permission`)}</button>
            <button type="button" className="sqcNo" disabled={busy} onClick={close}>{tx('Vazgeç', 'Cancel')}</button>
          </div>
        </div>
      </div>
    </section>
  </>
  return <span className="sqcAnchor" ref={anchor}>
    <button type="button" className="sqcOpen" onClick={() => setOpen(true)}>{tx('İzni yenile', 'Renew permission')}</button>
    {panel && (host ? createPortal(panel, host) : panel)}
  </span>
}

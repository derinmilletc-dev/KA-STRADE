/** Hologram alt kontrol çubuğu ve marka başlığı. Gerçek işlevler: yazıyla soru gönderme (mevcut sohbet gönderimi) ve dinlemeyi durdurma.
 *  Yazılı cevap "Son konuşma" panelinde ve sohbet geçmişinde görünür; burada sahte cevap üretilmez. */
import {useState, type FormEvent} from 'react'
import {Mic, Send} from 'lucide-react'

export function KaisHoloBrand({label}: {label: string}) {
  return <div className="khBrand"><img src="/kaistrade-logo-trim.png" alt="KaisTrade" width={896} height={216}/><span>{label}</span></div>
}

export default function KaisHoloDock({onSend, onStopListening, placeholder, sendLabel, stopLabel, busy}: {onSend: (text: string) => void; onStopListening: () => void; placeholder: string; sendLabel: string; stopLabel: string; busy: boolean}) {
  const [text, setText] = useState('')
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = text.trim()
    if (!value || busy) return
    onSend(value)
    setText('')
  }
  return <form className="khDock" onSubmit={submit}>
    <button type="button" className="khDockMic" onClick={onStopListening} aria-label={stopLabel} title={stopLabel}><Mic aria-hidden="true"/></button>
    <input type="text" value={text} maxLength={400} onChange={event => setText(event.target.value)} placeholder={placeholder} aria-label={placeholder} autoComplete="off"/>
    <button type="submit" className="khDockSend" disabled={busy || !text.trim()} aria-label={sendLabel} title={sendLabel}><Send aria-hidden="true"/></button>
  </form>
}

import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Check, TrendingUp, X} from 'lucide-react'
import './kais-long.css'

/** KAİS LONG: strateji adı ve kuralları. İki görünüm: Rehber şeridinde yeşil düğme ('chip'), canlı kartta küçük bağlantı ('link'). */
export const KAIS_LONG_RULES = {direction: 'Yalnız LONG', confidence: '84+', protection: 'Stop + Hedef'} as const

export default function KaisLongInfo({variant = 'chip'}: {variant?: 'chip' | 'link'}) {
  const [open, setOpen] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return <>
    {variant === 'chip'
      ? <button type="button" className="lcLongBtn" data-testid="open-kais-long" onClick={() => setOpen(true)}><TrendingUp aria-hidden="true"/>KAİS LONG<small>Yalnız LONG</small></button>
      : <button type="button" className="kaisLongLink" aria-label="KAİS LONG hakkında bilgi" onClick={() => setOpen(true)}><TrendingUp aria-hidden="true"/><span>Strateji nasıl çalışır?</span></button>}
    {open && createPortal(<div className="klBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="kl" role="dialog" aria-modal="true" aria-labelledby="kais-long-title">
        <div className="klGlow" aria-hidden="true"/>
        <div className="klHead">
          <span className="klIcon" aria-hidden="true"><TrendingUp/></span>
          <div><small>STRATEJİ</small><h3 id="kais-long-title">KAİS LONG</h3></div>
          <button ref={closeRef} type="button" className="klX" aria-label="Kapat" onClick={() => setOpen(false)}><X size={18}/></button>
        </div>
        <p className="klLead">Yalnız yükseliş fırsatlarını arar. Güçlü LONG sinyali gelince stop ve hedefiyle birlikte emir açar.</p>
        <div className="klTiles">
          <div><small>YÖN</small><b>{KAIS_LONG_RULES.direction}</b></div>
          <div><small>GÜVEN PUANI</small><b>{KAIS_LONG_RULES.confidence}</b></div>
          <div><small>KORUMA</small><b>{KAIS_LONG_RULES.protection}</b></div>
        </div>
        <ul className="klList">
          <li><i aria-hidden="true"><Check/></i>Her işlemde stop ve hedef emri birlikte yerleşir</li>
          <li><i aria-hidden="true"><Check/></i>Günlük kayıp sınırı ve ARM kilidi aktif kalır</li>
          <li><i aria-hidden="true"><Check/></i>İstediğin an durdurabilirsin; açık pozisyon korumaları sürer</li>
        </ul>
      </section>
    </div>, document.body)}
  </>
}

import {useEffect, useRef} from 'react'
import {createPortal} from 'react-dom'
import {Check, Sparkles} from 'lucide-react'
import './trial-celebration.css'

const REDIRECT_MS = 3000
const PIECES = Array.from({length: 18}, (_, index) => index)

export default function TrialCelebration({onDone}:{onDone:() => void}) {
  const latest = useRef(onDone)
  latest.current = onDone
  useEffect(() => {
    const timer = window.setTimeout(() => latest.current(), REDIRECT_MS)
    return () => window.clearTimeout(timer)
  }, [])
  return createPortal(
    <div className="ktTrialWin" role="dialog" aria-modal="true" aria-live="polite" aria-label="Deneme süren başladı">
      <div className="ktTrialWinConfetti" aria-hidden="true">
        {PIECES.map(index => <i key={index} style={{'--i': index} as React.CSSProperties}/>)}
      </div>
      <div className="ktTrialWinCard">
        <div className="ktTrialWinBadge" aria-hidden="true"><span/><Check strokeWidth={3}/></div>
        <small><Sparkles size={14}/> HOŞ GELDİN</small>
        <h2>Deneme süren başladı!</h2>
        <p>7 gün boyunca Master Trade analizi, grafikler ve demo işlemi ücretsiz kullanabilirsin.</p>
        <div className="ktTrialWinBar" aria-hidden="true"><b/></div>
        <span className="ktTrialWinHint">Ana ekrana yönlendiriliyorsun…</span>
        <button type="button" onClick={onDone}>Hemen başla</button>
      </div>
    </div>,
    document.body,
  )
}

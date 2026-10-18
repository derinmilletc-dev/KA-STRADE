import {createPortal} from 'react-dom'
import {X, MessageSquare} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import './kais-agent-holo.css'

export type HoloPhase = 'listening' | 'thinking' | 'speaking'
type Props = {phase: HoloPhase; status: string; userText?: string; replyText?: string; actions: string[]; chatLabel: string; closeLabel: string; onOpenChat: () => void; onMinimize: () => void}

/** Hologram ekran: sitedeki gerçek KaisEye bileşeni büyütülür; göz bebeğindeki halkalar yalnızca çalışırken hareket eder. */
export default function KaisAgentHolo({phase, status, userText, replyText, actions, chatLabel, closeLabel, onOpenChat, onMinimize}: Props) {
  return createPortal(<div className="kaisHolo" data-phase={phase} role="dialog" aria-label="Kais" aria-live="polite">
    <div className="kaisHoloScan" aria-hidden="true"/>
    <button type="button" className="kaisHoloClose" onClick={onMinimize} aria-label={closeLabel}><X aria-hidden="true"/></button>
    <div className="kaisHoloStage">
      <div className="kaisHoloEye">
        <KaisEye size={280} state={phase === 'thinking' ? 'thinking' : 'idle'}/>
        <svg className="kaisHoloPupil" viewBox="0 0 96 96" aria-hidden="true">
          <circle className="p1" cx="48" cy="48" r="7.5"/><circle className="p2" cx="48" cy="48" r="5"/>
          <path className="p3" d="M42 48h3.5l1.5-4 2.5 8 1.5-4H54"/>
        </svg>
        <div className="kaisHoloBeam" aria-hidden="true"/><div className="kaisHoloBase" aria-hidden="true"/>
      </div>
      {actions.slice(-4).map((action, index) => <span key={`${action}-${index}`} className="kaisHoloChip" style={{'--i': index} as React.CSSProperties}>▸ {action}</span>)}
    </div>
    <p className="kaisHoloStatus">{status}</p>
    {userText && <p className="kaisHoloUser">“{userText}”</p>}
    {replyText && phase !== 'listening' && <p className="kaisHoloReply">{replyText}</p>}
    <button type="button" className="kaisHoloChat" onClick={onOpenChat}><MessageSquare aria-hidden="true"/>{chatLabel}</button>
  </div>, document.body)
}

import {useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {X, MessageSquare} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import './kais-agent-holo.css'

export type HoloPhase = 'listening' | 'thinking' | 'speaking'
type Props = {phase: HoloPhase; status: string; userText?: string; replyText?: string; actions: string[]; chatLabel: string; closeLabel: string; onOpenChat: () => void; onMinimize: () => void}

/** Hologram ekran. Ana göz (KaisEye) olduğu gibi çizilir; hiçbir şekilde dönüştürülmez, filtrelenmez, üstüne efekt bindirilmez.
 *  Tüm hareket gözün DIŞINDAki bağımsız katmanlardır ve gerçek duruma bağlıdır:
 *  listening = mikrofon gerçekten açık · thinking = sunucu cevabı bekleniyor (tarama + ölçüm noktaları) · speaking = cevap okunuyor (odak halkası). */
export default function KaisAgentHolo({phase, status, userText, replyText, actions, chatLabel, closeLabel, onOpenChat, onMinimize}: Props) {
  const [hidden, setHidden] = useState(() => document.hidden)
  useEffect(() => {
    const change = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', change)
    return () => document.removeEventListener('visibilitychange', change)
  }, [])
  return createPortal(<div className="kaisHolo" data-phase={phase} data-paused={hidden} role="dialog" aria-label="Kais" aria-live="polite">
    <button type="button" className="kaisHoloClose" onClick={onMinimize} aria-label={closeLabel}><X aria-hidden="true"/></button>
    <div className="kaisHoloStage">
      <div className="kaisHoloGlow" aria-hidden="true"/>
      <svg className="kaisHoloRings" viewBox="-160 -160 320 320" aria-hidden="true">
        <g className="ring r1"><circle r="156"/></g>
        <g className="ring r2"><path d="M-150 0A150 150 0 0 1 0 -150M150 0A150 150 0 0 1 0 150"/></g>
        <g className="ring r3"><path d="M128 0h6" transform="rotate(0)"/><path d="M128 0h3" transform="rotate(6)"/><path d="M128 0h3" transform="rotate(12)"/><path d="M128 0h3" transform="rotate(18)"/><path d="M128 0h3" transform="rotate(24)"/><path d="M128 0h6" transform="rotate(30)"/><path d="M128 0h3" transform="rotate(36)"/><path d="M128 0h3" transform="rotate(42)"/><path d="M128 0h3" transform="rotate(48)"/><path d="M128 0h3" transform="rotate(54)"/><path d="M128 0h6" transform="rotate(60)"/><path d="M128 0h3" transform="rotate(66)"/><path d="M128 0h3" transform="rotate(72)"/><path d="M128 0h3" transform="rotate(78)"/><path d="M128 0h3" transform="rotate(84)"/><path d="M128 0h6" transform="rotate(90)"/><path d="M128 0h3" transform="rotate(96)"/><path d="M128 0h3" transform="rotate(102)"/><path d="M128 0h3" transform="rotate(108)"/><path d="M128 0h3" transform="rotate(114)"/><path d="M128 0h6" transform="rotate(120)"/><path d="M128 0h3" transform="rotate(126)"/><path d="M128 0h3" transform="rotate(132)"/><path d="M128 0h3" transform="rotate(138)"/><path d="M128 0h3" transform="rotate(144)"/><path d="M128 0h6" transform="rotate(150)"/><path d="M128 0h3" transform="rotate(156)"/><path d="M128 0h3" transform="rotate(162)"/><path d="M128 0h3" transform="rotate(168)"/><path d="M128 0h3" transform="rotate(174)"/><path d="M128 0h6" transform="rotate(180)"/><path d="M128 0h3" transform="rotate(186)"/><path d="M128 0h3" transform="rotate(192)"/><path d="M128 0h3" transform="rotate(198)"/><path d="M128 0h3" transform="rotate(204)"/><path d="M128 0h6" transform="rotate(210)"/><path d="M128 0h3" transform="rotate(216)"/><path d="M128 0h3" transform="rotate(222)"/><path d="M128 0h3" transform="rotate(228)"/><path d="M128 0h3" transform="rotate(234)"/><path d="M128 0h6" transform="rotate(240)"/><path d="M128 0h3" transform="rotate(246)"/><path d="M128 0h3" transform="rotate(252)"/><path d="M128 0h3" transform="rotate(258)"/><path d="M128 0h3" transform="rotate(264)"/><path d="M128 0h6" transform="rotate(270)"/><path d="M128 0h3" transform="rotate(276)"/><path d="M128 0h3" transform="rotate(282)"/><path d="M128 0h3" transform="rotate(288)"/><path d="M128 0h3" transform="rotate(294)"/><path d="M128 0h6" transform="rotate(300)"/><path d="M128 0h3" transform="rotate(306)"/><path d="M128 0h3" transform="rotate(312)"/><path d="M128 0h3" transform="rotate(318)"/><path d="M128 0h3" transform="rotate(324)"/><path d="M128 0h6" transform="rotate(330)"/><path d="M128 0h3" transform="rotate(336)"/><path d="M128 0h3" transform="rotate(342)"/><path d="M128 0h3" transform="rotate(348)"/><path d="M128 0h3" transform="rotate(354)"/></g>
        <g className="focus"><path transform="rotate(0)" d="M-146 -146h26M-146 -146v26"/><path transform="rotate(90)" d="M-146 -146h26M-146 -146v26"/><path transform="rotate(180)" d="M-146 -146h26M-146 -146v26"/><path transform="rotate(270)" d="M-146 -146h26M-146 -146v26"/><path transform="rotate(0)" d="M0 -146v-14"/><path transform="rotate(90)" d="M0 -146v-14"/><path transform="rotate(180)" d="M0 -146v-14"/><path transform="rotate(270)" d="M0 -146v-14"/><circle r="146"/></g>
        <g className="measure"><g className="m" style={{'--d': '0.0s'} as React.CSSProperties} transform="translate(-146 -34)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '0.45s'} as React.CSSProperties} transform="translate(146 -34)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '0.9s'} as React.CSSProperties} transform="translate(-150 34)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '1.35s'} as React.CSSProperties} transform="translate(150 34)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '1.8s'} as React.CSSProperties} transform="translate(-118 -96)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '2.25s'} as React.CSSProperties} transform="translate(118 -96)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '2.7s'} as React.CSSProperties} transform="translate(-118 96)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '3.15s'} as React.CSSProperties} transform="translate(118 96)"><circle r="1.8"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g></g>
      </svg>
      <div className="kaisHoloEye"><KaisEye size={260} state="idle"/></div>
      <div className="kaisHoloScanBand" aria-hidden="true"><i/></div>
      <div className="kaisHoloFloor" aria-hidden="true"/>
    </div>
    {actions.length > 0 && <div className="kaisHoloChips">{actions.slice(-4).map((action, index) => <span key={`${action}-${index}`} className="kaisHoloChip" style={{'--i': index} as React.CSSProperties}>▸ {action}</span>)}</div>}
    <p className="kaisHoloStatus">{status}</p>
    {userText && <p className="kaisHoloUser">“{userText}”</p>}
    {replyText && phase !== 'listening' && <p className="kaisHoloReply">{replyText}</p>}
    <button type="button" className="kaisHoloChat" onClick={onOpenChat}><MessageSquare aria-hidden="true"/>{chatLabel}</button>
  </div>, document.body)
}

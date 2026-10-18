import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {X, MessageSquare, RotateCcw} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import './kais-agent-holo.css'

export type HoloPhase = 'listening' | 'thinking' | 'speaking' | 'error'
type Props = {phase: HoloPhase; status: string; userText?: string; replyText?: string; actions: string[]; chatLabel: string; closeLabel: string; retryLabel: string; onOpenChat: () => void; onMinimize: () => void; onRetry: () => void}

/** Gerçek mikrofon seviyesi: yalnızca dinleme anında, yalnızca ince işaretçili (masaüstü) cihazlarda ikinci bir akış açılır.
 *  Telefonlarda ikinci akış SpeechRecognition ile çakışabileceği için açılmaz → ses dalgası hiç gösterilmez (sahte dalga yok). */
function useMicLevel(enabled: boolean, target: React.RefObject<HTMLElement | null>) {
  const [live, setLive] = useState(false)
  useEffect(() => {
    if (!enabled || window.matchMedia('(pointer:coarse)').matches || !navigator.mediaDevices?.getUserMedia) return
    let stopped = false, frame = 0, stream: MediaStream | null = null, context: AudioContext | null = null
    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({audio: true})
        if (stopped) { stream.getTracks().forEach(track => track.stop()); return }
        context = new AudioContext()
        const analyser = context.createAnalyser(); analyser.fftSize = 256
        context.createMediaStreamSource(stream).connect(analyser)
        const data = new Uint8Array(analyser.frequencyBinCount)
        setLive(true)
        let smooth = 0
        const tick = () => {
          analyser.getByteFrequencyData(data)
          const level = Math.min(1, data.reduce((sum, value) => sum + value, 0) / data.length / 90)
          smooth += (level - smooth) * .25
          target.current?.style.setProperty('--lvl', smooth.toFixed(3))
          frame = requestAnimationFrame(tick)
        }
        tick()
      } catch { /* mikrofon akışı açılamadı: dalga gösterilmez */ }
    })()
    return () => { stopped = true; cancelAnimationFrame(frame); stream?.getTracks().forEach(track => track.stop()); void context?.close(); setLive(false) }
  }, [enabled, target])
  return live
}

/** Hologram ekran. Ana göz (KaisEye) olduğu gibi çizilir; dönüşüm, filtre veya üst katman yok.
 *  Hareketler gözün DIŞINDaki bağımsız katmanlardır ve gerçek duruma bağlıdır:
 *  listening = mikrofon açık · thinking = sunucu cevabı bekleniyor (ilk 1.8 sn tarama, sonra hizalama + ölçüm işaretleri)
 *  speaking = cevap geldi, sonuç alanına odak (ilk 1.6 sn köşe işaretleri) · error = sakin, amber, yeniden dene. */
export default function KaisAgentHolo({phase, status, userText, replyText, actions, chatLabel, closeLabel, retryLabel, onOpenChat, onMinimize, onRetry}: Props) {
  const [hidden, setHidden] = useState(() => document.hidden)
  const [sub, setSub] = useState<'scan' | 'analyze' | 'lock' | ''>('')
  const stage = useRef<HTMLDivElement>(null)
  const live = useMicLevel(phase === 'listening' && !hidden, stage)
  useEffect(() => {
    const change = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', change)
    return () => document.removeEventListener('visibilitychange', change)
  }, [])
  useEffect(() => {
    if (phase === 'thinking') { setSub('scan'); const timer = window.setTimeout(() => setSub('analyze'), 1800); return () => window.clearTimeout(timer) }
    if (phase === 'speaking') { setSub('lock'); const timer = window.setTimeout(() => setSub(''), 1600); return () => window.clearTimeout(timer) }
    setSub('')
  }, [phase])
  return createPortal(<div className="kaisHolo" data-phase={phase} data-sub={sub} data-paused={hidden} role="dialog" aria-label="Kais" aria-live="polite">
    <button type="button" className="kaisHoloClose" onClick={onMinimize} aria-label={closeLabel}><X aria-hidden="true"/></button>
    <div className="kaisHoloStage" ref={stage}>
      <div className="kaisHoloGlow" aria-hidden="true"/>
      <svg className="kaisHoloRings" viewBox="-160 -160 320 320" aria-hidden="true">
        <g className="ring r1"><circle r="156"/></g>
        <g className="ring r2"><path d="M-150 0A150 150 0 0 1 0 -150M150 0A150 150 0 0 1 0 150"/></g>
        <g className="ring r3"><path d="M128 0h7" transform="rotate(0)"/><path d="M128 0h2" transform="rotate(6)"/><path d="M128 0h2" transform="rotate(12)"/><path d="M128 0h2" transform="rotate(18)"/><path d="M128 0h2" transform="rotate(24)"/><path d="M128 0h4" transform="rotate(30)"/><path d="M128 0h2" transform="rotate(36)"/><path d="M128 0h2" transform="rotate(42)"/><path d="M128 0h2" transform="rotate(48)"/><path d="M128 0h2" transform="rotate(54)"/><path d="M128 0h4" transform="rotate(60)"/><path d="M128 0h2" transform="rotate(66)"/><path d="M128 0h2" transform="rotate(72)"/><path d="M128 0h2" transform="rotate(78)"/><path d="M128 0h2" transform="rotate(84)"/><path d="M128 0h7" transform="rotate(90)"/><path d="M128 0h2" transform="rotate(96)"/><path d="M128 0h2" transform="rotate(102)"/><path d="M128 0h2" transform="rotate(108)"/><path d="M128 0h2" transform="rotate(114)"/><path d="M128 0h4" transform="rotate(120)"/><path d="M128 0h2" transform="rotate(126)"/><path d="M128 0h2" transform="rotate(132)"/><path d="M128 0h2" transform="rotate(138)"/><path d="M128 0h2" transform="rotate(144)"/><path d="M128 0h4" transform="rotate(150)"/><path d="M128 0h2" transform="rotate(156)"/><path d="M128 0h2" transform="rotate(162)"/><path d="M128 0h2" transform="rotate(168)"/><path d="M128 0h2" transform="rotate(174)"/><path d="M128 0h7" transform="rotate(180)"/><path d="M128 0h2" transform="rotate(186)"/><path d="M128 0h2" transform="rotate(192)"/><path d="M128 0h2" transform="rotate(198)"/><path d="M128 0h2" transform="rotate(204)"/><path d="M128 0h4" transform="rotate(210)"/><path d="M128 0h2" transform="rotate(216)"/><path d="M128 0h2" transform="rotate(222)"/><path d="M128 0h2" transform="rotate(228)"/><path d="M128 0h2" transform="rotate(234)"/><path d="M128 0h4" transform="rotate(240)"/><path d="M128 0h2" transform="rotate(246)"/><path d="M128 0h2" transform="rotate(252)"/><path d="M128 0h2" transform="rotate(258)"/><path d="M128 0h2" transform="rotate(264)"/><path d="M128 0h7" transform="rotate(270)"/><path d="M128 0h2" transform="rotate(276)"/><path d="M128 0h2" transform="rotate(282)"/><path d="M128 0h2" transform="rotate(288)"/><path d="M128 0h2" transform="rotate(294)"/><path d="M128 0h4" transform="rotate(300)"/><path d="M128 0h2" transform="rotate(306)"/><path d="M128 0h2" transform="rotate(312)"/><path d="M128 0h2" transform="rotate(318)"/><path d="M128 0h2" transform="rotate(324)"/><path d="M128 0h4" transform="rotate(330)"/><path d="M128 0h2" transform="rotate(336)"/><path d="M128 0h2" transform="rotate(342)"/><path d="M128 0h2" transform="rotate(348)"/><path d="M128 0h2" transform="rotate(354)"/></g>
        <g className="ring r4"><circle r="138" strokeDasharray="30 12 6 12"/></g>
        <g className="rulers"><path transform="rotate(0)" d="M156 0h7M156 -3v6"/><path transform="rotate(90)" d="M156 0h7M156 -3v6"/><path transform="rotate(180)" d="M156 0h7M156 -3v6"/><path transform="rotate(270)" d="M156 0h7M156 -3v6"/></g>
        <g className="align"><path transform="rotate(0)" d="M-30 -150A153 153 0 0 1 30 -150"/><path transform="rotate(90)" d="M-30 -150A153 153 0 0 1 30 -150"/><path transform="rotate(180)" d="M-30 -150A153 153 0 0 1 30 -150"/><path transform="rotate(270)" d="M-30 -150A153 153 0 0 1 30 -150"/></g>
        <g className="lock"><path transform="rotate(0)" d="M-146 -146h24M-146 -146v24"/><path transform="rotate(90)" d="M-146 -146h24M-146 -146v24"/><path transform="rotate(180)" d="M-146 -146h24M-146 -146v24"/><path transform="rotate(270)" d="M-146 -146h24M-146 -146v24"/></g>
        <g className="measure"><g className="m" style={{'--d': '0.0s'} as React.CSSProperties} transform="translate(-146 -34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '0.5s'} as React.CSSProperties} transform="translate(146 -34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '1.0s'} as React.CSSProperties} transform="translate(-150 34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '1.5s'} as React.CSSProperties} transform="translate(150 34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '2.0s'} as React.CSSProperties} transform="translate(-114 -98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '2.5s'} as React.CSSProperties} transform="translate(114 -98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '3.0s'} as React.CSSProperties} transform="translate(-114 98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '3.5s'} as React.CSSProperties} transform="translate(114 98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g></g>
      </svg>
      <div className="kaisHoloEye"><KaisEye size={260} state="idle"/></div>
      <div className="kaisHoloScanBand" aria-hidden="true"><i/></div>
      <div className="kaisHoloCone" aria-hidden="true"/><div className="kaisHoloFloor" aria-hidden="true"/>
      <div className="kaisHoloWave" data-live={live} aria-hidden="true"><i style={{'--k': 0.40} as React.CSSProperties}/><i style={{'--k': 0.87} as React.CSSProperties}/><i style={{'--k': 0.98} as React.CSSProperties}/><i style={{'--k': 0.66} as React.CSSProperties}/><i style={{'--k': 0.67} as React.CSSProperties}/><i style={{'--k': 0.99} as React.CSSProperties}/><i style={{'--k': 0.86} as React.CSSProperties}/><i style={{'--k': 0.41} as React.CSSProperties}/><i style={{'--k': 0.88} as React.CSSProperties}/><i style={{'--k': 0.98} as React.CSSProperties}/><i style={{'--k': 0.65} as React.CSSProperties}/><i style={{'--k': 0.67} as React.CSSProperties}/><i style={{'--k': 0.99} as React.CSSProperties}/><i style={{'--k': 0.86} as React.CSSProperties}/><i style={{'--k': 0.42} as React.CSSProperties}/><i style={{'--k': 0.88} as React.CSSProperties}/><i style={{'--k': 0.98} as React.CSSProperties}/><i style={{'--k': 0.64} as React.CSSProperties}/><i style={{'--k': 0.68} as React.CSSProperties}/><i style={{'--k': 0.99} as React.CSSProperties}/><i style={{'--k': 0.85} as React.CSSProperties}/></div>
    </div>
    {actions.length > 0 && <div className="kaisHoloChips">{actions.slice(-4).map((action, index) => <span key={`${action}-${index}`} className="kaisHoloChip">▸ {action}</span>)}</div>}
    <p className="kaisHoloStatus" data-error={phase === 'error'}>{status}</p>
    {userText && phase !== 'error' && <p className="kaisHoloUser">“{userText}”</p>}
    {replyText && phase === 'speaking' && <p className="kaisHoloReply">{replyText}</p>}
    {phase === 'error' && <button type="button" className="kaisHoloRetry" onClick={onRetry}><RotateCcw aria-hidden="true"/>{retryLabel}</button>}
    <button type="button" className="kaisHoloChat" onClick={onOpenChat}><MessageSquare aria-hidden="true"/>{chatLabel}</button>
  </div>, document.body)
}

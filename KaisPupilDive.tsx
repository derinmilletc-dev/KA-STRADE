/** Sayfa geçişi: göz bebeğine dalış. Kais'in gözü bulunduğu yerden (üst menüdeki göz ya da hologramdaki göz) belirir,
 *  odaklanır, kamera göz bebeğine doğru ilerler; göz bebeği ekranı kapladığı anda gerçek sayfa değişir (commit), ardından
 *  göz bebeği merkezden açılarak yeni sayfayı gösterir. Tamamen görsel: veri okumaz, ağ isteği yapmaz.
 *  Hareket azaltma açıksa yalnız kısa bir kararma/açılma yapılır. Bileşen kapanınca zamanlayıcılar temizlenir ve
 *  sayfa değişimi en geç o anda yapılır (geçiş yarıda kalsa da komut kaybolmaz). */
import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {PAGE_GLYPH} from './kais-portal-glyphs'
import './kais-pupil-dive.css'

export type PupilDiveOrigin = {x: number; y: number; size: number}
export type PupilDive = {view: string; label: string; origin: PupilDiveOrigin; commit: () => void; done: () => void}

type Phase = 'appear' | 'focus' | 'dive' | 'hold' | 'reveal'
const PUPIL_RATIO = .09  // göz bebeği yarıçapı / göz kutusu (viewBox 100'de r=9)
const SEGMENTS = Array.from({length: 60}, (_, index) => index)
const STREAKS = Array.from({length: 28}, (_, index) => index)

/** Ekrandaki gerçek Kais gözünün konumu: önce hologram gözü, sonra üst menüdeki göz; yoksa ekran ortası. */
export function pupilDiveOrigin(preferred?: Element | null): PupilDiveOrigin {
  const candidates = [preferred, document.querySelector('.kaisHolo .kaisHoloEye'), document.querySelector('.assistantLauncher .kaisEye')]
  for (const element of candidates) {
    const rect = element?.getBoundingClientRect()
    if (rect && rect.width > 8 && rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth) {
      return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, size: Math.max(56, Math.min(rect.width, rect.height))}
    }
  }
  return {x: window.innerWidth / 2, y: window.innerHeight / 2, size: Math.min(240, window.innerWidth * .5)}
}

export default function KaisPupilDive({dive}: {dive: PupilDive}) {
  const [phase, setPhase] = useState<Phase>('appear')
  const committed = useRef(false)
  const {x, y, size} = dive.origin
  // Göz önce bulunduğu yerden ekran ortasına gelip büyür (odak), sonra kamera göz bebeğine dalar.
  // SVG odak boyutunda çizilir ve başlangıçta küçültülür: odakta keskin kalır (büyütülmüş bulanık görüntü olmaz).
  const width = window.innerWidth, height = window.innerHeight
  const focus = Math.max(160, Math.min(300, Math.min(width, height) * .36))
  const reach = Math.hypot(width / 2, height / 2)
  const diveScale = Math.max(4, (reach / (focus * PUPIL_RATIO)) * 1.08)
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timers: number[] = []
    const at = (ms: number, fn: () => void) => timers.push(window.setTimeout(fn, ms))
    const commit = () => {
      if (committed.current) return
      committed.current = true
      try { dive.commit() } catch { /* sayfa açılamazsa mevcut güvenli davranış */ }
    }
    if (reduced) {
      setPhase('hold'); at(180, commit); at(220, () => setPhase('reveal')); at(520, () => dive.done())
    } else {
      at(30, () => setPhase('focus'))
      at(470, () => setPhase('dive'))
      at(1060, () => { setPhase('hold'); commit() })
      at(1320, () => setPhase('reveal'))
      at(2040, () => dive.done())
    }
    return () => { timers.forEach(window.clearTimeout); commit() }
  }, [dive])
  const style = {'--kpd-x': `${x}px`, '--kpd-y': `${y}px`, '--kpd-size': `${focus}px`, '--kpd-start': (size / focus).toFixed(3),
    '--kpd-dx': `${width / 2 - x}px`, '--kpd-dy': `${height / 2 - y}px`, '--kpd-scale': diveScale.toFixed(2)} as React.CSSProperties
  return createPortal(<div className="kaisPupilDive" data-phase={phase} style={style} aria-hidden="true">
    <i className="kpdShade"/>
    <svg className="kpdStreaks" viewBox="-100 -100 200 200" preserveAspectRatio="xMidYMid slice">
      {STREAKS.map(index => <path key={index} d={`M0 -${30 + (index * 7) % 18}V-${70 + (index * 13) % 30}`} transform={`rotate(${index * (360 / STREAKS.length) + (index % 3) * 4})`}/>)}
    </svg>
    <svg className="kpdEye" viewBox="-50 -50 100 100">
      <defs>
        <radialGradient id="kpdIris" r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform="scale(22)">
          <stop offset=".38" stopColor="#031414"/>
          <stop offset=".5" stopColor="#1fd3b4"/>
          <stop offset=".72" stopColor="#7dffe0"/>
          <stop offset=".9" stopColor="#14a98f"/>
          <stop offset="1" stopColor="#063a35"/>
        </radialGradient>
        <radialGradient id="kpdPupil" r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform="scale(9)">
          <stop offset="0" stopColor="#06201f"/>
          <stop offset=".8" stopColor="#020b0c"/>
          <stop offset="1" stopColor="#010607"/>
        </radialGradient>
      </defs>
      <path className="lid" d="M-46 0Q0 -40 46 0Q0 40 -46 0Z"/>
      <circle r="22" fill="url(#kpdIris)"/>
      <g className="irisRing">{SEGMENTS.map(index => <path key={index} d={index % 5 ? 'M0 -12.6V-14' : 'M0 -12V-15'} transform={`rotate(${index * 6})`}/>)}</g>
      <circle className="irisEdge" r="22"/>
      <circle className="focusRing" r="16.5" strokeDasharray="4 2.2"/>
      <circle r="9" fill="url(#kpdPupil)"/>
      <circle className="pupilRim" r="9"/>
      <ellipse className="glint" cx="-4" cy="-4.6" rx="2" ry="1.1" transform="rotate(-35 -4 -4.6)"/>
    </svg>
    <div className="kpdTag"><svg viewBox="0 0 24 24"><path d={PAGE_GLYPH[dive.view] ?? PAGE_GLYPH.dashboard}/></svg><b>{dive.label}</b></div>
    <i className="kpdRim"/>
  </div>, document.body)
}

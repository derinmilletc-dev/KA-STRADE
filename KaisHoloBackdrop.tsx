/** Kais hologram ekranı için yalnızca DEKORATİF sinematik ortam (aria-hidden, pointer-events:none).
 *  Katmanlar (arkadan öne): lacivert taban · zümrüt ortam ışığı · buz mavisi kenar ışığı · uzak ızgara · perspektif zemin ·
 *  kenar devre izleri · masaüstü yan raylar · seyrek parçacıklar · mobil holografik taban · vinyet. Gözün çevresindeki halkalar ve ışık süpürmesi
 *  sahneye bağlı CSS katmanlarıdır (kais-holo-backdrop.css). Gözle (KaisEye) ve kontrollerle ilişkisi yoktur.
 *  Hareket yalnız CSS (transform/opacity/stroke-dashoffset); JS döngüsü, canvas, ses verisi veya ek kütüphane yok. */
import './kais-holo-backdrop.css'

const TRACES = [
  'M0 46H96L128 78H232',
  'M0 92H58L104 138V196L132 224H188',
  'M0 150H34L72 188V292',
  'M46 0V54L82 90H150L176 116',
  'M118 0V30L146 58H262L292 88',
  'M196 0V22L214 40H340',
  'M0 236H22L48 262V340',
  'M268 0V14L282 28H402L424 50',
]
const PADS: [number, number][] = [[232, 78], [188, 224], [72, 292], [176, 116], [292, 88], [340, 40], [48, 340], [424, 50]]
// Işık akışı yalnız seçili izlerde, uzun aralıklarla.
const PULSES: [number, string][] = [[0, '0s'], [4, '5.5s']]
const PARTICLES = [[9, 24, 0], [18, 70, 2.4], [27, 86, 5.1], [72, 16, 3.6], [80, 74, 0.6], [91, 40, 4.4], [14, 46, 6], [86, 58, 7.2]]
const CORNERS = ['tl', 'tr', 'bl', 'br'] as const

function Corner({corner}: {corner: typeof CORNERS[number]}) {
  return <svg className={`kaisHoloCircuit ${corner}`} viewBox="0 0 440 340" preserveAspectRatio="xMinYMin meet">
    <g className="traces">{TRACES.map(d => <path key={d} d={d}/>)}</g>
    <g className="pads">{PADS.map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="2.6"/>)}</g>
    <g className="pulses">{PULSES.map(([index, delay]) => <path key={index} d={TRACES[index]} pathLength={100} style={{animationDelay: delay}}/>)}</g>
    <path className="bracket" d="M14 58V14H58"/>
  </svg>
}

export default function KaisHoloBackdrop() {
  return <div className="kaisHoloBackdrop" aria-hidden="true">
    <i className="base"/>
    <i className="ambient"/>
    <i className="rim"/>
    <i className="grid"/>
    <i className="floor"/>
    <i className="horizon"/>
    {CORNERS.map(corner => <Corner key={corner} corner={corner}/>)}
    <i className="rail l"/>
    <i className="rail r"/>
    <div className="particles">{PARTICLES.map(([x, y, delay]) => <i key={`${x}-${y}`} style={{left: `${x}%`, top: `${y}%`, animationDelay: `${delay}s`}}/>)}</div>
    <i className="dock"/>
    <i className="vignette"/>
  </div>
}

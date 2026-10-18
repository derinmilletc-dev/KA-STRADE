/** Kais hologram ekranı için yalnızca DEKORATİF arka plan (aria-hidden, pointer-events:none).
 *  Gözle (KaisEye) ve etkileşimli kontrollerle ilişkisi yoktur; yoğun ayrıntı kenarlarda, merkez boş bırakılır.
 *  Hareketler yalnızca CSS (transform/opacity/stroke-dashoffset); JS döngüsü, canvas veya ek kütüphane yok. */
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
// Darbe (ışık) yalnızca seçili izlerde, farklı gecikmelerle seyrek akar.
const PULSES: [number, string][] = [[0, '0s'], [1, '3.2s'], [4, '6.1s'], [6, '8.4s']]
const PARTICLES = [[8, 22, 0], [16, 64, 2.4], [24, 84, 5.1], [31, 14, 1.2], [69, 18, 3.6], [76, 70, 0.6], [84, 38, 4.4], [92, 82, 2], [12, 44, 6], [88, 56, 7.2], [58, 9, 5.6], [42, 91, 3]]
const CORNERS = ['tl', 'tr', 'bl', 'br'] as const
// Sayı içermeyen ince teknik işaretler (eski koordinat metinlerinin yerinde): köşe parantezi, artı, kısa çizgi dizisi.
const MARKS = [
  'M-486 -30h18M-486 -30v10', 'M-486 30h18M-486 30v-10', 'M-452 0h10M-447 -5v10', 'M-486 8h4M-478 8h4M-470 8h4',
  'M486 -30h-18M486 -30v10', 'M486 30h-18M486 30v-10', 'M452 0h-10M447 -5v10', 'M486 -8h-4M478 -8h-4M470 -8h-4',
]

function Corner({corner}: {corner: typeof CORNERS[number]}) {
  return <svg className={`kaisHoloCircuit ${corner}`} viewBox="0 0 440 340" preserveAspectRatio="xMinYMin meet">
    <g className="traces">{TRACES.map(d => <path key={d} d={d}/>)}</g>
    <g className="pads">{PADS.map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="3"/>)}</g>
    <g className="pulses">{PULSES.map(([index, delay]) => <path key={index} d={TRACES[index]} pathLength={100} style={{animationDelay: delay}}/>)}</g>
    <path className="bracket" d="M14 64V14H64"/>
  </svg>
}

export default function KaisHoloBackdrop() {
  return <div className="kaisHoloBackdrop" aria-hidden="true">
    <i className="grid"/>
    <i className="ambient"/>
    {CORNERS.map(corner => <Corner key={corner} corner={corner}/>)}
    {/* Dönen halkalar ayrı <svg> katmanlarında; dönüş SVG içindeki çizime değil, katmanın kendisine uygulanır. */}
    <div className="kaisHoloHud">
      <svg className="spin h1" viewBox="-500 -500 1000 1000"><circle r="440" pathLength={360}/></svg>
      <svg className="spin h2" viewBox="-500 -500 1000 1000"><circle r="392" pathLength={360}/></svg>
      <svg className="fixed" viewBox="-500 -500 1000 1000">
        <g className="ticks">{Array.from({length: 24}, (_, index) => <path key={index} d="M470 0h14" transform={`rotate(${index * 15})`}/>)}</g>
        <g className="marks">{MARKS.map(d => <path key={d} d={d}/>)}</g>
      </svg>
    </div>
    <i className="scan"/>
    <div className="particles">{PARTICLES.map(([x, y, delay]) => <i key={`${x}-${y}`} style={{left: `${x}%`, top: `${y}%`, animationDelay: `${delay}s`}}/>)}</div>
    <i className="floor"/>
  </div>
}

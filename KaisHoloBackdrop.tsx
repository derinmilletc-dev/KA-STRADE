/** Kais hologram ekranı — DEKORATİF sinematik holografik ortam (aria-hidden, pointer-events:none). Göz ve kontrollerle ilişkisi yoktur.
 *  Kutu/panel YOK: yanlarda birbirine bağlı, dallanan ve göze doğru kıvrılan enerji yolları, küçük ışıklı bağlantı noktaları,
 *  eğri teknik yaylar; havada süzülen seyrek holografik parçalar. Gözün arkasındaki radyal tarama yapıları ve alt platform sahneye
 *  bağlı CSS katmanlarıdır (kais-holo-backdrop.css). Sayı, metin, veri veya grafik gösterilmez. Hareket yalnız CSS. */
import './kais-holo-backdrop.css'

type Side = {trunk: string[]; flows: string[]; fine: string[]; arcs: string[]; junctions: [number, number, number][]; hot: [number, number][]; pulses: number[]}

// viewBox 0 0 360 900; x=0 ekran kenarı. Ana gövde kenarda; akış yolları içe, göze doğru kıvrılarak ilerler ve silinir.
const LEFT: Side = {
  trunk: ['M24 0V250C24 290 58 306 58 346V560C58 600 24 616 24 656V900'],
  flows: [
    'M24 118H96C156 118 196 158 232 214S300 290 352 300',
    'M58 420H142C206 420 252 396 300 352S344 300 360 296',
    'M58 498C122 498 168 536 226 592S300 650 360 660',
    'M24 770H118C176 770 222 734 268 694S330 640 360 632',
  ],
  fine: ['M24 196H72L96 220H132', 'M58 330H108C128 330 140 316 150 300', 'M24 846H186C236 846 274 822 326 788', 'M96 118V64C96 44 110 32 130 32H210', 'M142 420V470'],
  arcs: ['M150 22A560 560 0 0 1 356 236', 'M120 892A600 600 0 0 1 356 694'],
  junctions: [[24, 118, 4], [58, 420, 4], [58, 498, 3], [24, 770, 4], [132, 220, 2.5], [150, 300, 2.5], [210, 32, 2.5], [326, 788, 2.5], [142, 470, 2.5], [24, 196, 3]],
  hot: [[232, 214], [300, 352], [226, 592], [268, 694]],
  pulses: [0, 1, 3],
}
const RIGHT: Side = {
  trunk: ['M30 0V180C30 220 12 236 12 276V470C12 510 40 526 40 566V900'],
  flows: [
    'M30 150H120C178 150 214 192 246 250S318 318 360 322',
    'M12 360H84C150 360 200 392 252 440S326 490 360 494',
    'M40 610H134C194 610 236 580 280 540S334 500 360 494',
    'M40 724C112 724 160 760 214 806S292 860 352 868',
  ],
  fine: ['M30 70H86C110 70 124 84 132 104', 'M12 440H62L88 466V512', 'M40 830H120', 'M120 150V206C120 226 136 238 156 238', 'M134 610V660'],
  arcs: ['M200 0A520 520 0 0 1 356 330', 'M96 900A640 640 0 0 1 356 600'],
  junctions: [[30, 150, 4], [12, 360, 4], [40, 610, 4], [40, 724, 3], [132, 104, 2.5], [88, 512, 2.5], [120, 830, 2.5], [156, 238, 2.5], [134, 660, 2.5], [12, 440, 3]],
  hot: [[246, 250], [252, 440], [280, 540], [214, 806]],
  pulses: [0, 2, 3],
}

function Structure({side, data}: {side: 'l' | 'r'; data: Side}) {
  return <svg className={`kaisHoloSide ${side}`} viewBox="0 0 360 900" preserveAspectRatio="xMinYMid slice">
    <g className="arcs">{data.arcs.map(d => <path key={d} d={d}/>)}</g>
    <g className="fine">{data.fine.map(d => <path key={d} d={d}/>)}</g>
    <g className="flows">{data.flows.map(d => <path key={d} d={d}/>)}</g>
    <g className="trunk">{data.trunk.map(d => <path key={d} d={d}/>)}</g>
    <g className="junctions">{data.junctions.map(([x, y, r]) => <circle key={`${x}-${y}`} cx={x} cy={y} r={r}/>)}</g>
    <g className="hot">{data.hot.map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="2"/>)}</g>
    <g className="pulses">{data.pulses.map((index, order) => <path key={index} d={data.flows[index]} pathLength={100} style={{animationDelay: `${order * 4.7}s`}}/>)}</g>
  </svg>
}

// Havada süzülen seyrek holografik parçalar (kısa yay, açılı çizgi, çentik dizisi) — farklı derinlikte, kutu değil.
const FRAGMENTS: [number, number, number, string][] = [
  [17, 30, 1, 'M2 18A22 22 0 0 1 24 2'], [78, 22, .8, 'M2 2L12 12L2 22M10 2L20 12L10 22'], [84, 64, 1.1, 'M2 20A26 26 0 0 0 28 4'],
  [12, 62, .7, 'M2 12H6M10 12H14M18 12H22M26 12H30'], [30, 84, .9, 'M2 4L16 16L30 4'], [68, 88, .75, 'M2 16A18 18 0 0 1 20 2M8 22A12 12 0 0 1 22 8'],
]
const PARTICLES = [[9, 24, 0], [18, 70, 2.4], [27, 86, 5.1], [72, 16, 3.6], [80, 74, 0.6], [91, 40, 4.4], [14, 46, 6], [86, 58, 7.2], [40, 92, 1.4], [62, 88, 3]]

// Masaüstü kokpit katmanı (viewBox 1600×900, kenarlara yaslanır): tavan yayları, köşelerden kıvrılan yaylar, ışığı yukarıdan
// aşağı süzülen ince dikey hatlar. Yalnız dekoratif; dar ekranlarda CSS ile gizli.
const COCKPIT_ARCS = ['M0 120C240 120 360 40 560 18', 'M1600 120C1360 120 1240 40 1040 18', 'M0 190C200 190 300 120 420 70', 'M1600 190C1400 190 1300 120 1180 70']
const COCKPIT_RAYS = [420, 500, 1100, 1180]

export default function KaisHoloBackdrop() {
  return <div className="kaisHoloBackdrop" aria-hidden="true">
    <i className="base"/>
    <i className="ambient"/>
    <i className="rim"/>
    <i className="grid"/>
    <i className="floor"/>
    <svg className="kaisHoloCockpit" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">
      <ellipse className="ceiling" cx="800" cy="-40" rx="560" ry="120"/>
      <ellipse className="ceiling c2" cx="800" cy="-40" rx="470" ry="92"/>
      <g className="arcs">{COCKPIT_ARCS.map(d => <path key={d} d={d}/>)}</g>
      <g className="rays">{COCKPIT_RAYS.map((x, index) => <g key={x}><path d={`M${x} 0V520`}/><path className="drop" d={`M${x} 0V520`} pathLength={100} style={{animationDelay: `${index * 2.3}s`}}/></g>)}</g>
    </svg>
    <Structure side="l" data={LEFT}/>
    <Structure side="r" data={RIGHT}/>
    <div className="fragments">{FRAGMENTS.map(([x, y, scale, d], index) => <svg key={d} viewBox="0 0 32 24" style={{left: `${x}%`, top: `${y}%`, width: `${32 * scale}px`, animationDelay: `${index * 1.9}s`}}><path d={d}/></svg>)}</div>
    <div className="particles">{PARTICLES.map(([x, y, delay]) => <i key={`${x}-${y}`} style={{left: `${x}%`, top: `${y}%`, animationDelay: `${delay}s`}}/>)}</div>
    <i className="vignette"/>
  </div>
}

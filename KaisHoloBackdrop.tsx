/** Kais hologram ekranı — DEKORATİF sinematik "AI odası" (aria-hidden, pointer-events:none). Göz ve kontrollerle ilişkisi yoktur.
 *  Uzak düzlem: lacivert atmosfer, ortam ışığı, buz mavisi kenar ışığı, perspektif ızgara, vinyet.
 *  Orta düzlem: sol ve sağda FARKLI devre yapıları (ana hat, dallar, düğümler, yarı saydam teknik paneller, eksik geometrik çerçeveler).
 *  Gözün arkasındaki büyük eksik çerçeveler ve alt holografik platform sahneye bağlı CSS katmanlarıdır (kais-holo-backdrop.css).
 *  Panellerdeki çizgiler anlamsız teknik desendir; veri, sayı veya grafik göstermez. Hareket yalnız CSS; JS döngüsü/canvas yok. */
import './kais-holo-backdrop.css'

type Side = {bus: string[]; branches: string[]; nodes: [number, number, number][]; panels: [number, number, number, number][]; frames: string[]; pulses: number[]}

// viewBox 0 0 360 900; x=0 ekran kenarı. Yoğunluk kenara yakın, içe doğru seyrekleşir (mobilde yalnız dış şerit görünür).
const LEFT: Side = {
  bus: ['M26 30V250L50 274V520L26 544V900', 'M70 150V236L92 258V338', 'M12 600V900'],
  branches: [
    'M26 92H84L112 120H196', 'M26 176H70', 'M50 330H130L158 302H252', 'M50 420H104L130 446V506H214', 'M92 300H124',
    'M26 640H82L112 670H226L250 694', 'M26 724H58L86 752V900', 'M12 800H120L148 828H300', 'M12 860H210L232 838H340', 'M196 120V170',
  ],
  nodes: [[196, 120, 4], [252, 302, 4], [214, 506, 4], [250, 694, 4], [300, 828, 3], [340, 838, 3], [26, 176, 5], [50, 420, 5], [26, 640, 5], [196, 170, 3], [124, 300, 3]],
  panels: [[96, 194, 150, 76], [128, 566, 124, 58]],
  frames: ['M300 380V270L330 240', 'M318 470V540L288 570H250'],
  pulses: [0, 3, 7],
}
const RIGHT: Side = {
  bus: ['M34 60V200L14 220V470L38 494V900', 'M80 330V420L60 440V560'],
  branches: [
    'M34 140H120L146 166V230', 'M14 300H96L124 272H236', 'M38 520H90L118 548H190L214 524', 'M60 470H142',
    'M38 610H70L98 638H270', 'M38 700H150L176 726V790', 'M38 860H164L190 834H320', 'M146 230H200', 'M14 390H52',
  ],
  nodes: [[200, 230, 4], [236, 272, 4], [214, 524, 4], [270, 638, 4], [176, 790, 3], [320, 834, 3], [142, 470, 3], [14, 390, 5], [38, 610, 5], [52, 390, 3]],
  panels: [[150, 330, 132, 64], [96, 742, 116, 54]],
  frames: ['M290 120H330V200', 'M260 420H320V500L296 524'],
  pulses: [1, 4, 6],
}

function Panel({x, y, w, h}: {x: number; y: number; w: number; h: number}) {
  // Köşe çentikli yarı saydam panel; içinde anlamsız teknik desen (kısa çizgi dizileri), veri değildir.
  const rows = Math.max(2, Math.floor((h - 20) / 12))
  return <g className="panel">
    <path className="shell" d={`M${x + 8} ${y}H${x + w}V${y + h - 8}L${x + w - 8} ${y + h}H${x}V${y + 8}Z`}/>
    <path className="notch" d={`M${x} ${y + 14}V${y + 8}L${x + 8} ${y}H${x + 22}M${x + w} ${y + h - 14}V${y + h - 8}L${x + w - 8} ${y + h}H${x + w - 22}`}/>
    <g className="pattern">{Array.from({length: rows}, (_, index) => <path key={index} d={`M${x + 12} ${y + 14 + index * 12}h${(w - 24) * (index % 3 === 0 ? .7 : index % 3 === 1 ? .45 : .85)}`}/>)}</g>
  </g>
}

function Structure({side, data}: {side: 'l' | 'r'; data: Side}) {
  const paths = [...data.bus, ...data.branches]
  return <svg className={`kaisHoloSide ${side}`} viewBox="0 0 360 900" preserveAspectRatio="xMinYMid slice">
    {data.panels.map(([x, y, w, h]) => <Panel key={`${x}-${y}`} x={x} y={y} w={w} h={h}/>)}
    <g className="frames">{data.frames.map(d => <path key={d} d={d}/>)}</g>
    <g className="bus">{data.bus.map(d => <path key={d} d={d}/>)}</g>
    <g className="branches">{data.branches.map(d => <path key={d} d={d}/>)}</g>
    <g className="nodes">{data.nodes.map(([x, y, r]) => <circle key={`${x}-${y}`} cx={x} cy={y} r={r}/>)}</g>
    <g className="pulses">{data.pulses.map((index, order) => <path key={index} d={paths[index]} pathLength={100} style={{animationDelay: `${order * 4.5}s`}}/>)}</g>
  </svg>
}

const PARTICLES = [[9, 24, 0], [18, 70, 2.4], [27, 86, 5.1], [72, 16, 3.6], [80, 74, 0.6], [91, 40, 4.4], [14, 46, 6], [86, 58, 7.2], [40, 92, 1.4], [62, 88, 3]]

export default function KaisHoloBackdrop() {
  return <div className="kaisHoloBackdrop" aria-hidden="true">
    <i className="base"/>
    <i className="ambient"/>
    <i className="rim"/>
    <i className="grid"/>
    <i className="floor"/>
    <Structure side="l" data={LEFT}/>
    <Structure side="r" data={RIGHT}/>
    <div className="particles">{PARTICLES.map(([x, y, delay]) => <i key={`${x}-${y}`} style={{left: `${x}%`, top: `${y}%`, animationDelay: `${delay}s`}}/>)}</div>
    <i className="dock"/>
    <i className="vignette"/>
  </div>
}

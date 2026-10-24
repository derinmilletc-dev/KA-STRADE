/** Kais hologram çekirdeği (masaüstü): gözü saran şeffaf enerji küresi, katmanlı yörünge halkaları ve alttaki enerji platformu.
 *  Tamamen dekoratif (aria-hidden, pointer-events:none). Ana göze (KaisEye) dokunmaz: tüm çizgiler göz alanının DIŞINDA kalır (maske).
 *  Hareket yalnız CSS transform/opacity ile; durum ve ses tepkisi kök öğedeki data-phase ve --kh-level değişkeninden okunur. */
import {useId} from 'react'

const TAU = Math.PI * 2
const round = (value: number) => Math.round(value * 100) / 100

// Küre (viewBox -250..250). Göz halkası ≈ r 96; maske r 116 içini boş bırakır.
const SPHERE_R = 232
const LATITUDES = [-58, -36, -14, 14, 36, 58].map(degrees => {
  const radians = degrees * Math.PI / 180
  const rx = SPHERE_R * Math.cos(radians)
  return {y: round(SPHERE_R * Math.sin(radians)), rx: round(rx), ry: round(rx * .16)}
})
const MERIDIAN_DELAYS = [0, -10, -20, -30]  // 40 sn'lik döngüde eşit aralıklı dört boylam
const PARTICLES = Array.from({length: 14}, (_, index) => {
  const angle = index * 137.508 * Math.PI / 180
  const radius = 138 + (index * 41) % 88
  const x = Math.cos(angle) * radius, y = Math.sin(angle) * radius
  return {x: round(x), y: round(y), dx: round(-x * .3), dy: round(-y * .3), delay: round((index * .37) % 2.6), size: index % 4 === 0 ? 1.8 : 1.1}
})
const TICKS = Array.from({length: 120}, (_, index) => index)
const NODES_A = [0, 120, 240]
const NODES_B = [40, 220]

export function KaisHoloSphere() {
  const id = `khs-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return <svg className="khSphere" viewBox="-250 -250 500 500" aria-hidden="true" focusable="false">
    <defs>
      <radialGradient id={`${id}-shell`} r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform={`scale(${SPHERE_R})`}>
        <stop offset=".5" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: 0}}/>
        <stop offset=".86" style={{stopColor: 'rgb(var(--kh-emerald))', stopOpacity: .05}}/>
        <stop offset=".98" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: .16}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: 0}}/>
      </radialGradient>
      <linearGradient id={`${id}-scan`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: 0}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-ice))', stopOpacity: .12}}/>
      </linearGradient>
      <mask id={`${id}-clear`} maskUnits="userSpaceOnUse" x="-250" y="-250" width="500" height="500">
        <rect x="-250" y="-250" width="500" height="500" fill="#fff"/>
        <circle r="116" fill="#000"/>
      </mask>
      <clipPath id={`${id}-ball`}><circle r={SPHERE_R}/></clipPath>
    </defs>
    <circle className="shell" r={SPHERE_R} fill={`url(#${id}-shell)`}/>
    <g mask={`url(#${id}-clear)`}>
      <circle className="rim" r={SPHERE_R}/>
      <g className="lat">{LATITUDES.map(row => <ellipse key={row.y} cy={row.y} rx={row.rx} ry={row.ry}/>)}</g>
      <g className="lon">{MERIDIAN_DELAYS.map(delay => <ellipse key={delay} rx={SPHERE_R} ry={SPHERE_R} style={{animationDelay: `${delay}s`}}/>)}<path d={`M0 ${-SPHERE_R}V${SPHERE_R}`}/></g>
      <g className="orbit o1" transform="rotate(-22)"><ellipse rx="244" ry="64" className="path"/><ellipse rx="244" ry="64" className="runner" pathLength={100}/></g>
      <g className="orbit o2" transform="rotate(26)"><ellipse rx="226" ry="52" className="path"/><ellipse rx="226" ry="52" className="runner" pathLength={100}/></g>
      <g className="scanClip" clipPath={`url(#${id}-ball)`}><g className="scanLine"><rect x={-SPHERE_R} y="-16" width={SPHERE_R * 2} height="16" fill={`url(#${id}-scan)`}/><rect className="edge" x={-SPHERE_R} y="-1.2" width={SPHERE_R * 2} height="1.2"/></g></g>
      <circle className="pulse" r={SPHERE_R}/>
    </g>
    {/* Yörünge halkaları: göz çevresinde, farklı hız ve yönlerde (yapısal halkalar sabit) */}
    <g className="rings">
      <circle className="rC" r="152"/>
      <g className="rTicks">{TICKS.map(index => <path key={index} d={index % 10 ? 'M0 -168V-171' : 'M0 -166V-174'} transform={`rotate(${index * 3})`}/>)}</g>
      <g className="rA"><circle r="126" strokeDasharray="1.2 5"/>{NODES_A.map(angle => <circle key={angle} className="node" r="2.2" cx={round(126 * Math.cos(angle * TAU / 360))} cy={round(126 * Math.sin(angle * TAU / 360))}/>)}</g>
      <g className="rB"><circle r="140" strokeDasharray="64 22 10 22 120 30 40 30"/></g>
      <g className="rE"><circle r="196" strokeDasharray="150 46 18 46 210 60"/>{NODES_B.map(angle => <circle key={angle} className="node" r="2.6" cx={round(196 * Math.cos(angle * TAU / 360))} cy={round(196 * Math.sin(angle * TAU / 360))}/>)}</g>
      <g className="rScan"><circle r="140" pathLength={100} strokeDasharray="7 93"/></g>
      <g className="rVoice"><circle r="118"/></g>
    </g>
    <g className="particles">{PARTICLES.map(particle => <circle key={`${particle.x}-${particle.y}`} cx={particle.x} cy={particle.y} r={particle.size}
      style={{'--dx': `${particle.dx}px`, '--dy': `${particle.dy}px`, animationDelay: `${particle.delay}s`} as React.CSSProperties}/>)}</g>
  </svg>
}

// Platform (viewBox -380 -110 760 240; merkez 0,0). Halkaların rolleri: dış sabit çerçeve, işaretçili yörünge, ses halkası, parlak çekirdek.
const RADIALS = Array.from({length: 16}, (_, index) => {
  const angle = index * TAU / 16
  return `M${round(150 * Math.cos(angle))} ${round(27 * Math.sin(angle))}L${round(360 * Math.cos(angle))} ${round(64 * Math.sin(angle))}`
}).join('')
const BARS = Array.from({length: 23}, (_, index) => {
  const t = index / 22
  const angle = (18 + t * 144) * Math.PI / 180
  return {x: round(206 * Math.cos(angle)), y: round(37 * Math.sin(angle)), k: round(.3 + .7 * Math.sin(Math.PI * t) * (.6 + .4 * Math.abs(Math.sin(index * 1.9))))}
})

export function KaisHoloPlatform({complete}: {complete: number}) {
  const id = `khp-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return <svg className="khPlatform" viewBox="-380 -110 760 240" aria-hidden="true" focusable="false">
    <defs>
      <radialGradient id={`${id}-glow`} r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform="scale(300 56)">
        <stop offset="0" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: .42}}/>
        <stop offset=".35" style={{stopColor: 'rgb(var(--kh-emerald))', stopOpacity: .16}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-emerald))', stopOpacity: 0}}/>
      </radialGradient>
      <radialGradient id={`${id}-core`} r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform="scale(82 15)">
        <stop offset="0" stopColor="#e9fff8" stopOpacity=".9"/>
        <stop offset=".45" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: .6}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: 0}}/>
      </radialGradient>
      <linearGradient id={`${id}-beam`} x1="0" y1="0" x2="0" y2="-110" gradientUnits="userSpaceOnUse">
        <stop offset="0" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: .5}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-soft))', stopOpacity: 0}}/>
      </linearGradient>
      <radialGradient id={`${id}-reflect`} r="1" cx="0" cy="0" gradientUnits="userSpaceOnUse" gradientTransform="translate(0 40) scale(280 70)">
        <stop offset="0" style={{stopColor: 'rgb(var(--kh-emerald))', stopOpacity: .16}}/>
        <stop offset="1" style={{stopColor: 'rgb(var(--kh-emerald))', stopOpacity: 0}}/>
      </radialGradient>
    </defs>
    <ellipse className="reflect" cy="40" rx="280" ry="70" fill={`url(#${id}-reflect)`}/>
    <ellipse className="glow" rx="300" ry="56" fill={`url(#${id}-glow)`}/>
    <path className="radials" d={RADIALS}/>
    <ellipse className="pOuter" rx="360" ry="64"/>
    <ellipse className="pOuterRun" rx="360" ry="64" pathLength={100}/>
    <ellipse className="pTrack" rx="290" ry="52"/>
    <ellipse className="pTrackRun" rx="290" ry="52" pathLength={100}/>
    <ellipse className="pScan" rx="220" ry="39" pathLength={100}/>
    <g className="pVoice"><ellipse rx="220" ry="39"/></g>
    <ellipse className="pCoreRim" rx="150" ry="27"/>
    <ellipse className="pCore" rx="82" ry="15" fill={`url(#${id}-core)`}/>
    <g className="pPulse"><ellipse rx="150" ry="27"/></g>
    {complete > 0 && <g key={complete} className="pDone"><ellipse rx="150" ry="27"/></g>}
    <g className="beam"><rect x="-16" y="-110" width="32" height="110" fill={`url(#${id}-beam)`}/><path d="M0 0V-110"/></g>
    <g className="wave">{BARS.map(bar => <path key={bar.x} d={`M${bar.x} ${bar.y}v-16`} style={{'--k': bar.k} as React.CSSProperties}/>)}</g>
  </svg>
}


// Sinir ağı katmanı: sabit (deterministik) düğümler, yakın düğümleri bağlayan ince çizgiler ve çizgiler boyunca ilerleyen küçük sinyaller.
// Tamamen dekoratif; durumla yalnız hızı ve parlaklığı değişir (CSS). Göz bölgesi (r < 124) boş bırakılır.
const NEURAL_NODES = Array.from({length: 46}, (_, index) => {
  const angle = index * 137.508 * Math.PI / 180
  const radius = 128 + ((index * 53) % 100)
  return {x: round(Math.cos(angle) * radius), y: round(Math.sin(angle) * radius), r: radius}
})
const NEURAL_LINKS: [number, number][] = (() => {
  const links: [number, number][] = []
  NEURAL_NODES.forEach((a, i) => {
    NEURAL_NODES.map((b, j) => ({j, d: Math.hypot(a.x - b.x, a.y - b.y)})).filter(item => item.j > i && item.d < 74)
      .sort((x, y) => x.d - y.d).slice(0, 2).forEach(item => links.push([i, item.j]))
  })
  return links
})()
const NEURAL_PULSES = NEURAL_LINKS.filter((_, index) => index % 5 === 0).slice(0, 9)

export function KaisHoloNeural() {
  return <svg className="khNeural" viewBox="-250 -250 500 500" aria-hidden="true" focusable="false">
    <g className="khNeuralSpin">
      <g className="khNeuralLinks">{NEURAL_LINKS.map(([a, b]) => <path key={`${a}-${b}`} d={`M${NEURAL_NODES[a].x} ${NEURAL_NODES[a].y}L${NEURAL_NODES[b].x} ${NEURAL_NODES[b].y}`} style={{opacity: round(.12 + (1 - (NEURAL_NODES[a].r - 128) / 100) * .32)}}/>)}</g>
      <g className="khNeuralPulses">{NEURAL_PULSES.map(([a, b], index) => <path key={`${a}-${b}`} pathLength={100} d={`M${NEURAL_NODES[a].x} ${NEURAL_NODES[a].y}L${NEURAL_NODES[b].x} ${NEURAL_NODES[b].y}`} style={{animationDelay: `${-(index * .45).toFixed(2)}s`}}/>)}</g>
      <g className="khNeuralNodes">{NEURAL_NODES.map((node, index) => <circle key={index} cx={node.x} cy={node.y} r={index % 6 === 0 ? 2.8 : 1.7} style={{opacity: round(.35 + (1 - (node.r - 128) / 100) * .6)}}/>)}</g>
    </g>
  </svg>
}

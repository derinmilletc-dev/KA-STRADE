/** Hologram yan panelleri (yalnız masaüstü; dar ekranlarda CSS ile gizli).
 *  GERÇEK içerik taşıyan paneller: Durum (mevcut sesli sohbet aşaması), Ses etkinliği (gerçek tanıma/okuma olayları, --kh-level),
 *  Eylemler (Kais'in bu oturumda gerçekten yaptığı işlemler). Diğer üç panel tamamen DEKORATİFTİR: başlık, sayı ya da veri
 *  göstermez; yalnız ilgili gerçek aşamada (düşünme / konuşma) hafifçe parlar. */
import type {HoloPhase} from './KaisAgentHolo'

const COPY = {
  tr: {status: 'Durum', voice: 'Ses etkinliği', actions: 'Eylemler', none: 'Henüz eylem yok',
    you: 'Sen konuşuyorsun', kais: 'Kais konuşuyor', quiet: 'Sessiz',
    phases: {listening: 'Dinliyor', thinking: 'Düşünüyor', speaking: 'Konuşuyor', error: 'Sorun var'}},
  en: {status: 'Status', voice: 'Voice activity', actions: 'Actions', none: 'No actions yet',
    you: 'You are speaking', kais: 'Kais is speaking', quiet: 'Quiet',
    phases: {listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking', error: 'Problem'}},
}
const PHASES: HoloPhase[] = ['listening', 'thinking', 'speaking', 'error']
const BARS = Array.from({length: 28}, (_, index) => Math.round((.25 + .75 * Math.abs(Math.sin(index * 1.37)) * Math.sin(Math.PI * (index + .5) / 28)) * 100) / 100)
const NET: [number, number][] = [[18, 22], [52, 14], [86, 30], [124, 18], [160, 36], [196, 20], [30, 58], [70, 52], [112, 64], [150, 54], [188, 66], [48, 86], [96, 92], [138, 84], [178, 94]]
const LINKS: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [0, 6], [6, 7], [7, 2], [7, 8], [8, 3], [8, 9], [9, 4], [9, 10], [10, 5], [6, 11], [11, 12], [12, 8], [12, 13], [13, 9], [13, 14], [14, 10]]

function Panel({className, title, children, hidden}: {className: string; title?: string; children: React.ReactNode; hidden?: boolean}) {
  return <section className={`khPanel ${className}`} aria-hidden={hidden || undefined} aria-label={hidden ? undefined : title}>
    <i className="khPanelCorner" aria-hidden="true"/>
    {title && <header>{title}</header>}
    {children}
  </section>
}

export default function KaisHoloPanels({phase, actions, language}: {phase: HoloPhase; actions: string[]; language: 'tr' | 'en'}) {
  const copy = COPY[language]
  const speaker = phase === 'listening' ? copy.you : phase === 'speaking' ? copy.kais : copy.quiet
  return <div className="khPanels" data-phase={phase}>
    <div className="khColumn l">
      <Panel className="khNet" hidden>
        <svg viewBox="0 0 214 108"><g className="links">{LINKS.map(([a, b]) => <path key={`${a}-${b}`} d={`M${NET[a][0]} ${NET[a][1]}L${NET[b][0]} ${NET[b][1]}`}/>)}</g>
          <path className="run" pathLength={100} d={`M${[0, 6, 7, 8, 9, 10, 5].map(index => NET[index].join(' ')).join('L')}`}/>
          <g className="nodes">{NET.map(([x, y], index) => <circle key={index} cx={x} cy={y} r={index % 5 === 2 ? 2.6 : 1.6}/>)}</g></svg>
      </Panel>
      <Panel className="khVoice" title={copy.voice} hidden>
        <div className="khVoiceBars">{BARS.map((k, index) => <i key={index} style={{'--k': k} as React.CSSProperties}/>)}</div>
        <small>{speaker}</small>
      </Panel>
      <Panel className="khRadar" hidden>
        <svg viewBox="-60 -60 120 120"><circle r="54"/><circle r="36"/><circle r="18"/><path d="M-56 0H56M0 -56V56"/>
          <g className="sweep"><path d="M0 0L0 -54A54 54 0 0 1 38.2 -38.2Z"/></g><circle className="blip" cx="22" cy="-30" r="2"/><circle className="blip b2" cx="-30" cy="14" r="2"/></svg>
      </Panel>
    </div>
    <div className="khColumn r">
      <Panel className="khStatus" title={copy.status} hidden>
        <ul>{PHASES.map(item => <li key={item} data-on={item === phase}><i/>{copy.phases[item]}</li>)}</ul>
      </Panel>
      <Panel className="khActions" title={copy.actions}>
        {actions.length ? <ul>{actions.slice(-4).map((action, index) => <li key={`${action}-${index}`}>▸ {action}</li>)}</ul> : <p>{copy.none}</p>}
      </Panel>
      <Panel className="khGlobe" hidden>
        <svg viewBox="-60 -60 120 120"><circle r="44"/><ellipse rx="44" ry="15"/><ellipse rx="44" ry="15" cy="-24" className="f"/><ellipse rx="44" ry="15" cy="24" className="f"/>
          <g className="spin"><ellipse rx="30" ry="44"/><ellipse rx="12" ry="44"/></g><ellipse className="orbit" rx="58" ry="12" transform="rotate(-18)"/><circle className="sat" r="2.4" cx="0" cy="-12"/></svg>
      </Panel>
    </div>
  </div>
}

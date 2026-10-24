/** Hologram yan panelleri (yalnız masaüstü; dar ekranlarda CSS ile gizli).
 *  GERÇEK içerik taşıyan paneller: Durum (mevcut sesli sohbet aşaması), Ses etkinliği (gerçek tanıma/okuma olayları, --kh-level),
 *  Eylemler (Kais'in bu oturumda gerçekten yaptığı işlemler). Diğer üç panel tamamen DEKORATİFTİR: başlık, sayı ya da veri
 *  göstermez; yalnız ilgili gerçek aşamada (düşünme / konuşma) hafifçe parlar. */
import type {HoloPhase} from './KaisAgentHolo'

const COPY = {
  tr: {status: 'Durum', voice: 'Ses etkinliği', actions: 'Eylemler', none: 'Henüz eylem yok',
    recent: 'Son konuşma', noRecent: 'Henüz konuşma yok', youLabel: 'Sen', kaisLabel: 'Kais', io: 'Mikrofon ve ses', micLabel: 'Mikrofon', outLabel: 'Sesli cevap',
    mic: {unsupported: 'Bu tarayıcıda desteklenmiyor', starting: 'Hazırlanıyor', failed: 'Sorun var', listening: 'Açık · dinliyor', on: 'Açık'},
    out: {unsupported: 'Bu tarayıcıda desteklenmiyor', on: 'Açık', off: 'Kapalı'}, turnOn: 'Aç', turnOff: 'Kapat',
    you: 'Seni dinliyor', kais: 'Kais konuşuyor', quiet: 'Sessiz',
    phases: {listening: 'Dinliyor', thinking: 'Düşünüyor', speaking: 'Konuşuyor', error: 'Sorun var'}},
  en: {status: 'Status', voice: 'Voice activity', actions: 'Actions', none: 'No actions yet',
    recent: 'Recent conversation', noRecent: 'No conversation yet', youLabel: 'You', kaisLabel: 'Kais', io: 'Microphone and audio', micLabel: 'Microphone', outLabel: 'Spoken replies',
    mic: {unsupported: 'Not supported in this browser', starting: 'Preparing', failed: 'Problem', listening: 'On · listening', on: 'On'},
    out: {unsupported: 'Not supported in this browser', on: 'On', off: 'Off'}, turnOn: 'Turn on', turnOff: 'Turn off',
    you: 'Listening to you', kais: 'Kais is speaking', quiet: 'Quiet',
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

export type HoloRecent = {role: 'user' | 'assistant'; text: string}
export type HoloIO = {micState: 'unsupported' | 'starting' | 'failed' | 'listening' | 'on'; outputSupported: boolean; repliesOn: boolean; onToggleReplies: () => void}
export default function KaisHoloPanels({phase, actions, language, recent = [], io}: {phase: HoloPhase; actions: string[]; language: 'tr' | 'en'; recent?: HoloRecent[]; io?: HoloIO}) {
  const copy = COPY[language]
  const speaker = phase === 'listening' ? copy.you : phase === 'speaking' ? copy.kais : copy.quiet
  return <div className="khPanels" data-phase={phase}>
    <div className="khColumn l">
      <Panel className="khRecent" title={copy.recent}>
        {recent.length ? <ul>{recent.slice(-3).map((row, index) => <li key={index} data-role={row.role}><b>{row.role === 'user' ? copy.youLabel : copy.kaisLabel}</b> {row.text.length > 90 ? `${row.text.slice(0, 90)}…` : row.text}</li>)}</ul> : <p>{copy.noRecent}</p>}
      </Panel>
      <Panel className="khVoice" title={copy.voice} hidden>
        <div className="khVoiceBars">{BARS.map((k, index) => <i key={index} style={{'--k': k} as React.CSSProperties}/>)}</div>
        <small>{speaker}</small>
      </Panel>
    </div>
    <div className="khColumn r">
      <Panel className="khStatus" title={copy.status} hidden>
        <ul>{PHASES.map(item => <li key={item} data-on={item === phase}><i/>{copy.phases[item]}</li>)}</ul>
      </Panel>
      {io && <Panel className="khIO" title={copy.io}>
        <ul>
          <li><span>{copy.micLabel}</span><b data-state={io.micState}>{copy.mic[io.micState]}</b></li>
          <li><span>{copy.outLabel}</span><b data-state={io.outputSupported ? io.repliesOn ? 'on' : 'off' : 'unsupported'}>{io.outputSupported ? io.repliesOn ? copy.out.on : copy.out.off : copy.out.unsupported}</b></li>
        </ul>
        {io.outputSupported && <button type="button" className="khIOToggle" onClick={io.onToggleReplies}>{copy.outLabel}: {io.repliesOn ? copy.turnOff : copy.turnOn}</button>}
      </Panel>}
      <Panel className="khActions" title={copy.actions}>
        {actions.length ? <ul>{actions.slice(-4).map((action, index) => <li key={`${action}-${index}`}>▸ {action}</li>)}</ul> : <p>{copy.none}</p>}
      </Panel>
    </div>
  </div>
}

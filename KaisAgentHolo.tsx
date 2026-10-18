import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {X, MessageSquare, RotateCcw} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import KaisVoicePicker from './KaisVoicePicker'
import {emitKaisReaction} from './kais-reactions'
import {PAGE_GLYPH} from './kais-portal-glyphs'
import './kais-agent-holo.css'

/** Gerçekten gözlemlenebilen durumlar (sesli oturum + sohbet isteği yaşam döngüsü):
 *  listening = mikrofon açık · thinking = istek gönderildi, cevap bekleniyor (PROCESSING) · speaking = cevap geldi (RESPONSE_RECEIVED) · error = istek başarısız.
 *  IDLE (mikrofonsuz bekleme) ve ANALYZING (ayrı bir sunucu analiz olayı) uygulamada gözlemlenemediği için SİMÜLE EDİLMEZ.
 *  CANCELLED: bileşen kapanınca kaldırılır; rAF ve zamanlayıcılar temizlenir. */
export type HoloPhase = 'listening' | 'thinking' | 'speaking' | 'error'
export type HoloConfirm = {title: string; detail: string; confirmLabel: string; cancelLabel: string; busy: boolean; onConfirm: () => void; onCancel: () => void}
export type HoloPortal = {view: string; label: string; commit: () => void; done: () => void}
type Props = {alarm?: string | null; portal?: HoloPortal | null; confirm?: HoloConfirm; phase: HoloPhase; status: string; userText?: string; replyText?: string; actions: string[]; chatLabel: string; closeLabel: string; retryLabel: string; slowText: string; language: 'tr' | 'en'; onOpenChat: () => void; onMinimize: () => void; onRetry: () => void}

// derece/sn: birincil halka, ters yönde ikincil hassas halka, çok yavaş üçüncü halka. Durum geçişleri hızı yumuşakça (≈0.6 sn) değiştirir.
const SPEEDS: Record<HoloPhase, [number, number, number]> = {
  listening: [24, -18, -8], thinking: [120, -80, -30], speaking: [38, -28, -12], error: [0, 0, 0],
}
const TIME_CONSTANT = .22

/** Halkaları rAF ile döndürür; hedef hıza üstel yumuşatma uygular, hidden/reduced-motion'da ve hız sıfırlanınca durur. */
function useRings(phase: HoloPhase, paused: boolean, refs: React.RefObject<SVGGElement | null>[]) {
  const target = useRef(SPEEDS[phase])
  const kick = useRef<() => void>(() => undefined)
  target.current = SPEEDS[phase]
  useEffect(() => {
    if (paused || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const angle = [0, 0, 0], velocity = [0, 0, 0]
    let frame = 0, last = 0, stopped = false
    const step = (now: number) => {
      if (stopped) return
      const dt = Math.min(.1, last ? (now - last) / 1000 : 0); last = now
      const blend = 1 - Math.exp(-dt / TIME_CONSTANT)
      let moving = false
      refs.forEach((ref, index) => {
        velocity[index] += (target.current[index] - velocity[index]) * blend
        if (Math.abs(velocity[index]) < .05 && target.current[index] === 0) velocity[index] = 0
        if (velocity[index] !== 0) moving = true
        angle[index] = (angle[index] + velocity[index] * dt) % 360
        ref.current?.setAttribute('transform', `rotate(${angle[index].toFixed(2)})`)
      })
      frame = moving || target.current.some(value => value !== 0) ? requestAnimationFrame(step) : 0
      if (!frame) last = 0
    }
    kick.current = () => { if (!frame && !stopped) { last = 0; frame = requestAnimationFrame(step) } }
    kick.current()
    return () => { stopped = true; cancelAnimationFrame(frame); kick.current = () => undefined }
  }, [paused])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { kick.current() }, [phase])
}

/** Hologram ekran. Ana göz (KaisEye) kaynak dosyalarına dokunulmaz ve ekranda hareketsiz kalır (CSS ile yerleşik kırpma/bakış hareketi kapatılır);
 *  tüm hareket gözün DIŞINDaki bağımsız katmanlardır. */
export default function KaisAgentHolo({alarm, portal, confirm, phase, status, userText, replyText, actions, chatLabel, closeLabel, retryLabel, onOpenChat, onMinimize, onRetry, slowText, language}: Props) {
  const [hidden, setHidden] = useState(() => document.hidden)
  const [beam, setBeam] = useState(phase === 'thinking')
  const [slow, setSlow] = useState(false)
  // Gerçek bekleme süresi: cevap 8 sn'yi aşarsa kullanıcıya sunucunun geciktiğini söyler (sahte ilerleme değil).
  useEffect(() => {
    setSlow(false)
    if (phase !== 'thinking') return
    const timer = window.setTimeout(() => setSlow(true), 8000)
    return () => window.clearTimeout(timer)
  }, [phase])
  const stage = useRef<HTMLDivElement>(null)
  const pupil = useRef<SVGSVGElement>(null)
  const primary = useRef<SVGGElement>(null), secondary = useRef<SVGGElement>(null), precision = useRef<SVGGElement>(null)
  // Sayfa geçişi sahneleri (yalnızca ayrı katmanlar; ana göz hiç dönüştürülmez): A algıla → B göz bebeğinde pencere → C büyüme → D gerçek sayfa.
  const [scene, setScene] = useState<'' | 'A' | 'B' | 'C' | 'D'>('')
  const [fill, setFill] = useState(60)
  useEffect(() => {
    if (!portal) { setScene(''); return }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timers: number[] = []
    let committed = false
    const commit = () => { if (committed) return; committed = true; try { portal.commit() } catch { /* sayfa açılamazsa mevcut güvenli davranış */ } }
    const rect = stage.current?.getBoundingClientRect()
    if (rect) {
      // Çerçeve merkezden (göz bebeği) çıkar; en uzak ekran kenarını da kaplayacak ölçek.
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2
      setFill(Math.max(Math.max(cx, window.innerWidth - cx) / 30, Math.max(cy, window.innerHeight - cy) / 22) * 1.12)
    }
    const at = (ms: number, fn: () => void) => timers.push(window.setTimeout(fn, ms))
    if (reduced) { setScene('B'); at(450, () => { commit(); setScene('D') }); at(800, () => portal.done()) }
    else {
      setScene('A'); at(550, () => setScene('B')); at(1050, () => setScene('C'))
      at(1700, () => { commit(); setScene('D') }); at(2150, () => portal.done())
    }
    return () => { timers.forEach(window.clearTimeout); commit() }
  }, [portal])  // eslint-disable-line react-hooks/exhaustive-deps
  const ringPhase: HoloPhase = scene === 'A' || scene === 'B' ? 'thinking' : scene === 'C' || scene === 'D' ? 'speaking' : phase
  useRings(ringPhase, hidden, [primary, secondary, precision])
  // Gözün KENDİ mevcut bakış altyapısı (kais:react): telefonda işaretçi olmadığı için göz kıpırdamıyordu. Düşünürken sık, diğer durumlarda seyrek bakar.
  useEffect(() => {
    if (hidden || phase === 'error') return
    let timer = 0
    const next = () => {
      const rect = stage.current?.getBoundingClientRect()
      if (rect) {
        const angle = Math.random() * Math.PI * 2
        emitKaisReaction({type: 'navigation', point: {x: rect.left + rect.width / 2 + Math.cos(angle) * 220, y: rect.top + rect.height / 2 + Math.sin(angle) * 90}})
      }
      timer = window.setTimeout(next, (phase === 'thinking' ? 900 : 1800) + Math.random() * (phase === 'thinking' ? 700 : 1500))
    }
    timer = window.setTimeout(next, 500)
    return () => window.clearTimeout(timer)
  }, [phase, hidden])
  // Göz bakınca iris/göz bebeği gözün kendi altyapısıyla kayar (data-look-x/y). Göz bebeği katmanı aynı miktarda kaydırılır, böylece bebeğin içinde kalır.
  useEffect(() => {
    const eye = stage.current?.querySelector<SVGSVGElement>('.kaisEye')
    const overlay = pupil.current
    if (!eye || !overlay) return
    const follow = () => {
      const scale = eye.getBoundingClientRect().width / 96
      const x = Number(eye.dataset.lookX) || 0, y = Number(eye.dataset.lookY) || 0
      overlay.style.transform = `translate(${(x * 7 * scale).toFixed(2)}px, ${(y * 7 * .6 * scale).toFixed(2)}px)`
    }
    follow()
    const observer = new MutationObserver(follow)
    observer.observe(eye, {attributes: true, attributeFilter: ['data-look-x', 'data-look-y']})
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const change = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', change)
    return () => document.removeEventListener('visibilitychange', change)
  }, [])
  // Tarama ışığı yalnızca istek bekleniyorken; cevap/hata gelince 450 ms sönümlenip animasyonu kaldırılır.
  useEffect(() => {
    if (phase === 'thinking') { setBeam(true); return }
    const timer = window.setTimeout(() => setBeam(false), 450)
    return () => window.clearTimeout(timer)
  }, [phase])
  return createPortal(<div className="kaisHolo" data-phase={phase} data-scene={scene || undefined} data-alarm={alarm ? true : undefined} data-paused={hidden} role="dialog" aria-label="Kais" aria-live="polite">
    <KaisVoicePicker language={language}/>
    <button type="button" className="kaisHoloClose" onClick={onMinimize} aria-label={closeLabel}><X aria-hidden="true"/></button>
    <div className="kaisHoloStage" ref={stage}>
      <div className="kaisHoloGlow" aria-hidden="true"/>
      {alarm && <svg className="kaisHoloAlarm" viewBox="-160 -160 320 320" aria-hidden="true"><circle r="146"/><circle r="152" className="b"/></svg>}
      <svg className="kaisHoloRings" viewBox="-160 -160 320 320" aria-hidden="true">
        <circle className="ping" r="150"/>
        <g className="ring r1" ref={primary}><circle r="156"/></g>
        <g className="ring r2" ref={secondary}><path d="M-150 0A150 150 0 0 1 0 -150M150 0A150 150 0 0 1 0 150"/></g>
        <g className="ring r3"><path d="M128 0h7" transform="rotate(0)"/><path d="M128 0h2" transform="rotate(6)"/><path d="M128 0h2" transform="rotate(12)"/><path d="M128 0h2" transform="rotate(18)"/><path d="M128 0h2" transform="rotate(24)"/><path d="M128 0h4" transform="rotate(30)"/><path d="M128 0h2" transform="rotate(36)"/><path d="M128 0h2" transform="rotate(42)"/><path d="M128 0h2" transform="rotate(48)"/><path d="M128 0h2" transform="rotate(54)"/><path d="M128 0h4" transform="rotate(60)"/><path d="M128 0h2" transform="rotate(66)"/><path d="M128 0h2" transform="rotate(72)"/><path d="M128 0h2" transform="rotate(78)"/><path d="M128 0h2" transform="rotate(84)"/><path d="M128 0h7" transform="rotate(90)"/><path d="M128 0h2" transform="rotate(96)"/><path d="M128 0h2" transform="rotate(102)"/><path d="M128 0h2" transform="rotate(108)"/><path d="M128 0h2" transform="rotate(114)"/><path d="M128 0h4" transform="rotate(120)"/><path d="M128 0h2" transform="rotate(126)"/><path d="M128 0h2" transform="rotate(132)"/><path d="M128 0h2" transform="rotate(138)"/><path d="M128 0h2" transform="rotate(144)"/><path d="M128 0h4" transform="rotate(150)"/><path d="M128 0h2" transform="rotate(156)"/><path d="M128 0h2" transform="rotate(162)"/><path d="M128 0h2" transform="rotate(168)"/><path d="M128 0h2" transform="rotate(174)"/><path d="M128 0h7" transform="rotate(180)"/><path d="M128 0h2" transform="rotate(186)"/><path d="M128 0h2" transform="rotate(192)"/><path d="M128 0h2" transform="rotate(198)"/><path d="M128 0h2" transform="rotate(204)"/><path d="M128 0h4" transform="rotate(210)"/><path d="M128 0h2" transform="rotate(216)"/><path d="M128 0h2" transform="rotate(222)"/><path d="M128 0h2" transform="rotate(228)"/><path d="M128 0h2" transform="rotate(234)"/><path d="M128 0h4" transform="rotate(240)"/><path d="M128 0h2" transform="rotate(246)"/><path d="M128 0h2" transform="rotate(252)"/><path d="M128 0h2" transform="rotate(258)"/><path d="M128 0h2" transform="rotate(264)"/><path d="M128 0h7" transform="rotate(270)"/><path d="M128 0h2" transform="rotate(276)"/><path d="M128 0h2" transform="rotate(282)"/><path d="M128 0h2" transform="rotate(288)"/><path d="M128 0h2" transform="rotate(294)"/><path d="M128 0h4" transform="rotate(300)"/><path d="M128 0h2" transform="rotate(306)"/><path d="M128 0h2" transform="rotate(312)"/><path d="M128 0h2" transform="rotate(318)"/><path d="M128 0h2" transform="rotate(324)"/><path d="M128 0h4" transform="rotate(330)"/><path d="M128 0h2" transform="rotate(336)"/><path d="M128 0h2" transform="rotate(342)"/><path d="M128 0h2" transform="rotate(348)"/><path d="M128 0h2" transform="rotate(354)"/></g>
        <g className="ring r4" ref={precision}><circle r="138" strokeDasharray="30 12 6 12"/></g>
        <g className="rulers"><path transform="rotate(0)" d="M156 0h7M156 -3v6"/><path transform="rotate(90)" d="M156 0h7M156 -3v6"/><path transform="rotate(180)" d="M156 0h7M156 -3v6"/><path transform="rotate(270)" d="M156 0h7M156 -3v6"/></g>
        <g className="measure"><g className="m" style={{'--d': '0.0s'} as React.CSSProperties} transform="translate(-146 -34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '1.1s'} as React.CSSProperties} transform="translate(146 -34)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '2.2s'} as React.CSSProperties} transform="translate(-114 98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g><g className="m" style={{'--d': '3.3000000000000003s'} as React.CSSProperties} transform="translate(114 98)"><circle r="1.6"/><path d="M-6 0h3M3 0h3M0-6v3M0 3v3"/></g></g>
      </svg>
      <div className="kaisHoloEye"><KaisEye size={260} state={phase === 'thinking' ? 'thinking' : 'idle'}/>
        {/* Göz bebeği içi: ayrı, gözün çizimine dokunmayan katman (göz bebeği alanına kırpılı). Durumla değişir: tarama / yayılan dalga / sakin halka. */}
        <svg className="kaisHoloPupil" ref={pupil} viewBox="-10 -10 20 20" aria-hidden="true">
          <circle className="pRing" r="9.2"/>
          <g className="pSpinA"><circle r="7.4" strokeDasharray="2.2 1.6"/></g>
          <g className="pSpinB"><circle r="5" strokeDasharray="1 2"/></g>
          <g className="pSweep"><path d="M0 0L0 -9.4" className="pLine"/><path d="M0 0L0 -9.4A9.4 9.4 0 0 0 -6.6 -6.7Z" className="pWedge"/></g>
          <g className="pBlips"><circle cx="3.6" cy="-3.9" r=".55"/><circle cx="-4.4" cy="1.8" r=".55"/><circle cx="1.6" cy="5.2" r=".55"/></g>
          <circle className="pWave pWave1" r="9"/><circle className="pWave pWave2" r="9"/>
          <circle className="pDot" r=".9"/>
        </svg>
      </div>
      {scene && <div className="kaisHoloPortal" aria-hidden="true" style={{'--fill': fill} as React.CSSProperties}>
        <i className="frame"/>
        <div className="tag"><svg viewBox="0 0 24 24"><path d={PAGE_GLYPH[portal?.view ?? ''] ?? PAGE_GLYPH.dashboard}/></svg><b>{portal?.label}</b></div>
      </div>}
      <div className="kaisHoloScanBand" data-on={(beam && phase === 'thinking') || scene === 'A'} aria-hidden="true">{(beam || scene === 'A') && <i/>}</div>
      <div className="kaisHoloFloor" aria-hidden="true"/>
    </div>
    {alarm && <p className="kaisHoloAlarmText" role="alert">{alarm}</p>}
    {actions.length > 0 && <div className="kaisHoloChips">{actions.slice(-4).map((action, index) => <span key={`${action}-${index}`} className="kaisHoloChip">▸ {action}</span>)}</div>}
    <p className="kaisHoloStatus" data-error={phase === 'error'}>{status}</p>
    {slow && phase === 'thinking' && <p className="kaisHoloSlow">{slowText}</p>}
    {userText && phase !== 'error' && <p className="kaisHoloUser">“{userText}”</p>}
    {replyText && phase === 'speaking' && <p className="kaisHoloReply">{replyText}</p>}
    {confirm && <div className="kaisHoloConfirm" role="group" aria-label={confirm.title}><b>{confirm.title}</b><small>{confirm.detail}</small>
      <div><button type="button" className="ok" disabled={confirm.busy} onClick={confirm.onConfirm}>{confirm.confirmLabel}</button><button type="button" disabled={confirm.busy} onClick={confirm.onCancel}>{confirm.cancelLabel}</button></div></div>}
    {phase === 'error' && <button type="button" className="kaisHoloRetry" onClick={onRetry}><RotateCcw aria-hidden="true"/>{retryLabel}</button>}
    <button type="button" className="kaisHoloChat" onClick={onOpenChat}><MessageSquare aria-hidden="true"/>{chatLabel}</button>
  </div>, document.body)
}

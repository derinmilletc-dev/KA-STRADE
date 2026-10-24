import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {X, MessageSquare, RotateCcw, Hand} from 'lucide-react'
import KaisEye from './frontend/src/KaisEye'
import KaisVoicePicker from './KaisVoicePicker'
import KaisHoloBackdrop from './KaisHoloBackdrop'
import {KaisHoloSphere, KaisHoloPlatform, KaisHoloNeural} from './KaisHoloCore'
import KaisHoloDock, {KaisHoloBrand} from './KaisHoloDock'
import KaisHoloCanvas from './KaisHoloCanvas'
import KaisHoloUniverse from './KaisHoloUniverse'
import KaisHoloPanels, {type HoloIO, type HoloRecent} from './KaisHoloPanels'
import {micMeterActive, readMicLevel} from './kais-mic-meter'
import {VOICE_ACTIVITY_EVENT, type VoiceActivitySource} from './kais-voice-activity'
import {PAGE_GLYPH} from './kais-portal-glyphs'
import './kais-agent-holo.css'
import './kais-holo-v2.css'

/** Gerçekten gözlemlenebilen durumlar (sesli oturum + sohbet isteği yaşam döngüsü):
 *  listening = mikrofon açık · thinking = istek gönderildi, cevap bekleniyor (PROCESSING) · speaking = cevap geldi (RESPONSE_RECEIVED) · error = istek başarısız.
 *  IDLE (mikrofonsuz bekleme) ve ANALYZING (ayrı bir sunucu analiz olayı) uygulamada gözlemlenemediği için SİMÜLE EDİLMEZ.
 *  CANCELLED: bileşen kapanınca kaldırılır; rAF ve zamanlayıcılar temizlenir. */
export type HoloPhase = 'listening' | 'thinking' | 'speaking' | 'error'
export type HoloConfirm = {title: string; detail: string; confirmLabel: string; cancelLabel: string; busy: boolean; onConfirm: () => void; onCancel: () => void}
export type HoloPortal = {view: string; label: string; commit: () => void; done: () => void}
type Props = {onSendText?: (text: string) => void; onStopListening?: () => void; busy?: boolean; recent?: HoloRecent[]; io?: HoloIO; timing?: string; alarm?: string | null; portal?: HoloPortal | null; confirm?: HoloConfirm; phase: HoloPhase; status: string; interruptLabel?: string; onInterrupt?: () => void; replyText?: string; actions: string[]; chatLabel: string; closeLabel: string; retryLabel: string; slowText: string; language: 'tr' | 'en'; onOpenChat: () => void; onMinimize: () => void; onRetry: () => void}

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

/** Masaüstü sinematik katmanları yalnız geniş ekranda çalışır (CSS ile aynı eşik); telefonda ek döngü açılmaz. */
const DESKTOP_QUERY = '(min-width: 1025px)'
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** Gerçek ses etkinliği → yumuşatılmış seviye (0–1). Kaynak: kais-voice-activity olayları (tanıma sonucu / okuma kelime sınırı).
 *  Yalnız ilgili aşamada kabul edilir (dinlerken "input", konuşurken "output"); aşama değişince hızla sıfıra söner.
 *  Değer React durumuna yazılmaz: kök öğedeki --kh-level değişkenine rAF ile yazılır, seviye sıfırlanınca döngü durur. */
function useVoiceLevel(root: React.RefObject<HTMLDivElement | null>, phase: HoloPhase, paused: boolean) {
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  const kick = useRef<() => void>(() => undefined)
  useEffect(() => {
    const node = root.current
    if (!node || paused || reducedMotion() || !window.matchMedia(DESKTOP_QUERY).matches) { node?.style.setProperty('--kh-level', '0'); return }
    let level = 0, target = 0, frame = 0, last = 0, stopped = false
    const write = () => node.style.setProperty('--kh-level', level.toFixed(3))
    const step = (now: number) => {
      if (stopped) return
      const dt = Math.min(.1, last ? (now - last) / 1000 : .016); last = now
      const current = phaseRef.current
      if (current === 'listening' && micMeterActive()) {
        // GERÇEK mikrofon genliği (AnalyserNode); olay tabanlı hedef bu aşamada kullanılmaz.
        target = 0; level = readMicLevel(dt); write(); frame = requestAnimationFrame(step); return
      }
      // Hedef her olayda yükselir, sonra kendiliğinden söner; konuşma/dinleme bitince sönme hızlanır.
      target *= Math.exp(-dt / (current === 'listening' || current === 'speaking' ? .32 : .08))
      level += (target - level) * (1 - Math.exp(-dt / .07))
      if (level < .005 && target < .005) { level = 0; target = 0; write(); frame = 0; last = 0; return }
      write()
      frame = requestAnimationFrame(step)
    }
    const onActivity = (event: Event) => {
      const source = (event as CustomEvent<VoiceActivitySource>).detail
      const current = phaseRef.current
      if (source === 'input' && current === 'listening' && micMeterActive()) return
      if (!((source === 'input' && current === 'listening') || (source === 'output' && current === 'speaking'))) return
      target = Math.min(1, target + .55)
      if (!frame) frame = requestAnimationFrame(step)
    }
    kick.current = () => { if (!frame && !stopped && (level > 0 || (phaseRef.current === 'listening' && micMeterActive()))) { last = 0; frame = requestAnimationFrame(step) } }
    window.addEventListener(VOICE_ACTIVITY_EVENT, onActivity)
    return () => { stopped = true; cancelAnimationFrame(frame); window.removeEventListener(VOICE_ACTIVITY_EVENT, onActivity); node.style.setProperty('--kh-level', '0'); kick.current = () => undefined }
  }, [paused])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    kick.current()
    // Ölçer dinleme başladıktan kısa süre sonra hazır olur: döngüyü o zaman başlat
    if (phase !== 'listening') return
    const id = window.setTimeout(() => kick.current(), 300)
    return () => window.clearTimeout(id)
  }, [phase])
}

/** Hafif derinlik (paralaks): yalnız masaüstü + ince imleç + hareket azaltma kapalıyken. Göz ve tıklama alanları hiç kaydırılmaz;
 *  yalnız dekoratif katmanlar --kh-px/--kh-py (−1…1) ile birkaç piksel kayar. İmleç değeri doğrudan kopyalanmaz, yumuşatılır. */
function useParallax(root: React.RefObject<HTMLDivElement | null>, paused: boolean) {
  useEffect(() => {
    const node = root.current
    if (!node || paused || reducedMotion() || !window.matchMedia(`${DESKTOP_QUERY} and (pointer: fine)`).matches) return
    let tx = 0, ty = 0, x = 0, y = 0, frame = 0
    const step = () => {
      x += (tx - x) * .06; y += (ty - y) * .06
      node.style.setProperty('--kh-px', x.toFixed(3)); node.style.setProperty('--kh-py', y.toFixed(3))
      frame = Math.abs(tx - x) > .002 || Math.abs(ty - y) > .002 ? requestAnimationFrame(step) : 0
    }
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return
      tx = Math.max(-1, Math.min(1, (event.clientX / window.innerWidth) * 2 - 1))
      ty = Math.max(-1, Math.min(1, (event.clientY / window.innerHeight) * 2 - 1))
      if (!frame) frame = requestAnimationFrame(step)
    }
    window.addEventListener('pointermove', move, {passive: true})
    return () => { window.removeEventListener('pointermove', move); cancelAnimationFrame(frame) }
  }, [paused])  // eslint-disable-line react-hooks/exhaustive-deps
}

/** Hologram ekran. Ana göz (KaisEye) kaynak dosyalarına dokunulmaz ve ekranda hareketsiz kalır (CSS ile yerleşik kırpma/bakış hareketi kapatılır);
 *  tüm hareket gözün DIŞINDaki bağımsız katmanlardır. */
export default function KaisAgentHolo({onSendText, onStopListening, busy, recent, io, timing, alarm, portal, confirm, phase, status, interruptLabel, onInterrupt, replyText, actions, chatLabel, closeLabel, retryLabel, onOpenChat, onMinimize, onRetry, slowText, language}: Props) {
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
  const root = useRef<HTMLDivElement>(null)
  useVoiceLevel(root, phase, hidden)
  useParallax(root, hidden)
  // Cevap gerçekten bitip dinlemeye dönünce platformdan tek, ölçülü bir dışa doğru dalga (sayaç anahtarıyla bir kez oynar).
  const [complete, setComplete] = useState(0)
  const previousPhase = useRef(phase)
  useEffect(() => {
    if (previousPhase.current === 'speaking' && phase === 'listening') setComplete(value => value + 1)
    previousPhase.current = phase
  }, [phase])
  const pupil = useRef<SVGSVGElement>(null)
  const iris = useRef<SVGSVGElement>(null)
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
  // Bakış artık gözün kendi prosedürel sistemiyle sürülür (presence = dinliyor/düşünüyor/konuşuyor): sabit aralıklı, hep kenara giden
  // rastgele "navigation" tepkileri kaldırıldı. Göz çoğunlukla öne bakar, ara sıra küçük ve değişken kaymalar yapar.
  // Göz bakınca iris/göz bebeği gözün kendi altyapısıyla kayar (data-look-x/y). Optik iris ve göz bebeği katmanları aynı miktarda kaydırılır, böylece hizalı kalır.
  useEffect(() => {
    const eye = stage.current?.querySelector<SVGSVGElement>('.kaisEye')
    const overlays = [pupil.current, iris.current].filter((node): node is SVGSVGElement => node !== null)
    if (!eye || overlays.length === 0) return
    // Ölçek yalnız boyut değişince okunur; bakış her karede güncellenirken düzen hesaplaması zorlanmaz.
    let scale = eye.getBoundingClientRect().width / 96
    const resize = new ResizeObserver(() => { scale = eye.getBoundingClientRect().width / 96; follow() })
    resize.observe(eye)
    const follow = () => {
      const x = Number(eye.dataset.lookX) || 0, y = Number(eye.dataset.lookY) || 0
      const shift = `translate(${(x * 7 * scale).toFixed(2)}px, ${(y * 7 * .6 * scale).toFixed(2)}px)`
      overlays.forEach(node => { node.style.transform = shift })
    }
    follow()
    const observer = new MutationObserver(follow)
    observer.observe(eye, {attributes: true, attributeFilter: ['data-look-x', 'data-look-y']})
    return () => { observer.disconnect(); resize.disconnect() }
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
  return createPortal(<div ref={root} className="kaisHolo" data-phase={phase} data-scene={scene || undefined} data-alarm={alarm ? true : undefined} data-paused={hidden} role="dialog" aria-label="Kais" aria-live="polite">
    <KaisHoloBackdrop/>
    <KaisHoloUniverse phase={phase} root={root} paused={hidden}/>
    <KaisHoloBrand label={language === 'en' ? 'KAIS AI · DIGITAL CONSCIOUSNESS' : 'KAIS AI · DİJİTAL BİLİNÇ'}/>
    <KaisHoloPanels phase={phase} actions={actions} language={language} recent={recent} io={io}/>
    <KaisVoicePicker language={language}/>
    <button type="button" className="kaisHoloClose" onClick={onMinimize} aria-label={closeLabel}><X aria-hidden="true"/></button>
    <div className="kaisHoloStage" ref={stage}>
      <div className="kaisHoloGlow" aria-hidden="true"/>
      <KaisHoloCanvas phase={phase} root={root} paused={hidden}/>
      <KaisHoloSphere/>
      <KaisHoloNeural/>
      <KaisHoloPlatform complete={complete}/>
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
      <div className="kaisHoloEye"><KaisEye size={260} state={phase === 'thinking' ? 'thinking' : 'idle'} presence={phase === 'error' ? 'listening' : phase}/>
        {/* Optik iris: ayrı, gözün çizimine dokunmayan katman (iris dairesine kırpılı; r 10–18 halka bölgesi). İnce eşmerkezli halkalar,
            radyal segmentler, hizalama işaretleri, cam parıltısı ve yansımalar; düşünürken seçili halkalar farklı hızlarda yavaşça döner. */}
        <svg className="kaisHoloIris" ref={iris} viewBox="-18 -18 36 36" aria-hidden="true">
          <defs>
            <radialGradient id="kaisIrisGlass" cx="-6" cy="-7" r="9" gradientUnits="userSpaceOnUse"><stop stopColor="#e9fbff" stopOpacity=".5"/><stop offset="1" stopColor="#8ADFFF" stopOpacity="0"/></radialGradient>
          </defs>
          <circle className="iInner" r="11"/>
          <g className="iRingA"><circle r="14.6" strokeDasharray="5 1.2 .6 1.2"/></g>
          <g className="iSegments">{Array.from({length: 48}, (_, index) => <path key={index} d={index % 4 ? 'M0 -12.3V-13.4' : 'M0 -12V-13.9'} transform={`rotate(${index * 7.5})`}/>)}</g>
          <g className="iRingB"><circle r="16.6" strokeDasharray=".5 1.4"/><path d="M0 -17.6V-15.6M17.6 0H15.6M0 17.6V15.6M-17.6 0H-15.6"/></g>
          <g className="iScan"><path d="M0 -10.6L0 -17.4" className="iScanLine"/><path d="M0 -10.6L0 -17.4A17.4 17.4 0 0 0 -4.5 -16.8L-2.7 -10.2Z" className="iScanWedge"/></g>
          <path className="iGlass" d="M-12.4 -6.6A14 14 0 0 1 -4.4 -13.3A12.6 12.6 0 0 0 -11.2 -5.8Z"/>
          <path className="iReflect" d="M9.6 9.8A13.4 13.4 0 0 1 4.8 12.6"/>
        </svg>
        {/* Göz bebeği içi: ayrı, gözün çizimine dokunmayan katman (göz bebeği alanına kırpılı). Durumla değişir: tarama / yayılan dalga / sakin halka. */}
        <svg className="kaisHoloPupil" ref={pupil} viewBox="-10 -10 20 20" aria-hidden="true">
          <circle className="pRing" r="9.2"/>
          <g className="pSpinA"><circle r="7.4" strokeDasharray="2.2 1.6"/></g>
          <g className="pSpinB"><circle r="5" strokeDasharray="1 2"/></g>
          <g className="pSweep"><path d="M0 0L0 -9.4" className="pLine"/><path d="M0 0L0 -9.4A9.4 9.4 0 0 0 -6.6 -6.7Z" className="pWedge"/></g>
          <g className="pBlips"><circle cx="3.6" cy="-3.9" r=".55"/><circle cx="-4.4" cy="1.8" r=".55"/><circle cx="1.6" cy="5.2" r=".55"/></g>
          <circle className="pWave pWave1" r="9"/><circle className="pWave pWave2" r="9"/>
          <g className="pFocus"><path d="M0 -2.6V-1.7M2.6 0H1.7M0 2.6V1.7M-2.6 0H-1.7"/></g>
          <g className="pGlint"><ellipse cx="-3.2" cy="-4.4" rx="1.1" ry=".55" transform="rotate(-35 -3.2 -4.4)"/></g>
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
    {timing && <p className="kaisHoloTiming">{timing}</p>}
    {alarm && <p className="kaisHoloAlarmText" role="alert">{alarm}</p>}
    {actions.length > 0 && <div className="kaisHoloChips">{actions.slice(-4).map((action, index) => <span key={`${action}-${index}`} className="kaisHoloChip">▸ {action}</span>)}</div>}
    <p className="kaisHoloStatus" data-error={phase === 'error'}>{status}</p>
    {slow && phase === 'thinking' && <p className="kaisHoloSlow">{slowText}</p>}
    {replyText && phase === 'speaking' && <p className="kaisHoloReply">{replyText}</p>}
    {confirm && <div className="kaisHoloConfirm" role="group" aria-label={confirm.title}><b>{confirm.title}</b><small>{confirm.detail}</small>
      <div><button type="button" className="ok" disabled={confirm.busy} onClick={confirm.onConfirm}>{confirm.confirmLabel}</button><button type="button" disabled={confirm.busy} onClick={confirm.onCancel}>{confirm.cancelLabel}</button></div></div>}
    {phase === 'speaking' && onInterrupt && <button type="button" className="kaisHoloInterrupt" onClick={onInterrupt}><Hand aria-hidden="true"/>{interruptLabel}</button>}
    {phase === 'error' && <button type="button" className="kaisHoloRetry" onClick={onRetry}><RotateCcw aria-hidden="true"/>{retryLabel}</button>}
    {onSendText && onStopListening && <KaisHoloDock onSend={onSendText} onStopListening={onStopListening} busy={Boolean(busy)} placeholder={language === 'en' ? 'Type to Kais…' : 'Kais’e yaz…'} sendLabel={language === 'en' ? 'Send' : 'Gönder'} stopLabel={language === 'en' ? 'Stop listening' : 'Dinlemeyi durdur'}/>}
    <button type="button" className="kaisHoloChat" onClick={onOpenChat}><MessageSquare aria-hidden="true"/>{chatLabel}</button>
  </div>, document.body)
}

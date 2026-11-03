import {useEffect, useRef, useState} from 'react'
import KaisEye from './KaisEye'
import './founding-success.css'

export const INTRO_VIDEO = '/founding-intro.mp4'
const INTRO_WEBM = '/founding-intro.webm'
const INTRO_POSTER = '/founding-intro-poster.jpg'
const VIDEO_MS = 4900      // video süresi
const SAFETY_MS = 6800     // video takılırsa en geç bu sürede devam
const LEAD_S = 0.6         // dalış, video bitmeden bu kadar önce başlar (donma olmasın)
const FALLBACK_MS = 2600   // video oynatılamazsa yedek animasyon süresi
const REDUCED_MS = 700     // "hareketi azalt" açıkken
const OUT_MS = 1000        // gözün içine dalış geçişi
const PUSH_SCALE = 1.16    // video boyunca kameranın sabit hızla yaklaşması
const DIVE_SCALE = 3.6     // çıkışta gözbebeğine dalış
const ABORT_MS = 280

export type IntroOutcome = 'pending' | 'ok' | 'error'
type Stage = 'in' | 'play' | 'hold' | 'out' | 'abort'

/** Video önbelleğe alınır; böylece "Başvuruyu Gönder"e basınca beklemeden oynar. */
export function preloadIntro(): () => void {
  try {
    const video = document.createElement('video')
    video.muted = true; video.preload = 'auto'
    video.src = video.canPlayType('video/webm; codecs="vp9"') ? INTRO_WEBM : INTRO_VIDEO; video.load()
    return () => { video.removeAttribute('src'); video.load() }
  } catch { return () => undefined }
}

type Props = {outcome: IntroOutcome; holdText: string; onReveal: () => void; onClosed: () => void}

/**
 * Başvuru gönderilince tam ekran açılan 5 sn'lik geçiş.
 * Sıra: video oynar -> sonuç olumluysa gözün içine dalınır ve başarı ekranı açılır.
 * Hata olursa (her an) kısa bir solmayla kapanır; video yüklenemezse yedek animasyon oynar. Asla takılı kalmaz.
 */
export default function FoundingIntro({outcome, holdText, onReveal, onClosed}: Props) {
  const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const [stage, setStage] = useState<Stage>('in')
  const [fallback, setFallback] = useState(reduced)
  const [elapsed, setElapsed] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const cam = useRef<HTMLDivElement>(null)
  const motion = useRef<Animation | null>(null)
  const done = useRef(false)

  // Oynatma ve süre sayacı
  useEffect(() => {
    const raf = requestAnimationFrame(() => setStage(current => current === 'in' ? 'play' : current))
    let timer = window.setTimeout(() => setElapsed(true), reduced ? REDUCED_MS : SAFETY_MS)
    const element = video.current
    if (!reduced && element) {
      element.muted = true
      const started = element.play()
      if (started && typeof started.catch === 'function') started.catch(() => {
        setFallback(true)
        window.clearTimeout(timer); timer = window.setTimeout(() => setElapsed(true), FALLBACK_MS)
      })
    }
    return () => { cancelAnimationFrame(raf); window.clearTimeout(timer); motion.current?.cancel() }
  }, [reduced])

  // Sonuca göre çıkış
  useEffect(() => {
    if (done.current) return
    if (outcome === 'error') {
      done.current = true; setStage('abort')
      const timer = window.setTimeout(onClosed, ABORT_MS)
      return () => window.clearTimeout(timer)
    }
    if (!elapsed) return
    if (outcome === 'pending') { setStage('hold'); drift(); return }
    done.current = true; setStage('out'); dive(); onReveal()
    const timer = window.setTimeout(onClosed, OUT_MS)
    return () => window.clearTimeout(timer)
  }, [outcome, elapsed]) // eslint-disable-line react-hooks/exhaustive-deps

  // Kameranın o anki ölçeği (animasyon nerede kaldıysa oradan devam etmek için)
  const currentScale = () => {
    const value = cam.current ? getComputedStyle(cam.current).transform : 'none'
    const match = /^matrix\(([-\d.e]+)/.exec(value)
    return match ? Number(match[1]) || 1 : 1
  }
  const drift = () => { // sunucu yavaşsa çok yavaş yaklaşmaya devam
    if (reduced || !cam.current?.animate) return
    const from = currentScale(); motion.current?.cancel()
    motion.current = cam.current.animate([{transform: `scale(${from})`}, {transform: `scale(${from * 1.08})`}], {duration: 9000, easing: 'linear', fill: 'forwards'})
  }
  const dive = () => { // aynı hızdan devam edip hızlanarak gözbebeğine dalış
    if (reduced || !cam.current?.animate) return
    const from = currentScale(); motion.current?.cancel()
    motion.current = cam.current.animate([
      {transform: `scale(${from})`, filter: 'brightness(1) blur(0px)'},
      {transform: `scale(${from * DIVE_SCALE})`, filter: 'brightness(1.9) blur(4px)'},
    ], {duration: OUT_MS, easing: 'cubic-bezier(.42,.08,.78,.45)', fill: 'forwards'})
  }

  // Kamera hareketi videonun gerçekten başladığı an başlar ve onun süresine eşitlenir
  const onPlaying = () => {
    const element = video.current
    if (reduced || !cam.current?.animate || !element || motion.current) return
    const total = Number.isFinite(element.duration) && element.duration > 1 ? element.duration : VIDEO_MS / 1000
    const remaining = Math.max(.8, total - element.currentTime) * 1000
    motion.current = cam.current.animate([{transform: 'scale(1)'}, {transform: `scale(${PUSH_SCALE})`}],
      {duration: remaining, easing: 'linear', fill: 'forwards'})
    // Video süresini kare kare izle (timeupdate seyrek gelir); dalışı tam zamanında başlat
    const watch = () => {
      const el = video.current
      if (!el || done.current) return
      if (Number.isFinite(el.duration) && el.currentTime >= el.duration - LEAD_S) { setElapsed(true); return }
      requestAnimationFrame(watch)
    }
    requestAnimationFrame(watch)
  }
  const onTime = () => {
    const element = video.current
    if (element && Number.isFinite(element.duration) && element.currentTime >= element.duration - LEAD_S) setElapsed(true)
  }

  const onVideoError = () => {
    if (fallback) return
    setFallback(true)
    window.setTimeout(() => setElapsed(true), FALLBACK_MS)
  }

  return <div className={`fdIntro is-${stage} ${fallback ? 'is-fallback' : ''}`} role="status" aria-live="polite" aria-label={holdText}>
    <div className="fdIntroGlow" aria-hidden="true"/>
    <div className="fdIntroCam" ref={cam}>
    {!reduced && <video ref={video} className="fdIntroVideo" poster={INTRO_POSTER} muted playsInline autoPlay preload="auto" aria-hidden="true"
      onPlaying={onPlaying} onTimeUpdate={onTime} onEnded={() => setElapsed(true)} onError={onVideoError}>
      <source src={INTRO_WEBM} type='video/webm; codecs="vp9"'/>
      <source src={INTRO_VIDEO} type="video/mp4" onError={onVideoError}/>
    </video>}
    {fallback && <div className="fdIntroFallback" aria-hidden="true"><span/><span/><span/><KaisEye size={220} state="thinking"/></div>}
    </div>
    <div className="fdIntroFlash" aria-hidden="true"/>
    <p className="fdIntroHold">{holdText}</p>
  </div>
}

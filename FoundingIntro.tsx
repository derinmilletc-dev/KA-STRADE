import {useEffect, useRef, useState} from 'react'
import KaisEye from './KaisEye'
import './founding-success.css'

export const INTRO_VIDEO = '/founding-intro.mp4'
const INTRO_WEBM = '/founding-intro.webm'
const INTRO_POSTER = '/founding-intro-poster.jpg'
const VIDEO_MS = 4900      // video süresi
const FALLBACK_MS = 2600   // video oynatılamazsa yedek animasyon süresi
const REDUCED_MS = 700     // "hareketi azalt" açıkken
const OUT_MS = 950         // gözün içine dalış geçişi
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
  const done = useRef(false)

  // Oynatma ve süre sayacı
  useEffect(() => {
    const raf = requestAnimationFrame(() => setStage(current => current === 'in' ? 'play' : current))
    let timer = window.setTimeout(() => setElapsed(true), reduced ? REDUCED_MS : VIDEO_MS)
    const element = video.current
    if (!reduced && element) {
      element.muted = true
      const started = element.play()
      if (started && typeof started.catch === 'function') started.catch(() => {
        setFallback(true)
        window.clearTimeout(timer); timer = window.setTimeout(() => setElapsed(true), FALLBACK_MS)
      })
    }
    return () => { cancelAnimationFrame(raf); window.clearTimeout(timer) }
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
    if (outcome === 'pending') { setStage('hold'); return }
    done.current = true; setStage('out'); onReveal()
    const timer = window.setTimeout(onClosed, OUT_MS)
    return () => window.clearTimeout(timer)
  }, [outcome, elapsed]) // eslint-disable-line react-hooks/exhaustive-deps

  const onVideoError = () => {
    if (fallback) return
    setFallback(true)
    window.setTimeout(() => setElapsed(true), FALLBACK_MS)
  }

  return <div className={`fdIntro is-${stage} ${fallback ? 'is-fallback' : ''}`} role="status" aria-live="polite" aria-label={holdText}>
    <div className="fdIntroGlow" aria-hidden="true"/>
    {!reduced && <video ref={video} className="fdIntroVideo" poster={INTRO_POSTER} muted playsInline autoPlay preload="auto" aria-hidden="true"
      onEnded={() => setElapsed(true)} onError={onVideoError}>
      <source src={INTRO_WEBM} type='video/webm; codecs="vp9"'/>
      <source src={INTRO_VIDEO} type="video/mp4" onError={onVideoError}/>
    </video>}
    {fallback && <div className="fdIntroFallback" aria-hidden="true"><span/><span/><span/><KaisEye size={220} state="thinking"/></div>}
    <div className="fdIntroFlash" aria-hidden="true"/>
    <p className="fdIntroHold">{holdText}</p>
  </div>
}

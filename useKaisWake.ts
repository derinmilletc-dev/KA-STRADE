import {useEffect, useRef, useState} from 'react'
import {detectWake, WAKE_KEY} from './kais-wake-text'

// "Hey Kais": uygulama AÇIK ve EKRANDA iken mikrofonu dinler; "Hey Kais …" duyunca onWake çağrılır.
// Telefonlar web uygulamasına arka planda (kapalıyken / başka uygulamadayken) dinleme izni vermez; sekme gizlenince durur.
// Ses kaydedilmez ve sunucuya gönderilmez; tanıma tarayıcının kendi konuşma tanımasıyla yapılır.

type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number
  start(): void; stop(): void; abort(): void
  onresult: ((event: {resultIndex: number; results: ArrayLike<ArrayLike<{transcript: string}> & {isFinal: boolean}>}) => void) | null
  onerror: ((event: {error?: string}) => void) | null
  onend: (() => void) | null
}
const recognitionClass = (): (new () => Recognition) | null => {
  if (typeof window === 'undefined') return null
  const scope = window as unknown as {SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition}
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

export const wakeSupported = () => recognitionClass() !== null
export function readWake(): boolean { try { return localStorage.getItem(WAKE_KEY) === '1' } catch { return false } }
export function writeWake(value: boolean) { try { if (value) localStorage.setItem(WAKE_KEY, '1'); else localStorage.removeItem(WAKE_KEY) } catch { /* saklanamazsa oturum boyunca geçerli */ } }

/** active false iken hiç dinlemez. Sürekli hata alırsa (izin yok vb.) durur ve blocked döner. */
export function useKaisWake(active: boolean, language: string, onWake: (question: string) => void): {listening: boolean; blocked: string | null} {
  const [listening, setListening] = useState(false)
  const [blocked, setBlocked] = useState<string | null>(null)
  const wakeRef = useRef(onWake)
  wakeRef.current = onWake
  useEffect(() => {
    const Recognizer = recognitionClass()
    if (!active || !Recognizer) { setListening(false); return }
    let stopped = false
    let current: Recognition | null = null
    let timer = 0
    let failures = 0
    const begin = () => {
      if (stopped || document.visibilityState !== 'visible') return
      const instance = new Recognizer()
      current = instance
      instance.lang = language.toLowerCase().startsWith('en') ? 'en-US' : 'tr-TR'
      instance.continuous = true
      instance.interimResults = true
      instance.maxAlternatives = 1
      instance.onresult = event => {
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index]
          const heard = result[0]?.transcript ?? ''
          const wake = detectWake(heard)
          // Ara sonuçta ad duyulunca bekle; cümle bitince (isFinal) soruyla birlikte uyan. Yalnız "Hey Kais" ise hemen uyan.
          if (wake && (result.isFinal || !wake.question)) {
            stopped = true
            instance.onend = null
            try { instance.abort() } catch { /* zaten durmuş */ }
            setListening(false)
            wakeRef.current(wake.question)
            return
          }
        }
      }
      instance.onerror = event => {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          stopped = true
          setBlocked('Mikrofon izni yok. Tarayıcı ayarlarından KaisTrade için mikrofona izin ver.')
        }
        if (event.error && event.error !== 'no-speech' && event.error !== 'aborted') failures += 1
      }
      instance.onend = () => {
        current = null
        setListening(false)
        if (stopped) return
        if (failures >= 5) { setBlocked('Dinleme tekrar tekrar kesildi; "Hey Kais" durduruldu.'); return }
        timer = window.setTimeout(begin, 400)  // telefon tarayıcıları sessizlikte dinlemeyi bitirir; yeniden başlat
      }
      try { instance.start(); setListening(true); setBlocked(null) } catch { timer = window.setTimeout(begin, 1500) }
    }
    const visibility = () => {
      if (document.visibilityState === 'visible') { if (!current && !stopped) begin() }
      else { window.clearTimeout(timer); if (current) { current.onend = null; try { current.abort() } catch { /* yok */ } current = null; setListening(false) } }
    }
    begin()
    document.addEventListener('visibilitychange', visibility)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', visibility)
      if (current) { current.onend = null; try { current.abort() } catch { /* yok */ } }
      setListening(false)
    }
  }, [active, language])
  return {listening, blocked}
}

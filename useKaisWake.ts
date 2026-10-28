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
export const WAKE_CHANGED_EVENT = 'kais:wake-changed'
/** 'on'/'off' = kullanıcının seçimi; 'default' = hiç seçmedi (ana ekrandan açılan uygulamada açık sayılır). */
export function readWakeSetting(): 'on' | 'off' | 'default' {
  try { const value = localStorage.getItem(WAKE_KEY); return value === '1' ? 'on' : value === '0' ? 'off' : 'default' } catch { return 'default' }
}
export function writeWake(value: boolean) {
  try { localStorage.setItem(WAKE_KEY, value ? '1' : '0') } catch { /* saklanamazsa oturum boyunca geçerli */ }
  window.dispatchEvent(new Event(WAKE_CHANGED_EVENT))
}
export const isStandaloneApp = () => typeof window !== 'undefined' && (window.matchMedia?.('(display-mode: standalone)').matches || (navigator as {standalone?: boolean}).standalone === true)

/** "Hey Kais" açık mı: kullanıcı seçtiyse o; seçmediyse ana ekrandan açılan uygulamada ve mikrofon izni zaten verilmişse açık.
 * (İzin yokken kendiliğinden mikrofon penceresi açılmasın diye izin şartı var; izin "Hepsini aç" kartından istenir.) */
export function useWakeEnabled(): [boolean, (value: boolean) => void] {
  const [setting, setSetting] = useState(readWakeSetting)
  const [micGranted, setMicGranted] = useState(false)
  useEffect(() => {
    let status: PermissionStatus | null = null
    const check = () => {
      navigator.permissions?.query({name: 'microphone' as PermissionName}).then(result => {
        status = result; setMicGranted(result.state === 'granted')
        result.onchange = () => setMicGranted(result.state === 'granted')
      }).catch(() => setMicGranted(false))
    }
    const changed = () => { setSetting(readWakeSetting()); check() }
    check()
    window.addEventListener(WAKE_CHANGED_EVENT, changed)
    return () => { window.removeEventListener(WAKE_CHANGED_EVENT, changed); if (status) status.onchange = null }
  }, [])
  const enabled = setting === 'on' || (setting === 'default' && isStandaloneApp() && micGranted)
  return [enabled, writeWake]
}

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

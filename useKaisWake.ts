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
/** onHeard: ad duyulduğu AN (ara sonuç) — ekran hemen açılsın. onWake: cümle bitince (ya da en geç ~1,8 sn sonra) soruyla. */
export function useKaisWake(active: boolean, language: string, onWake: (question: string) => void, onHeard?: () => void): {listening: boolean; blocked: string | null} {
  const [listening, setListening] = useState(false)
  const [blocked, setBlocked] = useState<string | null>(null)
  const wakeRef = useRef(onWake)
  wakeRef.current = onWake
  const heardRef = useRef(onHeard)
  heardRef.current = onHeard
  useEffect(() => {
    const Recognizer = recognitionClass()
    if (!active || !Recognizer) { setListening(false); return }
    let stopped = false
    let current: Recognition | null = null
    let timer = 0
    let failures = 0
    let heard = false
    let question = ''
    let deadline = 0
    const begin = () => {
      if (stopped || document.visibilityState !== 'visible') return
      const instance = new Recognizer()
      current = instance
      instance.lang = language.toLowerCase().startsWith('en') ? 'en-US' : 'tr-TR'
      instance.continuous = true
      instance.interimResults = true
      instance.maxAlternatives = 1
      const fire = () => {
        if (stopped) return
        stopped = true
        window.clearTimeout(deadline)
        instance.onend = null
        try { instance.abort() } catch { /* zaten durmuş */ }
        setListening(false)
        wakeRef.current(question)
      }
      instance.onresult = event => {
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index]
          const wake = detectWake(result[0]?.transcript ?? '')
          if (!wake) continue
          question = wake.question
          // Ad duyulur duyulmaz ekranı aç; soru için aynı dinlemeyi sürdür (mikrofon kapatılıp açılmaz, kelime kaybolmaz).
          if (!heard) { heard = true; heardRef.current?.(); deadline = window.setTimeout(fire, 1800) }
          if (result.isFinal) { fire(); return }
          // Soru gelmeye devam ettikçe bekleme süresini uzat (konuşma sürerken kesilmesin).
          if (wake.question) { window.clearTimeout(deadline); deadline = window.setTimeout(fire, 1200) }
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
        if (heard) { fire(); return }  // ad duyulmuştu ama tanıma kapandı: eldeki soruyla uyan
        timer = window.setTimeout(begin, 120)  // telefon tarayıcıları sessizlikte dinlemeyi bitirir; hemen yeniden başlat
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
      window.clearTimeout(deadline)
      if (current) { current.onend = null; try { current.abort() } catch { /* yok */ } }
      setListening(false)
    }
  }, [active, language])
  return {listening, blocked}
}

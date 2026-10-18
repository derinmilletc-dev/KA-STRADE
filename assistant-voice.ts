import {useCallback, useEffect, useRef, useState} from 'react'

export type VoiceLanguage = string

/** Site dili → konuşma tanıma/okuma dil etiketi. Yeni dil eklenince buraya bir satır eklemek yeterli. */
const SPEECH_TAGS: Record<string, string> = {
  tr: 'tr-TR', en: 'en-US', de: 'de-DE', fr: 'fr-FR', es: 'es-ES', it: 'it-IT', pt: 'pt-BR', ru: 'ru-RU',
  ar: 'ar-SA', fa: 'fa-IR', az: 'az-AZ', nl: 'nl-NL', pl: 'pl-PL', uk: 'uk-UA', zh: 'zh-CN', ja: 'ja-JP', ko: 'ko-KR', hi: 'hi-IN', id: 'id-ID',
}
export const speechTag = (language: string) => SPEECH_TAGS[language.slice(0, 2).toLowerCase()] ?? language

export const voiceCopy = {
  tr: {
    mic: 'Sesle sor', stopMic: 'Dinlemeyi durdur', listening: 'Dinliyorum…', repliesOn: 'Sesli cevap açık', repliesOff: 'Sesli cevap kapalı',
    unsupported: 'Bu tarayıcı sesli girişi desteklemiyor. Chrome, Edge veya Safari kullan.',
    denied: 'Mikrofon izni verilmedi. Tarayıcı ayarlarından bu site için mikrofona izin ver.',
    noSpeech: 'Ses duyulmadı, tekrar dene.', failed: 'Ses algılanamadı, tekrar dene.',
    noAnswer: 'Cevap alınamadı. Bağlantını ya da kullanım hakkını kontrol et.', speechFailed: 'Sesli cevap oynatılamadı; yazılı cevap sohbette.',
    hint: 'Mavi ses düğmesine bir kez bas, sonra konuşarak sohbet et. Kais konuşurken araya girebilirsin. Bitirmek için “bitir” de.',
    conversation: 'Sesli sohbet', endConversation: 'Sesli sohbeti bitir', interrupt: 'Sözünü kes', thinking: 'Düşünüyorum…', speaking: 'Konuşuyor…',
    open: 'Sohbeti aç', end: 'Bitir', ended: 'Sesli sohbet bitti.', idleEnded: 'Uzun süre ses gelmediği için sesli sohbet bitti.',
  },
  en: {
    mic: 'Ask by voice', stopMic: 'Stop listening', listening: 'Listening…', repliesOn: 'Spoken replies on', repliesOff: 'Spoken replies off',
    unsupported: 'This browser does not support voice input. Use Chrome, Edge or Safari.',
    denied: 'Microphone permission was not granted. Allow the microphone for this site in your browser settings.',
    noSpeech: 'No speech was heard, please try again.', failed: 'Speech could not be recognised, please try again.',
    noAnswer: 'No answer received. Check your connection or usage limit.', speechFailed: 'The spoken reply could not be played; the written reply is in the chat.',
    hint: 'Tap the blue voice button once, then just talk. You can interrupt Kais while it speaks. Say “stop” to end.',
    conversation: 'Voice chat', endConversation: 'End voice chat', interrupt: 'Interrupt', thinking: 'Thinking…', speaking: 'Speaking…',
    open: 'Open chat', end: 'End', ended: 'Voice chat ended.', idleEnded: 'Voice chat ended after a long silence.',
  },
} as const

type RecognitionEvent = {results: ArrayLike<ArrayLike<{transcript: string}> & {isFinal?: boolean}>}

// Kelime kökü (ilk 4 harf): ses tanıma ekleri farklı yazsa da eşleşsin.
const normalWords = (value: string) => value.toLocaleLowerCase('tr').replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(word => word.length > 1).map(word => word.slice(0, 4))
/** Duyulan metin büyük ölçüde Kais'in okuduğu metinse yankıdır. */
export function isEcho(heard: string, spoken: string): boolean {
  const words = normalWords(heard)
  if (!words.length) return true
  const spokenWords = new Set(normalWords(spoken))
  return words.filter(word => spokenWords.has(word)).length / words.length >= 0.5
}
type Recognition = {
  lang: string; interimResults: boolean; maxAlternatives: number; continuous: boolean
  start(): void; stop(): void; abort(): void
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: {error?: string}) => void) | null
  onend: (() => void) | null
}
type RecognitionConstructor = new () => Recognition

const recognitionConstructor = (): RecognitionConstructor | null => {
  const scope = window as unknown as {SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor}
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

/** “Hey Kais, …” önekini ve dolgu sözcüklerini soru metninden ayıklar. */
export function stripWakeWord(value: string): string {
  const cleaned = value.trim().replace(/^(hey|hay|hey,|hi|merhaba|selam)?[\s,]*(kais|kaiz|kays|kayis|kaız|kaes)[\s,.:!?-]*/i, '').trim()
  return cleaned || value.trim()
}

/** Markdown ve simgeleri konuşmaya uygun düz metne çevirir. */
export function speakableText(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[\p{Extended_Pictographic}\p{Regional_Indicator}\u{FE0F}\u{200D}\u{20E3}\u{1F3FB}-\u{1F3FF}]/gu, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_#>~|]+/g, ' ')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 900)
}

/** Sohbeti bitiren kısa komutlar. */
export function isStopPhrase(value: string): boolean {
  const text = value.toLocaleLowerCase('tr').replace(/[.,!?]/g, ' ').replace(/\s+/g, ' ').trim()
  return /^(sohbeti )?(bitir|bitti|dur|kapat|tamam bitir|görüşürüz|gorusuruz|hoşça kal|hosca kal|stop|end|bye|goodbye)( kais)?$/.test(text)
}

/** Uzun metni cümlelere böler; tarayıcı uzun tek parçayı yarıda kesebiliyor. */
function chunks(value: string): string[] {
  const parts = value.match(/[^.!?…]+[.!?…]*/g) ?? [value]
  const out: string[] = []
  for (const part of parts.map(item => item.trim()).filter(Boolean)) {
    const last = out[out.length - 1]
    if (last && last.length + part.length < 180) out[out.length - 1] = `${last} ${part}`
    else out.push(part)
  }
  return out
}

/** Dilin en doğal sesini seçer (Edge "Natural"/"Online", Google, Apple gelişmiş sesleri önce). */
const VOICE_KEY = 'kt_voice_name'
/** Kullanıcının seçtiği ses (cihaz sesleri cinsiyet bilgisi vermediği için seçimi kullanıcı yapar). Boş = otomatik. */
const RATE_KEY = 'kais-voice-rate'
export const savedRate = (): number => { try { const v = Number(localStorage.getItem(RATE_KEY)); return v >= .8 && v <= 1.5 ? v : 1.15 } catch { return 1.15 } }
export const saveRate = (value: number) => { try { localStorage.setItem(RATE_KEY, String(value)) } catch { /* yok say */ } }
export const savedVoiceName = () => { try { return localStorage.getItem(VOICE_KEY) || '' } catch { return '' } }
export const saveVoiceName = (name: string) => { try { if (name) localStorage.setItem(VOICE_KEY, name); else localStorage.removeItem(VOICE_KEY) } catch { /* saklanamazsa oturum boyunca geçerli olmaz */ } }
export const otherVoices = (language: string): SpeechSynthesisVoice[] => typeof window === 'undefined' || !window.speechSynthesis ? []
  : window.speechSynthesis.getVoices().filter(item => !item.lang.replace('_', '-').toLowerCase().startsWith(language.slice(0, 2).toLowerCase()))
export const voicesFor = (language: string): SpeechSynthesisVoice[] => typeof window === 'undefined' || !window.speechSynthesis ? []
  : window.speechSynthesis.getVoices().filter(item => item.lang.replace('_', '-').toLowerCase().startsWith(language.slice(0, 2).toLowerCase()))

function pickVoice(voices: SpeechSynthesisVoice[], tag: string, language: string): SpeechSynthesisVoice | undefined {
  const matching = voices.filter(item => item.lang.replace('_', '-').toLowerCase().startsWith(language))
  const saved = savedVoiceName()
  const chosen = saved ? voices.find(item => item.name === saved) : undefined  // kayıtlı ses başka dilden de olabilir (kullanıcı seçtiyse)
  if (chosen) return chosen
  const score = (item: SpeechSynthesisVoice) => {
    const name = item.name.toLowerCase()
    let value = item.lang.replace('_', '-') === tag ? 1 : 0
    if (/natural|neural/.test(name)) value += 8
    if (/online/.test(name)) value += 5
    if (/google/.test(name)) value += 4
    if (/premium|enhanced|siri/.test(name)) value += 4
    if (item.localService === false) value += 1
    // Erkek ses tercihi: cihaz cinsiyet bilgisi vermez, bilinen isimlerden anlaşılır.
    if (/\b(male|erkek|ahmet|cem|tolga|yusuf|mehmet|emre|kerem|david|mark|guy|daniel|james|george|alex|fred)\b/.test(name) && !/female/.test(name)) value += 12
    if (/female|kad[ıi]n|emel|filiz|yelda|seda|zira|hazel|susan|samantha|karen|moira|tessa|victoria/.test(name)) value -= 12
    return value
  }
  return matching.sort((a, b) => score(b) - score(a))[0]
}

const coarsePointer = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches

/** Seçilen ses erkek olarak tanınmıyorsa tonu düşürürüz. */
const isKnownMale = (voice?: SpeechSynthesisVoice) => !!voice && /\b(male|erkek|ahmet|cem|tolga|yusuf|mehmet|emre|kerem|david|mark|guy|daniel|james|george|alex|fred)\b/.test(voice.name.toLowerCase()) && !/female/.test(voice.name.toLowerCase())

export type VoicePhase = 'off' | 'listening' | 'thinking' | 'speaking'
const IDLE_LIMIT_MS = 5 * 60 * 1000

const STORAGE_KEY = 'kt_voice_replies'
const readReplies = () => { try { return localStorage.getItem(STORAGE_KEY) === '1' } catch { return false } }
const writeReplies = (value: boolean) => { try { localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* tercih saklanamazsa oturum boyunca geçerli */ } }

export function useVoice(language: VoiceLanguage, onTranscript: (text: string) => Promise<string | null>) {
  const base = language.slice(0, 2).toLowerCase()
  const copy = voiceCopy[base as keyof typeof voiceCopy] ?? voiceCopy.en
  const recognition = useRef<Recognition | null>(null)
  const callback = useRef(onTranscript)
  callback.current = onTranscript
  const active = useRef(false)
  const failures = useRef(0)
  const noAnswers = useRef(0)
  const lastSpeech = useRef(0)
  const speakToken = useRef(0)
  const [phase, setPhase] = useState<VoicePhase>('off')
  const phaseRef = useRef<VoicePhase>('off')
  phaseRef.current = phase
  const [message, setMessage] = useState('')
  const [replies, setReplies] = useState(readReplies)
  const inputSupported = typeof window !== 'undefined' && recognitionConstructor() !== null
  const outputSupported = typeof window !== 'undefined' && 'speechSynthesis' in window
  const tag = speechTag(language)
  const tagRef = useRef(tag)
  tagRef.current = tag

  const stopSpeaking = useCallback(() => { speakToken.current += 1; if (outputSupported) window.speechSynthesis.cancel() }, [outputSupported])
  const speak = useCallback((text: string, onDone?: () => void) => {
    const clean = speakableText(text)
    if (!outputSupported || !clean) { onDone?.(); return }
    // Chrome Android'de cancel() hemen ardından speak() ilk cümleyi yutabiliyor; yalnız bir şey çalıyorsa iptal et.
    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) window.speechSynthesis.cancel()
    window.speechSynthesis.resume()
    const token = ++speakToken.current
    const voices = window.speechSynthesis.getVoices()
    const voice = pickVoice(voices, tag, base)
    const parts = chunks(clean)
    let finished = false
    const done = () => { if (finished || token !== speakToken.current) return; finished = true; onDone?.() }
    // Bazı tarayıcılarda "bitti" olayı gelmeyebilir; metin uzunluğuna göre yedek süre.
    const fallback = window.setTimeout(done, 4000 + clean.length * 90)
    parts.forEach((part, index) => {
      const utterance = new SpeechSynthesisUtterance(part)
      utterance.lang = tag
      if (voice) utterance.voice = voice
      if (index === 0) utterance.onstart = () => { const last = (window as unknown as {__kaisLast?: {t0: number; speechMs?: number}}).__kaisLast; if (last && last.speechMs === undefined) last.speechMs = Math.round(performance.now() - last.t0) }
      utterance.rate = savedRate()
      utterance.pitch = savedVoiceName() || isKnownMale(voice) ? 1 : .7
      if (index === parts.length - 1) {
        utterance.onend = () => { window.clearTimeout(fallback); done() }
        utterance.onerror = () => { window.clearTimeout(fallback); done() }
      }
      utterance.addEventListener('error', event => {
        const reason = (event as SpeechSynthesisErrorEvent).error
        if (reason && reason !== 'canceled' && reason !== 'interrupted' && token === speakToken.current) setMessage(copy.speechFailed)
      })
      window.speechSynthesis.speak(utterance)
    })
  }, [base, copy, outputSupported, tag])

  const end = useCallback((note = '') => {
    active.current = false
    recognition.current?.abort()
    recognition.current = null
    stopSpeaking()
    setPhase('off')
    setMessage(note)
  }, [stopSpeaking])

  const listenRef = useRef<(barge?: boolean, spoken?: string) => void>(() => undefined)
  const barge = useRef<{instance: Recognition; interrupted: boolean; retired: boolean} | null>(null)
  const lastReply = useRef({text: '', startedAt: 0, endedAt: 0})

  /** Soruyu gönderir, cevabı okur; okurken araya girilirse dinlemeye devam eder. */
  const lastQuestion = useRef('')
  const answerId = useRef(0)
  const answer = useCallback((question: string) => {
    lastQuestion.current = question
    const requestId = ++answerId.current
    setPhase('thinking')
    setMessage('')
    void callback.current(question).then(reply => {
      if (!active.current || requestId !== answerId.current) return  // eski isteğin cevabı yeni isteğin durumunu ezmesin
      if (!reply) {
        // Cevap gelmediyse (bağlantı, hak, oturum) sessizce dinlemeye dönme; nedenini göster, 3 kez üst üste olursa bitir.
        noAnswers.current += 1
        if (noAnswers.current >= 3) { noAnswers.current = 0; end(copy.noAnswer); return }
        setMessage(copy.noAnswer); setPhase('listening')
        window.setTimeout(() => { if (active.current && !recognition.current) listenRef.current(false) }, 600)
        return
      }
      noAnswers.current = 0
      setPhase('speaking')
      let finished = false
      lastReply.current = {text: reply, startedAt: Date.now(), endedAt: 0}
      speak(reply, () => {
        finished = true
        lastReply.current.endedAt = Date.now()
        if (!active.current) return
        const current = barge.current
        if (current && !current.interrupted) { current.retired = true; current.instance.abort(); barge.current = null }
        if (!current || !current.interrupted) {
          setPhase('listening')
          // Hoparlördeki son sesin yankısı geçsin diye kısa bir ara.
          window.setTimeout(() => { if (active.current && !recognition.current) listenRef.current(false) }, 600)
        }
      })
      // Kais konuşurken de dinle: kullanıcı araya girerse susup onu dinleriz.
      // Telefonda hoparlör sesi mikrofona karışıyor ve Android dinleme sırasında konuşma sesini kısabiliyor: araya girme yalnız düğmeyle.
      if (!finished && !coarsePointer()) listenRef.current(true, reply)
    })
  }, [copy, end, speak])

  /** Cevap alınamadıysa aynı soruyu bir kez daha gönderir (yalnızca sohbet isteği; emir/onay içermez). */
  const retryAnswer = useCallback(() => {
    if (!active.current || !lastQuestion.current || phaseRef.current === 'thinking') return
    recognition.current?.abort()
    answer(lastQuestion.current)
  }, [answer])

  const listen = useCallback((bargeIn = false, spoken = '') => {
    const Constructor = recognitionConstructor()
    if (!active.current || !Constructor) return
    if (!bargeIn && Date.now() - lastSpeech.current > IDLE_LIMIT_MS) { end(copy.idleEnded); return }
    recognition.current?.abort()
    const instance = new Constructor()
    // Sohbet sırasında site dili değişirse bir sonraki dinlemede yeni dil kullanılır.
    instance.lang = tagRef.current
    instance.interimResults = bargeIn
    instance.maxAlternatives = 1
    instance.continuous = bargeIn
    const state = {instance, interrupted: false, retired: false}
    if (bargeIn) barge.current = state
    let transcript = ''
    let fatal = false
    instance.onresult = event => {
      const rows = Array.from(event.results)
      if (!bargeIn) { transcript = rows.map(result => result[0]?.transcript ?? '').join(' ').trim(); return }
      // Kais'in kendi sesini (yankı) kullanıcı sanmamak için okunan metinle karşılaştırırız.
      const heard = rows.map(result => result[0]?.transcript ?? '').filter(text => !isEcho(text, spoken))
      const text = heard.join(' ').trim()
      if (!state.interrupted) {
        // Konuşmanın ilk anlarını ve kısa sesleri yok say; en az üç yeni kelime gerekir.
        if (Date.now() - lastReply.current.startedAt < 1200 || text.split(/\s+/).filter(Boolean).length < 3) return
        state.interrupted = true
        stopSpeaking()
        setPhase('listening')
      }
      transcript = text
      if (rows[rows.length - 1]?.isFinal) instance.stop()
    }
    instance.onerror = event => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') { fatal = true; end(copy.denied) }
      else if (event.error !== 'no-speech' && event.error !== 'aborted') failures.current += 1
    }
    instance.onend = () => {
      if (recognition.current === instance) recognition.current = null
      if (barge.current === state) barge.current = null
      if (!active.current || fatal || state.retired) return
      if (bargeIn && !state.interrupted) {
        // Kais hâlâ konuşuyorsa araya girme dinlemesini yeniden başlat.
        if (phaseRef.current === 'speaking') window.setTimeout(() => { if (active.current && phaseRef.current === 'speaking') listenRef.current(true, spoken) }, 120)
        return
      }
      const question = stripWakeWord(transcript)
      if (!transcript || !question) {
        if (failures.current > 5) { end(copy.failed); return }
        window.setTimeout(() => listenRef.current(false), failures.current ? 600 : 150)
        return
      }
      // Kais'in az önce söylediğinin yankısı geldiyse soru sayma, dinlemeye devam et.
      if (!bargeIn && lastReply.current.endedAt && Date.now() - lastReply.current.endedAt < 5000 && isEcho(question, lastReply.current.text)) {
        window.setTimeout(() => listenRef.current(false), 150)
        return
      }
      failures.current = 0
      lastSpeech.current = Date.now()
      if (isStopPhrase(question)) { end(copy.ended); return }
      answer(question)
    }
    recognition.current = instance
    try { instance.start(); if (!bargeIn) setPhase('listening') } catch { failures.current += 1; if (!bargeIn) window.setTimeout(() => listenRef.current(false), 800) }
  }, [answer, copy, end, stopSpeaking])
  listenRef.current = listen

  const start = useCallback(async () => {
    if (!recognitionConstructor()) { setMessage(copy.unsupported); return }
    stopSpeaking()
    // İzin penceresini doğrudan tıklamayla açmak için mikrofonu önce isteriz.
    if (navigator.mediaDevices?.getUserMedia) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({audio: true})
        stream.getTracks().forEach(track => track.stop())
      } catch (error) {
        const name = error instanceof DOMException ? error.name : ''
        setMessage(name === 'NotFoundError' ? copy.failed : copy.denied)
        return
      }
    }
    active.current = true
    failures.current = 0
    lastSpeech.current = Date.now()
    setMessage('')
    listen()
  }, [copy, listen, stopSpeaking])

  const stop = useCallback(() => end(''), [end])

  /** Kais konuşurken düğmeye basılırsa susar ve dinlemeye geçer. */
  const interrupt = useCallback(() => {
    if (!active.current) return
    const current = barge.current
    if (current) { current.retired = true; current.instance.abort(); barge.current = null }
    stopSpeaking()
    lastReply.current.endedAt = Date.now()
    setPhase('listening')
    window.setTimeout(() => listenRef.current(false), 250)
  }, [stopSpeaking])

  const toggleReplies = useCallback(() => {
    setReplies(current => { const next = !current; writeReplies(next); if (!next && !active.current) stopSpeaking(); return next })
  }, [stopSpeaking])

  // Sekme tekrar görünür olunca dinlemeyi sürdür.
  useEffect(() => {
    const resume = () => { if (document.visibilityState === 'visible' && active.current && !recognition.current && phaseRef.current === 'listening') listen() }
    document.addEventListener('visibilitychange', resume)
    return () => document.removeEventListener('visibilitychange', resume)
  }, [listen])
  useEffect(() => () => { active.current = false; recognition.current?.abort(); if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel() }, [])
  return {inputSupported, outputSupported, active: phase !== 'off', phase, listening: phase === 'listening', message, replies, start, stop, interrupt, retry: retryAnswer, speak, stopSpeaking, toggleReplies, copy}
}

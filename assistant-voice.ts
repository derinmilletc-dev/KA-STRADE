import {useCallback, useEffect, useRef, useState} from 'react'

export type VoiceLanguage = 'tr' | 'en'

export const voiceCopy = {
  tr: {
    mic: 'Sesle sor', stopMic: 'Dinlemeyi durdur', listening: 'Dinliyorum…', repliesOn: 'Sesli cevap açık', repliesOff: 'Sesli cevap kapalı',
    unsupported: 'Bu tarayıcı sesli girişi desteklemiyor. Chrome, Edge veya Safari kullan.',
    denied: 'Mikrofon izni verilmedi. Tarayıcı ayarlarından bu site için mikrofona izin ver.',
    noSpeech: 'Ses duyulmadı, tekrar dene.', failed: 'Ses algılanamadı, tekrar dene.',
    hint: 'Mikrofona bir kez bas, sonra konuşarak sohbet et. Bitirmek için “bitir” de.',
    conversation: 'Sesli sohbeti başlat', endConversation: 'Sesli sohbeti bitir', thinking: 'Düşünüyorum…', speaking: 'Konuşuyor…',
    open: 'Sohbeti aç', end: 'Bitir', ended: 'Sesli sohbet bitti.', idleEnded: 'Uzun süre ses gelmediği için sesli sohbet bitti.',
  },
  en: {
    mic: 'Ask by voice', stopMic: 'Stop listening', listening: 'Listening…', repliesOn: 'Spoken replies on', repliesOff: 'Spoken replies off',
    unsupported: 'This browser does not support voice input. Use Chrome, Edge or Safari.',
    denied: 'Microphone permission was not granted. Allow the microphone for this site in your browser settings.',
    noSpeech: 'No speech was heard, please try again.', failed: 'Speech could not be recognised, please try again.',
    hint: 'Tap the microphone once, then just talk. Say “stop” to end.',
    conversation: 'Start voice chat', endConversation: 'End voice chat', thinking: 'Thinking…', speaking: 'Speaking…',
    open: 'Open chat', end: 'End', ended: 'Voice chat ended.', idleEnded: 'Voice chat ended after a long silence.',
  },
} as const

type RecognitionEvent = {results: ArrayLike<ArrayLike<{transcript: string}> & {isFinal?: boolean}>}
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
function pickVoice(voices: SpeechSynthesisVoice[], tag: string, language: string): SpeechSynthesisVoice | undefined {
  const matching = voices.filter(item => item.lang.replace('_', '-').toLowerCase().startsWith(language))
  const score = (item: SpeechSynthesisVoice) => {
    const name = item.name.toLowerCase()
    let value = item.lang.replace('_', '-') === tag ? 1 : 0
    if (/natural|neural/.test(name)) value += 8
    if (/online/.test(name)) value += 5
    if (/google/.test(name)) value += 4
    if (/premium|enhanced|siri/.test(name)) value += 4
    if (item.localService === false) value += 1
    return value
  }
  return matching.sort((a, b) => score(b) - score(a))[0]
}

export type VoicePhase = 'off' | 'listening' | 'thinking' | 'speaking'
const IDLE_LIMIT_MS = 5 * 60 * 1000

const STORAGE_KEY = 'kt_voice_replies'
const readReplies = () => { try { return localStorage.getItem(STORAGE_KEY) === '1' } catch { return false } }
const writeReplies = (value: boolean) => { try { localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* tercih saklanamazsa oturum boyunca geçerli */ } }

export function useVoice(language: VoiceLanguage, onTranscript: (text: string) => Promise<string | null>) {
  const copy = voiceCopy[language]
  const recognition = useRef<Recognition | null>(null)
  const callback = useRef(onTranscript)
  callback.current = onTranscript
  const active = useRef(false)
  const failures = useRef(0)
  const lastSpeech = useRef(0)
  const speakToken = useRef(0)
  const [phase, setPhase] = useState<VoicePhase>('off')
  const phaseRef = useRef<VoicePhase>('off')
  phaseRef.current = phase
  const [message, setMessage] = useState('')
  const [replies, setReplies] = useState(readReplies)
  const inputSupported = typeof window !== 'undefined' && recognitionConstructor() !== null
  const outputSupported = typeof window !== 'undefined' && 'speechSynthesis' in window
  const tag = language === 'tr' ? 'tr-TR' : 'en-US'

  const stopSpeaking = useCallback(() => { speakToken.current += 1; if (outputSupported) window.speechSynthesis.cancel() }, [outputSupported])
  const speak = useCallback((text: string, onDone?: () => void) => {
    const clean = speakableText(text)
    if (!outputSupported || !clean) { onDone?.(); return }
    window.speechSynthesis.cancel()
    const token = ++speakToken.current
    const voices = window.speechSynthesis.getVoices()
    const voice = pickVoice(voices, tag, language)
    const parts = chunks(clean)
    let finished = false
    const done = () => { if (finished || token !== speakToken.current) return; finished = true; onDone?.() }
    // Bazı tarayıcılarda "bitti" olayı gelmeyebilir; metin uzunluğuna göre yedek süre.
    const fallback = window.setTimeout(done, 4000 + clean.length * 90)
    parts.forEach((part, index) => {
      const utterance = new SpeechSynthesisUtterance(part)
      utterance.lang = tag
      if (voice) utterance.voice = voice
      utterance.rate = 1.05
      if (index === parts.length - 1) {
        utterance.onend = () => { window.clearTimeout(fallback); done() }
        utterance.onerror = () => { window.clearTimeout(fallback); done() }
      }
      window.speechSynthesis.speak(utterance)
    })
  }, [language, outputSupported, tag])

  const end = useCallback((note = '') => {
    active.current = false
    recognition.current?.abort()
    recognition.current = null
    stopSpeaking()
    setPhase('off')
    setMessage(note)
  }, [stopSpeaking])

  const listen = useCallback(() => {
    const Constructor = recognitionConstructor()
    if (!active.current || !Constructor) return
    if (Date.now() - lastSpeech.current > IDLE_LIMIT_MS) { end(copy.idleEnded); return }
    recognition.current?.abort()
    const instance = new Constructor()
    instance.lang = tag
    instance.interimResults = false
    instance.maxAlternatives = 1
    instance.continuous = false
    let transcript = ''
    let fatal = false
    instance.onresult = event => {
      transcript = Array.from(event.results).map(result => result[0]?.transcript ?? '').join(' ').trim()
    }
    instance.onerror = event => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') { fatal = true; end(copy.denied) }
      else if (event.error !== 'no-speech' && event.error !== 'aborted') failures.current += 1
    }
    instance.onend = () => {
      if (recognition.current === instance) recognition.current = null
      if (!active.current || fatal) return
      const question = stripWakeWord(transcript)
      if (!transcript || !question) {
        if (failures.current > 5) { end(copy.failed); return }
        window.setTimeout(listen, failures.current ? 600 : 150)
        return
      }
      failures.current = 0
      lastSpeech.current = Date.now()
      if (isStopPhrase(question)) { end(copy.ended); return }
      setPhase('thinking')
      void callback.current(question).then(reply => {
        if (!active.current) return
        if (!reply) { setPhase('listening'); listen(); return }
        setPhase('speaking')
        speak(reply, () => { if (active.current) { setPhase('listening'); listen() } })
      })
    }
    recognition.current = instance
    try { instance.start(); setPhase('listening') } catch { failures.current += 1; window.setTimeout(listen, 800) }
  }, [copy, end, speak, tag])

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
  return {inputSupported, outputSupported, active: phase !== 'off', phase, listening: phase === 'listening', message, replies, start, stop, speak, stopSpeaking, toggleReplies, copy}
}

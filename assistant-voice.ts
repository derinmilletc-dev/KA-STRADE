import {useCallback, useEffect, useRef, useState} from 'react'

export type VoiceLanguage = 'tr' | 'en'

export const voiceCopy = {
  tr: {
    mic: 'Sesle sor', stopMic: 'Dinlemeyi durdur', listening: 'Dinliyorum…', repliesOn: 'Sesli cevap açık', repliesOff: 'Sesli cevap kapalı',
    unsupported: 'Bu tarayıcı sesli girişi desteklemiyor. Chrome, Edge veya Safari kullan.',
    denied: 'Mikrofon izni verilmedi. Tarayıcı ayarlarından bu site için mikrofona izin ver.',
    noSpeech: 'Ses duyulmadı, tekrar dene.', failed: 'Ses algılanamadı, tekrar dene.',
    hint: 'İpucu: “Hey Kais, işlemlerim ne durumda?” diyebilirsin.',
  },
  en: {
    mic: 'Ask by voice', stopMic: 'Stop listening', listening: 'Listening…', repliesOn: 'Spoken replies on', repliesOff: 'Spoken replies off',
    unsupported: 'This browser does not support voice input. Use Chrome, Edge or Safari.',
    denied: 'Microphone permission was not granted. Allow the microphone for this site in your browser settings.',
    noSpeech: 'No speech was heard, please try again.', failed: 'Speech could not be recognised, please try again.',
    hint: 'Tip: you can say “Hey Kais, how are my trades doing?”.',
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
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_#>~|]+/g, ' ')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 900)
}

const STORAGE_KEY = 'kt_voice_replies'
const readReplies = () => { try { return localStorage.getItem(STORAGE_KEY) === '1' } catch { return false } }
const writeReplies = (value: boolean) => { try { localStorage.setItem(STORAGE_KEY, value ? '1' : '0') } catch { /* tercih saklanamazsa oturum boyunca geçerli */ } }

export function useVoice(language: VoiceLanguage, onTranscript: (text: string) => void) {
  const copy = voiceCopy[language]
  const recognition = useRef<Recognition | null>(null)
  const callback = useRef(onTranscript)
  callback.current = onTranscript
  const [listening, setListening] = useState(false)
  const [message, setMessage] = useState('')
  const [replies, setReplies] = useState(readReplies)
  const inputSupported = typeof window !== 'undefined' && recognitionConstructor() !== null
  const outputSupported = typeof window !== 'undefined' && 'speechSynthesis' in window

  const stopSpeaking = useCallback(() => { if (outputSupported) window.speechSynthesis.cancel() }, [outputSupported])
  const speak = useCallback((text: string) => {
    if (!outputSupported) return
    const clean = speakableText(text)
    if (!clean) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(clean)
    const tag = language === 'tr' ? 'tr-TR' : 'en-US'
    utterance.lang = tag
    const voice = window.speechSynthesis.getVoices().find(item => item.lang === tag) ?? window.speechSynthesis.getVoices().find(item => item.lang.startsWith(language))
    if (voice) utterance.voice = voice
    utterance.rate = 1
    window.speechSynthesis.speak(utterance)
  }, [language, outputSupported])

  const stop = useCallback(() => { recognition.current?.stop() }, [])
  const start = useCallback(() => {
    const Constructor = recognitionConstructor()
    if (!Constructor) { setMessage(copy.unsupported); return }
    stopSpeaking()
    recognition.current?.abort()
    const instance = new Constructor()
    instance.lang = language === 'tr' ? 'tr-TR' : 'en-US'
    instance.interimResults = false
    instance.maxAlternatives = 1
    instance.continuous = false
    let heard = false
    instance.onresult = event => {
      const transcript = Array.from(event.results).map(result => result[0]?.transcript ?? '').join(' ').trim()
      if (transcript) { heard = true; callback.current(stripWakeWord(transcript)) }
    }
    instance.onerror = event => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') setMessage(copy.denied)
      else if (event.error === 'no-speech') setMessage(copy.noSpeech)
      else if (event.error !== 'aborted') setMessage(copy.failed)
    }
    instance.onend = () => { setListening(false); if (recognition.current === instance) recognition.current = null; if (!heard) setMessage(current => current || copy.noSpeech) }
    recognition.current = instance
    setMessage('')
    try { instance.start(); setListening(true) } catch { setListening(false); setMessage(copy.failed) }
  }, [copy, language, stopSpeaking])

  const toggleReplies = useCallback(() => {
    setReplies(current => { const next = !current; writeReplies(next); if (!next) stopSpeaking(); return next })
  }, [stopSpeaking])

  useEffect(() => () => { recognition.current?.abort(); if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel() }, [])
  return {inputSupported, outputSupported, listening, message, replies, start, stop, speak, stopSpeaking, toggleReplies, copy}
}

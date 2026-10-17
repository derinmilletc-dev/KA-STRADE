import {useCallback, useEffect, useRef, useState} from 'react'

type Language = 'tr' | 'en'
type RecognitionResult = {isFinal: boolean; 0: {transcript: string}}
type RecognitionEvent = {resultIndex: number; results: ArrayLike<RecognitionResult>}
type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: {error: string}) => void) | null
  onend: (() => void) | null
  start(): void; abort(): void
}
type RecognitionConstructor = new () => Recognition

export type VoiceStatus = 'off' | 'wake' | 'conversation' | 'speaking'
export type VoiceError = 'denied' | 'nomic' | null

// Speech engines write the wake word in many ways ("hey kais", "hey kays", "hey case"...).
const wakeWord = /\bhey+\s*(?:kais|kays|kaiz|kayis|keys|case|kes|kaz)\b[\s,.!?:;-]*/i
const stopWord = /^(?:tamam|bitir|kapat|yeter|sus|stop|that's all|goodbye|bye|güle güle)[\s.!?]*$/i
const CONVERSATION_IDLE_MS = 30_000

const constructorOf = (): RecognitionConstructor | null => {
  const scope = window as unknown as {SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor}
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}
const localeOf = (language: Language) => language === 'tr' ? 'tr-TR' : 'en-US'
const spokenText = (value: string) => value.replace(/\*\*/g, '').replace(/[`#>_~]/g, '').replace(/\s+/g, ' ').trim()

export function useKaisVoice({language, onCommand, greeting}: {language: Language; onCommand: (text: string) => void; greeting: Record<Language, string>}) {
  const [supported] = useState(() => constructorOf() !== null && 'speechSynthesis' in window)
  const [status, setStatus] = useState<VoiceStatus>('off')
  const [error, setError] = useState<VoiceError>(null)
  const recognition = useRef<Recognition | null>(null)
  const enabled = useRef(false)
  const speaking = useRef(false)
  const conversation = useRef(false)
  const idleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const restartTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const commandRef = useRef(onCommand)
  const languageRef = useRef(language)
  const greetingRef = useRef(greeting)
  const speakRef = useRef<(text: string, spokenLanguage: Language) => void>(() => undefined)
  useEffect(() => { commandRef.current = onCommand; languageRef.current = language; greetingRef.current = greeting })

  const idleLabel = useCallback((): VoiceStatus => conversation.current ? 'conversation' : 'wake', [])

  const endConversation = useCallback(() => {
    conversation.current = false
    clearTimeout(idleTimer.current)
    if (enabled.current && !speaking.current) setStatus('wake')
  }, [])
  const touchConversation = useCallback(() => {
    clearTimeout(idleTimer.current)
    idleTimer.current = setTimeout(endConversation, CONVERSATION_IDLE_MS)
  }, [endConversation])

  const begin = useCallback(() => {
    clearTimeout(restartTimer.current)
    const Engine = constructorOf()
    if (!Engine || !enabled.current || speaking.current) return
    const engine = new Engine()
    engine.lang = localeOf(languageRef.current); engine.continuous = true; engine.interimResults = false
    engine.onresult = event => {
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        if (!result.isFinal || speaking.current) continue
        const heard = result[0].transcript.trim()
        if (!heard) continue
        if (conversation.current) {
          if (stopWord.test(heard)) { endConversation(); continue }
          touchConversation(); commandRef.current(heard)
          continue
        }
        const match = wakeWord.exec(heard)
        if (!match) continue
        conversation.current = true; setStatus('conversation'); touchConversation()
        const rest = heard.slice(match.index + match[0].length).trim()
        if (rest) commandRef.current(rest)
        else speakRef.current(greetingRef.current[languageRef.current], languageRef.current)
      }
    }
    engine.onerror = event => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') { setError('denied'); stopAll() }
      else if (event.error === 'audio-capture') { setError('nomic'); stopAll() }
    }
    engine.onend = () => {
      if (recognition.current === engine) recognition.current = null
      if (enabled.current && !speaking.current) restartTimer.current = setTimeout(begin, 250)
    }
    recognition.current = engine
    try { engine.start() } catch (failure) { if (!(failure instanceof DOMException)) throw failure }
  }, [endConversation, touchConversation])

  function stopAll() {
    enabled.current = false; conversation.current = false; speaking.current = false
    clearTimeout(idleTimer.current); clearTimeout(restartTimer.current)
    const engine = recognition.current
    recognition.current = null
    if (engine) { engine.onend = null; engine.onerror = null; engine.onresult = null; engine.abort() }
    window.speechSynthesis?.cancel()
    setStatus('off')
  }

  const speak = useCallback((value: string, spokenLanguage: Language) => {
    const content = spokenText(value)
    if (!enabled.current || !content) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(content)
    utterance.lang = localeOf(spokenLanguage)
    const voice = window.speechSynthesis.getVoices().find(item => item.lang.toLowerCase().startsWith(spokenLanguage))
    if (voice) utterance.voice = voice
    const done = () => {
      speaking.current = false
      if (!enabled.current) return
      setStatus(idleLabel()); touchConversation()
      restartTimer.current = setTimeout(begin, 400)
    }
    utterance.onstart = () => {
      speaking.current = true; setStatus('speaking')
      const engine = recognition.current
      recognition.current = null
      if (engine) { engine.onend = null; engine.abort() }
    }
    utterance.onend = done; utterance.onerror = done
    window.speechSynthesis.speak(utterance)
  }, [begin, idleLabel, touchConversation])
  useEffect(() => { speakRef.current = speak }, [speak])

  const start = useCallback(() => {
    if (!supported || enabled.current) return
    enabled.current = true; setError(null); setStatus('wake')
    begin()
  }, [supported, begin])

  // The recognition language follows the language selected on the site.
  useEffect(() => {
    if (!enabled.current) return
    const engine = recognition.current
    if (engine) engine.abort()
  }, [language])

  useEffect(() => () => stopAll(), [])

  const dismiss = useCallback(() => { stopAll(); setError(null) }, [])

  return {supported, status, error, start, stop: stopAll, dismiss, speak}
}

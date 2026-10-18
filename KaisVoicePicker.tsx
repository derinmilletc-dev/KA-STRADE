import {useEffect, useState} from 'react'
import {Volume2} from 'lucide-react'
import {savedVoiceName, saveVoiceName, voicesFor, savedRate, saveRate} from './assistant-voice'

/** Ses seçici: cihazın sesleri listelenir, dokununca örnek okunur ve seçim saklanır. "Otomatik" erkek ismi öncelikli seçer. */
export default function KaisVoicePicker({language}: {language: 'tr' | 'en'}) {
  const [open, setOpen] = useState(false)
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => voicesFor(language))
  const [current, setCurrent] = useState(savedVoiceName)
  const [rate, setRate] = useState(savedRate)
  useEffect(() => {
    if (!window.speechSynthesis) return
    const load = () => setVoices(voicesFor(language))
    load()
    window.speechSynthesis.addEventListener?.('voiceschanged', load)
    return () => window.speechSynthesis.removeEventListener?.('voiceschanged', load)
  }, [language])
  if (!window.speechSynthesis || voices.length === 0) return null
  const sample = language === 'en' ? 'Hello, I am Kais.' : 'Merhaba, ben Kais.'
  const choose = (name: string) => {
    saveVoiceName(name); setCurrent(name)
    const synth = window.speechSynthesis
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(sample)
    const voice = voices.find(item => item.name === name)
    if (voice) { utterance.voice = voice; utterance.lang = voice.lang }
    utterance.rate = 1.1
    synth.speak(utterance)
  }
  return <div className="kaisVoicePicker">
    <button type="button" className="kaisVoicePickerToggle" aria-expanded={open} onClick={() => setOpen(value => !value)}><Volume2 aria-hidden="true"/>{language === 'en' ? 'Voice' : 'Ses'}</button>
    {open && <div className="kaisVoiceRate" role="group" aria-label={language === 'en' ? 'Speaking speed' : 'Konuşma hızı'}>
      {[[.95, language === 'en' ? 'Slow' : 'Yavaş'], [1.15, language === 'en' ? 'Normal' : 'Normal'], [1.35, language === 'en' ? 'Fast' : 'Hızlı']].map(([value, label]) => <button key={String(value)} type="button" aria-pressed={rate === value} onClick={() => { saveRate(value as number); setRate(value as number) }}>{label}</button>)}
    </div>}
    {open && <ul role="listbox" aria-label={language === 'en' ? 'Voices' : 'Sesler'}>
      <li><button type="button" role="option" aria-selected={!current} onClick={() => choose('')}>{language === 'en' ? 'Automatic (male first)' : 'Otomatik (erkek öncelikli)'}</button></li>
      {voices.map(voice => <li key={voice.name}><button type="button" role="option" aria-selected={current === voice.name} onClick={() => choose(voice.name)}>{voice.name}</button></li>)}
    </ul>}
  </div>
}

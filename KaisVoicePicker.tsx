import {useEffect, useState} from 'react'
import {Volume2} from 'lucide-react'
import {savedVoiceName, saveVoiceName, voicesFor, otherVoices, savedRate, saveRate} from './assistant-voice'

/** Ses seçici: cihazın sesleri listelenir, dokununca örnek okunur ve seçim saklanır. "Otomatik" erkek ismi öncelikli seçer. */
export default function KaisVoicePicker({language}: {language: 'tr' | 'en'}) {
  const [open, setOpen] = useState(false)
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => voicesFor(language))
  const [current, setCurrent] = useState(savedVoiceName)
  const [rate, setRate] = useState(savedRate)
  const [others, setOthers] = useState<SpeechSynthesisVoice[]>(() => otherVoices(language))
  const [showOthers, setShowOthers] = useState(false)
  useEffect(() => {
    if (!window.speechSynthesis) return
    const load = () => { setVoices(voicesFor(language)); setOthers(otherVoices(language)) }
    load()
    window.speechSynthesis.addEventListener?.('voiceschanged', load)
    return () => window.speechSynthesis.removeEventListener?.('voiceschanged', load)
  }, [language])
  if (!window.speechSynthesis || voices.length === 0) return null
  const sample = language === 'en' ? 'Hello, I am Kais.' : 'Merhaba, ben Kais.'
  const preview = (value: number) => {
    const synth = window.speechSynthesis; synth.cancel()
    const utterance = new SpeechSynthesisUtterance(sample)
    const voice = [...voices, ...others].find(item => item.name === current)
    if (voice) { utterance.voice = voice; utterance.lang = voice.lang }
    utterance.rate = value; synth.speak(utterance)
  }
  const choose = (name: string) => {
    saveVoiceName(name); setCurrent(name)
    const synth = window.speechSynthesis
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(sample)
    const voice = [...voices, ...others].find(item => item.name === name)
    if (voice) { utterance.voice = voice; utterance.lang = voice.lang }
    utterance.rate = 1.1
    synth.speak(utterance)
  }
  return <div className="kaisVoicePicker">
    <button type="button" className="kaisVoicePickerToggle" aria-expanded={open} onClick={() => setOpen(value => !value)}><Volume2 aria-hidden="true"/>{language === 'en' ? 'Voice' : 'Ses'}</button>
    {open && <div className="kaisVoiceRate" role="group" aria-label={language === 'en' ? 'Speaking speed' : 'Konuşma hızı'}>
      {([[.85, language === 'en' ? 'Very slow' : 'Çok yavaş'], [1, language === 'en' ? 'Slow' : 'Yavaş'], [1.15, 'Normal'], [1.3, language === 'en' ? 'Fast' : 'Hızlı'], [1.45, language === 'en' ? 'Very fast' : 'Çok hızlı']] as [number, string][]).map(([value, label]) => <button key={value} type="button" aria-pressed={Math.abs(rate - value) < .03} onClick={() => { saveRate(value); setRate(value) }}>{label}</button>)}
      <input type="range" min=".8" max="1.5" step=".05" value={rate} aria-label={language === 'en' ? 'Fine speed' : 'İnce hız ayarı'} onChange={event => { const value = Number(event.target.value); saveRate(value); setRate(value) }} onPointerUp={() => preview(rate)}/>
    </div>}
    {open && <ul role="listbox" aria-label={language === 'en' ? 'Voices' : 'Sesler'}>
      <li><button type="button" role="option" aria-selected={!current} onClick={() => choose('')}>{language === 'en' ? 'Automatic (male first)' : 'Otomatik (erkek öncelikli)'}</button></li>
      {voices.map(voice => <li key={voice.name}><button type="button" role="option" aria-selected={current === voice.name} onClick={() => choose(voice.name)}>{voice.name}</button></li>)}
      {others.length > 0 && <li className="kaisVoiceMore"><button type="button" aria-expanded={showOthers} onClick={() => setShowOthers(value => !value)}>{language === 'en' ? 'Other languages' : 'Diğer diller'} ({others.length})</button></li>}
      {showOthers && others.map(voice => <li key={voice.name}><button type="button" role="option" aria-selected={current === voice.name} onClick={() => choose(voice.name)}>{voice.name} · {voice.lang}</button></li>)}
    </ul>}
  </div>
}

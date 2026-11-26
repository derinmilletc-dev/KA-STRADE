import { useCallback, useEffect, useRef, useState } from 'react'
import { Moon, PartyPopper, Snowflake, Sun, Sunrise, Sunset, Activity } from 'lucide-react'
import { pickGreeting, moodFromChange, type Greeting as GreetingValue, type GreetingKind } from './greetings'
import { useLiveTickers } from './use-live-tickers'

const ICONS: Record<GreetingKind, typeof Sun> = {morning: Sunrise, afternoon: Sun, evening: Sunset, night: Moon, newYear: PartyPopper, winter: Snowflake, mood: Activity}

/** Giriş ekranı için yalnız düz metin: özel gün ve uzun aradan dönüş dahil. */
export function useGreeting(): string {
  const [greeting] = useState(() => pickGreeting())
  return greeting.text
}

/** Harf harf yazılır; tam metin yer tutucu olarak görünmez durur, böylece düzen oynamaz. */
function useTypewriter(text: string): string {
  const [count, setCount] = useState(0)
  useEffect(() => {
    const reduce = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setCount(text.length); return }
    setCount(0)
    let index = 0
    const timer = window.setInterval(() => { index += 1; setCount(index); if (index >= text.length) window.clearInterval(timer) }, 28)
    return () => window.clearInterval(timer)
  }, [text])
  return text.slice(0, count)
}

/** Ana ekran karşılaması: saate göre ikon, piyasa ruh hali, yazılma efekti; dokununca yenisi gelir. */
export default function Greeting({className = ''}: {className?: string}) {
  const [greeting, setGreeting] = useState<GreetingValue>(() => pickGreeting())
  const live = useLiveTickers(['BTCUSDT'])
  const useMood = useRef(Math.random() < 0.4)
  const moodShown = useRef(false)
  const change = live.data.BTCUSDT?.changePercent
  useEffect(() => {
    if (change === undefined || !useMood.current || moodShown.current) return
    moodShown.current = true
    setGreeting(pickGreeting({mood: moodFromChange(change)}))
  }, [change])
  const refresh = useCallback(() => setGreeting(pickGreeting()), [])
  const shown = useTypewriter(greeting.text)
  const Icon = ICONS[greeting.kind]
  return <button type="button" className={`ktGreeting ${className}`.trim()} data-i18n-skip="true" translate="no" aria-label={greeting.text} title="↻" onClick={refresh}>
    <Icon aria-hidden="true"/>
    <span className="ktGreetingText" aria-hidden="true"><span>{shown}</span><span className="ktGreetingRest">{greeting.text.slice(shown.length)}</span></span>
  </button>
}

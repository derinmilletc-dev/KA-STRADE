import { useState } from 'react'
import { pickGreeting } from './greetings'

/** Her açılışta bir kez seçilen kısa, sıcak karşılama cümlesi (dile ve saate göre). */
export function useGreeting(): string {
  const [text] = useState(() => pickGreeting())
  return text
}

export default function Greeting({className = ''}: {className?: string}) {
  const text = useGreeting()
  return <p className={`ktGreeting ${className}`.trim()} data-i18n-skip="true" translate="no">{text}</p>
}

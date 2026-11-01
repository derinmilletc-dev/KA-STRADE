import {useEffect} from 'react'
import {setKaisScreenContext} from './kais-screen-context'

/** Ekran bileşeni içinde çağrılır; bileşen kapanınca kendini siler. text null/boş ise kayıt yazılmaz. */
export function useKaisScreenContext(key: string, text: string | null) {
  useEffect(() => {
    setKaisScreenContext(key, text)
    return () => setKaisScreenContext(key, null)
  }, [key, text])
}

import {useEffect} from 'react'
import {accountRequest} from './account-settings-api'
import {applyAccountLang} from './i18n'

/** Giriş yapan müşterinin hesabındaki dili uygular (başka cihazda seçtiyse burada da o dil açılır). */
export default function AccountLanguageSync() {
  useEffect(() => {
    let alive = true
    accountRequest<{language?: string | null}>('/account/overview')
      .then(data => { if (alive) applyAccountLang(data.language) })
      .catch(() => undefined)
    return () => { alive = false }
  }, [])
  return null
}

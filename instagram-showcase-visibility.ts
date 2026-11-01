import { useEffect, useState } from 'react'

/** Instagram vitrini yalnız geniş masaüstü ekranlarda gösterilir (CSS ile aynı eşik). Telefon ve tablette
 * bileşen hiç yüklenmez ve akış isteği yapılmaz. */
export const SHOWCASE_MEDIA_QUERY = '(min-width: 1280px) and (min-height: 600px)'

export function useShowcaseVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia(SHOWCASE_MEDIA_QUERY).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(SHOWCASE_MEDIA_QUERY)
    const update = () => setVisible(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return visible
}

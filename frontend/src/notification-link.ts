// Bildirime basınca açılan adresin (?ex=coinbase&alert=SEMBOL) çözümü. Saf mantık + küçük adres işlemleri.
export type LinkExchange = 'binance' | 'coinbase'
const isExchange = (value: string | null): value is LinkExchange => value === 'binance' || value === 'coinbase'
const SYMBOL = /^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$/

/** Adresten borsa ve alarm sembolünü okur; geçersizse null. */
export function parseNotificationLink(search: string): {exchange: LinkExchange | null; alert: string | null} {
  const params = new URLSearchParams(search)
  const ex = params.get('ex'), alert = params.get('alert')
  return {exchange: isExchange(ex) ? ex : null, alert: alert && SYMBOL.test(alert) ? alert : null}
}

/** Bu borsa ekranı için bekleyen alarm sembolünü alır ve adresten siler (bir kez). Başka borsa içinse dokunmaz. */
export function takeAlertSymbol(exchange: LinkExchange): string | null {
  try {
    const link = parseNotificationLink(window.location.search)
    if (!link.alert || (link.exchange && link.exchange !== exchange)) return null
    const url = new URL(window.location.href)
    url.searchParams.delete('alert'); url.searchParams.delete('ex')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
    return link.alert
  } catch { return null }
}

/** Bildirimdeki ?read=SEMBOL (pozisyon yorum alarmı): bu borsaya aitse ve sembol geçerliyse okur; adrese dokunmaz. */
export function readLinkTarget(exchange: LinkExchange, search: string = window.location.search): string | null {
  const params = new URLSearchParams(search)
  const read = params.get('read'), ex = params.get('ex')
  if (!read || !SYMBOL.test(read) || (isExchange(ex) && ex !== exchange)) return null
  return read
}

/** Bildirim hedefi işlendikten sonra adresten ?read ve ?ex silinir (yenilemede tekrar açılmasın). */
export function clearReadLink(): void {
  try {
    const url = new URL(window.location.href)
    url.searchParams.delete('read'); url.searchParams.delete('ex')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  } catch { /* adres işlenemedi */ }
}

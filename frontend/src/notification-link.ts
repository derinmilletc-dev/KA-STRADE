// Bildirime basınca açılan adresin (?ex=coinbase&alert=SEMBOL) çözümü. Saf mantık + küçük adres işlemleri.
import {writeExchange} from './exchange-pref.ts'

export type LinkExchange = 'binance' | 'coinbase'
const isExchange = (value: string | null): value is LinkExchange => value === 'binance' || value === 'coinbase'
const SYMBOL = /^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$/

/** Adresten borsa ve alarm sembolünü okur; geçersizse null. */
export function parseNotificationLink(search: string): {exchange: LinkExchange | null; alert: string | null} {
  const params = new URLSearchParams(search)
  const ex = params.get('ex'), alert = params.get('alert')
  return {exchange: isExchange(ex) ? ex : null, alert: alert && SYMBOL.test(alert) ? alert : null}
}

/** Uygulama açılırken: bildirimdeki borsayı seçili yapar (sayfa o borsayla açılsın). */
export function applyNotificationExchange(): void {
  try { const {exchange} = parseNotificationLink(window.location.search); if (exchange) writeExchange(exchange) } catch { /* adres okunamadı */ }
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

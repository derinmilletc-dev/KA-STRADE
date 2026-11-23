// Uygulama açılırken: bildirimdeki borsayı seçili yapar (sayfa o borsayla açılsın).
import {writeExchange} from './exchange-pref'
import {parseNotificationLink} from './notification-link'

export function applyNotificationExchange(): void {
  try { const {exchange} = parseNotificationLink(window.location.search); if (exchange) writeExchange(exchange) } catch { /* adres okunamadı */ }
}

// Seçili borsa (Binance / Coinbase) cihazda hatırlanır; ana sayfa ve Master Trade aynı seçimi kullanır.
import type {Exchange} from './CoinbaseExchangeSwitch'

const KEY = 'kt_exchange'

export function readExchange(): Exchange | null {
  try {
    const saved = window.localStorage.getItem(KEY)
    return saved === 'binance' || saved === 'coinbase' ? saved : null
  } catch { return null }
}

export function writeExchange(value: Exchange): void {
  try { window.localStorage.setItem(KEY, value) } catch { /* depolama kapalı olabilir; seçim yalnız bu oturumda geçerli */ }
}

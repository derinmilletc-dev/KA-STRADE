// Analiz stüdyosu ile Manuel işlem arasında küçük bir köprü.
// Emir göndermez: yalnız "şu coinle manuel işlem ekranını aç" ve "şu coini grafikte göster" isteklerini taşır.
// Emir yine aynı Manuel işlem formundan, aynı dry-run / ARM / yazı onayı akışıyla gider.

export type TradeSide = 'LONG' | 'SHORT'
type Intent = {symbol: string; direction: TradeSide | null; at: number; applied?: boolean; opened?: boolean; mode?: 'grid'; pct?: number}

const KEY = 'kt-manual-intent-v1'
const TTL_MS = 2 * 60 * 1000
export const MANUAL_INTENT_EVENT = 'kt:manual-intent'
export const MANUAL_CLOSE_EVENT = 'kt:manual-close'
export const FOLLOW_SYMBOL_EVENT = 'kt:follow-symbol'

const read = (): Intent | null => {
  try {
    const raw = window.sessionStorage.getItem(KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Intent
    if (!value || typeof value.symbol !== 'string' || !/^[A-Z0-9]{2,20}$/.test(value.symbol) || !(Date.now() - Number(value.at) < TTL_MS)) {
      window.sessionStorage.removeItem(KEY)
      return null
    }
    return value
  } catch { return null }
}
const write = (value: Intent | null) => {
  try {
    if (!value || (value.applied && value.opened)) window.sessionStorage.removeItem(KEY)
    else window.sessionStorage.setItem(KEY, JSON.stringify(value))
  } catch { /* depolama kapalıysa olay yine çalışır */ }
}

let memory: Intent | null = null
const current = () => read() ?? (memory && Date.now() - memory.at < TTL_MS ? memory : null)
const save = (value: Intent) => { memory = value.applied && value.opened ? null : value; write(value) }

/** Stüdyodaki "İşlem aç": Manuel işlem ekranını bu coinle açtırır. */
export function requestManualTrade(symbol: string, direction?: string | null, options?: {mode?: 'grid'; pct?: number}) {
  const clean = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
  if (!clean) return
  const pct = Number(options?.pct)
  save({symbol: clean, direction: direction === 'LONG' || direction === 'SHORT' ? direction : null, at: Date.now(),
    ...(options?.mode === 'grid' ? {mode: 'grid' as const, pct: Number.isFinite(pct) && pct > 0 && pct <= 50 ? pct : 5} : {})})
  window.setTimeout(() => window.dispatchEvent(new CustomEvent(MANUAL_INTENT_EVENT)), 60)
}

/** Manuel işlem formu: coin ve yönü bir kez alır. */
export function takeIntentForOrder(): {symbol: string; direction: TradeSide | null; mode?: 'grid'; pct?: number} | null {
  const value = current()
  if (!value || value.applied) return null
  save({...value, applied: true})
  return {symbol: value.symbol, direction: value.direction, ...(value.mode ? {mode: value.mode, pct: value.pct} : {})}
}

/** Canlı İşlem kartı: paneli bir kez açar. */
export function takeIntentForOpen(): boolean {
  const value = current()
  if (!value || value.opened) return false
  save({...value, opened: true})
  return true
}

// Analysis Studio'yu (tam ekran grafik) açma isteği. Analiz ekranı henüz yüklenmemişse istek kısa süre saklanır;
// ekran açılınca tüketilir. Bayat istek (5 sn'den eski) yok sayılır, böylece sonradan kendiliğinden açılmaz.
export const OPEN_STUDIO_EVENT = 'kt:open-studio'
const STUDIO_REQUEST_TTL_MS = 5000
let studioRequestAt = 0
export function consumeStudioRequest(now = Date.now()): boolean {
  const pending = studioRequestAt > 0 && now - studioRequestAt <= STUDIO_REQUEST_TTL_MS
  studioRequestAt = 0
  return pending
}

/** Emir sonrası "Grafikte canlı takip et": paneli kapatır, analiz stüdyosunu bu coinle açar. */
export function followSymbol(symbol: string) {
  studioRequestAt = Date.now()
  window.dispatchEvent(new CustomEvent(MANUAL_CLOSE_EVENT))
  window.dispatchEvent(new CustomEvent(FOLLOW_SYMBOL_EVENT, {detail: {symbol, tab: 'analiz'}}))
  window.dispatchEvent(new CustomEvent(OPEN_STUDIO_EVENT))
}

/** "Pozisyonlarıma git": paneli kapatır, Pozisyonlar sekmesini açar. */
export function goPositions() {
  window.dispatchEvent(new CustomEvent(MANUAL_CLOSE_EVENT))
  window.dispatchEvent(new CustomEvent(FOLLOW_SYMBOL_EVENT, {detail: {symbol: null, tab: 'pozisyonlar'}}))
}

// Analiz stüdyosu ile Manuel işlem arasında küçük bir köprü.
// Emir göndermez: yalnız "şu coinle manuel işlem ekranını aç" ve "şu coini grafikte göster" isteklerini taşır.
// Emir yine aynı Manuel işlem formundan, aynı dry-run / ARM / yazı onayı akışıyla gider.

export type TradeSide = 'LONG' | 'SHORT'
/** Kais'in hazırladığı seviyeler. Emir göndermez: Manuel işlem formunu doldurur; dry-run, izin, ARM ve yazılı onay aynen geçerlidir. */
export type IntentLevels = {stop_loss: number; tp1: number; tp2: number; tp3: number; margin_usdt?: number; leverage?: number}
type Intent = {symbol: string; direction: TradeSide | null; at: number; applied?: boolean; opened?: boolean; mode?: 'grid'; pct?: number; levels?: IntentLevels}

const KEY = 'kt-manual-intent-v1'
const TTL_MS = 2 * 60 * 1000
export const MANUAL_INTENT_EVENT = 'kt:manual-intent'
export const MANUAL_CLOSE_EVENT = 'kt:manual-close'
/** Manuel işlem paneli kapandı (çarpı, dış alan, Esc): form bir sonraki açılışta en baştan, güncel fiyatlarla başlasın. */
export const MANUAL_CLOSED_EVENT = 'kt:manual-closed'
export const FOLLOW_SYMBOL_EVENT = 'kt:follow-symbol'
/** Canlı manuel emir borsaya GÖNDERİLDİKTEN sonra (sunucu kabul etti) yayılır; Kais takip alarmı kurmak için dinler. */
export const LIVE_ORDER_SENT_EVENT = 'kt:live-order-sent'
export type LiveOrderSent = {symbol: string; direction: TradeSide; stop_loss: number; tp1: number}

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
const cleanLevels = (levels?: IntentLevels): IntentLevels | undefined => {
  if (!levels) return undefined
  const positive = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0
  if (![levels.stop_loss, levels.tp1, levels.tp2, levels.tp3].every(positive)) return undefined
  return {stop_loss: levels.stop_loss, tp1: levels.tp1, tp2: levels.tp2, tp3: levels.tp3,
    ...(positive(levels.margin_usdt) ? {margin_usdt: levels.margin_usdt} : {}), ...(positive(levels.leverage) && Number.isInteger(levels.leverage) ? {leverage: levels.leverage} : {})}
}

export function requestManualTrade(symbol: string, direction?: string | null, options?: {mode?: 'grid'; pct?: number; levels?: IntentLevels}) {
  const clean = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
  if (!clean) return
  const pct = Number(options?.pct)
  save({symbol: clean, direction: direction === 'LONG' || direction === 'SHORT' ? direction : null, at: Date.now(),
    ...(options?.mode === 'grid' ? {mode: 'grid' as const, pct: Number.isFinite(pct) && pct > 0 && pct <= 50 ? pct : 5} : {}),
    ...(cleanLevels(options?.levels) ? {levels: cleanLevels(options?.levels)} : {})})
  window.setTimeout(() => window.dispatchEvent(new CustomEvent(MANUAL_INTENT_EVENT)), 60)
}

/** Manuel işlem formu: coin ve yönü bir kez alır. */
export function takeIntentForOrder(): {symbol: string; direction: TradeSide | null; mode?: 'grid'; pct?: number; levels?: IntentLevels} | null {
  const value = current()
  if (!value || value.applied) return null
  save({...value, applied: true})
  return {symbol: value.symbol, direction: value.direction, ...(value.mode ? {mode: value.mode, pct: value.pct} : {}), ...(value.levels ? {levels: value.levels} : {})}
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

/** "Pozisyonlarıma git": paneli kapatır, Canlı İşlem sekmesindeki "Açık pozisyonlar" kartına götürür (Pozisyonlar sekmesine değil). `focus` verilirse o coinin pozisyonuna kaydırılır (grafik coini değişmez). */
export function goPositions(focus?: string) {
  window.dispatchEvent(new CustomEvent(MANUAL_CLOSE_EVENT))
  window.dispatchEvent(new CustomEvent(FOLLOW_SYMBOL_EVENT, {detail: {symbol: null, tab: 'canli', focus: focus || null}}))
  if (focus) requestPositionsRefresh()
}

const isShown = (element: Element | null): element is HTMLElement => element instanceof HTMLElement && element.getClientRects().length > 0

/** Sekme açıldıktan sonra açık pozisyonun bulunduğu yere kaydırır: önce o coinin kartı, yoksa pozisyon listesi.
 *  Yeni emir borsadan birkaç sn geç göründüğü için kısa süre beklenir; kart gelmezse liste başına gidilir. Hata vermez. */
export function scrollToPositions(focus?: string | null, tries = 0) {
  const symbol = String(focus || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
  const card = symbol ? document.querySelector(`[data-pos-symbol="${symbol}"]`) : null
  const list = Array.from(document.querySelectorAll('.pvRoot, .positionsPanel')).find(isShown) ?? null
  const target = isShown(card) ? card : tries >= 12 ? list : null
  if (target) {
    target.scrollIntoView({behavior: 'smooth', block: 'start'})
    if (target === card) { target.setAttribute('data-pos-flash', ''); window.setTimeout(() => target.removeAttribute('data-pos-flash'), 2200) }
    return
  }
  if (tries < 24) window.setTimeout(() => scrollToPositions(focus, tries + 1), 250)
}

/** Hızlı AL/SAT, kapatma ve azaltma sonrası: açık pozisyon listesinin hemen yenilenmesini ister (Demo ve Canlı dinler). */
export const POSITIONS_REFRESH_EVENT = 'kt:positions-refresh'
export function requestPositionsRefresh(extended = false) {
  // Borsa kaydı birkaç saniye geç yansıyabilir; kısa aralıklarla üç kez istenir. Yeni açılan emirde (extended) pozisyon görünene kadar ~25 sn denenir.
  const delays = extended ? [0, 1500, 3000, 5000, 8000, 12000, 18000, 25000] : [0, 1500, 4500]
  for (const delay of delays) window.setTimeout(() => window.dispatchEvent(new CustomEvent(POSITIONS_REFRESH_EVENT)), delay)
}

/** Emir borsaya gitti ama pozisyon listesi henüz yenilenmedi: kart "Açık pozisyon yok" yerine "yükleniyor" gösterir (en fazla 60 sn). */
const PENDING_KEY = 'kt-order-pending-v1'
const PENDING_MS = 60 * 1000
let pendingMemory: {symbol: string; until: number} | null = null
export function markOrderPending(symbol: string) {
  const clean = String(symbol || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
  if (!clean) return
  pendingMemory = {symbol: clean, until: Date.now() + PENDING_MS}
  try { window.sessionStorage.setItem(PENDING_KEY, JSON.stringify(pendingMemory)) } catch { /* depolama kapalıysa bellekteki değer yeter */ }
}
export function pendingOrderSymbol(): string | null {
  let value = pendingMemory
  if (!value) {
    try { const raw = window.sessionStorage.getItem(PENDING_KEY); value = raw ? JSON.parse(raw) as {symbol: string; until: number} : null } catch { value = null }
  }
  if (!value || typeof value.symbol !== 'string' || !(Number(value.until) > Date.now())) return null
  return value.symbol
}
export function clearOrderPending() {
  pendingMemory = null
  try { window.sessionStorage.removeItem(PENDING_KEY) } catch { /* yok say */ }
}

/** Manuel emir ekranından Canlı İşlem kokpitindeki bir pencereyi açar (acil durdurmayı kaldır / kurtarma kontrolü). Önce manuel panel kapanır. */
export const OPEN_LIVE_SHEET_EVENT = 'kt:open-live-sheet'
export function openLiveSheet(action: 'clear-emergency' | 'recovery') {
  window.dispatchEvent(new CustomEvent(MANUAL_CLOSE_EVENT))
  window.setTimeout(() => window.dispatchEvent(new CustomEvent(OPEN_LIVE_SHEET_EVENT, {detail: {action}})), 150)
}

/** Manuel işlem paneli (modal) açıldı: form, önceki emir gönderildiyse sıfırlanır. */
export const MANUAL_OPENED_EVENT = 'kt:manual-opened'

/** Kais Rehber'deki strateji seçici: Manuel işlem panelini istenen emir türünde (DCA gibi) açtırır. Emir göndermez. */
export const MANUAL_MODE_EVENT = 'kt:manual-mode'
export type ManualMode = 'single' | 'grid' | 'dca'
export function requestManualMode(mode: ManualMode, options: {delayMs?: number; lock?: boolean} = {}) {
  window.setTimeout(() => window.dispatchEvent(new CustomEvent(MANUAL_MODE_EVENT, {detail: {mode, lock: options.lock === true}})), options.delayMs ?? 120)
}

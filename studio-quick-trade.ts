// Analysis Studio hızlı AL / SAT: yalnız hesap ve yardımcı işlevler (ekran yok, ağ yok).
// Seviyeler, risk özeti ve hata metinleri burada; bu yüzden birim testle doğrulanabilir.

export type Side = 'LONG' | 'SHORT'
export type LevelSource = 'analysis' | 'mirror' | 'default'
export type Levels = {stop: number; tp1: number; tp2: number; tp3: number; source: LevelSource}
export type AnalysisLevels = {direction?: string | null; entry?: number | null; stop_loss?: number | null; tp1?: number | null; tp2?: number | null; tp3?: number | null} | null | undefined

/** Dil okuyucusu: ekran bileşeni yükleyince gerçek dili bildirir; testlerde Türkçe kalır. */
let readEnglish: () => boolean = () => false
export const setLanguageReader = (reader: () => boolean) => { readEnglish = reader }
export const tx = (tr: string, en: string): string => readEnglish() ? en : tr

export const LIVE_PHRASE = 'CANLI EMİR GÖNDER'
export const DEMO_PHRASE = 'DEMO'
export const LIVE_CLOSE_PHRASE = 'CANLI POZİSYONU KAPAT'
export const LIVE_REDUCE_PHRASE = 'KISMİ KAPAT'
export const DEMO_CLOSE_PHRASE = 'DEMO KAPAT'
export const DEMO_REDUCE_PHRASE = 'DEMO AZALT'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0
const round = (value: number) => Number(value.toPrecision(6))

/** Analizin yönüne göre mutlak seviyeler bu fiyat için tutarlı mı? (LONG: stop < fiyat < TP1 < TP2 < TP3) */
function coherent(side: Side, entry: number, stop: number, tp1: number, tp2: number, tp3: number): boolean {
  return side === 'LONG' ? stop < entry && entry < tp1 && tp1 < tp2 && tp2 < tp3 : stop > entry && entry > tp1 && tp1 > tp2 && tp2 > tp3
}

/** Öntanımlı mesafeler (fiyatın yüzdesi): stop %1,5 · hedefler %1,5 / %3 / %4,5. */
const DEFAULT_PCT = {stop: 1.5, tp1: 1.5, tp2: 3, tp3: 4.5}

/**
 * Seçilen yön için stop ve hedefleri hazırlar.
 * 1) Analiz aynı yönde ve seviyeleri bu fiyata uyuyorsa analizin kendi seviyeleri.
 * 2) Analiz ters yönde ya da fiyat seviyeyi geçtiyse: analizin mesafeleri bu yöne çevrilir (ayna).
 * 3) Analiz yoksa sabit yüzdeler.
 * Çıktı her zaman yön için tutarlıdır; tutarlı üretilemezse null döner (emir gönderilmez).
 */
export function levelsFor(side: Side, price: number | null | undefined, analysis: AnalysisLevels): Levels | null {
  if (!finite(price)) return null
  const entry = price
  const sign = side === 'LONG' ? 1 : -1
  const a = analysis
  const analysisSide: Side | null = a?.direction === 'LONG' || a?.direction === 'SHORT' ? a.direction : null
  if (a && finite(a.stop_loss) && finite(a.tp1) && finite(a.tp2) && finite(a.tp3)) {
    if (analysisSide === side && coherent(side, entry, a.stop_loss, a.tp1, a.tp2, a.tp3)) {
      return {stop: round(a.stop_loss), tp1: round(a.tp1), tp2: round(a.tp2), tp3: round(a.tp3), source: 'analysis'}
    }
    const base = finite(a.entry) ? a.entry : entry
    // Analizin kendi seviyeleri kendi içinde tutarlı değilse (ör. LONG'da hedef girişin altında) hiç kullanılmaz.
    const own: Side = analysisSide ?? (a.stop_loss < base ? 'LONG' : 'SHORT')
    if (!coherent(own, base, a.stop_loss, a.tp1, a.tp2, a.tp3)) return fallback(side, entry)
    const dStop = Math.abs(base - a.stop_loss), d1 = Math.abs(a.tp1 - base), d2 = Math.abs(a.tp2 - base), d3 = Math.abs(a.tp3 - base)
    if (dStop > 0 && d1 > 0 && d1 < d2 && d2 < d3) {
      // Mesafe, mevcut fiyatın %0,2'sinden küçük ya da %30'undan büyükse güvenilmez: sabit yüzdelere düşülür.
      const pct = (distance: number) => distance / entry * 100
      if (pct(dStop) >= .2 && pct(dStop) <= 30 && pct(d3) <= 60) {
        const next = {stop: round(entry - sign * dStop), tp1: round(entry + sign * d1), tp2: round(entry + sign * d2), tp3: round(entry + sign * d3)}
        if (next.stop > 0 && next.tp1 > 0 && coherent(side, entry, next.stop, next.tp1, next.tp2, next.tp3)) return {...next, source: 'mirror'}
      }
    }
  }
  return fallback(side, entry)
}

function fallback(side: Side, entry: number): Levels | null {
  const sign = side === 'LONG' ? 1 : -1
  const next = {
    stop: round(entry * (1 - sign * DEFAULT_PCT.stop / 100)), tp1: round(entry * (1 + sign * DEFAULT_PCT.tp1 / 100)),
    tp2: round(entry * (1 + sign * DEFAULT_PCT.tp2 / 100)), tp3: round(entry * (1 + sign * DEFAULT_PCT.tp3 / 100)),
  }
  return coherent(side, entry, next.stop, next.tp1, next.tp2, next.tp3) ? {...next, source: 'default'} : null
}

export type RiskSummary = {notional: number; lossAtStop: number; gainAtTp1: number; rewardRisk: number | null; stopPct: number; liquidationPct: number; stopBeforeLiquidation: boolean}

/** Teminat ve kaldıraca göre tahmini sonuç. Komisyon ve kayma dahil değildir; sunucu kendi risk kontrolünü ayrıca yapar. */
export function riskSummary(entry: number, levels: Levels, margin: number, leverage: number): RiskSummary | null {
  if (!finite(entry) || !finite(margin) || !finite(leverage)) return null
  const notional = margin * leverage
  const stopPct = Math.abs(entry - levels.stop) / entry * 100
  const lossAtStop = notional * stopPct / 100
  const gainAtTp1 = notional * Math.abs(levels.tp1 - entry) / entry
  // İzole pozisyonda likidasyon kabaca 100/kaldıraç % uzaktadır (bakım marjini hariç); yalnız karşılaştırma için kullanılır.
  const liquidationPct = 100 / leverage
  return {notional, lossAtStop, gainAtTp1, rewardRisk: lossAtStop > 0 ? gainAtTp1 / lossAtStop : null, stopPct, liquidationPct, stopBeforeLiquidation: stopPct < liquidationPct * .9}
}

export const MARGIN_CHOICES = [10, 25, 50, 100] as const
export const LEVERAGE_CHOICES = [1, 2, 3] as const

/** Kullanıcı sınırlarına uyan seçenekler; hiçbiri uymazsa en küçük geçerli değer tek seçenek olur. */
export function allowedMargins(max: number | null | undefined, min = 5): number[] {
  const cap = finite(max) ? Math.min(max, 300) : 100
  const list = MARGIN_CHOICES.filter(value => value >= min && value <= cap)
  return list.length ? [...list] : [Math.max(min, Math.floor(Math.min(cap, 100)))]
}
export function allowedLeverages(max: number | null | undefined): number[] {
  const cap = typeof max === 'number' && Number.isFinite(max) ? Math.floor(max) : 3
  const list = LEVERAGE_CHOICES.filter(value => value <= cap)
  return list.length ? [...list] : [1]
}
export const nearestChoice = (value: number, choices: readonly number[]): number =>
  choices.includes(value) ? value : choices.reduce((best, item) => Math.abs(item - value) < Math.abs(best - value) ? item : best, choices[0])

export const newIntentId = () => `studio-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

/** FastAPI hata gövdesinden okunur Türkçe metin çıkarır. */
export function errorText(payload: unknown, fallback: string): string {
  const detail = payload && typeof payload === 'object' ? (payload as {detail?: unknown; message?: unknown}).detail ?? (payload as {message?: unknown}).message : payload
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const parts = detail.map(item => item && typeof item === 'object' && typeof (item as {msg?: unknown}).msg === 'string' ? String((item as {msg: string}).msg) : typeof item === 'string' ? item : '').filter(Boolean)
    if (parts.length) return parts.join(' · ')
  }
  if (detail && typeof detail === 'object' && typeof (detail as {message?: unknown}).message === 'string') return String((detail as {message: string}).message)
  return fallback
}

export type CallResult<T> = {ok: true; data: T} | {ok: false; message: string; uncertain: boolean; status: number}

/**
 * Emir uçları için tek istek işlevi. `uncertain`: sunucudan yanıt gelmedi (zaman aşımı/ağ) ya da 5xx;
 * emir gitmiş olabilir, bu yüzden aynı emir otomatik tekrarlanmaz — önce pozisyonlar kontrol edilir.
 */
export async function postJson<T>(url: string, body: unknown, timeoutMs = 45000, fetcher: typeof fetch = fetch): Promise<CallResult<T>> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetcher(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: controller.signal})
    let payload: unknown = null
    try { payload = await response.json() } catch { payload = null }
    if (response.ok) return {ok: true, data: payload as T}
    const serverFault = response.status >= 500 && response.status !== 503
    const label = response.status === 423 ? tx('Kilitli: ', 'Locked: ') : response.status === 503 ? tx('Bakım / geçici kapalı: ', 'Maintenance / temporarily closed: ') : ''
    return {ok: false, status: response.status, uncertain: serverFault, message: `${label}${errorText(payload, tx(`İstek reddedildi (HTTP ${response.status}).`, `Request rejected (HTTP ${response.status}).`))}`}
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError'
    return {ok: false, status: 0, uncertain: true, message: aborted ? tx('Sunucu zamanında yanıt vermedi.', 'The server did not respond in time.') : tx('Sunucuya ulaşılamadı.', 'Could not reach the server.')}
  } finally {
    clearTimeout(timer)
  }
}

export type PositionLike = {symbol: string; quantity?: number; position_side?: string; direction?: string; entry_price?: number; mark_price?: number; unrealized_pnl?: number; leverage?: number | null}
export const isOpen = (position: PositionLike) => Math.abs(Number(position.quantity) || 0) > 0
export const sideOf = (position: PositionLike): Side => position.direction === 'SHORT' || position.position_side === 'SHORT' || Number(position.quantity) < 0 ? 'SHORT' : 'LONG'

/** Canlı: pozisyonun açık planı (kapat/azalt için plan numarası gerekir). */
const TERMINAL = ['CLOSED', 'CANCELLED', 'KAPANDI', 'İPTAL']
export type PlanLike = Record<string, unknown>
export function activePlan(plans: readonly PlanLike[] | undefined, symbol: string): PlanLike | undefined {
  return plans?.find(plan => String(plan.symbol || '').toUpperCase() === symbol.toUpperCase() && !TERMINAL.includes(String(plan.status || '').toUpperCase()))
}
/** Yalnız sahipliği doğrulanmış plan kapatılabilir; kısmi kapatma yalnız MANUEL planlarda. */
export function planRights(plan: PlanLike | undefined): {id: string; close: boolean; reduce: boolean} {
  const id = plan && typeof plan.id === 'string' ? plan.id : ''
  const confirmed = Boolean(plan) && plan?.provenance_state === 'CONFIRMED' && plan?.source !== 'READ_ONLY_EXTERNAL' && plan?.mutation_policy !== 'READ_ONLY_EXTERNAL'
  return {id, close: confirmed && id.length >= 6, reduce: confirmed && id.length >= 6 && plan?.source === 'MANUAL'}
}

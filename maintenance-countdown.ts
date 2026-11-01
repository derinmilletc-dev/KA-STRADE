// Zamanlı bakım geri sayımı: saf yardımcılar (tarayıcıdan bağımsız, node ile test edilir).

export type PublicMaintenance = {mode: 'NORMAL' | 'MAINTENANCE' | 'EMERGENCY'; scheduledAt: number | null; serverNow: number | null}

const MODES = new Set(['NORMAL', 'MAINTENANCE', 'EMERGENCY'])

function epochMs(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 && value < 4_102_444_800 ? Math.round(value * 1000) : null
}

/** Sunucunun herkese açık bakım yanıtını güvenle okur; bozuksa null (arayüz hiçbir şey göstermez). */
export function parsePublicMaintenance(payload: unknown): PublicMaintenance | null {
  if (!payload || typeof payload !== 'object') return null
  const raw = payload as Record<string, unknown>
  const mode = typeof raw.mode === 'string' && MODES.has(raw.mode) ? (raw.mode as PublicMaintenance['mode']) : null
  if (!mode) return null
  return {mode, scheduledAt: epochMs(raw.scheduled_at), serverNow: epochMs(raw.now)}
}

/** Sunucu saatine göre kalan süre (ms). Kullanıcının cihaz saati yanlış olsa da sayaç doğru kalır. */
export function remainingMs(scheduledAtMs: number, offsetMs: number, nowMs: number): number {
  return scheduledAtMs - (nowMs + offsetMs)
}

export function formatCountdown(ms: number, lang: 'tr' | 'en' = 'tr'): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  const clock = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
  return days > 0 ? `${days}${lang === 'en' ? 'd' : 'g'} ${clock}` : clock
}

export type CountdownPhase = 'far' | 'soon' | 'imminent' | 'due'

export function countdownPhase(ms: number): CountdownPhase {
  if (ms <= 0) return 'due'
  if (ms <= 60_000) return 'imminent'
  if (ms <= 10 * 60_000) return 'soon'
  return 'far'
}

/** Bir sonraki sunucu sorgusuna kadar bekleme (ms). Süre dolunca sık, uzakken seyrek sorar. */
export function nextPollDelay(remaining: number | null, failed: boolean): number {
  if (failed) return 30_000
  if (remaining === null) return 60_000
  if (remaining <= 0) return 2_000
  return Math.min(20_000, Math.max(1_500, remaining + 500))
}

export type CountdownParts = {days: number; hours: number; minutes: number; seconds: number}

/** Geri sayımı kutu kutu göstermek için gün / saat / dakika / saniye parçaları. */
export function countdownParts(ms: number): CountdownParts {
  const total = Math.max(0, Math.ceil(ms / 1000))
  return {days: Math.floor(total / 86400), hours: Math.floor((total % 86400) / 3600), minutes: Math.floor((total % 3600) / 60), seconds: total % 60}
}

/** Admin ekranında "başlangıç saati" göstermek için (cihazın yerel saati, sunucu farkıyla düzeltilmiş). */
export function startClock(scheduledAtMs: number, locale?: string): string {
  return new Date(scheduledAtMs).toLocaleTimeString(locale, {hour: '2-digit', minute: '2-digit'})
}

// Kais gözü için prosedürel bakış ve göz kırpma planlayıcısı (saf fonksiyonlar; React/DOM bağımsız, testte rastgelelik enjekte edilir).
// Amaç: sabit aralıklı sağ-sol döngüsü yerine çoğunlukla öne bakan, ara sıra küçük ve değişken bakış kaymaları ile düzensiz kırpmalar.

export type EyePresence = 'idle' | 'listening' | 'thinking' | 'speaking'
export type Rng = () => number
export type GazePoint = {x: number; y: number}
export type GazePlan = {target: GazePoint; duration: number; hold: number; kind: 'return' | 'glance' | 'drift'}
export type BlinkPlan = {delay: number; double: boolean; gap: number}

/** Otonom bakışın çıkabileceği en büyük normalize yarıçap (işaretçi takibi 1'e kadar gidebilir). */
export const GAZE_LIMIT = 0.55
/** Bakış kayması süresi sınırları (ms): mesafeye göre 180–500. */
export const GAZE_MIN_MS = 180
export const GAZE_MAX_MS = 500
/** Kırpma süresi (ms): kısa ve yumuşak. */
export const BLINK_MS = 150
export const BLINK_DELAY_MIN_MS = 1800
export const BLINK_DELAY_MAX_MS = 12000

type Profile = {
  /** Merkezdeyken kısa bir bakış kayması yapma olasılığı (aksi hâlde neredeyse durağan mikro kayma). */
  glance: number
  /** Öne bakışın sürdüğü aralık (ms). */
  forward: [number, number]
  /** Kaymadan sonra o noktada kalma aralığı (ms). */
  glanceHold: [number, number]
  /** Nadiren daha büyük kayma olasılığı. */
  large: number
}

// Dinlerken sakin ve dikkatli; düşünürken biraz daha sık ama kısa; konuşurken çoğunlukla öne.
export const PROFILES: Record<EyePresence, Profile> = {
  idle: {glance: 0.45, forward: [2200, 8500], glanceHold: [450, 1500], large: 0.12},
  listening: {glance: 0.28, forward: [3000, 9500], glanceHold: [350, 1000], large: 0.05},
  thinking: {glance: 0.55, forward: [1500, 5000], glanceHold: [400, 1100], large: 0.1},
  speaking: {glance: 0.35, forward: [2000, 7000], glanceHold: [350, 1000], large: 0.06},
}

const between = (rng: Rng, [min, max]: [number, number]) => min + rng() * (max - min)
const magnitude = ({x, y}: GazePoint) => Math.hypot(x, y)

/** Sınırlı nokta: yarıçap GAZE_LIMIT'i aşamaz; dikey hareket yatayın yarısıyla sınırlıdır. */
export function boundGaze({x, y}: GazePoint): GazePoint {
  const safeX = Number.isFinite(x) ? x : 0, safeY = Number.isFinite(y) ? Math.max(-GAZE_LIMIT / 2, Math.min(GAZE_LIMIT / 2, y)) : 0
  const length = Math.hypot(safeX, safeY)
  return length > GAZE_LIMIT ? {x: safeX / length * GAZE_LIMIT, y: safeY / length * GAZE_LIMIT} : {x: safeX, y: safeY}
}

/** Mesafeye bağlı, biraz da rastgele süre (ms): kısa kaymalar hızlı, uzunlar biraz daha yavaş. */
export function gazeDuration(distance: number, rng: Rng): number {
  const base = GAZE_MIN_MS + Math.min(1, distance / GAZE_LIMIT) * (GAZE_MAX_MS - GAZE_MIN_MS) * 0.8
  return Math.round(Math.min(GAZE_MAX_MS, Math.max(GAZE_MIN_MS, base + (rng() - 0.5) * 80)))
}

/** Bir sonraki bakış: merkezden uzaktaysa doğal dönüş; merkezdeyse ya kısa bir kayma ya da çok küçük bir mikro kayma. */
export function planGaze(presence: EyePresence, current: GazePoint, rng: Rng): GazePlan {
  const profile = PROFILES[presence]
  if (magnitude(current) > 0.1) {
    // Merkeze tam değil, yakına döner (her seferinde aynı noktaya oturmaz).
    const target = boundGaze({x: (rng() - 0.5) * 0.06, y: (rng() - 0.5) * 0.03})
    return {target, duration: gazeDuration(magnitude(current), rng), hold: Math.round(between(rng, profile.forward)), kind: 'return'}
  }
  if (rng() < profile.glance) {
    const large = rng() < profile.large
    const radius = large ? 0.32 + rng() * (GAZE_LIMIT - 0.32) : 0.12 + rng() * 0.16
    // Yön: çoğunlukla yatay, biraz dikey; düşünürken yukarı bakma klişesine özel ağırlık verilmez.
    const angle = rng() * Math.PI * 2
    const target = boundGaze({x: current.x + Math.cos(angle) * radius, y: current.y + Math.sin(angle) * radius * 0.45})
    return {target, duration: gazeDuration(magnitude({x: target.x - current.x, y: target.y - current.y}), rng), hold: Math.round(between(rng, profile.glanceHold)), kind: 'glance'}
  }
  const target = boundGaze({x: current.x * 0.5 + (rng() - 0.5) * 0.05, y: current.y * 0.5 + (rng() - 0.5) * 0.025})
  return {target, duration: Math.round(between(rng, [260, 480])), hold: Math.round(between(rng, profile.forward)), kind: 'drift'}
}

/** Düzensiz kırpma: çoğunlukla 2,5–6 sn, bazen hızlı ardışık (çift) kırpma, bazen uzun ara. */
export function planBlink(rng: Rng): BlinkPlan {
  const roll = rng()
  const delay = roll < 0.12 ? between(rng, [7500, BLINK_DELAY_MAX_MS]) : roll < 0.3 ? between(rng, [BLINK_DELAY_MIN_MS, 2800]) : between(rng, [2800, 6200])
  const double = rng() < 0.16
  return {delay: Math.round(delay), double, gap: Math.round(between(rng, [180, 320]))}
}

/** Yumuşak hızlanma ve yavaşlama. */
export const easeInOut = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export function lerpGaze(from: GazePoint, to: GazePoint, t: number): GazePoint {
  const k = easeInOut(t)
  return {x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k}
}

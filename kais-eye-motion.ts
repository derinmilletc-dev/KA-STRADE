// Kais gözü için prosedürel bakış ve göz kırpma planlayıcısı (saf fonksiyonlar; React/DOM bağımsız, testte rastgelelik enjekte edilir).
// Amaç: az ama yerinde hareket. Göz çoğunlukla öne sabit bakar; dakikada yalnız birkaç küçük, yavaş bakış ve 6–11 sn'de bir düzensiz kırpma.
// Rastgele mikro kıpırdama yoktur: hareket etmeyen turlar gerçekten durağandır.

export type EyePresence = 'idle' | 'listening' | 'thinking' | 'speaking'
export type Rng = () => number
export type GazePoint = {x: number; y: number}
export type GazePlan = {target: GazePoint; duration: number; hold: number; kind: 'return' | 'glance' | 'stay'}
export type BlinkPlan = {delay: number; double: boolean; gap: number}

/** Otonom bakışın çıkabileceği en büyük normalize yarıçap (işaretçi takibi 1'e kadar gidebilir). */
export const GAZE_LIMIT = 0.3
/** Bakış kayması süresi sınırları (ms): sakin, yumuşak geçiş (mesafeye göre 350–600). */
export const GAZE_MIN_MS = 350
export const GAZE_MAX_MS = 600
/** Kırpma süresi (ms): kısa ve yumuşak. */
export const BLINK_MS = 170
/** Kırpma aralığı: 6–11 sn, düzensiz. */
export const BLINK_DELAY_MIN_MS = 6000
export const BLINK_DELAY_MAX_MS = 11000

type Profile = {
  /** Öne bakış turunun sonunda kısa bir bakış yapma olasılığı (aksi hâlde göz yerinde kalır). */
  glance: number
  /** Öne sabit bakışın sürdüğü aralık (ms). */
  forward: [number, number]
  /** Kaymadan sonra o noktada kalma aralığı (ms). */
  glanceHold: [number, number]
}

// Hedef: boşta dakikada ~2–4, dinlerken ~2, düşünürken ~3, konuşurken ~1 küçük bakış. Konuşurken göz öne sabittir.
export const PROFILES: Record<EyePresence, Profile> = {
  idle: {glance: 0.6, forward: [8000, 16000], glanceHold: [700, 1400]},
  listening: {glance: 0.45, forward: [9000, 16000], glanceHold: [600, 1100]},
  thinking: {glance: 0.55, forward: [6000, 12000], glanceHold: [700, 1300]},
  speaking: {glance: 0.22, forward: [10000, 18000], glanceHold: [500, 900]},
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
  const base = GAZE_MIN_MS + Math.min(1, distance / 0.2) * (GAZE_MAX_MS - GAZE_MIN_MS) * 0.75
  return Math.round(Math.min(GAZE_MAX_MS, Math.max(GAZE_MIN_MS, base + (rng() - 0.5) * 80)))
}

/** Küçük bir bakış hedefi: 0,08–0,18 yarıçap, çoğunlukla yatay (dikey bileşen basık ama hedef her zaman en az 0,08 uzakta). */
function glanceTarget(rng: Rng): GazePoint {
  const radius = 0.08 + rng() * 0.1
  const angle = rng() * Math.PI * 2
  const point = {x: Math.cos(angle) * radius, y: Math.sin(angle) * radius * 0.4}
  const length = Math.hypot(point.x, point.y)
  return boundGaze({x: point.x / length * radius, y: point.y / length * radius})
}

/**
 * Bir sonraki bakış: merkezden uzaktaysa sakin bir dönüş; merkezdeyse ya kısa bir bakış ya da tamamen yerinde kalma.
 * `glance: true` durum değişimi gibi anlamlı anlarda kısa bir bakışı zorlar.
 */
export function planGaze(presence: EyePresence, current: GazePoint, rng: Rng, options: {glance?: boolean} = {}): GazePlan {
  const profile = PROFILES[presence]
  // Merkezde değilse (bir bakışın ya da kesilen bir geçişin ardından) her zaman sakin dönüş: göz kenarda takılı kalmaz.
  if (magnitude(current) > 0.001) {
    return {target: {x: 0, y: 0}, duration: gazeDuration(magnitude(current), rng), hold: Math.round(between(rng, profile.forward)), kind: 'return'}
  }
  if (options.glance || rng() < profile.glance) {
    const target = glanceTarget(rng)
    return {target, duration: gazeDuration(magnitude(target), rng), hold: Math.round(between(rng, profile.glanceHold)), kind: 'glance'}
  }
  // Yerinde kal: hiçbir hareket yok (mikro kıpırdama göz yorucu ve "robotik" görünüyordu).
  return {target: current, duration: 0, hold: Math.round(between(rng, profile.forward)), kind: 'stay'}
}

/** Düzensiz kırpma: 6–11 sn aralık (eşit olmayan dağılım), çok nadiren hızlı çift kırpma. */
export function planBlink(rng: Rng): BlinkPlan {
  // İki rastgele değerin ortalaması: uçlardan çok ortalara yakın ama yine de düzensiz aralıklar.
  const spread = (rng() + rng()) / 2
  const delay = BLINK_DELAY_MIN_MS + spread * (BLINK_DELAY_MAX_MS - BLINK_DELAY_MIN_MS)
  return {delay: Math.round(delay), double: rng() < 0.06, gap: Math.round(between(rng, [200, 320]))}
}

/** Yumuşak hızlanma ve yavaşlama. */
export const easeInOut = (t: number) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export function lerpGaze(from: GazePoint, to: GazePoint, t: number): GazePoint {
  const k = easeInOut(t)
  return {x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k}
}

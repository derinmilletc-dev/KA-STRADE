// Kais ajanı — aşama 3, adım 4: plan hafızası. YALNIZCA bu cihazda (localStorage) tutulur; sunucuya gitmez.
// Hatırlanan: güven eşiği, işlem sayısı, marjin, kaldıraç, net kâr hedefi. ASLA hatırlanmayan: Demo/Canlı seçimi, seviyeler, emir.
// Hafızadan doldurulan her değer kullanıcıya söylenir; "unut" hepsini siler.
import type {CommandPlan, PlanStep} from './kais-plan-intents'

export type PlanMemory = {min_confidence?: number; count?: number; marginUsdt?: number; leverage?: number; targetUsdt?: number}
export const MEMORY_KEY = 'kais-plan-memory'
type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const num = (value: unknown, min: number, max: number) => (typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max ? value : undefined)

/** Bozuk/elle değiştirilmiş kayıtlar sınırların dışına çıkamaz; tanınmayan alan atılır. */
export function sanitizeMemory(value: unknown): PlanMemory {
  if (!value || typeof value !== 'object') return {}
  const row = value as Record<string, unknown>
  const clean: PlanMemory = {}
  const confidence = num(row.min_confidence, 50, 100), count = num(row.count, 1, 5), margin = num(row.marginUsdt, 5, 100), leverage = num(row.leverage, 1, 125), target = num(row.targetUsdt, 0.000001, 1000)
  if (confidence !== undefined) clean.min_confidence = confidence
  if (count !== undefined && Number.isInteger(count)) clean.count = count
  if (margin !== undefined) clean.marginUsdt = margin
  if (leverage !== undefined && Number.isInteger(leverage)) clean.leverage = leverage
  if (target !== undefined) clean.targetUsdt = target
  return clean
}

export function loadMemory(store: Store = localStorage): PlanMemory {
  try { return sanitizeMemory(JSON.parse(store.getItem(MEMORY_KEY) || '{}')) } catch { return {} }
}
export function saveMemory(memory: PlanMemory, store: Store = localStorage): boolean {
  try { store.setItem(MEMORY_KEY, JSON.stringify(sanitizeMemory(memory))); return true } catch { return false }
}
export function clearMemory(store: Store = localStorage): void {
  try { store.removeItem(MEMORY_KEY) } catch { /* yok say */ }
}

/** Plandan hatırlanacak değerleri çıkarır (yalnız söylenmiş olanlar). */
export function memoryFromPlan(plan: CommandPlan): PlanMemory {
  const scan = plan.steps.find(step => step.kind === 'scan')?.params ?? {}
  const target = plan.steps.find(step => step.kind === 'target_profit')?.params.usdt
  return sanitizeMemory({min_confidence: scan.min_confidence, count: scan.count, marginUsdt: plan.marginUsdt, leverage: plan.leverage, targetUsdt: target})
}

/** Eksik alanları hafızadan doldurur; söylenmiş değerlerin üstüne yazmaz, Demo/Canlı'ya dokunmaz. used: kullanıcıya söylenecek açıklamalar. */
export function applyMemory(plan: CommandPlan, memory: PlanMemory, language: 'tr' | 'en' = 'tr'): {plan: CommandPlan; used: string[]} {
  const used: string[] = []
  const steps: PlanStep[] = plan.steps.map(step => ({...step, params: {...step.params}}))
  const missing = [...plan.missing]
  const drop = (key: CommandPlan['missing'][number]) => { const index = missing.indexOf(key); if (index >= 0) missing.splice(index, 1) }
  const scan = steps.find(step => step.kind === 'scan')
  if (scan && scan.params.min_confidence === undefined && memory.min_confidence !== undefined && !plan.invalid.includes('confidence')) {
    scan.params.min_confidence = memory.min_confidence; drop('confidence'); used.push(language === 'en' ? `confidence ${memory.min_confidence}%+` : `güven %${memory.min_confidence}+`)
  }
  if (scan && scan.params.count === undefined && memory.count !== undefined && !plan.invalid.includes('count')) {
    scan.params.count = memory.count; drop('count'); used.push(language === 'en' ? `${memory.count} trades` : `${memory.count} işlem`)
  }
  const target = steps.find(step => step.kind === 'target_profit')
  if (target && target.params.usdt === undefined && memory.targetUsdt !== undefined && !plan.invalid.includes('target_profit')) {
    target.params.usdt = memory.targetUsdt; drop('target_profit'); used.push(language === 'en' ? `${memory.targetUsdt} USD profit` : `${memory.targetUsdt} dolar kâr`)
  }
  let marginUsdt = plan.marginUsdt, leverage = plan.leverage
  const hasTarget = Boolean(target?.params.usdt)
  if (marginUsdt === null && memory.marginUsdt !== undefined && !plan.invalid.includes('margin') && (hasTarget || missing.includes('margin'))) {
    marginUsdt = memory.marginUsdt; drop('margin'); used.push(language === 'en' ? `${memory.marginUsdt} USD margin` : `${memory.marginUsdt} dolar marjin`)
  }
  if (leverage === null && memory.leverage !== undefined && !plan.invalid.includes('leverage') && (hasTarget || missing.includes('leverage'))) {
    leverage = memory.leverage; drop('leverage'); used.push(language === 'en' ? `${memory.leverage}x leverage` : `${memory.leverage}x kaldıraç`)
  }
  return {plan: {...plan, steps, missing, marginUsdt, leverage}, used}
}

const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '')

/** "bu planı hatırla" / "plan ayarlarımı unut". Başka cümleler (ör. "kuralları unut") eşleşmez: onlar modele gider ve reddedilir. */
export function parseMemoryCommand(message: string): 'remember' | 'forget' | null {
  const text = fold(message).replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
  if (text.split(' ').length > 10) return null
  if (/\b(plan\w*|ayarlar\w*)\b.*\b(unut|sil)\w*\b|\b(unut|sil)\w*\b.*\bplan (hafiza|ayar)\w*/.test(text) && !/\b(kural|prompt|talimat|sistem)\w*/.test(text)) return 'forget'
  if (/\b(bu |son )?(plan\w*|ayarlar\w*)\b.*\bhatirla\w*\b|\bhatirla\b.*\b(plan\w*|ayarlar\w*)\b/.test(text) && !/\b(kural|prompt|talimat|sistem)\w*/.test(text)) return 'remember'
  return null
}

/** Aynı planın kısa sürede iki kez çalışmasını engelleyen anahtar. */
export function planKey(plan: CommandPlan): string {
  return JSON.stringify([plan.account, plan.steps.map(step => [step.kind, step.params]), plan.marginUsdt, plan.leverage])
}
export function isDuplicate(last: {key: string; at: number} | null, key: string, now: number, windowMs = 30_000): boolean {
  return last !== null && last.key === key && now - last.at < windowMs
}

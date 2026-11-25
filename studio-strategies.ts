// Kullanıcının kendi stratejileri: tarayıcıda saklanır (yalnız kural ve ayarlar; emir/anahtar içermez). Saf mantık + ince depolama.
import {cleanStrategy, type Strategy} from './studio-backtest'
import {sanitizeRules} from './studio-rules'

export type SavedStrategy = {id: string; name: string; strategy: Strategy}
export const MAX_SAVED = 8
const KEY = 'kaistrade.studio.strategies.v1'

export function sanitizeStrategy(value: unknown): Strategy {
  const item = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return cleanStrategy({...item, entry: sanitizeRules(item.entry), confirm: sanitizeRules(item.confirm), exit: sanitizeRules(item.exit)} as Partial<Strategy>)
}
export function sanitizeSaved(raw: unknown): SavedStrategy[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const out: SavedStrategy[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const {id, name, strategy} = item as Record<string, unknown>
    if (typeof id !== 'string' || !id || seen.has(id) || typeof name !== 'string') continue
    const clean = sanitizeStrategy(strategy)
    if (!clean.entry.length) continue
    seen.add(id)
    out.push({id: id.slice(0, 40), name: name.trim().slice(0, 30) || 'Strateji', strategy: clean})
    if (out.length >= MAX_SAVED) break
  }
  return out
}
/** Aynı adla kaydedilen strateji güncellenir; liste dolduysa en eski düşer. */
export function upsertSaved(list: readonly SavedStrategy[], name: string, strategy: Strategy, newId: () => string): SavedStrategy[] {
  const clean = name.trim().slice(0, 30) || 'Strateji'
  const entry: SavedStrategy = {id: list.find(item => item.name === clean)?.id ?? newId(), name: clean, strategy: sanitizeStrategy(strategy)}
  const rest = list.filter(item => item.id !== entry.id)
  return [entry, ...rest].slice(0, MAX_SAVED)
}
export const removeSaved = (list: readonly SavedStrategy[], id: string) => list.filter(item => item.id !== id)

export function readSaved(): SavedStrategy[] {
  try { return sanitizeSaved(JSON.parse(window.localStorage.getItem(KEY) ?? '[]')) } catch { return [] }
}
export function writeSaved(list: readonly SavedStrategy[]) {
  try { window.localStorage.setItem(KEY, JSON.stringify(list)) } catch { /* depolama kapalı: oturumla sınırlı kalır */ }
}

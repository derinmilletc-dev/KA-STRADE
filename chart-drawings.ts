// Kullanıcının grafiğe çizdiği çizgiler. Yalnız görüntü; bu tarayıcıda coin bazında saklanır, emir göndermez.
export type DrawMode = 'none' | 'hline' | 'trend' | 'erase'
export type ChartPoint = {time: number; price: number}
export type Drawing = {id: string; type: 'hline'; price: number} | {id: string; type: 'trend'; a: ChartPoint; b: ChartPoint}

const KEY = 'kaistrade-chart-drawings'
const LIMIT = 50
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const point = (value: unknown): value is ChartPoint =>
  typeof value === 'object' && value !== null && finite((value as ChartPoint).time) && finite((value as ChartPoint).price)

function readAll(): Record<string, unknown> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return typeof saved === 'object' && saved !== null && !Array.isArray(saved) ? saved as Record<string, unknown> : {}
  } catch { return {} }
}

export function readDrawings(symbol: string): Drawing[] {
  const list = readAll()[symbol]
  if (!Array.isArray(list)) return []
  return list.filter((item): item is Drawing => {
    if (typeof item !== 'object' || item === null || typeof (item as Drawing).id !== 'string') return false
    const drawing = item as Drawing
    return drawing.type === 'hline' ? finite(drawing.price) : drawing.type === 'trend' && point(drawing.a) && point(drawing.b)
  }).slice(-LIMIT)
}

export function saveDrawings(symbol: string, drawings: Drawing[]) {
  try {
    const all = readAll()
    if (drawings.length) all[symbol] = drawings.slice(-LIMIT); else delete all[symbol]
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch { /* tarayıcı kaydı kapalı olabilir; çizimler bu oturumda kalır */ }
}

export const drawingId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`

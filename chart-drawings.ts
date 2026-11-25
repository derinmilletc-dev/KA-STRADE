// Kullanıcının grafiğe çizdiği şekiller. Yalnız görüntü; emir göndermez. Tarayıcıda coin bazında saklanır ve giriş yapılmışsa
// sunucuyla eşitlenir (telefon ↔ bilgisayar). Eski kayıtlar (yatay çizgi, trend) aynen okunur.
export type DrawMode = 'none' | 'hline' | 'trend' | 'erase' | 'alarm' | 'fib' | 'rect' | 'channel' | 'text' | 'measure'
export type ChartPoint = {time: number; price: number}
// Alarm: fiyat çizgiye "above" (yukarıdan) ya da "below" (aşağıdan) ulaşınca bir kez uyarır.
export type Alarm = {side: 'above' | 'below'; fired?: boolean; serverId?: number}
export type Drawing =
  | {id: string; type: 'hline'; price: number; alarm?: Alarm}
  | {id: string; type: 'trend'; a: ChartPoint; b: ChartPoint}
  | {id: string; type: 'fib'; a: ChartPoint; b: ChartPoint}
  | {id: string; type: 'rect'; a: ChartPoint; b: ChartPoint}
  | {id: string; type: 'channel'; a: ChartPoint; b: ChartPoint; c: ChartPoint}
  | {id: string; type: 'measure'; a: ChartPoint; b: ChartPoint}
  | {id: string; type: 'text'; p: ChartPoint; text: string}

const KEY = 'kaistrade-chart-drawings'
export const DRAWING_LIMIT = 50
export const TEXT_LIMIT = 80
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const point = (value: unknown): value is ChartPoint =>
  typeof value === 'object' && value !== null && finite((value as ChartPoint).time) && finite((value as ChartPoint).price)

function validDrawing(item: unknown): item is Drawing {
  if (typeof item !== 'object' || item === null || typeof (item as Drawing).id !== 'string' || (item as Drawing).id.length > 40) return false
  const drawing = item as Drawing
  switch (drawing.type) {
    case 'hline': {
      const alarm = drawing.alarm
      if (alarm !== undefined && !(typeof alarm === 'object' && alarm !== null && (alarm.side === 'above' || alarm.side === 'below'))) return false
      return finite(drawing.price)
    }
    case 'trend': case 'fib': case 'rect': case 'measure': return point(drawing.a) && point(drawing.b)
    case 'channel': return point(drawing.a) && point(drawing.b) && point(drawing.c)
    case 'text': return point(drawing.p) && typeof drawing.text === 'string' && drawing.text.trim().length > 0
    default: return false
  }
}

/** Dışarıdan (yerel kayıt ya da sunucu) gelen listeyi doğrular: geçersizleri atar, metni kırpar, sınırı uygular. */
export function sanitizeDrawings(list: unknown): Drawing[] {
  if (!Array.isArray(list)) return []
  const seen = new Set<string>()
  const out: Drawing[] = []
  for (const item of list) {
    if (!validDrawing(item) || seen.has(item.id)) continue
    seen.add(item.id)
    out.push(item.type === 'text' ? {...item, text: item.text.trim().slice(0, TEXT_LIMIT)} : item)
  }
  return out.slice(-DRAWING_LIMIT)
}

type Stored = Record<string, unknown>
function readAll(): Stored {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return typeof saved === 'object' && saved !== null && !Array.isArray(saved) ? saved as Stored : {}
  } catch { return {} }
}

export function readDrawings(symbol: string): Drawing[] {
  return sanitizeDrawings(readAll()[symbol])
}

export function saveDrawings(symbol: string, drawings: Drawing[]) {
  try {
    const all = readAll()
    if (drawings.length) all[symbol] = drawings.slice(-DRAWING_LIMIT); else delete all[symbol]
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch { /* tarayıcı kaydı kapalı olabilir; çizimler bu oturumda kalır */ }
}

export const drawingId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`

/** Çizimi tamamlamak için gereken tıklama sayısı (metin: 1 + yazı kutusu). */
export const CLICKS_NEEDED: Partial<Record<DrawMode, number>> = {hline: 1, alarm: 1, text: 1, trend: 2, fib: 2, rect: 2, measure: 2, channel: 3}

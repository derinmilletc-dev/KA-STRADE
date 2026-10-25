// Mevcut açık pozisyonların STOPA KADAR kalan olası zararı. Saf fonksiyon.
// Stop bilgisi okunamayan pozisyon varsa toplam "bilinmiyor" (null) olur; grid planı bu durumda onaylanmaz.

type Row = Record<string, unknown>

export type PositionRisk = {symbol: string; risk: number | null; reason: string}
export type OpenRisk = {total: number | null; items: PositionRisk[]}

const num = (value: unknown) => {
  const n = typeof value === 'string' || typeof value === 'number' ? Number(value) : NaN
  return Number.isFinite(n) ? n : NaN
}

export function openPositionsRiskToStop(positions: Row[], orders: Row[]): OpenRisk {
  const items: PositionRisk[] = []
  for (const position of positions) {
    const symbol = String(position.symbol || '')
    const qty = Math.abs(num(position.quantity))
    const mark = num(position.mark_price)
    const direction = String(position.direction || '').toUpperCase() || (num(position.quantity) < 0 ? 'SHORT' : 'LONG')
    if (!symbol || !(qty > 0)) continue                      // boş pozisyon satırı
    if (!(mark > 0) || (direction !== 'LONG' && direction !== 'SHORT')) { items.push({symbol, risk: null, reason: 'Fiyat/yön okunamadı'}); continue }
    const closingSide = direction === 'LONG' ? 'SELL' : 'BUY'
    const stops = orders
      .filter(order => String(order.symbol || '') === symbol && String(order.side || '').toUpperCase() === closingSide && /STOP/i.test(String(order.type || '')) && !/TAKE_PROFIT/i.test(String(order.type || '')))
      .map(order => num(order.trigger_price ?? order.stop_price ?? order.stopPrice ?? order.triggerPrice))
      .filter(value => value > 0)
    if (!stops.length) { items.push({symbol, risk: null, reason: 'Koruyucu stop bulunamadı'}); continue }
    const stop = direction === 'LONG' ? Math.min(...stops) : Math.max(...stops)   // en uzak stop = en kötü durum
    const distance = direction === 'LONG' ? mark - stop : stop - mark
    items.push({symbol, risk: qty * Math.max(0, distance), reason: ''})
  }
  const unknown = items.some(item => item.risk === null)
  return {total: unknown ? null : items.reduce((sum, item) => sum + (item.risk ?? 0), 0), items}
}

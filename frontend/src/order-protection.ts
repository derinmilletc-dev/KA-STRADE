// Emir sonrası koruma kontrolü: hesap verisinde pozisyon, stop ve hedef emirleri var mı? (yalnız okuma, saf hesap)
type Row = Record<string, unknown>
export type Protection = {position: boolean; stop: boolean; targets: number}

const num = (value: unknown) => { const result = Number(value); return Number.isFinite(result) ? result : 0 }

/** `symbol` için açık pozisyon, kapatan yönde stop ve hedef (take profit / reduce-only limit) sayısı. */
export function checkProtection(symbol: string, direction: string, positions: Row[], orders: Row[]): Protection {
  const position = positions.some(item => String(item.symbol || '') === symbol && Math.abs(num(item.quantity)) > 0)
  const closingSide = String(direction).toUpperCase() === 'SHORT' ? 'BUY' : 'SELL'
  const mine = orders.filter(order => String(order.symbol || '') === symbol && String(order.side || '').toUpperCase() === closingSide)
  const typeOf = (order: Row) => String(order.type ?? order.orderType ?? order.origType ?? '')
  const stop = mine.some(order => /STOP/i.test(typeOf(order)) && !/TAKE_PROFIT/i.test(typeOf(order)))
  const targets = mine.filter(order => /TAKE_PROFIT/i.test(typeOf(order)) || (/^LIMIT$/i.test(typeOf(order)) && (order.reduce_only === true || order.reduceOnly === true || order.close_position === true))).length
  return {position, stop, targets}
}

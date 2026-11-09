// Kais ajanı — aşama 3, adım 3: gönderilen Demo emri için hedef ve stop fiyat alarmları. Mevcut fiyat alarmı altyapısını kullanır:
// yalnızca site açıkken çalışır, emir vermez, kapatmaz. İkisinden biri tetiklenince eşi silinir (aynı işlem için iki kez haber verilmez).
import type {PriceAlert} from './kais-alert-intents'

export type FollowInput = {symbol: string; direction: 'LONG' | 'SHORT'; stop_loss: number; tp1: number}

export function followAlerts(order: FollowInput, pairId: string): PriceAlert[] | null {
  const levels = [order.stop_loss, order.tp1]
  if (!/^[A-Z]{2,12}USDT$/.test(order.symbol) || !levels.every(value => Number.isFinite(value) && value > 0)) return null
  const long = order.direction === 'LONG'
  // Tutarsız seviye (hedef ve stop aynı tarafta) → takip kurulmaz, uydurulmaz.
  if (long ? !(order.stop_loss < order.tp1) : !(order.tp1 < order.stop_loss)) return null
  return [
    {id: `${pairId}-target`, symbol: order.symbol, level: order.tp1, side: long ? 'above' : 'below', tag: 'target', pairId},
    {id: `${pairId}-stop`, symbol: order.symbol, level: order.stop_loss, side: long ? 'below' : 'above', tag: 'stop', pairId},
  ]
}

/** Tetiklenen alarmların eşlerini de silinecekler listesine ekler. */
export function withPairs(firedIds: string[], rows: PriceAlert[]): string[] {
  const pairs = new Set(rows.filter(row => firedIds.includes(row.id) && row.pairId).map(row => row.pairId))
  return [...new Set([...firedIds, ...rows.filter(row => row.pairId && pairs.has(row.pairId)).map(row => row.id)])]
}

export function followText(row: PriceAlert, price: number | null, language: 'tr' | 'en'): string {
  const coin = row.symbol.replace('USDT', '')
  const shown = price !== null ? Number(price.toPrecision(8)) : row.level
  if (row.tag === 'target') return language === 'en' ? `${coin} reached your target ${row.level} (price ${shown}). Check your Demo position.` : `${coin} hedef fiyatına ulaştı: ${row.level} (fiyat ${shown}). Demo pozisyonunu kontrol et.`
  if (row.tag === 'stop') return language === 'en' ? `${coin} touched your stop ${row.level} (price ${shown}). The Demo position may have been stopped; check it.` : `${coin} stop seviyene değdi: ${row.level} (fiyat ${shown}). Demo pozisyonu stopla kapanmış olabilir; kontrol et.`
  return language === 'en' ? `${row.symbol} reached ${shown}. Your alert at ${row.level} fired.` : `${row.symbol} ${shown} oldu. ${row.level} alarmın tetiklendi.`
}

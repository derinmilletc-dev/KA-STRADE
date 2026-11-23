// "Son işlemi tekrarla": stop/hedef MUTLAK fiyat değil, girişe göre YÜZDE olarak saklanır; tekrar açılırken güncel fiyata uygulanır.
// Ağ yok, emir yok (yalnız bilet doldurur; açmak için yine kontrol + onay gerekir).

export type RepeatRecord = {productId: string; side: 'BUY' | 'SELL'; marginUsd: string; contracts: string; leverage: string; stopPct: number; tpPct: number | null}

const pos = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null }

/** Açılan işlemin planından kayıt üretir; stop yönü yanlışsa/okunamıyorsa null. */
export function makeRepeat(plan: {product_id: unknown; side: unknown; margin_usd: unknown; contracts: unknown; leverage: unknown; entry_reference: unknown; stop_price: unknown; take_profit_price?: unknown}): RepeatRecord | null {
  const side = plan.side === 'BUY' || plan.side === 'SELL' ? plan.side : null
  const entry = pos(plan.entry_reference), stop = pos(plan.stop_price), tp = pos(plan.take_profit_price)
  const margin = pos(plan.margin_usd), productId = String(plan.product_id ?? '')
  if (!side || !entry || !stop || !margin || !productId) return null
  const sign = side === 'BUY' ? 1 : -1
  const stopPct = (entry - stop) * sign / entry * 100
  if (!(stopPct > 0.01 && stopPct < 90)) return null
  const tpPct = tp ? (tp - entry) * sign / entry * 100 : null
  return {productId, side, marginUsd: String(plan.margin_usd), contracts: String(plan.contracts ?? ''), leverage: String(Math.max(1, Math.floor(Number(plan.leverage) || 1))),
    stopPct: Math.round(stopPct * 1000) / 1000, tpPct: tpPct !== null && tpPct > 0.01 ? Math.round(tpPct * 1000) / 1000 : null}
}

const fmt = (value: number) => String(Number(value.toPrecision(8)))

/** Güncel fiyata göre stop ve hedef fiyatları. Fiyat geçersizse null. */
export function repeatLevels(record: RepeatRecord, price: number): {stop: string; takeProfit: string} | null {
  if (!(price > 0)) return null
  const sign = record.side === 'BUY' ? 1 : -1
  return {stop: fmt(price * (1 - sign * record.stopPct / 100)), takeProfit: record.tpPct === null ? '' : fmt(price * (1 + sign * record.tpPct / 100))}
}

/** Depodan okunan ham değeri doğrular (bozuk/eski kayıt → null). */
export function parseRepeat(raw: unknown): RepeatRecord | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const side = r.side === 'BUY' || r.side === 'SELL' ? r.side : null
  if (!side || typeof r.productId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$/.test(r.productId)) return null
  if (!pos(r.marginUsd) || !pos(r.stopPct) || typeof r.leverage !== 'string' || typeof r.contracts !== 'string') return null
  const tp = r.tpPct === null || r.tpPct === undefined ? null : pos(r.tpPct)
  return {productId: r.productId, side, marginUsd: String(r.marginUsd), contracts: r.contracts, leverage: r.leverage, stopPct: Number(r.stopPct), tpPct: tp}
}

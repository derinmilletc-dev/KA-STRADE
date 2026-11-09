// Kais ajanı — aşama 3, adım 2: tarama adaylarını Demo emir TASLAĞINA çevirir ve "net kâr X dolar olsun" için TP fiyatını hesaplar.
// Saf hesap: ağ yok, emir yok. Seviyeler (giriş/stop) yalnızca sunucunun taramasından gelir; burada uydurulmaz.
import type {OrderDraft} from './kais-order-intents'

export type PlanPick = {symbol: string; direction: 'LONG' | 'SHORT'; confidence_value: number; entry: number; stop_loss: number; tp1: number; tp2: number; tp3: number; opportunity_score?: number | null}

/** Taraf başı komisyon oranı (taker, Binance Futures varsayılanına yakın %0,05). Kayma ve fonlama dahil değildir. */
export const PLAN_FEE_RATE = 0.0005

/**
 * Giriş `entry`, marjin ve kaldıraçla açılan pozisyonda, giriş+çıkış komisyonu düşüldükten sonra net `targetNet` USDT kazandıran çıkış fiyatı.
 * net = q·d·(1 − s·r) − 2·q·entry·r  (d: fiyat farkı, s: +1 LONG / −1 SHORT, r: komisyon oranı, q: miktar).
 * Geçersiz girdide null (varsayılan değer uydurulmaz).
 */
export function targetExitPrice(direction: 'LONG' | 'SHORT', entry: number, marginUsdt: number, leverage: number, targetNet: number, feeRate = PLAN_FEE_RATE): number | null {
  if (![entry, marginUsdt, leverage, targetNet, feeRate].every(Number.isFinite) || entry <= 0 || marginUsdt <= 0 || leverage < 1 || targetNet <= 0 || feeRate < 0) return null
  const quantity = (marginUsdt * leverage) / entry
  const side = direction === 'LONG' ? 1 : -1
  const distance = (targetNet + 2 * quantity * entry * feeRate) / (quantity * (1 - side * feeRate))
  const price = entry + side * distance
  return Number.isFinite(price) && price > 0 ? price : null
}

const text = (value: number) => String(Number(value.toPrecision(8)))

export type PlanDraft = {draft: OrderDraft; pick: PlanPick; targetPrice: number | null; movePercent: number | null; warning: string}

/**
 * Aday → taslak. Marjin/kaldıraç ve (varsa) net kâr hedefi plandan gelir. Hedef verilmişse TP1=TP2=TP3 = hedef fiyat (tüm pozisyon o fiyatta kapanır);
 * verilmemişse sunucunun TP1–TP3 seviyeleri aynen kullanılır. Hedef fiyat girişin yanlış tarafına düşerse ya da stop ile çelişirse taslak yine de
 * üretilir ama uyarıyla; sunucu testi ve yazılı ONAYLA zaten zorunludur.
 */
export function buildPlanDraft(pick: PlanPick, options: {marginUsdt: number | null; leverage: number | null; targetNet: number | null}): PlanDraft {
  const {marginUsdt, leverage, targetNet} = options
  let tps = [pick.tp1, pick.tp2, pick.tp3]
  let targetPrice: number | null = null
  let movePercent: number | null = null
  let warning = ''
  if (targetNet !== null) {
    if (marginUsdt === null || leverage === null) warning = 'Net kâr hesabı için marjin ve kaldıraç gerekir.'
    else {
      targetPrice = targetExitPrice(pick.direction, pick.entry, marginUsdt, leverage, targetNet)
      if (targetPrice === null) warning = 'Hedef fiyat hesaplanamadı.'
      else {
        movePercent = Math.abs(targetPrice - pick.entry) / pick.entry * 100
        tps = [targetPrice, targetPrice, targetPrice]
        const beyondStop = pick.direction === 'LONG' ? targetPrice <= pick.entry : targetPrice >= pick.entry
        if (beyondStop) warning = 'Hedef fiyat girişin yanlış tarafında; taslağı kontrol et.'
        else if (movePercent > 20) warning = `Hedef çok uzak (girişten %${movePercent.toFixed(1)}); marjin ya da kaldıraç çok düşük olabilir.`
      }
    }
  }
  const draft: OrderDraft = {
    symbol: pick.symbol, direction: pick.direction, margin: marginUsdt !== null ? text(marginUsdt) : '', leverage: leverage !== null ? text(leverage) : '',
    stop: text(pick.stop_loss), tp1: text(tps[0]), tp2: text(tps[1]), tp3: text(tps[2]),
  }
  return {draft, pick, targetPrice, movePercent, warning}
}

/** Sunucudan gelen ham aday listesini doğrular; bozuk satırları atar (seviye onarılmaz). */
export function parsePlanPicks(value: unknown): PlanPick[] {
  if (!Array.isArray(value)) return []
  const picks: PlanPick[] = []
  for (const row of value) {
    if (!row || typeof row !== 'object') continue
    const item = row as Record<string, unknown>
    const direction = item.direction
    const numbers = ['confidence_value', 'entry', 'stop_loss', 'tp1', 'tp2', 'tp3'].map(key => item[key])
    if (typeof item.symbol !== 'string' || (direction !== 'LONG' && direction !== 'SHORT') || !numbers.every(entry => typeof entry === 'number' && Number.isFinite(entry) && entry > 0)) continue
    const [confidence_value, entry, stop_loss, tp1, tp2, tp3] = numbers as number[]
    picks.push({symbol: item.symbol, direction, confidence_value, entry, stop_loss, tp1, tp2, tp3, opportunity_score: typeof item.opportunity_score === 'number' ? item.opportunity_score : null})
  }
  return picks
}

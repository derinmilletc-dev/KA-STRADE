// Coinbase manuel emir: Kais analizi / dalga (oynaklık) stop önerileri, vade bitiş bilgisi ve en küçük emir.
// SAF fonksiyonlar: ağ yok, emir yok. Analiz ve mumlar Binance verisinden gelir (aynı coin); Coinbase fiyatına YÜZDE olarak taşınır,
// çünkü iki borsanın fiyatı birebir aynı değildir. Kaldıraçta tasfiyeye takılacak stop, güvenli sınıra çekilir.

// Binance tarafındaki manual-order-state ile AYNI kural (o dosya burada içe aktarılamıyor: düğüm testleri uzantısız içe aktarmayı çözemiyor).
const LIQUIDATION_SAFETY_PCT = 1.2
function clampStopToLeverage(direction: 'LONG' | 'SHORT', entry: number, stop: number, leverage: number): {stop: number; clamped: boolean; maxPct: number | null} {
  const maxPct = Number.isFinite(leverage) && leverage >= 2 ? Math.max(0.1, 100 / leverage - LIQUIDATION_SAFETY_PCT) : null
  if (maxPct === null || !(entry > 0) || !(stop > 0)) return {stop, clamped: false, maxPct}
  if (Math.abs(entry - stop) / entry * 100 <= maxPct) return {stop, clamped: false, maxPct}
  return {stop: entry * (1 - (direction === 'LONG' ? 1 : -1) * maxPct / 100), clamped: true, maxPct}
}

export type Dir = 'LONG' | 'SHORT'
export type AnalysisLevels = {direction?: unknown; entry?: unknown; stop_loss?: unknown; tp1?: unknown; tp2?: unknown; confidence?: unknown}
export type Suggested = {direction: Dir; stop: number; target: number | null; stopPct: number; targetPct: number | null; clamped: boolean; maxPct: number | null}

const pos = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null }

/** Analizin stop ve (2. ya da 1.) hedefini Coinbase giriş fiyatına yüzde olarak uygular. Yön analizden gelir. Net yön yoksa null. */
export function levelsFromAnalysis(analysis: AnalysisLevels | null, entry: number, leverage: number): Suggested | null {
  if (!analysis || !(entry > 0)) return null
  const direction = String(analysis.direction ?? '').toUpperCase()
  if (direction !== 'LONG' && direction !== 'SHORT') return null
  const aEntry = pos(analysis.entry), aStop = pos(analysis.stop_loss), aTarget = pos(analysis.tp2) ?? pos(analysis.tp1)
  if (aEntry === null || aStop === null) return null
  const sign = direction === 'LONG' ? 1 : -1
  const stopPct = (aEntry - aStop) * sign / aEntry * 100
  if (!(stopPct > 0) || stopPct > 25) return null
  const targetPct = aTarget !== null && (aTarget - aEntry) * sign > 0 ? (aTarget - aEntry) * sign / aEntry * 100 : null
  const raw = entry * (1 - sign * stopPct / 100)
  const fitted = clampStopToLeverage(direction, entry, raw, leverage)
  return {
    direction, stop: fitted.stop, clamped: fitted.clamped, maxPct: fitted.maxPct,
    stopPct: Math.abs(entry - fitted.stop) / entry * 100,
    target: targetPct !== null ? entry * (1 + sign * targetPct / 100) : null, targetPct,
  }
}

/** Dalgaya göre stop: mumlardan bulunan uzaklık yüzdesi Coinbase girişine uygulanır; hedef = 2 × stop mesafesi. */
export function levelsFromVolatility(direction: Dir, entry: number, distancePct: number, leverage: number): Suggested | null {
  if (!(entry > 0) || !(distancePct > 0)) return null
  const sign = direction === 'LONG' ? 1 : -1
  const fitted = clampStopToLeverage(direction, entry, entry * (1 - sign * distancePct / 100), leverage)
  const stopPct = Math.abs(entry - fitted.stop) / entry * 100
  return {direction, stop: fitted.stop, clamped: fitted.clamped, maxPct: fitted.maxPct, stopPct, target: entry * (1 + sign * stopPct * 2 / 100), targetPct: stopPct * 2}
}

export type Expiry = {msLeft: number; days: number; text: string; tone: 'info' | 'warn' | 'bad'; when: string}
const TR_MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara']

/** Tarihli ürünün kapanışına kalan süre. Perp (2050+ yer tutucu) ya da okunamayan tarih için null. */
export function expiryInfo(expiry: string | null | undefined, now = Date.now()): Expiry | null {
  if (!expiry) return null
  const time = Date.parse(expiry)
  if (!Number.isFinite(time) || new Date(time).getUTCFullYear() >= 2050) return null
  const msLeft = time - now
  const d = new Date(time)
  const when = `${d.getDate()} ${TR_MONTHS[d.getMonth()]} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (msLeft <= 0) return {msLeft, days: 0, text: 'Bu ürünün vadesi doldu; işlem açma.', tone: 'bad', when}
  const hours = msLeft / 3_600_000
  const days = Math.floor(hours / 24)
  const left = hours < 24 ? `${Math.max(1, Math.round(hours))} saat` : `${days} gün`
  const tone = hours < 24 ? 'bad' : days <= 3 ? 'warn' : 'info'
  return {msLeft, days, tone, when, text: `Bu ürün ${left} sonra (${when}) kapanır. O an açık pozisyon otomatik kapanır.`}
}

/** En küçük emir: 1 sözleşmenin pozisyon değeri ve bu kaldıraçla gereken yaklaşık teminat. */
export function minOrder(contractSize: number, entry: number, leverage: number): {notional: number; margin: number} | null {
  if (!(contractSize > 0) || !(entry > 0)) return null
  const notional = contractSize * entry
  return {notional, margin: notional / (leverage >= 1 ? leverage : 1)}
}

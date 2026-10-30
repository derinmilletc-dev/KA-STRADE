// Canlı İşlem > Pozisyonlar: saf hesaplamalar (ekran yok, ağ yok). Yalnız Binance pozisyonu ve plan kaydından türetilir;
// bir değer gelmediyse null döner ve ekran o kutuyu hiç göstermez. Hiçbir değer uydurulmaz.

export type RawRecord = Record<string, unknown>

export const num = (value: unknown): number | null => {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value.replace(',', '.')) : NaN
  return Number.isFinite(parsed) ? parsed : null
}
const positive = (value: unknown): number | null => { const parsed = num(value); return parsed !== null && parsed > 0 ? parsed : null }

/** Fiyat gösterimi: büyük fiyatlarda az, küçüklerde çok ondalık; gereksiz sıfır yok. */
export function formatPrice(value: number | null): string {
  if (value === null) return '—'
  const digits = value >= 1000 ? 2 : value >= 1 ? 4 : 6
  return value.toLocaleString('tr-TR', {minimumFractionDigits: 0, maximumFractionDigits: digits})
}
export const formatUsdt = (value: number | null, signed = false): string => {
  if (value === null) return '—'
  const text = Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return `${value < 0 ? '−' : signed && value > 0 ? '+' : ''}${text}`
}
export const formatPct = (value: number | null, digits = 2): string => value === null ? '—' : `%${value.toLocaleString('tr-TR', {minimumFractionDigits: digits, maximumFractionDigits: digits})}`
export const formatR = (value: number | null): string => value === null ? '—' : `${value < 0 ? '−' : value > 0 ? '+' : ''}${Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} R`

export type PositionLevel = 'ok' | 'warn' | 'danger'
export type LadderMarker = {key: 'stop' | 'entry' | 'tp1' | 'tp2' | 'tp3'; label: string; price: number; pos: number; row: 0 | 1}
export type Ladder = {markers: LadderMarker[]; markPos: number; entryPos: number; long: boolean}

export type PositionMetrics = {
  symbol: string; coin: string; long: boolean; direction: string
  entry: number | null; mark: number | null; stop: number | null; tp1: number | null; tp2: number | null; tp3: number | null
  quantity: number | null; notional: number | null; leverage: number | null; marginType: string | null; margin: number | null
  liquidation: number | null
  pnl: number | null; pnlOnMarginPct: number | null; rMultiple: number | null
  /** Stop çalışırsa kalan zarar (>0) ya da kilitli kâr (<0: stop girişin kâr tarafında). */
  riskAtStop: number | null; rewardAtTp1: number | null
  toStopPct: number | null; toTp1Pct: number | null; toLiqPct: number | null
  openedMs: number | null
  ladder: Ladder | null
  level: PositionLevel
}

const MARKER_LABEL = {stop: 'Stop', entry: 'Giriş', tp1: 'TP1', tp2: 'TP2', tp3: 'TP3'} as const

/** Fiyat eksenine yerleşim: etiketler kesilmesin diye %6–%94 aralığına; yakın etiketler ikinci satıra. */
export function buildLadder(long: boolean, mark: number | null, levels: {stop: number | null; entry: number | null; tp1: number | null; tp2: number | null; tp3: number | null}): Ladder | null {
  const entries = (Object.keys(MARKER_LABEL) as Array<keyof typeof MARKER_LABEL>)
    .map(key => ({key, price: levels[key]})).filter((item): item is {key: keyof typeof MARKER_LABEL; price: number} => item.price !== null && item.price > 0)
  if (mark === null || mark <= 0 || entries.length < 2 || !entries.some(item => item.key === 'entry')) return null
  const prices = [...entries.map(item => item.price), mark]
  const min = Math.min(...prices), max = Math.max(...prices)
  if (!(max > min)) return null
  const at = (price: number) => 6 + (price - min) / (max - min) * 88
  const sorted = entries.map(item => ({...item, pos: at(item.price)})).sort((a, b) => a.pos - b.pos)
  const lastOnRow: [number, number] = [-100, -100]
  const markers: LadderMarker[] = []
  for (const item of sorted) {
    let row: 0 | 1 | null = null
    if (item.pos - lastOnRow[0] >= 15) row = 0
    else if (item.pos - lastOnRow[1] >= 15) row = 1
    if (row === null) continue // çok yakın: ikisini birden yazmak okunmaz; öncelik soldakinde
    lastOnRow[row] = item.pos
    markers.push({key: item.key, label: MARKER_LABEL[item.key], price: item.price, pos: item.pos, row})
  }
  const entryPos = at(levels.entry as number)
  return {markers, markPos: at(mark), entryPos, long}
}

const planOf = (plan: RawRecord | undefined) => plan ?? {}

/** Tek pozisyonun tüm türev değerleri. */
export function positionMetrics(position: RawRecord, plan: RawRecord | undefined, nowMs: number): PositionMetrics {
  const p = planOf(plan)
  const direction = String(position.direction ?? '').toUpperCase()
  const long = direction !== 'SHORT'
  const symbol = String(position.symbol ?? '')
  const entry = positive(position.entry_price) ?? positive(p.entry_price)
  const mark = positive(position.mark_price)
  const stop = positive(p.stop_loss)
  const targets = Array.isArray(p.targets) ? p.targets : []
  const tp1 = positive(targets[0]), tp2 = positive(targets[1]), tp3 = positive(targets[2])
  const quantity = positive(position.quantity)
  const notional = positive(position.notional) ?? (quantity !== null && mark !== null ? quantity * mark : null)
  const leverage = positive(position.leverage) ?? positive(p.leverage)
  const marginType = typeof position.margin_type === 'string' && position.margin_type ? position.margin_type.toLowerCase() : null
  const margin = notional !== null && leverage !== null ? notional / leverage : positive(p.margin_usdt)
  const liquidation = positive(position.liquidation_price)
  const pnl = num(position.unrealized_pnl)
  const side = long ? 1 : -1
  // Zarar tarafında kalan mesafe: +  = henüz ulaşılmadı. Mark stopu geçtiyse 0'a sabitlenir.
  const toStopPct = stop !== null && mark !== null ? Math.max(0, (mark - stop) * side / mark * 100) : null
  const toTp1Pct = tp1 !== null && mark !== null ? (tp1 - mark) * side / mark * 100 : null
  const toLiqPct = liquidation !== null && mark !== null ? Math.max(0, (mark - liquidation) * side / mark * 100) : null
  const riskAtStop = stop !== null && entry !== null && quantity !== null ? (entry - stop) * side * quantity : null
  const rewardAtTp1 = tp1 !== null && entry !== null && quantity !== null ? (tp1 - entry) * side * quantity : null
  const initialRisk = positive(p.initial_risk_usdt) ?? positive(p.original_risk_budget)
  const rMultiple = pnl !== null && initialRisk !== null ? pnl / initialRisk : null
  const opened = Date.parse(String(p.opened_at ?? p.created_at ?? ''))
  const openedMs = Number.isFinite(opened) && opened <= nowMs ? opened : null
  const ladder = buildLadder(long, mark, {stop, entry, tp1, tp2, tp3})
  let level: PositionLevel = 'ok'
  if ((toStopPct !== null && toStopPct < 0.8 && (riskAtStop ?? 1) > 0) || (toLiqPct !== null && toLiqPct < 8)) level = 'danger'
  else if ((toStopPct !== null && toStopPct < 1.5 && (riskAtStop ?? 1) > 0) || (toLiqPct !== null && toLiqPct < 15)) level = 'warn'
  return {
    symbol, coin: symbol.replace(/USDT$/, ''), long, direction, entry, mark, stop, tp1, tp2, tp3, quantity, notional, leverage, marginType, margin, liquidation,
    pnl, pnlOnMarginPct: pnl !== null && margin !== null && margin > 0 ? pnl / margin * 100 : null, rMultiple,
    riskAtStop, rewardAtTp1, toStopPct, toTp1Pct, toLiqPct, openedMs, ladder, level,
  }
}

export type Insight = {tone: 'warn' | 'ok' | 'info'; text: string}

/** "Akıllı yorumlar": yalnız hesaplanabilen sayılardan. */
export function positionInsights(metrics: PositionMetrics, plan: RawRecord | undefined, stopVerified: boolean): Insight[] {
  const out: Insight[] = []
  const p = planOf(plan)
  const source = String(p.source ?? '')
  const bot = source === 'ORIGINAL_V2_LIVE'
  const fraction = num(p.tp1_fraction)
  const tp1Share = fraction !== null && fraction > 0 && fraction < 1 ? Math.round(fraction * 100) : 60
  const breakeven = p.breakeven_done === true || p.original_tp1_complete === true
  const risk = metrics.riskAtStop
  if (metrics.toStopPct !== null && risk !== null) {
    if (risk > 0 && metrics.toStopPct < 1.5) {
      out.push({tone: 'warn', text: `Fiyat stopa ${formatPct(metrics.toStopPct)} yaklaştı. ${stopVerified ? 'Stop Binance’te kayıtlı; ulaşırsa pozisyon kendiliğinden kapanır' : 'Stop henüz doğrulanamadı'}; ${stopVerified ? 'en fazla' : 'ulaşılırsa yaklaşık'} ${formatUsdt(risk)} USDT kaybedersin.`})
    } else if (risk < -0.005) {
      out.push({tone: 'ok', text: `Stop girişin kâr tarafına çekilmiş: stopa ulaşılsa bile yaklaşık ${formatUsdt(-risk)} USDT kâr kalır.`})
    } else if (Math.abs(risk) <= 0.005 || breakeven) {
      out.push({tone: 'ok', text: 'Stop girişe (başabaş) çekilmiş: stopa ulaşılırsa zarar etmeden çıkılır.'})
    }
  }
  if (metrics.toTp1Pct !== null && !breakeven) {
    if (metrics.toTp1Pct <= 0) out.push({tone: 'ok', text: 'TP1 seviyesi geçildi; emir Binance’te dolmayı bekliyor.'})
    else if (metrics.toTp1Pct <= 2.5) out.push({tone: 'info', text: `TP1’e ${formatPct(metrics.toTp1Pct)} kaldı. TP1 dolunca pozisyonun %${tp1Share}’i kapanır${bot ? ' ve stop girişe çekilir' : ''}.`})
  }
  if (metrics.toLiqPct !== null && metrics.toLiqPct < 15) out.push({tone: 'warn', text: `Likidasyon fiyatına yalnız ${formatPct(metrics.toLiqPct)} uzaklık var; teminatı ya da boyutu gözden geçir.`})
  return out
}

export type PositionSort = 'risk' | 'pnl' | 'time'
export function sortPositions<T extends {metrics: PositionMetrics}>(items: T[], sort: PositionSort): T[] {
  const cmpNullLast = (a: number | null, b: number | null, dir: 1 | -1) => a === null ? (b === null ? 0 : 1) : b === null ? -1 : (a - b) * dir
  const copy = [...items]
  if (sort === 'risk') copy.sort((a, b) => cmpNullLast(a.metrics.toStopPct, b.metrics.toStopPct, 1))
  else if (sort === 'pnl') copy.sort((a, b) => cmpNullLast(a.metrics.pnl, b.metrics.pnl, -1))
  else copy.sort((a, b) => cmpNullLast(a.metrics.openedMs, b.metrics.openedMs, -1))
  return copy
}

export type PortfolioSummary = {
  count: number; pnl: number | null; pnlOfBalancePct: number | null
  riskAtStop: number | null; riskUnknown: number; riskOfRemainingDailyPct: number | null
  notional: number | null; margin: number | null; exposureLimit: number | null; exposurePct: number | null
  protectedCount: number
}

export function summarize(items: Array<{metrics: PositionMetrics; protectedStop: boolean}>, context: {wallet: number | null; dailyLoss: number | null; dailyLimit: number | null; exposureLimit: number | null}): PortfolioSummary {
  const finite = (values: Array<number | null>) => { const list = values.filter((value): value is number => value !== null); return list.length ? list.reduce((a, b) => a + b, 0) : null }
  const pnl = finite(items.map(item => item.metrics.pnl))
  // Stopta toplam risk yalnız zarar tarafındaki stoplardan (kilitli kâr riske sayılmaz).
  const risks = items.map(item => item.metrics.riskAtStop === null ? null : Math.max(0, item.metrics.riskAtStop))
  const riskAtStop = finite(risks)
  const remaining = context.dailyLimit !== null && context.dailyLoss !== null ? Math.max(0, context.dailyLimit - context.dailyLoss) : null
  const notional = finite(items.map(item => item.metrics.notional))
  const exposureLimit = context.exposureLimit !== null && context.exposureLimit > 0 ? context.exposureLimit : null
  return {
    count: items.length, pnl, pnlOfBalancePct: pnl !== null && context.wallet !== null && context.wallet > 0 ? pnl / context.wallet * 100 : null,
    riskAtStop, riskUnknown: risks.filter(value => value === null).length,
    riskOfRemainingDailyPct: riskAtStop !== null && remaining !== null && remaining > 0 ? riskAtStop / remaining * 100 : null,
    notional, margin: finite(items.map(item => item.metrics.margin)), exposureLimit,
    exposurePct: notional !== null && exposureLimit !== null ? notional / exposureLimit * 100 : null,
    protectedCount: items.filter(item => item.protectedStop).length,
  }
}

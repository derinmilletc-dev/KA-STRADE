// Akıllı yorum ekranının saf yardımcıları (ekrandan bağımsız, test edilebilir). Veri sunucu motorundan (coin_read.py) gelir.
export type ReadTone = 'up' | 'down' | 'warn' | 'flat'
export type ReadPattern = {name: string; bias: 'up' | 'down' | 'flat'; text: string}
export type ReadTf = {
  tf: string; label: string; trend: 'up' | 'down' | 'flat'; net_pct: number; event: string | null; volume_spike: boolean; pullback: 'up' | 'down' | null
  rsi: number; rsi_state: 'overbought' | 'oversold' | 'normal'; divergence: 'bullish' | 'bearish' | null; ema: 'up' | 'down' | 'mixed'; squeeze: boolean
  patterns: ReadPattern[]; place: number; score: number; tone: ReadTone; head: string; text: string
}
export type ReadLevel = {price: number; dist_pct: number; strength: number; touches: number}
export type ReadNote = {tone: string; text: string}
export type ReadFutures = {funding_pct: number | null; oi_change_pct: number | null; taker_ratio: number | null; notes: ReadNote[]; bias: number}
export type ReadSummary = {bias: number; lean: 'LONG' | 'SHORT' | 'NEUTRAL'; confidence: 'Yüksek' | 'Orta' | 'Düşük'; title: string; text: string; flags: ReadNote[]; conflict: string | null; alignment: {up: number; down: number; flat: number; total: number}}
export type ReadPosition = {side: 'LONG' | 'SHORT'; fit: 'uyumlu' | 'ters' | 'kararsiz'; text: string; pnl_pct: number | null; guard: {price: number; dist_pct: number; label: string} | null; target: {price: number; dist_pct: number; label: string} | null; events: Record<string, {tone: string; text: string}>}
export type ReadHistory = {tf: string; text: string | null; event: string | null; candles: number; horizon: number}
export type ReadContext = {regime: 'trend' | 'yatay' | 'karisik' | null; regime_text: string | null; btc: {lean: 'LONG' | 'SHORT' | 'NEUTRAL'; bias: number; text: string} | null; corr: number | null; relation: 'bagli' | 'orta' | 'bagimsiz' | null; notes: string[]}
export type ReadPlanTarget = {price: number; rr: number; basis: 'seviye' | '2R'}
export type ReadPlan = {side: 'LONG' | 'SHORT'; entry: number; stop: number; stop_pct: number; stop_basis: 'seviye' | 'atr'; targets: ReadPlanTarget[]; rr: number; verdict: string; tone: 'good' | 'flat' | 'warn'; confidence: string; text: string}
export type ReadWall = {price: number; value: number; dist_pct: number}
export type ReadBook = {imbalance: number; bid_value: number; ask_value: number; bid_walls: ReadWall[]; ask_walls: ReadWall[]; notes: string[]; caveat: string}
export type ReadLiquidations = {long_usd: number; short_usd: number; zones: {price: number; usd: number; dist_pct: number}[]; text: string}
export type ReadEventItem = {kind: 'fomc' | 'cpi' | 'jobs' | 'funding'; at: string; minutes: number; level: 'danger' | 'warn' | 'info'; text: string}
export type ReadEvents = {covered: boolean; items: ReadEventItem[]; next_funding: string}
export type CoinRead = {symbol: string; price: number; timeframes: ReadTf[]; levels: {support: ReadLevel[]; resistance: ReadLevel[]}; futures: ReadFutures | null; summary: ReadSummary; history: ReadHistory | null; position?: ReadPosition
  context?: ReadContext | null; plan?: ReadPlan | null; book?: ReadBook | null; liquidations?: ReadLiquidations | null; events?: ReadEvents | null}
export type Tally = {n: number; hit: number; miss: number; flat: number; rate: number | null}
export type ReadStats = {days: number; overall: Tally; by_confidence: Record<string, Tally>; symbol: (Tally & {symbol: string}) | null}
export type WatchDirection = 'both' | 'long' | 'short'
export type WatchItem = {symbol: string; direction: WatchDirection}

export const EVENT_LABEL: Record<string, string> = {breakout_up: 'Yukarı kırılım', breakout_down: 'Aşağı kırılım', bull_trap: 'Boğa tuzağı', bear_trap: 'Ayı tuzağı'}
export const TF_ORDER = ['1m', '5m', '15m', '1h', '4h', '1d'] as const

export const arrowOf = (tone: ReadTone, score: number): string => tone === 'warn' ? '!' : score >= 0.75 ? '▲' : score <= -0.75 ? '▼' : '■'

/** Sunucu fiyatı yoksa güvenli metin; küçük fiyatlarda daha çok basamak. */
export function priceText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2})
}

export const signedPct = (value: number | null | undefined, digits = 2): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value < 0 ? '−' : '+'}%${Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: digits, maximumFractionDigits: digits})}`
}

/** Yön ölçeği: bias -2..2 → 0..100 (50 = nötr). Sınır dışı değerler kenara sabitlenir. */
export function gaugePos(bias: number): number {
  if (!Number.isFinite(bias)) return 50
  return Math.round(Math.min(100, Math.max(0, 50 + bias * 25)))
}

export type LevelBar = {pos: number; marks: {key: string; pos: number; price: number; kind: 'support' | 'resistance'}[]}
/** Seviye çubuğu: en uzak destek ile en uzak direnç arasında fiyatın yeri (%). Seviye yoksa null. */
export function levelBar(price: number, levels: {support: ReadLevel[]; resistance: ReadLevel[]}): LevelBar | null {
  const all = [...levels.support.map(l => ({...l, kind: 'support' as const})), ...levels.resistance.map(l => ({...l, kind: 'resistance' as const}))]
  if (!all.length || !(price > 0)) return null
  const lo = Math.min(price, ...all.map(l => l.price)), hi = Math.max(price, ...all.map(l => l.price))
  if (!(hi > lo)) return null
  const span = hi - lo, pad = span * 0.06
  const from = lo - pad, width = span + pad * 2
  const at = (value: number) => Math.round((value - from) / width * 1000) / 10
  return {pos: at(price), marks: all.map((l, i) => ({key: `${l.kind}${i}`, pos: at(l.price), price: l.price, kind: l.kind}))}
}

export const confidenceTone = (value: string): 'high' | 'mid' | 'low' => value === 'Yüksek' ? 'high' : value === 'Orta' ? 'mid' : 'low'

export function fitTone(fit: ReadPosition['fit']): ReadTone { return fit === 'uyumlu' ? 'up' : fit === 'ters' ? 'down' : 'flat' }
export const fitTitle = (fit: ReadPosition['fit']): string => fit === 'uyumlu' ? 'Pozisyonunla uyumlu' : fit === 'ters' ? 'Pozisyonuna ters' : 'Pozisyonun için kararsız'

// ---- Pozisyon yorum alarmı ayarı
export type AlarmMode = 'against' | 'all'
export type AlarmTf = '5m' | '15m' | '1h'
export type ReadAlarm = {id: number; exchange: 'binance' | 'coinbase'; product_id: string; symbol: string; side: 'LONG' | 'SHORT'; entry: number | null; mode: AlarmMode; min_tf: AlarmTf; stop?: number | null; active_keys: string[]}
export const ALARM_MODES: ReadonlyArray<{id: AlarmMode; label: string; hint: string}> = [
  {id: 'against', label: 'Yalnız ters sinyal', hint: 'Pozisyonuna karşı bir şey çıkınca haber ver'},
  {id: 'all', label: 'Her şey', hint: 'Lehine olan gelişmeleri de bildir'},
]
export const ALARM_TFS: ReadonlyArray<{id: AlarmTf; label: string; hint: string}> = [
  {id: '5m', label: 'Hassas', hint: '5 dk ve üstü: daha sık, daha erken'},
  {id: '15m', label: 'Dengeli', hint: '15 dk ve üstü'},
  {id: '1h', label: 'Sakin', hint: '1 saat ve üstü: seyrek, daha sağlam'},
]
export const alarmSummary = (alarm: Pick<ReadAlarm, 'mode' | 'min_tf'>): string =>
  `${ALARM_MODES.find(m => m.id === alarm.mode)?.label ?? 'Yalnız ters sinyal'} · ${ALARM_TFS.find(t => t.id === alarm.min_tf)?.label ?? 'Dengeli'}`

/** Binance: planın yönü; Coinbase: dayanak varlıktan Binance analiz anahtarı. */
export const analysisKey = (root: string | null | undefined): string | null => root && /^[A-Z0-9]{2,10}$/.test(root) ? `${root}USDT` : null


// ---- Ek katmanlar (piyasa bağlamı, plan, emir defteri, önemli saatler, doğruluk)
/** Emir defteri dengesi (-1..1) → 5..95 çubuk konumu. */
export const bookPos = (imbalance: number): number => Number.isFinite(imbalance) ? Math.round(Math.min(95, Math.max(5, 50 + imbalance * 50))) : 50

/** Gösterilecek en önemli takvim uyarısı: danger > warn > info, aynı seviyede en yakın olan. Takvim kapsamı bittiyse null. */
export function topEvent(events: ReadEvents | null | undefined): ReadEventItem | null {
  if (!events || !events.items.length) return null
  const rank = {danger: 0, warn: 1, info: 2} as const
  return [...events.items].sort((a, b) => rank[a.level] - rank[b.level] || a.minutes - b.minutes)[0]
}

/** Süre metni: 45 → "45 dk", 150 → "2 sa 30 dk", 3000 → "2 gün". */
export function minutesText(minutes: number): string {
  const total = Math.max(0, Math.round(minutes))
  if (total < 60) return `${total} dk`
  const hours = Math.floor(total / 60), rest = total % 60
  if (hours < 24) return rest ? `${hours} sa ${rest} dk` : `${hours} sa`
  return `${Math.floor(hours / 24)} gün`
}

/** Doğruluk satırı: coinin kendi örneği yeterliyse onu, değilse genel oranı yazar; ikisi de yetersizse null (az veriden yargı çıkarılmaz). */
export function accuracyLine(stats: ReadStats | null | undefined): string | null {
  if (!stats) return null
  const own = stats.symbol
  if (own && own.rate !== null) return `Son ${stats.days} günde bu coin için ${own.n} yorumun %${own.rate}'i 1 saat sonra yönünde gitti.`
  if (stats.overall.rate !== null) return `Son ${stats.days} günde tüm coinlerde ${stats.overall.n} yorumun %${stats.overall.rate}'i 1 saat sonra yönünde gitti.`
  return null
}

/** Plan hedefinin risk/ödül etiketi: 1:2,3 */
export const rrText = (rr: number): string => `1:${rr.toLocaleString('tr-TR', {minimumFractionDigits: 1, maximumFractionDigits: 1})}`

export const WATCH_LABEL: Record<WatchDirection, string> = {both: 'İki yön', long: 'Yalnız LONG', short: 'Yalnız SHORT'}
export const nextDirection = (value: WatchDirection): WatchDirection => value === 'both' ? 'long' : value === 'long' ? 'short' : 'both'

/** Büyük para değerleri: 1.250.000 → "1,3 mn $", 48.000 → "48 bin $". */
export function usdText(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toLocaleString('tr-TR', {maximumFractionDigits: 1})} mn $`
  if (value >= 1_000) return `${Math.round(value / 1_000).toLocaleString('tr-TR')} bin $`
  return `${Math.round(value)} $`
}

export type FormLevels = {ok: true; side: 'LONG' | 'SHORT'; stop: number; tps: [number, number, number]} | {ok: false; error: string}
/** Planı emir formuna yazılacak seviyelere çevirir. Form girişi plan fiyatından farklıysa (limit emir) seviyeler FORMUN giriş fiyatına göre doğrulanır:
 *  stop yanlış tarafta ya da hedef girişin ötesinde değilse yazılmaz. Üç hedef istenirse eksikler 1,5 ve 2 katı mesafeyle tamamlanır. */
export function planForForm(plan: ReadPlan, entry: number): FormLevels {
  const long = plan.side === 'LONG'
  if (!(entry > 0) || !Number.isFinite(entry)) return {ok: false, error: 'Giriş fiyatı henüz belli değil; birkaç saniye sonra tekrar dene.'}
  if (long ? !(plan.stop < entry) : !(plan.stop > entry)) return {ok: false, error: 'Fiyat planın stop seviyesini geçmiş görünüyor; yorum yenilenince tekrar dene.'}
  const first = plan.targets[0]?.price
  if (!first || (long ? !(first > entry) : !(first < entry))) return {ok: false, error: 'Plan hedefi giriş fiyatının ötesinde değil; yorum yenilenince tekrar dene.'}
  const dist = Math.abs(first - entry), sign = long ? 1 : -1
  const beyond = (value: number | undefined, min: number) => value !== undefined && (long ? value > entry + min : value < entry - min)
  const second = beyond(plan.targets[1]?.price, dist) ? plan.targets[1].price : entry + sign * dist * 1.5
  const third = entry + sign * Math.max(dist * 2, Math.abs(second - entry) * 1.3)
  return {ok: true, side: plan.side, stop: plan.stop, tps: [first, second, third]}
}

export type AlertCandidate = {key: string; label: string; price: number; tone: 'up' | 'down' | 'warn'}
/** Tek dokunuşla alarm kurulabilecek seviyeler: plan stopu/hedefleri ile en yakın destek/direnç. Fiyata %0,1'den yakın ya da birbirine %0,15'ten yakın olanlar elenir. */
export function alertCandidates(data: Pick<CoinRead, 'price' | 'levels' | 'plan'>): AlertCandidate[] {
  const raw: AlertCandidate[] = []
  const plan = data.plan
  if (plan) {
    plan.targets.forEach((target, index) => raw.push({key: `t${index}`, label: `Hedef ${index + 1}`, price: target.price, tone: 'up'}))
    raw.push({key: 'stop', label: 'Stop', price: plan.stop, tone: 'warn'})
  }
  if (data.levels.resistance[0]) raw.push({key: 'res', label: 'Direnç', price: data.levels.resistance[0].price, tone: 'down'})
  if (data.levels.support[0]) raw.push({key: 'sup', label: 'Destek', price: data.levels.support[0].price, tone: 'up'})
  const kept: AlertCandidate[] = []
  for (const item of raw) {
    if (!(item.price > 0) || !(data.price > 0) || Math.abs(item.price / data.price - 1) < 0.001) continue
    if (kept.some(other => Math.abs(other.price / item.price - 1) < 0.0015)) continue
    kept.push(item)
  }
  return kept.sort((a, b) => b.price - a.price)
}

// ---- Açık pozisyon nabzı (Binance pozisyon kartı)
export type Pulse = {symbol: string; side: 'LONG' | 'SHORT'; lean: 'LONG' | 'SHORT' | 'NEUTRAL'; confidence: string; fit: ReadPosition['fit']; conflict: boolean; warnings: string[]; price: number}
export type PulseData = {items: Pulse[]; events: ReadEvents | null}

export function pulseChip(pulse: Pulse): {text: string; tone: ReadTone} {
  const base = pulse.fit === 'uyumlu' ? {text: 'Yorum uyumlu', tone: 'up' as const} : pulse.fit === 'ters' ? {text: 'Yorum ters', tone: 'down' as const} : {text: 'Yorum kararsız', tone: 'flat' as const}
  return pulse.fit !== 'ters' && pulse.warnings.length ? {text: `${base.text} · uyarı`, tone: 'warn'} : base
}

/** Portföy düzeyinde kısa özet: yorumu ters olanlar, aktif uyarılar, tek yöne yığılma, yaklaşan önemli saat. Hiçbiri yoksa tek olumlu satır. */
export function pulseSummary(pulses: Pulse[], events: ReadEvents | null | undefined, sides: Array<'LONG' | 'SHORT'>): {tone: 'up' | 'down' | 'warn' | 'flat'; text: string}[] {
  if (!pulses.length && !sides.length) return []
  const lines: {tone: 'up' | 'down' | 'warn' | 'flat'; text: string}[] = []
  const name = (p: Pulse) => p.symbol.replace(/USDT$/, '')
  const against = pulses.filter(p => p.fit === 'ters')
  if (against.length) lines.push({tone: 'down', text: `${against.map(name).join(', ')} için yorum pozisyonuna ters. Stopun ve planın hâlâ geçerli mi kontrol et.`})
  const warned = pulses.filter(p => p.fit !== 'ters' && p.warnings.length)
  if (warned.length) lines.push({tone: 'warn', text: `${warned.map(name).join(', ')} için dikkat uyarısı var: ${warned[0].warnings[0]}`})
  if (sides.length >= 3 && sides.every(side => side === sides[0])) lines.push({tone: 'warn', text: `Tüm ${sides.length} pozisyonun ${sides[0]}: piyasa tersine dönerse hepsi aynı anda zarar eder.`})
  const event = topEvent(events)
  if (event && event.level !== 'info') lines.push({tone: event.level === 'danger' ? 'down' : 'warn', text: event.text})
  if (!lines.length && pulses.length) lines.push({tone: 'up', text: 'Yorumlar pozisyonlarınla çelişmiyor, aktif uyarı yok.'})
  return lines.slice(0, 4)
}

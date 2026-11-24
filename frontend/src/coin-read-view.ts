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
export type CoinRead = {symbol: string; price: number; timeframes: ReadTf[]; levels: {support: ReadLevel[]; resistance: ReadLevel[]}; futures: ReadFutures | null; summary: ReadSummary; history: ReadHistory | null; position?: ReadPosition}

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
export type ReadAlarm = {id: number; exchange: 'binance' | 'coinbase'; product_id: string; symbol: string; side: 'LONG' | 'SHORT'; entry: number | null; mode: AlarmMode; min_tf: AlarmTf; active_keys: string[]}
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

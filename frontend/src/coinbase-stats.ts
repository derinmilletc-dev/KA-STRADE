// Coinbase işlem istatistikleri için SAF yardımcılar (ağ yok): biçimlendirme ve "Kais önerisi mi, elle mi" yorumu.
export type Bucket = {trades: number; wins: number; net: string}
export type StatsData = {
  trades: number; wins: number; losses: number; unknown: number; win_rate: number | null
  net: string; gross: string; fees: string; avg_win: string | null; avg_loss: string | null; profit_factor: number | null
  best: {coin: string; net: string} | null; worst: {coin: string; net: string} | null
  by_coin: Array<Bucket & {coin: string}>
  by_exit: {target: number; stop: number; manual_close: number}
  by_source: {kais: Bucket; manual: Bucket; unknown: Bucket}
  insights?: Insights | null
}
export type InsightBlock = {from: number; to: number; win_rate: number; trades: number}
export type Insights = {n: number; enough: boolean; insights: Array<{tone: string; text: string}>; bad_blocks: InsightBlock[]; good_blocks: InsightBlock[]; win_rate?: number
  by_coin?: Array<{coin: string; n: number; win_rate: number; net: number}>; avg_win?: number | null; avg_loss?: number | null}

const MIN_COMPARE = 3

export const parseMoney = (text: string | null | undefined): number | null => { const n = Number(text); return text != null && text !== '' && Number.isFinite(n) ? n : null }
export const fmtMoney = (value: number | null, sign = true): string =>
  value === null ? '—' : `${sign && value > 0 ? '+' : ''}${value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USD`
export const moneyTone = (value: number | null): 'up' | 'down' | undefined => value === null || value === 0 ? undefined : value > 0 ? 'up' : 'down'

/** Gelen veri bozuksa ya da hiç kapanan işlem yoksa null: ekranda kart gösterilmez. */
export function readStats(raw: unknown): StatsData | null {
  if (raw === null || typeof raw !== 'object') return null
  const data = raw as Partial<StatsData>
  if (typeof data.trades !== 'number' || data.trades < 1 || typeof data.net !== 'string' || !data.by_source || !data.by_exit) return null
  return data as StatsData
}

export const rateOf = (bucket: Bucket): number | null => bucket.trades > 0 ? Math.round(bucket.wins / bucket.trades * 100) : null

/** Kais önerisiyle açılan işlemlerle elle açılanları karşılaştırır. Her grupta en az 3 kapanmış işlem yoksa karşılaştırma yapılmaz (az veriden yargı çıkarılmaz). */
export function sourceVerdict(source: StatsData['by_source']): {ready: boolean; text: string} {
  const {kais, manual} = source
  if (kais.trades < MIN_COMPARE || manual.trades < MIN_COMPARE) {
    const need = []
    if (kais.trades < MIN_COMPARE) need.push(`Kais önerisiyle ${MIN_COMPARE - kais.trades} işlem daha`)
    if (manual.trades < MIN_COMPARE) need.push(`elle ${MIN_COMPARE - manual.trades} işlem daha`)
    return {ready: false, text: `Karşılaştırma için ${need.join(' ve ')} kapanmalı.`}
  }
  const kRate = rateOf(kais) ?? 0, mRate = rateOf(manual) ?? 0
  const kAvg = (parseMoney(kais.net) ?? 0) / kais.trades, mAvg = (parseMoney(manual.net) ?? 0) / manual.trades
  const better = kAvg > mAvg ? 'Kais önerisiyle açtıkların' : 'Elle açtıkların'
  return {ready: true, text: `${better} işlem başına daha iyi sonuç veriyor (kazanma oranı Kais %${kRate}, elle %${mRate}).`}
}

export function exitText(exit: StatsData['by_exit']): string {
  const parts: string[] = []
  if (exit.target) parts.push(`${exit.target} hedef`)
  if (exit.stop) parts.push(`${exit.stop} stop`)
  if (exit.manual_close) parts.push(`${exit.manual_close} elle kapatma`)
  return parts.join(' · ')
}


/** Şu an (Türkiye saati) kullanıcının geçmişte zayıf kaldığı saat bloğundaysa kısa uyarı; değilse null. Yalnız yeterli örnekte (sunucu karar verir). */
export function weakHourNote(insights: Insights | null | undefined, now: Date): string | null {
  if (!insights || !insights.enough || !insights.bad_blocks.length) return null
  const hour = (now.getUTCHours() + 3) % 24
  const block = insights.bad_blocks.find(item => hour >= item.from && hour < item.to)
  return block ? `Şu an senin zayıf saatlerinden biri (TSİ ${String(block.from).padStart(2, '0')}-${String(block.to).padStart(2, '0')}): bu aralıkta ${block.trades} işlemin %${block.win_rate}'i kazandı.` : null
}

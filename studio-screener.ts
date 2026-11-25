// Tarayıcı: kullanıcının kurallarını birçok sembolün son kapanmış mumuna uygular. Veri çekme dışarıdan verilir (test edilebilir).
import {allSignals, type Rule} from './studio-rules'
import type {IndicatorCandle} from './studio-indicators'

export type ScanRow = {symbol: string; match: boolean; price: number; changePct: number; error?: undefined} | {symbol: string; error: string; match?: undefined}
export type Fetcher = (symbol: string, signal: AbortSignal) => Promise<IndicatorCandle[]>
export const MAX_SCAN_SYMBOLS = 40
export const MIN_SCAN_CANDLES = 30

/** Son mum hâlâ oluşuyor olabileceği için sinyal son KAPANMIŞ mumda aranır (sondan bir önceki). */
export function scanCandles(symbol: string, candles: readonly IndicatorCandle[], rules: readonly Rule[]): ScanRow {
  if (candles.length < MIN_SCAN_CANDLES) return {symbol, error: 'Yeterli veri yok'}
  const signals = allSignals(candles, rules)
  const index = candles.length - 2
  const last = candles[candles.length - 1], base = candles[Math.max(0, candles.length - 25)]
  return {symbol, match: signals[index] === true, price: last.close, changePct: base.close > 0 ? (last.close / base.close - 1) * 100 : 0}
}

export async function scanMarkets(symbols: readonly string[], rules: readonly Rule[], fetcher: Fetcher, options: {concurrency?: number; signal: AbortSignal; onRow?: (row: ScanRow, done: number, total: number) => void}): Promise<ScanRow[]> {
  const list = [...new Set(symbols)].slice(0, MAX_SCAN_SYMBOLS)
  const rows: ScanRow[] = []
  let next = 0, done = 0
  const worker = async () => {
    while (!options.signal.aborted) {
      const symbol = list[next++]
      if (symbol === undefined) return
      let row: ScanRow
      try { row = scanCandles(symbol, await fetcher(symbol, options.signal), rules) } catch { row = {symbol, error: 'Veri alınamadı'} }
      if (options.signal.aborted) return
      rows.push(row); done++
      options.onRow?.(row, done, list.length)
    }
  }
  await Promise.all(Array.from({length: Math.max(1, Math.min(options.concurrency ?? 4, 8))}, worker))
  return rows
}

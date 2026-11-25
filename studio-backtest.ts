// Strateji backtest: grafikteki mumlar üzerinde kural tabanlı simülasyon. Saf mantık, gerçek emir yok.
// Dürüstlük kuralları: sinyal mum kapanışında görülür, işlem SONRAKİ mumun açılışından girer (ileriye bakma yok);
// aynı mumda hem stop hem hedef değerse kötü senaryo (stop) sayılır; komisyon her giriş ve çıkışta uygulanır.
import {allSignals, anySignals, type Rule} from './studio-rules'
import type {IndicatorCandle} from './studio-indicators'

export type Side = 'long' | 'short'
export type Strategy = {side: Side; entry: Rule[]; exit: Rule[]; stopPct: number; takePct: number; feePct: number}
export type Trade = {side: Side; entryIndex: number; exitIndex: number; entryPrice: number; exitPrice: number; returnPct: number; reason: 'stop' | 'take' | 'kural' | 'son'}
export type BacktestResult = {
  trades: Trade[]; equity: number[]; netPct: number; winRate: number; profitFactor: number | null; maxDrawdownPct: number; avgTradePct: number; buyHoldPct: number; warning: string
}
export const MIN_CANDLES = 50
export const DEFAULT_STRATEGY: Strategy = {side: 'long', entry: [], exit: [], stopPct: 3, takePct: 6, feePct: 0.05}

const pct = (value: unknown, fallback: number, max: number) => { const n = Number(value); return Number.isFinite(n) && n >= 0 && n <= max ? n : fallback }
export const cleanStrategy = (value: Partial<Strategy>): Strategy => ({
  side: value.side === 'short' ? 'short' : 'long', entry: value.entry ?? [], exit: value.exit ?? [],
  stopPct: pct(value.stopPct, 3, 50), takePct: pct(value.takePct, 6, 500), feePct: pct(value.feePct, 0.05, 2),
})

export function runBacktest(candles: readonly IndicatorCandle[], input: Partial<Strategy>): BacktestResult {
  const strategy = cleanStrategy(input)
  const empty: BacktestResult = {trades: [], equity: [], netPct: 0, winRate: 0, profitFactor: null, maxDrawdownPct: 0, avgTradePct: 0, buyHoldPct: 0, warning: ''}
  if (candles.length < MIN_CANDLES) return {...empty, warning: `En az ${MIN_CANDLES} mum gerekir.`}
  if (!strategy.entry.length) return {...empty, warning: 'Giriş kuralı ekle.'}
  const enter = allSignals(candles, strategy.entry)
  const leave = strategy.exit.length ? anySignals(candles, strategy.exit) : candles.map(() => false)
  const dir = strategy.side === 'long' ? 1 : -1
  const fee = strategy.feePct / 100
  const trades: Trade[] = []
  const equity: number[] = new Array(candles.length).fill(1)
  let balance = 1
  let open: {index: number; price: number} | null = null
  const close = (index: number, rawPrice: number, reason: Trade['reason']) => {
    if (!open) return
    const gross = dir * (rawPrice - open.price) / open.price
    const net = gross - 2 * fee
    balance *= 1 + net
    trades.push({side: strategy.side, entryIndex: open.index, exitIndex: index, entryPrice: open.price, exitPrice: rawPrice, returnPct: net * 100, reason})
    open = null
  }
  for (let i = 0; i < candles.length; i++) {
    const candle = candles[i]
    if (open && i >= open.index) {
      const stop = strategy.stopPct > 0 ? open.price * (1 - dir * strategy.stopPct / 100) : null
      const take = strategy.takePct > 0 ? open.price * (1 + dir * strategy.takePct / 100) : null
      const stopHit = stop !== null && (dir === 1 ? candle.low <= stop : candle.high >= stop)
      const takeHit = take !== null && (dir === 1 ? candle.high >= take : candle.low <= take)
      if (stopHit && stop !== null) close(i, dir === 1 ? Math.min(stop, candle.open) : Math.max(stop, candle.open), 'stop')
      else if (takeHit && take !== null) close(i, take, 'take')
      else if (leave[i]) close(i, candle.close, 'kural')
    }
    // Sinyal bu mumun kapanışında görülür; giriş sonraki mumun açılışı.
    if (!open && enter[i] && i + 1 < candles.length) open = {index: i + 1, price: candles[i + 1].open}
    const mark = open && i >= open.index ? balance * (1 + dir * (candle.close - open.price) / open.price - fee) : balance
    equity[i] = mark
  }
  if (open) close(candles.length - 1, candles[candles.length - 1].close, 'son')
  equity[candles.length - 1] = balance
  let peak = 1, maxDrawdown = 0
  for (const value of equity) { peak = Math.max(peak, value); maxDrawdown = Math.max(maxDrawdown, (peak - value) / peak) }
  const wins = trades.filter(trade => trade.returnPct > 0), losses = trades.filter(trade => trade.returnPct <= 0)
  const grossWin = wins.reduce((sum, trade) => sum + trade.returnPct, 0), grossLoss = -losses.reduce((sum, trade) => sum + trade.returnPct, 0)
  return {
    trades, equity, netPct: (balance - 1) * 100, winRate: trades.length ? wins.length / trades.length * 100 : 0,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : null, maxDrawdownPct: maxDrawdown * 100,
    avgTradePct: trades.length ? trades.reduce((sum, trade) => sum + trade.returnPct, 0) / trades.length : 0,
    buyHoldPct: (candles[candles.length - 1].close / candles[0].close - 1) * 100,
    warning: trades.length === 0 ? 'Bu kurallar bu aralıkta hiç işlem üretmedi.' : trades.length < 10 ? 'İşlem sayısı az (10\'dan az); sonuç güvenilir bir kanıt değildir.' : '',
  }
}

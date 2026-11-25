// Strateji backtest: grafikteki mumlar üzerinde kural tabanlı simülasyon. Saf mantık, gerçek emir yok.
// Akış: Giriş → Onay → Çıkış → SL/TP → Pozisyon boyutu → Metrikler.
// Dürüstlük kuralları: sinyal mum kapanışında görülür, işlem SONRAKİ mumun açılışından girer (ileriye bakma yok);
// aynı mumda hem stop hem hedef değerse kötü senaryo (stop) sayılır; komisyon her giriş ve çıkışta (pozisyon büyüklüğü üzerinden) uygulanır.
import {allSignals, anySignals, type Rule} from './studio-rules'
import {atr, type IndicatorCandle} from './studio-indicators'

export type Side = 'long' | 'short'
export type LevelMode = 'pct' | 'atr'
export type SizingMode = 'all' | 'fixed' | 'risk'
export type Strategy = {
  side: Side
  entry: Rule[]
  /** Onay: giriş sinyalinden sonra (aynı mum ya da en çok `confirmBars` mum içinde) bu kurallar da sağlanmalı. Boşsa onay yok. */
  confirm: Rule[]
  confirmBars: number
  exit: Rule[]
  stopMode: LevelMode; stopPct: number; stopAtr: number
  takeMode: LevelMode; takePct: number; takeAtr: number
  /** İz süren stop (%): en iyi fiyattan bu kadar geri çekilince çıkar. 0 = kapalı. */
  trailPct: number
  /** En fazla bu kadar mum açık kalır. 0 = sınırsız. */
  maxBars: number
  sizing: SizingMode; sizingValue: number; maxLeverage: number
  capital: number
  feePct: number
}
export type ExitReason = 'stop' | 'take' | 'trail' | 'kural' | 'süre' | 'son'
export type Trade = {
  side: Side; entryIndex: number; exitIndex: number; entryPrice: number; exitPrice: number
  /** Fiyat üzerinden net getiri (komisyon dahil, pozisyon büyüklüğünden bağımsız). */
  returnPct: number
  /** Hesaba etkisi: returnPct × pozisyon oranı. */
  equityPct: number
  sizePct: number
  /** Risk katı (R): getiri / ilk stop mesafesi. Stop yoksa null. */
  r: number | null
  bars: number
  reason: ExitReason
}
export type BacktestResult = {
  trades: Trade[]; equity: number[]; netPct: number; winRate: number; profitFactor: number | null; maxDrawdownPct: number; avgTradePct: number; buyHoldPct: number; warning: string
  finalCapital: number
  expectancyR: number | null
  payoff: number | null
  avgWinPct: number; avgLossPct: number
  bestPct: number; worstPct: number
  maxConsecLosses: number
  exposurePct: number
  avgBars: number
  sharpe: number | null
}
export const MIN_CANDLES = 50
export const DEFAULT_STRATEGY: Strategy = {
  side: 'long', entry: [], confirm: [], confirmBars: 0, exit: [],
  stopMode: 'pct', stopPct: 3, stopAtr: 2, takeMode: 'pct', takePct: 6, takeAtr: 4,
  trailPct: 0, maxBars: 0, sizing: 'all', sizingValue: 100, maxLeverage: 1, capital: 1000, feePct: 0.05,
}

const num = (value: unknown, fallback: number, min: number, max: number) => { const n = Number(value); return Number.isFinite(n) && n >= min && n <= max ? n : fallback }
export const cleanStrategy = (value: Partial<Strategy>): Strategy => {
  const d = DEFAULT_STRATEGY
  const sizing: SizingMode = value.sizing === 'fixed' || value.sizing === 'risk' ? value.sizing : 'all'
  return {
    side: value.side === 'short' ? 'short' : 'long',
    entry: value.entry ?? [], confirm: value.confirm ?? [], confirmBars: Math.round(num(value.confirmBars, 0, 0, 20)), exit: value.exit ?? [],
    stopMode: value.stopMode === 'atr' ? 'atr' : 'pct', stopPct: num(value.stopPct, d.stopPct, 0, 50), stopAtr: num(value.stopAtr, d.stopAtr, 0, 20),
    takeMode: value.takeMode === 'atr' ? 'atr' : 'pct', takePct: num(value.takePct, d.takePct, 0, 500), takeAtr: num(value.takeAtr, d.takeAtr, 0, 50),
    trailPct: num(value.trailPct, 0, 0, 50), maxBars: Math.round(num(value.maxBars, 0, 0, 5000)),
    sizing, sizingValue: num(value.sizingValue, sizing === 'risk' ? 1 : 100, 0.01, 100), maxLeverage: num(value.maxLeverage, 1, 1, 10),
    capital: num(value.capital, d.capital, 1, 1e9), feePct: num(value.feePct, d.feePct, 0, 2),
  }
}

type Open = {index: number; price: number; stopDist: number | null; stop: number | null; take: number | null; fraction: number; extreme: number}

export function runBacktest(candles: readonly IndicatorCandle[], input: Partial<Strategy>, options: {barsPerYear?: number} = {}): BacktestResult {
  const strategy = cleanStrategy(input)
  const empty: BacktestResult = {
    trades: [], equity: [], netPct: 0, winRate: 0, profitFactor: null, maxDrawdownPct: 0, avgTradePct: 0, buyHoldPct: 0, warning: '',
    finalCapital: strategy.capital, expectancyR: null, payoff: null, avgWinPct: 0, avgLossPct: 0, bestPct: 0, worstPct: 0, maxConsecLosses: 0, exposurePct: 0, avgBars: 0, sharpe: null,
  }
  if (candles.length < MIN_CANDLES) return {...empty, warning: `En az ${MIN_CANDLES} mum gerekir.`}
  if (!strategy.entry.length) return {...empty, warning: 'Giriş kuralı ekle.'}
  const enter = allSignals(candles, strategy.entry)
  const confirmed = strategy.confirm.length ? allSignals(candles, strategy.confirm) : null
  const leave = strategy.exit.length ? anySignals(candles, strategy.exit) : candles.map(() => false)
  const atrSeries = strategy.stopMode === 'atr' || strategy.takeMode === 'atr' ? atr(candles, 14) : []
  const dir = strategy.side === 'long' ? 1 : -1
  const fee = strategy.feePct / 100
  const trades: Trade[] = []
  const equity: number[] = new Array(candles.length).fill(1)
  const notes = new Set<string>()
  let balance = 1
  let open: Open | null = null
  let pending: {deadline: number} | null = null
  let barsInMarket = 0

  const close = (index: number, rawPrice: number, reason: ExitReason) => {
    if (!open) return
    const gross = dir * (rawPrice - open.price) / open.price
    const net = gross - 2 * fee
    const equityNet = net * open.fraction
    balance *= 1 + equityNet
    trades.push({
      side: strategy.side, entryIndex: open.index, exitIndex: index, entryPrice: open.price, exitPrice: rawPrice,
      returnPct: net * 100, equityPct: equityNet * 100, sizePct: open.fraction * 100, r: open.stopDist ? net / open.stopDist : null, bars: index - open.index + 1, reason,
    })
    open = null
  }
  /** Girişte stop/hedef mesafesi ve pozisyon oranı bir kez hesaplanır (sonradan değişmez). */
  const openTrade = (index: number): Open => {
    const price = candles[index].open
    const signalAtr = atrSeries[index - 1] ?? null
    const stopDist = strategy.stopMode === 'atr'
      ? (signalAtr !== null && strategy.stopAtr > 0 ? strategy.stopAtr * signalAtr / price : null)
      : (strategy.stopPct > 0 ? strategy.stopPct / 100 : null)
    const takeDist = strategy.takeMode === 'atr'
      ? (signalAtr !== null && strategy.takeAtr > 0 ? strategy.takeAtr * signalAtr / price : null)
      : (strategy.takePct > 0 ? strategy.takePct / 100 : null)
    let fraction = 1
    if (strategy.sizing === 'fixed') fraction = strategy.sizingValue / 100
    else if (strategy.sizing === 'risk') {
      if (stopDist) fraction = Math.min(strategy.maxLeverage, strategy.sizingValue / 100 / stopDist)
      else notes.add('Risk bazlı boyut için stop gerekir; stop olmayan işlemlerde tüm sermaye kullanıldı.')
    }
    return {index, price, stopDist, stop: stopDist ? price * (1 - dir * stopDist) : null, take: takeDist ? price * (1 + dir * takeDist) : null, fraction: Math.max(0, Math.min(strategy.maxLeverage, fraction)), extreme: price}
  }

  for (let i = 0; i < candles.length; i++) {
    const candle = candles[i]
    if (open && i >= open.index) {
      barsInMarket++
      // İz süren stop yalnız ÖNCEKİ mumlara kadar görülen en iyi fiyattan hesaplanır (ileriye bakma yok).
      const trail = strategy.trailPct > 0 ? open.extreme * (1 - dir * strategy.trailPct / 100) : null
      let stop = open.stop, stopReason: ExitReason = 'stop'
      if (trail !== null && (stop === null || (dir === 1 ? trail > stop : trail < stop))) { stop = trail; stopReason = 'trail' }
      const take = open.take
      const stopHit = stop !== null && (dir === 1 ? candle.low <= stop : candle.high >= stop)
      const takeHit = take !== null && (dir === 1 ? candle.high >= take : candle.low <= take)
      if (stopHit && stop !== null) close(i, dir === 1 ? Math.min(stop, candle.open) : Math.max(stop, candle.open), stopReason)
      else if (takeHit && take !== null) close(i, take, 'take')
      else if (leave[i]) close(i, candle.close, 'kural')
      else if (strategy.maxBars > 0 && i - open.index + 1 >= strategy.maxBars) close(i, candle.close, 'süre')
      else open.extreme = dir === 1 ? Math.max(open.extreme, candle.high) : Math.min(open.extreme, candle.low)
    }
    // Sinyal (ve varsa onay) bu mumun kapanışında görülür; giriş sonraki mumun açılışı.
    if (!open && i + 1 < candles.length) {
      let fire = false
      if (!confirmed) fire = enter[i]
      else if (enter[i] && confirmed[i]) { fire = true; pending = null }
      else if (enter[i] && strategy.confirmBars > 0) pending = {deadline: i + strategy.confirmBars}
      else if (pending && confirmed[i]) { fire = true; pending = null }
      if (pending && i >= pending.deadline) pending = null
      if (fire) { pending = null; open = openTrade(i + 1) }
    }
    const current: Open | null = open
    equity[i] = current && i >= current.index ? balance * (1 + current.fraction * (dir * (candle.close - current.price) / current.price - fee)) : balance
  }
  if (open) close(candles.length - 1, candles[candles.length - 1].close, 'son')
  equity[candles.length - 1] = balance

  let peak = 1, maxDrawdown = 0
  for (const value of equity) { peak = Math.max(peak, value); maxDrawdown = Math.max(maxDrawdown, (peak - value) / peak) }
  const wins = trades.filter(trade => trade.equityPct > 0), losses = trades.filter(trade => trade.equityPct <= 0)
  const grossWin = wins.reduce((sum, trade) => sum + trade.equityPct, 0), grossLoss = -losses.reduce((sum, trade) => sum + trade.equityPct, 0)
  const avgWin = wins.length ? grossWin / wins.length : 0, avgLoss = losses.length ? grossLoss / losses.length : 0
  const rs = trades.map(trade => trade.r).filter((value): value is number => value !== null)
  let streak = 0, maxStreak = 0
  for (const trade of trades) { streak = trade.equityPct <= 0 ? streak + 1 : 0; maxStreak = Math.max(maxStreak, streak) }
  // Sharpe: mum başı sermaye getirilerinin ortalaması / sapması, yıllık mum sayısıyla ölçeklenir (verilmediyse hesaplanmaz).
  let sharpe: number | null = null
  if (options.barsPerYear && options.barsPerYear > 0 && trades.length >= 2) {
    const returns = equity.slice(1).map((value, i) => value / equity[i] - 1)
    const mean = returns.reduce((sum, v) => sum + v, 0) / returns.length
    const variance = returns.reduce((sum, v) => sum + (v - mean) ** 2, 0) / returns.length
    sharpe = variance > 0 ? mean / Math.sqrt(variance) * Math.sqrt(options.barsPerYear) : null
  }
  const warning = [
    trades.length === 0 ? 'Bu kurallar bu aralıkta hiç işlem üretmedi.' : trades.length < 10 ? 'İşlem sayısı az (10\'dan az); sonuç güvenilir bir kanıt değildir.' : '',
    ...notes,
  ].filter(Boolean).join(' ')
  return {
    trades, equity, netPct: (balance - 1) * 100, winRate: trades.length ? wins.length / trades.length * 100 : 0,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : null, maxDrawdownPct: maxDrawdown * 100,
    avgTradePct: trades.length ? trades.reduce((sum, trade) => sum + trade.equityPct, 0) / trades.length : 0,
    buyHoldPct: (candles[candles.length - 1].close / candles[0].close - 1) * 100,
    finalCapital: strategy.capital * balance,
    expectancyR: rs.length ? rs.reduce((sum, v) => sum + v, 0) / rs.length : null,
    payoff: avgLoss > 0 ? avgWin / avgLoss : null, avgWinPct: avgWin, avgLossPct: avgLoss,
    bestPct: trades.length ? Math.max(...trades.map(trade => trade.equityPct)) : 0, worstPct: trades.length ? Math.min(...trades.map(trade => trade.equityPct)) : 0,
    maxConsecLosses: maxStreak, exposurePct: barsInMarket / candles.length * 100,
    avgBars: trades.length ? trades.reduce((sum, trade) => sum + trade.bars, 0) / trades.length : 0, sharpe, warning,
  }
}

/** Aşırı uydurma (overfit) kontrolü: aynı kurallar verinin ilk %70'inde ve son %30'unda ayrı çalıştırılır. */
export function splitCheck(candles: readonly IndicatorCandle[], input: Partial<Strategy>, options: {barsPerYear?: number} = {}) {
  const cut = Math.floor(candles.length * .7)
  if (cut < MIN_CANDLES || candles.length - cut < MIN_CANDLES) return null
  const early = runBacktest(candles.slice(0, cut), input, options), late = runBacktest(candles.slice(cut), input, options)
  return {early, late, consistent: early.netPct > 0 === late.netPct > 0}
}

export const INTERVAL_MINUTES: Record<string, number> = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '6h': 360, '8h': 480, '12h': 720, '1d': 1440, '3d': 4320, '1w': 10080}
export const barsPerYear = (interval: string) => { const minutes = INTERVAL_MINUTES[interval]; return minutes ? 365 * 24 * 60 / minutes : undefined }

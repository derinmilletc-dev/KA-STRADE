// Kural motoru: backtest ve tarayıcı aynı kuralları kullanır. Saf mantık (React yok); girişler temizlenir, hatalı kural asla sinyal üretmez.
import {atr, bollinger, ema, macd, rsi, sma, type IndicatorCandle, type Series} from './studio-indicators'

export type PriceField = 'close' | 'open' | 'high' | 'low' | 'volume'
export type Operand =
  | {kind: 'price'; field: PriceField}
  | {kind: 'sma' | 'ema' | 'rsi' | 'volsma' | 'atr' | 'hh' | 'll'; period: number}
  | {kind: 'macd' | 'macdsig' | 'bbup' | 'bblow'}
  | {kind: 'num'; value: number}
export const OPERATORS = [
  {id: '>', label: 'büyük'}, {id: '<', label: 'küçük'}, {id: 'crossUp', label: 'yukarı keser'}, {id: 'crossDown', label: 'aşağı keser'},
] as const
export type Operator = typeof OPERATORS[number]['id']
export type Rule = {left: Operand; op: Operator; right: Operand}

const FIELDS: readonly PriceField[] = ['close', 'open', 'high', 'low', 'volume']
const OPS = new Set<string>(OPERATORS.map(item => item.id))
export const MAX_RULES = 6
export const MAX_PERIOD = 400

export function sanitizeOperand(value: unknown): Operand | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  if (item.kind === 'price') return FIELDS.includes(item.field as PriceField) ? {kind: 'price', field: item.field as PriceField} : null
  if (item.kind === 'macd' || item.kind === 'macdsig' || item.kind === 'bbup' || item.kind === 'bblow') return {kind: item.kind}
  if (item.kind === 'sma' || item.kind === 'ema' || item.kind === 'rsi' || item.kind === 'volsma' || item.kind === 'atr' || item.kind === 'hh' || item.kind === 'll') {
    const period = Number(item.period)
    return Number.isInteger(period) && period >= 2 && period <= MAX_PERIOD ? {kind: item.kind, period} : null
  }
  if (item.kind === 'num') { const number = Number(item.value); return Number.isFinite(number) && Math.abs(number) < 1e12 ? {kind: 'num', value: number} : null }
  return null
}
export function sanitizeRule(value: unknown): Rule | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const left = sanitizeOperand(item.left), right = sanitizeOperand(item.right)
  return left && right && typeof item.op === 'string' && OPS.has(item.op) ? {left, op: item.op as Operator, right} : null
}
export const sanitizeRules = (value: unknown): Rule[] => Array.isArray(value) ? value.map(sanitizeRule).filter((rule): rule is Rule => rule !== null).slice(0, MAX_RULES) : []

export function operandText(operand: Operand): string {
  if (operand.kind === 'price') return ({close: 'Kapanış', open: 'Açılış', high: 'Yüksek', low: 'Düşük', volume: 'Hacim'})[operand.field]
  if (operand.kind === 'num') return String(operand.value)
  if (operand.kind === 'macd') return 'MACD'
  if (operand.kind === 'macdsig') return 'MACD sinyal'
  if (operand.kind === 'bbup') return 'Bollinger üst'
  if (operand.kind === 'bblow') return 'Bollinger alt'
  if (operand.kind === 'volsma') return `Hacim SMA(${operand.period})`
  if (operand.kind === 'hh') return `En yüksek(${operand.period})`
  if (operand.kind === 'll') return `En düşük(${operand.period})`
  return 'period' in operand ? `${operand.kind.toUpperCase()}(${operand.period})` : operand.kind
}
const OPERAND_EN: Record<string, string> = {close: 'Close', open: 'Open', high: 'High', low: 'Low', volume: 'Volume'}
export function operandTextEn(operand: Operand): string {
  if (operand.kind === 'price') return OPERAND_EN[operand.field]
  if (operand.kind === 'num') return String(operand.value)
  if (operand.kind === 'macd') return 'MACD'
  if (operand.kind === 'macdsig') return 'MACD signal'
  if (operand.kind === 'bbup') return 'Bollinger upper'
  if (operand.kind === 'bblow') return 'Bollinger lower'
  if (operand.kind === 'volsma') return `Volume SMA(${operand.period})`
  if (operand.kind === 'hh') return `Highest(${operand.period})`
  if (operand.kind === 'll') return `Lowest(${operand.period})`
  return 'period' in operand ? `${operand.kind.toUpperCase()}(${operand.period})` : operand.kind
}
const OPERATOR_EN: Record<string, string> = {'>': 'is above', '<': 'is below', crossUp: 'crosses above', crossDown: 'crosses below'}
/** İngilizce kural cümlesi: "Close crosses above EMA(50)". */
export const ruleTextEn = (rule: Rule) => `${operandTextEn(rule.left)} ${OPERATOR_EN[rule.op] ?? ''} ${operandTextEn(rule.right)}`.replace(/\s+/g, ' ').trim()
export const ruleText = (rule: Rule) => `${operandText(rule.left)} ${operandText(rule.right)} ${OPERATORS.find(item => item.id === rule.op)?.label ?? ''}`

export function operandSeries(candles: readonly IndicatorCandle[], operand: Operand, cache?: Map<string, Series>): Series {
  const key = JSON.stringify(operand)
  const hit = cache?.get(key)
  if (hit) return hit
  let out: Series
  if (operand.kind === 'num') out = candles.map(() => operand.value)
  else if (operand.kind === 'price') out = candles.map(candle => candle[operand.field])
  else if (operand.kind === 'macd' || operand.kind === 'macdsig') { const m = macd(candles.map(candle => candle.close)); out = operand.kind === 'macd' ? m.macd : m.signal }
  else if (operand.kind === 'bbup' || operand.kind === 'bblow') { const b = bollinger(candles.map(candle => candle.close)); out = operand.kind === 'bbup' ? b.upper : b.lower }
  else if (operand.kind === 'atr') out = atr(candles, operand.period)
  else if (operand.kind === 'volsma') out = sma(candles.map(candle => candle.volume), operand.period)
  else if (operand.kind === 'hh' || operand.kind === 'll') {
    // Önceki N mumun en yükseği/en düşüğü (mevcut mum dahil değil): kırılım kuralları ileriye bakmaz.
    const high = operand.kind === 'hh'
    out = candles.map((_, i) => {
      if (i < operand.period) return null
      let best = high ? -Infinity : Infinity
      for (let j = i - operand.period; j < i; j++) best = high ? Math.max(best, candles[j].high) : Math.min(best, candles[j].low)
      return best
    })
  }
  else {
    const closes = candles.map(candle => candle.close)
    out = (() => { const period = 'period' in operand ? operand.period : 14; return operand.kind === 'sma' ? sma(closes, period) : operand.kind === 'ema' ? ema(closes, period) : rsi(closes, period) })()
  }
  cache?.set(key, out)
  return out
}

/** Her mum için kuralın sağlanıp sağlanmadığı. Hesaplanamayan (ısınma) mumlarda false. Kesişim, bir önceki mumla karşılaştırılır. */
export function ruleSignals(candles: readonly IndicatorCandle[], rule: Rule, cache?: Map<string, Series>): boolean[] {
  const left = operandSeries(candles, rule.left, cache), right = operandSeries(candles, rule.right, cache)
  return candles.map((_, i) => {
    const a = left[i], b = right[i]
    if (a === null || b === null || a === undefined || b === undefined) return false
    if (rule.op === '>') return a > b
    if (rule.op === '<') return a < b
    const pa = left[i - 1], pb = right[i - 1]
    if (i === 0 || pa === null || pb === null || pa === undefined || pb === undefined) return false
    return rule.op === 'crossUp' ? pa <= pb && a > b : pa >= pb && a < b
  })
}
/** Tüm kurallar birlikte (VE). Kural listesi boşsa hiçbir mumda sinyal yok. */
export function allSignals(candles: readonly IndicatorCandle[], rules: readonly Rule[]): boolean[] {
  if (!rules.length) return candles.map(() => false)
  const cache = new Map<string, Series>()
  const each = rules.map(rule => ruleSignals(candles, rule, cache))
  return candles.map((_, i) => each.every(signals => signals[i]))
}
export const anySignals = (candles: readonly IndicatorCandle[], rules: readonly Rule[]): boolean[] => {
  if (!rules.length) return candles.map(() => false)
  const cache = new Map<string, Series>()
  const each = rules.map(rule => ruleSignals(candles, rule, cache))
  return candles.map((_, i) => each.some(signals => signals[i]))
}

export type Preset = {id: string; label: string; entry: Rule[]; exit: Rule[]; confirm?: Rule[]}
const P = (field: PriceField): Operand => ({kind: 'price', field})
const N = (value: number): Operand => ({kind: 'num', value})
export const PRESETS: readonly Preset[] = [
  {id: 'rsi', label: 'RSI aşırı satım → al', entry: [{left: {kind: 'rsi', period: 14}, op: 'crossUp', right: N(30)}], exit: [{left: {kind: 'rsi', period: 14}, op: '>', right: N(60)}]},
  {id: 'ema', label: 'EMA 20 / 50 kesişimi', entry: [{left: {kind: 'ema', period: 20}, op: 'crossUp', right: {kind: 'ema', period: 50}}], exit: [{left: {kind: 'ema', period: 20}, op: 'crossDown', right: {kind: 'ema', period: 50}}]},
  {id: 'trend', label: 'Trend takibi (EMA 50 üstü)', entry: [{left: P('close'), op: 'crossUp', right: {kind: 'ema', period: 50}}], exit: [{left: P('close'), op: 'crossDown', right: {kind: 'ema', period: 50}}]},
  {id: 'breakout', label: 'Kırılım (20 mum zirvesi + hacim)', entry: [{left: P('close'), op: '>', right: {kind: 'hh', period: 20}}], confirm: [{left: P('volume'), op: '>', right: {kind: 'volsma', period: 20}}], exit: [{left: P('close'), op: 'crossDown', right: {kind: 'ema', period: 20}}]},
  {id: 'macd', label: 'MACD kesişimi (trend filtreli)', entry: [{left: {kind: 'macd'}, op: 'crossUp', right: {kind: 'macdsig'}}], confirm: [{left: P('close'), op: '>', right: {kind: 'ema', period: 200}}], exit: [{left: {kind: 'macd'}, op: 'crossDown', right: {kind: 'macdsig'}}]},
  {id: 'bb', label: 'Bollinger alt bant dönüşü', entry: [{left: P('close'), op: 'crossUp', right: {kind: 'bblow'}}], exit: [{left: P('close'), op: '>', right: {kind: 'bbup'}}]},
]

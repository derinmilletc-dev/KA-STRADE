// Kural motoru: backtest ve tarayıcı aynı kuralları kullanır. Saf mantık (React yok); girişler temizlenir, hatalı kural asla sinyal üretmez.
import {ema, rsi, sma, type IndicatorCandle, type Series} from './studio-indicators'

export type PriceField = 'close' | 'open' | 'high' | 'low' | 'volume'
export type Operand =
  | {kind: 'price'; field: PriceField}
  | {kind: 'sma' | 'ema' | 'rsi'; period: number}
  | {kind: 'num'; value: number}
export const OPERATORS = [
  {id: '>', label: 'büyük'}, {id: '<', label: 'küçük'}, {id: 'crossUp', label: 'yukarı keser'}, {id: 'crossDown', label: 'aşağı keser'},
] as const
export type Operator = typeof OPERATORS[number]['id']
export type Rule = {left: Operand; op: Operator; right: Operand}

const FIELDS: readonly PriceField[] = ['close', 'open', 'high', 'low', 'volume']
const OPS = new Set<string>(OPERATORS.map(item => item.id))
export const MAX_RULES = 4
export const MAX_PERIOD = 400

export function sanitizeOperand(value: unknown): Operand | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  if (item.kind === 'price') return FIELDS.includes(item.field as PriceField) ? {kind: 'price', field: item.field as PriceField} : null
  if (item.kind === 'sma' || item.kind === 'ema' || item.kind === 'rsi') {
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
  return `${operand.kind.toUpperCase()}(${operand.period})`
}
export const ruleText = (rule: Rule) => `${operandText(rule.left)} ${operandText(rule.right)} ${OPERATORS.find(item => item.id === rule.op)?.label ?? ''}`

export function operandSeries(candles: readonly IndicatorCandle[], operand: Operand, cache?: Map<string, Series>): Series {
  const key = JSON.stringify(operand)
  const hit = cache?.get(key)
  if (hit) return hit
  let out: Series
  if (operand.kind === 'num') out = candles.map(() => operand.value)
  else if (operand.kind === 'price') out = candles.map(candle => candle[operand.field])
  else {
    const closes = candles.map(candle => candle.close)
    out = operand.kind === 'sma' ? sma(closes, operand.period) : operand.kind === 'ema' ? ema(closes, operand.period) : rsi(closes, operand.period)
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

export type Preset = {id: string; label: string; entry: Rule[]; exit: Rule[]}
const P = (field: PriceField): Operand => ({kind: 'price', field})
const N = (value: number): Operand => ({kind: 'num', value})
export const PRESETS: readonly Preset[] = [
  {id: 'rsi', label: 'RSI aşırı satım → al', entry: [{left: {kind: 'rsi', period: 14}, op: 'crossUp', right: N(30)}], exit: [{left: {kind: 'rsi', period: 14}, op: '>', right: N(60)}]},
  {id: 'ema', label: 'EMA 20 / 50 kesişimi', entry: [{left: {kind: 'ema', period: 20}, op: 'crossUp', right: {kind: 'ema', period: 50}}], exit: [{left: {kind: 'ema', period: 20}, op: 'crossDown', right: {kind: 'ema', period: 50}}]},
  {id: 'trend', label: 'Trend takibi (EMA 50 üstü)', entry: [{left: P('close'), op: 'crossUp', right: {kind: 'ema', period: 50}}], exit: [{left: P('close'), op: 'crossDown', right: {kind: 'ema', period: 50}}]},
]

// Binance elle ödeme planları. Plan, ödenen tutardan çıkar (sunucudaki plan_for_amount ile aynı kural).
export type PaidPlan = 'MASTER_MODE' | 'SMART_TRADE'
export const PAID_PLAN_AMOUNT: Record<PaidPlan, string> = {MASTER_MODE: '119.90', SMART_TRADE: '39.90'}
export const PAID_PLAN_NAME: Record<PaidPlan, string> = {MASTER_MODE: 'MASTER MODE', SMART_TRADE: 'SMART TRADE'}
export function planForAmount(amount: unknown): PaidPlan | null {
  return amount === PAID_PLAN_AMOUNT.MASTER_MODE ? 'MASTER_MODE' : amount === PAID_PLAN_AMOUNT.SMART_TRADE ? 'SMART_TRADE' : null
}
export const SMART_TRADE_FEATURES = [
  'Manuel işlem ve Grid · Demo ve LIVE',
  'Analiz Stüdyosu ve gelişmiş analizler',
  'Kais AI asistanı ve AI Analist',
  'Stop, hedef ve pozisyon koruması',
]

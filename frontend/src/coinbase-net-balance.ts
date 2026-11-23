// Tek "Net bakiye" rakamı: ana sayfa, hesap kartı ve istatistik aynı sunucu cevabından (GET /api/coinbase/net-balance) okur.
// Net bakiye = hesaba işlenmiş bakiye + bugünkü gerçekleşen K/Z − bugünkü komisyon. Saf yardımcılar (ağ yok).
export type NetBalance = {balance: number; gross: number; fees: number; netToday: number; netBalance: number; exact: boolean}

const num = (value: unknown): number | null => { const n = typeof value === 'string' || typeof value === 'number' ? Number(value) : NaN; return value !== '' && Number.isFinite(n) ? n : null }

/** Bozuk, eksik ya da henüz güncellenmemiş sunucu cevabında null döner: ekran eski gösterime düşer. */
export function readNetBalance(raw: unknown): NetBalance | null {
  if (raw === null || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  if (data.available !== true) return null
  const balance = num(data.balance), gross = num(data.gross), fees = num(data.fees), netToday = num(data.net_today), netBalance = num(data.net_balance)
  if (balance === null || gross === null || fees === null || netToday === null || netBalance === null) return null
  return {balance, gross, fees, netToday, netBalance, exact: data.exact !== false}
}

export const fmtUsd = (value: number, sign = false): string =>
  `${sign && value > 0 ? '+' : ''}${value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USD`

/** "Bugün net +4,63 (komisyon 1,07 düşüldü)"; bugün kazanç/zarar yoksa null. */
export function todayText(net: NetBalance): string | null {
  if (net.gross === 0) return null
  const fee = net.fees > 0 ? ` (komisyon ${net.fees.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} düşüldü${net.exact ? '' : ', yaklaşık'})` : ''
  return `Bugün net ${fmtUsd(net.netToday, true)}${fee}`
}

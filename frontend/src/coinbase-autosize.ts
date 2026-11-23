// Akıllı kurulum: bakiye, borsa marjini, kayıp limiti ve stop mesafesine göre GEÇERLİ hazır boyutlar (saf hesap; ağ ve emir yok).
// Her seçenek ön kontrolün kurallarına göre kurulur: sözleşme ≥ 1, borsa marjini bakiyeye sığar, kayıp limitini aşmaz, kaldıraç 1x..sınır arasında.
export type Profile = 'careful' | 'balanced' | 'bold'
export type SizeInput = {
  side: 'BUY' | 'SELL'; entry: number; stop: number; target: number | null; contractSize: number
  minMargin: number | null            // 1 sözleşme için borsanın istediği en az teminat (bilinmiyorsa null)
  spendable: number                   // harcanabilir tutar
  maxLossUsd: number | null           // işlem başı kayıp limiti
  dailyLeftUsd: number | null         // bugün kalan zarar hakkı
  leverageMax: number | null          // kendi kaldıraç sınırın
  feeRate: number
}
export type SizeOption = {
  id: Profile; label: string; contracts: number; marginUsd: number; leverage: number; notional: number
  lossUsd: number; gainUsd: number | null; riskPct: number; warn: string | null
}
export type SizeResult = {options: SizeOption[]; blocked: string | null}

const PROFILES: Array<[Profile, string, number]> = [['careful', 'Temkinli', 0.02], ['balanced', 'Dengeli', 0.05], ['bold', 'Atak', 0.10]]
const usd = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
const ok = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0

export function recommendSizes(input: SizeInput): SizeResult {
  const {side, entry, stop, target, contractSize, spendable, feeRate} = input
  if (![entry, stop, contractSize, spendable].every(ok)) return {options: [], blocked: null}
  const risk = side === 'BUY' ? entry - stop : stop - entry
  if (!(risk > 0)) return {options: [], blocked: 'Stop yanlış tarafta: LONG için girişin altında, SHORT için üstünde olmalı.'}
  const notional1 = entry * contractSize
  const fee1 = notional1 * feeRate * 2
  const loss1 = contractSize * risk + fee1
  const reward = target !== null && ok(target) ? (side === 'BUY' ? target - entry : entry - target) : 0
  const gain1 = reward > 0 ? Math.max(0, contractSize * reward - fee1) : null
  const levMax = Math.min(ok(input.leverageMax) && input.leverageMax < 1000 ? input.leverageMax : 20, 20)
  const caps = [input.maxLossUsd, input.dailyLeftUsd].filter((v): v is number => ok(v) && v < 1_000_000)
  const lossCap = caps.length ? Math.min(...caps) : Infinity
  if (loss1 > lossCap) return {options: [], blocked: `Bu stopla 1 sözleşme stopta ~${usd(loss1)} USD kaybettirir; kayıp limitin ${usd(lossCap)} USD. Stopu yaklaştır ya da daha küçük sözleşmeli coin seç.`}
  const perMargin = ok(input.minMargin) ? input.minMargin : notional1 / Math.min(levMax, 5)   // oran bilinmiyorsa 5x varsay; asıl kontrol ön kontrolde
  const byBalance = Math.floor(spendable * 0.97 / perMargin + 1e-9)
  if (byBalance < 1) return {options: [], blocked: `Bu coinde 1 sözleşme için ~${usd(perMargin)} USD teminat gerekir; harcanabilir ${usd(spendable)} USD yetmiyor. Başka coin seç ya da bakiye ekle.`}

  const options: SizeOption[] = []
  for (const [id, label, pct] of PROFILES) {
    const budget = Math.min(spendable * pct, lossCap)
    const contracts = Math.max(1, Math.min(byBalance, Math.floor(budget / loss1 + 1e-9)))
    if (options.some(item => item.contracts === contracts)) continue
    const notional = notional1 * contracts
    const exchange = perMargin * contracts
    const lo = notional / levMax * 1.02                      // kaldıraç sınırını aşmamak için en az teminat
    const hi = Math.min(notional * 0.98, spendable * 0.98)   // kaldıraç 1x'in altına düşmemek ve bakiyeyi aşmamak için en çok teminat
    if (lo > hi) continue
    const marginUsd = Math.round(Math.min(Math.max(Math.min(exchange, notional * 0.98), lo), hi) * 100) / 100
    const lossUsd = loss1 * contracts
    const riskPct = lossUsd / spendable * 100
    options.push({
      id, label, contracts, marginUsd, leverage: notional / marginUsd, notional, lossUsd, gainUsd: gain1 === null ? null : gain1 * contracts, riskPct,
      warn: riskPct > 10 ? `Stopta bakiyenin %${Math.round(riskPct)}'i gider; genelde %5-10 aralığı önerilir.` : null,
    })
  }
  return options.length ? {options, blocked: null} : {options: [], blocked: 'Bu bakiye ve kaldıraç sınırıyla geçerli bir boyut bulunamadı.'}
}

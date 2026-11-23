// Ön kontrol "Düzeltilmeli" dediğinde tek dokunuşla uygun ayarı bulan SAF hesap (ağ yok, emir yok).
// Yalnızca teminat ve sözleşme sayısını değiştirir; stop, hedef ve yön asla değişmez. Düzeltme yine ön kontrolden geçer.
export type AutoFixInput = {
  reasons: string[]
  marginUsd: number            // girilen teminat
  contracts: number | null     // şu an hesaplanan/yazılan sözleşme sayısı
  spendable: number | null     // harcanabilir tutar (alım gücü ile kullanılabilir marjinin küçüğü)
}
export type AutoFix = {label: string; patch: {marginUsd?: string; contracts?: string}} | {label: null; hint: string}

const SAFETY = 0.97   // borsa gerçek marjini ve komisyon payı için küçük tampon
const num = (text: string | undefined): number | null => { const n = Number(text); return text !== undefined && Number.isFinite(n) ? n : null }
const money = (value: number) => String(Math.floor(value * 100) / 100)

export function autoFix(input: AutoFixInput): AutoFix | null {
  const {reasons, marginUsd, contracts, spendable} = input
  let margin: number | null = null
  let count: number | null = null
  let blocked: string | null = null
  const capContracts = (value: number) => { count = count === null ? value : Math.min(count, value) }

  for (const reason of reasons) {
    // Coinbase'in kendi hesabına göre gereken marjin bakiyeyi aşıyor: sözleşmeyi gerçek marjin oranına göre küçült.
    const need = reason.match(/gereken marjin ≈ ([\d.]+) USD/)
    if (need) {
      const total = num(need[1])
      if (total !== null && contracts !== null && contracts >= 1 && spendable !== null && spendable > 0) {
        const fit = Math.floor(spendable * SAFETY / (total / contracts) + 1e-9)
        if (fit >= 1) capContracts(fit)
        else blocked = `Bu üründe 1 sözleşme için yaklaşık ${(total / contracts).toFixed(2)} USD marjin gerekiyor; bakiyen yetmiyor. Bakiye ekle ya da daha küçük sözleşmeli ürün seç.`
      }
      continue
    }
    if (reason.includes('Alım gücü yetersiz')) {
      if (spendable !== null && spendable > 0 && marginUsd > spendable) margin = Math.min(margin ?? Infinity, Math.floor(spendable * 0.98 * 100) / 100)
      continue
    }
    const lev = reason.match(/kaldıraç sınırını aşar \(bu teminatla en fazla (\d+) sözleşme\)/)
    if (lev) { const n = num(lev[1]); if (n !== null && n >= 1) capContracts(n); continue }
    const loss = reason.match(/kayıp .*sınırını aşar \(bu stop ile en fazla (\d+) sözleşme\)/)
    if (loss) { const n = num(loss[1]); if (n !== null && n >= 1) capContracts(n); else blocked = 'Bu stop mesafesiyle 1 sözleşme bile kayıp sınırına sığmıyor. Stopu yaklaştır.'; continue }
    const worst = reason.match(/En kötü kayıp ([\d.]+) USD; sınır ([\d.]+) USD/)
    if (worst) {
      const have = num(worst[1]), cap = num(worst[2])
      if (have !== null && cap !== null && have > 0 && contracts !== null) { const n = Math.floor(contracts * cap / have); if (n >= 1) capContracts(n); else blocked = 'Bu stop mesafesiyle 1 sözleşme bile kayıp sınırına sığmıyor. Stopu yaklaştır.' }
      continue
    }
    // "Bu teminatla 1 sözleşme kaldıracı aşar ... en az X USD teminat (Nx) gerekir": gereken teminata çık (bakiyen yetiyorsa).
    const least = reason.match(/en az ([\d.]+) USD teminat/)
    if (least) {
      const min = num(least[1])
      if (min !== null) {
        const want = Math.ceil(min * 1.03 * 100) / 100
        if (spendable !== null && want <= spendable * 0.98) margin = Math.max(margin ?? 0, want)
        else blocked = `1 sözleşme için en az ${min.toFixed(2)} USD teminat gerekiyor; bakiyen yetmiyor. Bakiye ekle ya da daha küçük sözleşmeli ürün seç.`
      }
    }
  }
  if (margin === null && count === null) return blocked ? {label: null, hint: blocked} : null
  const patch: {marginUsd?: string; contracts?: string} = {}
  if (margin !== null) patch.marginUsd = money(margin)
  if (count !== null) patch.contracts = String(count)
  else if (margin !== null) patch.contracts = ''   // sözleşme sayısı yeni teminat ve kaldıraçtan yeniden hesaplansın
  const parts = [margin !== null ? `teminat ${money(margin)} USD` : null, count !== null ? `${count} sözleşme` : null].filter(Boolean)
  return {label: `Otomatik düzelt: ${parts.join(' · ')}`, patch}
}

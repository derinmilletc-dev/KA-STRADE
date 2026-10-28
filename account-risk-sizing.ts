export function preferredRiskMargin(input: {wallet: number; available: number; entry: number; stop: number; leverage: number; percent: number}): {margin: number | null; reason: string} {
  if (![input.wallet, input.available, input.entry, input.stop, input.leverage, input.percent].every(value => Number.isFinite(value) && value > 0)) return {margin: null, reason: 'Güncel bakiye, giriş, stop, kaldıraç ve risk tercihi gerekli.'}
  if (input.percent < 0.1 || input.percent > 1 || input.leverage > 125) return {margin: null, reason: 'Risk veya kaldıraç tercihi desteklenen aralığın dışında.'}
  const distance = Math.abs(input.entry - input.stop) / input.entry
  if (distance <= 0 || distance >= 1) return {margin: null, reason: 'Geçerli bir stop mesafesi gerekli.'}
  const margin = Math.floor((input.wallet * input.percent / 100 / distance / input.leverage) * 100) / 100
  if (!Number.isFinite(margin) || margin < 5) return {margin: null, reason: 'Hesaplanan marj mevcut minimumun altında.'}
  if (margin > input.available) return {margin: null, reason: 'Hesaplanan marj kullanılabilir bakiyeyi aşıyor; risk veya stop tercihini kontrol edin.'}
  return {margin, reason: 'Tahmini marj. Ücret, kayma ve canlı risk sınırları mevcut emir öncesi kontrollerde doğrulanır.'}
}

// Telefon "Risk gir": kullanıcı stopta en fazla kaç USDT kaybetmek istediğini yazar, marjin buradan hesaplanır.
// Yalnız tahmin; komisyon, kayma ve sunucunun risk sınırları emir öncesi kontrollerde ayrıca doğrulanır.
export function marginForLoss(input: {lossUsdt: number; available: number; entry: number; stop: number; leverage: number}): {margin: number | null; reason: string} {
  if (![input.lossUsdt, input.available, input.entry, input.stop, input.leverage].every(value => Number.isFinite(value) && value > 0)) return {margin: null, reason: 'Kayıp tutarı, bakiye, giriş, stop ve kaldıraç gerekli.'}
  if (input.leverage > 125) return {margin: null, reason: 'Kaldıraç desteklenen aralığın dışında.'}
  const distance = Math.abs(input.entry - input.stop) / input.entry
  if (distance <= 0 || distance >= 1) return {margin: null, reason: 'Geçerli bir stop mesafesi gerekli.'}
  const margin = Math.floor((input.lossUsdt / distance / input.leverage) * 100) / 100
  if (!Number.isFinite(margin) || margin < 5) return {margin: null, reason: 'Hesaplanan teminat en az 5 USDT olmalı; kayıp tutarını artır ya da stopu yaklaştır.'}
  if (margin > input.available) return {margin: null, reason: 'Hesaplanan teminat kullanılabilir bakiyeyi aşıyor; kayıp tutarını azalt.'}
  return {margin, reason: `Teminat ${margin} USDT olarak ayarlandı. Stopta fiyat kaybı yaklaşık ${input.lossUsdt} USDT olur; komisyon ayrıca eklenir.`}
}

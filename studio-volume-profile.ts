// Hacim profili: görünen mumların hacmini fiyat kademelerine dağıtır. Kontrol noktası (POC) ve değer alanı (%70) hesaplar.
export type ProfileCandle = {high: number; low: number; volume: number}
export type ProfileBin = {low: number; high: number; volume: number}
export type VolumeProfile = {bins: ProfileBin[]; poc: number; valueLow: number; valueHigh: number; max: number}

export function volumeProfile(candles: readonly ProfileCandle[], binCount = 32, valueShare = 0.7): VolumeProfile | null {
  const clean = candles.filter(c => Number.isFinite(c.high) && Number.isFinite(c.low) && Number.isFinite(c.volume) && c.high >= c.low && c.volume >= 0)
  if (!clean.length || !Number.isInteger(binCount) || binCount < 2) return null
  const low = Math.min(...clean.map(c => c.low)), high = Math.max(...clean.map(c => c.high))
  if (!(high > low)) return null
  const size = (high - low) / binCount
  const volumes = new Array<number>(binCount).fill(0)
  for (const c of clean) {
    if (c.volume === 0) continue
    const first = Math.min(binCount - 1, Math.floor((c.low - low) / size))
    const last = Math.min(binCount - 1, Math.floor((c.high - low) / size))
    if (c.high === c.low) { volumes[first] += c.volume; continue }
    for (let i = first; i <= last; i++) {
      const from = Math.max(c.low, low + i * size), to = Math.min(c.high, low + (i + 1) * size)
      if (to > from) volumes[i] += c.volume * (to - from) / (c.high - c.low)
    }
  }
  const total = volumes.reduce((sum, v) => sum + v, 0)
  if (!(total > 0)) return null
  const pocIndex = volumes.indexOf(Math.max(...volumes))
  // Değer alanı: POC'tan başlayıp hacmi büyük olan komşuya doğru genişler.
  let lo = pocIndex, hi = pocIndex, acc = volumes[pocIndex]
  while (acc < total * valueShare && (lo > 0 || hi < binCount - 1)) {
    const down = lo > 0 ? volumes[lo - 1] : -1, up = hi < binCount - 1 ? volumes[hi + 1] : -1
    if (up >= down) { hi++; acc += volumes[hi] } else { lo--; acc += volumes[lo] }
  }
  const bins = volumes.map((volume, i) => ({low: low + i * size, high: low + (i + 1) * size, volume}))
  return {bins, poc: low + (pocIndex + 0.5) * size, valueLow: low + lo * size, valueHigh: low + (hi + 1) * size, max: Math.max(...volumes)}
}

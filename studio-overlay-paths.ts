// Gösterge çizgilerini SVG yoluna çeviren saf yardımcılar (React yok → test edilebilir).
import type {Series} from './studio-indicators'

const fixed = (value: number) => Math.round(value * 100) / 100

/** Tam seri içindeki [from, to) penceresini çizer; null (ısınma) noktalarında çizgi kopar. */
export function segmentPaths(series: Series | undefined, from: number, to: number, xOf: (visibleIndex: number) => number, yOf: (value: number) => number): string[] {
  if (!series) return []
  const paths: string[] = []
  let current = ''
  for (let i = Math.max(0, from); i < Math.min(series.length, to); i++) {
    const value = series[i]
    if (value === null || !Number.isFinite(value)) {
      if (current) paths.push(current)
      current = ''
      continue
    }
    const y = yOf(value)
    if (!Number.isFinite(y)) continue
    current += `${current ? 'L' : 'M'}${fixed(xOf(i - from))} ${fixed(y)}`
  }
  if (current) paths.push(current)
  return paths
}

/** İki seri arasındaki bant (Bollinger) için kapalı yol; yalnız ikisi de dolu olan kesintisiz parçalar çizilir. */
export function bandPaths(upper: Series | undefined, lower: Series | undefined, from: number, to: number, xOf: (visibleIndex: number) => number, yOf: (value: number) => number): string[] {
  if (!upper || !lower) return []
  const out: string[] = []
  let top: string[] = [], bottom: string[] = []
  const flush = () => {
    if (top.length > 1) out.push(`M${top.join('L')}L${bottom.reverse().join('L')}Z`)
    top = []; bottom = []
  }
  for (let i = Math.max(0, from); i < Math.min(upper.length, lower.length, to); i++) {
    const u = upper[i], l = lower[i]
    if (u === null || l === null || !Number.isFinite(u) || !Number.isFinite(l)) { flush(); continue }
    const x = fixed(xOf(i - from))
    top.push(`${x} ${fixed(yOf(u))}`)
    bottom.push(`${x} ${fixed(yOf(l))}`)
  }
  flush()
  return out
}

/** Alt panel ölçeği: RSI sabit 0–100; MACD için pencere içindeki en büyük |değer| etrafında simetrik. */
export function symmetricExtent(series: ReadonlyArray<Series | undefined>, from: number, to: number): number {
  let max = 0
  for (const one of series) {
    if (!one) continue
    for (let i = Math.max(0, from); i < Math.min(one.length, to); i++) {
      const value = one[i]
      if (value !== null && Number.isFinite(value)) max = Math.max(max, Math.abs(value))
    }
  }
  return max > 0 ? max * 1.15 : 1
}

export const valueText = (value: number | null | undefined, digits = 2) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', {maximumFractionDigits: Math.abs(value) >= 1000 ? 2 : digits, minimumFractionDigits: 0}) : '—'

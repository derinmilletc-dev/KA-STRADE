// Canlı mini grafikteki mumlardan otomatik, kısa Türkçe yorum (saf hesap, ekrandan bağımsız, test edilebilir).
// Yalnız okuma: tahmin değil, son mumların ne yaptığının tarifi. Emir/hesap verisi yoktur.
export type ReadCandle = {time: number; open: number; high: number; low: number; close: number; volume: number}
export type ReadTone = 'up' | 'down' | 'flat' | 'warn'
export type CandleRead = {tone: ReadTone; head: string; text: string; chips: string[]}

export const MIN_CANDLES = 24
const REF_LEN = 20       // kırılım/tuzak için bakılan önceki mum sayısı
const TAIL_LEN = 3       // "yeni" sayılan son mumlar
const SPIKE_RATIO = 1.8  // son mumların hacmi önceki ortalamanın bu katından büyükse "hacim yüksek"

const pct = (a: number, b: number) => (b > 0 ? (a - b) / b * 100 : 0)
const mean = (values: number[]) => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0)
const num = (value: number, digits = 2) => Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: digits, maximumFractionDigits: digits})
export const signedPct = (value: number) => `${value < 0 ? '−' : '+'}%${num(value)}`

const clean = (rows: ReadonlyArray<ReadCandle> | null | undefined): ReadCandle[] => (rows ?? [])
  .map(row => ({time: Number(row.time), open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: Number(row.volume) || 0}))
  .filter(row => [row.open, row.high, row.low, row.close].every(Number.isFinite) && row.close > 0 && row.high >= row.low)

/** Pencere adı: "son 1 saat" gibi; mum sayısı × mum süresi. */
export function windowText(candleSeconds: number, count: number): string {
  const minutes = Math.round(candleSeconds * count / 60)
  if (!(minutes > 0)) return `son ${count} mum`
  if (minutes % 1440 === 0) return `son ${minutes / 1440} gün`
  return minutes % 60 === 0 ? `son ${minutes / 60} saat` : `son ${minutes} dk`
}

/** "15m" / "1h" / "1d" → saniye; tanınmayan süre için 0. */
export function intervalSeconds(interval: string): number {
  const match = /^(\d+)([mhd])$/.exec(String(interval ?? '').trim())
  if (!match) return 0
  return Number(match[1]) * {m: 60, h: 3600, d: 86400}[match[2] as 'm' | 'h' | 'd']
}

export function readCandles(input: ReadonlyArray<ReadCandle> | null | undefined, window = 'son 60 mum'): CandleRead | null {
  const rows = clean(input)
  if (rows.length < MIN_CANDLES) return null
  const last = rows[rows.length - 1]
  const first = rows[0]
  const net = pct(last.close, first.close)
  const lows = rows.map(row => row.low)
  const highs = rows.map(row => row.high)
  const lo = Math.min(...lows)
  const hi = Math.max(...highs)
  const place = hi > lo ? (last.close - lo) / (hi - lo) : 0.5

  let path = 0
  for (let i = 1; i < rows.length; i += 1) path += Math.abs(rows[i].close - rows[i - 1].close)
  const efficiency = path > 0 ? Math.abs(last.close - first.close) / path : 0   // 1'e yakın: düz gidiş, 0'a yakın: gelgit
  const noise = mean(rows.map(row => (row.high - row.low) / row.close * 100))  // ortalama mum boyu (%)

  const tail = rows.slice(-TAIL_LEN)
  const ref = rows.slice(-(REF_LEN + TAIL_LEN), -TAIL_LEN)
  const refHigh = Math.max(...ref.map(row => row.high))
  const refLow = Math.min(...ref.map(row => row.low))
  const poked = (side: 'up' | 'down') => side === 'up' ? tail.some(row => row.high > refHigh) : tail.some(row => row.low < refLow)
  const refVol = mean(ref.map(row => row.volume))
  const spike = refVol > 0 && mean(tail.map(row => row.volume)) > refVol * SPIKE_RATIO
  const recent = pct(last.close, rows[rows.length - 6].close)

  const chips = [`${signedPct(net)} · ${window}`]
  chips.push(place >= 0.8 ? 'Aralığın tepesine yakın' : place <= 0.2 ? 'Aralığın dibine yakın' : 'Aralığın ortasında')
  if (spike) chips.push('Hacim yüksek')

  const up = poked('up'), down = poked('down')
  if (up && !down && last.close < refHigh) {
    return {tone: 'warn', head: 'Yukarı kırılım tuzağı olabilir', chips,
      text: `Fiyat son ${REF_LEN} mumun tepesini aştı ama üstünde tutunamadı, geri döndü. Yukarı yön gibi görünüp ters dönüyor olabilir (bull trap). Acele alım riskli.${spike ? ' Hacim de yüksek, hareket sert.' : ''}`}
  }
  if (down && !up && last.close > refLow) {
    return {tone: 'warn', head: 'Aşağı kırılım tuzağı olabilir', chips,
      text: `Fiyat son ${REF_LEN} mumun dibinin altına indi ama toparlanıp geri çıktı. Aşağı yön gibi görünüp ters dönüyor olabilir (bear trap). Acele satış riskli.${spike ? ' Hacim de yüksek, hareket sert.' : ''}`}
  }
  if (last.close > refHigh && !down) {
    return {tone: 'up', head: 'Yukarı kırılım', chips,
      text: `Fiyat son ${REF_LEN} mumun tepesinin üstünde kapanıyor.${spike ? ' Hacim de destekliyor, kırılım daha güvenilir.' : ' Hacim güçlü değil, kırılımın tutması için sıradaki mumlara bak.'}`}
  }
  if (last.close < refLow && !up) {
    return {tone: 'down', head: 'Aşağı kırılım', chips,
      text: `Fiyat son ${REF_LEN} mumun dibinin altında kapanıyor.${spike ? ' Hacim de destekliyor, düşüş daha güvenilir.' : ' Hacim güçlü değil, kırılımın tutması için sıradaki mumlara bak.'}`}
  }
  const trending = efficiency >= 0.3 && Math.abs(net) >= noise * 1.5
  if (trending) {
    const rising = net > 0
    const against = rising ? recent <= -noise : recent >= noise
    if (against) {
      return {tone: 'flat', head: rising ? 'Yükselişte geri çekilme' : 'Düşüşte tepki yükselişi', chips,
        text: rising ? `${window} yukarı gidiyor ama son mumlarda ${signedPct(recent)} geri çekilme var. Ana yön hâlâ yukarı.` : `${window} aşağı gidiyor ama son mumlarda ${signedPct(recent)} tepki yükselişi var. Ana yön hâlâ aşağı.`}
    }
    return {tone: rising ? 'up' : 'down', head: rising ? 'Düzenli yükseliş' : 'Düzenli düşüş', chips,
      text: rising ? `${window} içinde fiyat ${signedPct(net)} yükseldi ve geri çekilmeler sığ kaldı.` : `${window} içinde fiyat ${signedPct(net)} düştü ve toparlanmalar zayıf kaldı.`}
  }
  const where = place >= 0.8 ? 'Aralığın tepesinde, yukarıda direnç aranabilir.' : place <= 0.2 ? 'Aralığın dibinde, aşağıda destek aranabilir.' : 'Aralığın ortasında, net bir avantaj yok.'
  return {tone: 'flat', head: efficiency < 0.15 ? 'Yatay ve dalgalı' : 'Belirgin yön yok', chips,
    text: `${window} içinde fiyat bir aralıkta gidip geliyor (${signedPct(net)}). ${where}`}
}

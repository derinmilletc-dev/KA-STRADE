import type {Insights} from './coinbase-stats'

// "Sana özel" notları: kişisel Binance kalıplarını (saat, coin, kazanç/kayıp büyüklüğü) açılan coinin yorumuna bağlar.
// Yorumun yönünü ya da puanını DEĞİŞTİRMEZ; yalnız not düşer. Gelir geçmişi işlemin yönünü vermediği için yön notu yoktur.
export type PersonalNote = {key: string; tone: 'up' | 'warn' | 'flat'; text: string}

const MAX_NOTES = 3
const pad = (n: number) => String(n).padStart(2, '0')
const money = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USD`
const rootOf = (symbol: string) => symbol.toUpperCase().replace(/-.*$/, '').replace(/(USDT|USDC|BUSD)$/, '')

export function personalNotes(insights: Insights | null | undefined, symbol: string, lean: string, now: Date): PersonalNote[] {
  if (!insights || !insights.enough) return []
  const notes: PersonalNote[] = []
  const hour = (now.getUTCHours() + 3) % 24
  const inside = (b: {from: number; to: number}) => hour >= b.from && hour < b.to
  const weak = insights.bad_blocks.find(inside)
  const good = !weak ? insights.good_blocks.find(inside) : undefined
  if (weak) notes.push({key: 'hour', tone: 'warn', text: `Şu an senin zayıf saatlerinden biri (TSİ ${pad(weak.from)}–${pad(weak.to)}): bu aralıkta ${weak.trades} işlemin %${weak.win_rate}'i kazandı.`})
  else if (good) notes.push({key: 'hour', tone: 'up', text: `Şu an senin güçlü saatlerinden biri (TSİ ${pad(good.from)}–${pad(good.to)}): bu aralıkta ${good.trades} işlemin %${good.win_rate}'i kazandı.`})

  const root = rootOf(symbol)
  const coin = (insights.by_coin ?? []).find(item => rootOf(item.coin) === root)
  if (coin && coin.n >= 4) {
    const tone = coin.net < 0 && coin.win_rate <= 40 ? 'warn' : coin.net > 0 && coin.win_rate >= 60 ? 'up' : 'flat'
    notes.push({key: 'coin', tone, text: `${root}: son 30 günde ${coin.n} işlemin %${coin.win_rate}'i kazandı, net ${money(coin.net)}.`})
  }

  const win = insights.avg_win, loss = insights.avg_loss
  if ((lean === 'LONG' || lean === 'SHORT') && typeof win === 'number' && typeof loss === 'number' && win > 0 && loss > win * 1.2) {
    notes.push({key: 'size', tone: 'warn', text: `Ortalama kaybın (${loss.toLocaleString('tr-TR', {maximumFractionDigits: 2})}) kazancından (${win.toLocaleString('tr-TR', {maximumFractionDigits: 2})}) büyük: girersen kaybı küçük tutmaya dikkat et.`})
  }
  const order = {warn: 0, up: 1, flat: 2} as const
  return notes.sort((a, b) => order[a.tone] - order[b.tone]).slice(0, MAX_NOTES)
}

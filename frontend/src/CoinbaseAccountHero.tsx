// Coinbase ekranının üst özeti: bakiye, durum ve (vadeli hesap hazır değilse) sade yönlendirme.
// Yalnızca gösterim; hiçbir emir/ayar buradan değişmez.
import {Lock, LockOpen, RefreshCw, Wallet} from 'lucide-react'

type Amount = {value: string | null; currency: string} | null
export type HeroBalance = {
  futures_buying_power: Amount; available_margin: Amount; unrealized_pnl: Amount; daily_realized_pnl: Amount
  worst_margin_level: string | null; worst_margin_window: string | null
}

const fmt = (amount: Amount | undefined): string => {
  if (amount?.value == null) return '—'
  const number = Number(amount.value)
  const text = Number.isFinite(number) ? number.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : amount.value
  return `${text} ${amount.currency}`
}
const tone = (amount: Amount | undefined): 'up' | 'down' | undefined => {
  const number = amount?.value == null ? NaN : Number(amount.value)
  return Number.isFinite(number) && number !== 0 ? (number > 0 ? 'up' : 'down') : undefined
}
const levelName = (level: string | null): string => {
  const name = String(level ?? '').split('_').pop()?.toUpperCase() ?? ''
  return name === 'BASE' ? 'Güvenli' : name === 'WARNING' ? 'Uyarı' : name === 'DANGER' ? 'Tehlike' : name === 'LIQUIDATION' ? 'Tasfiye' : name || '—'
}

type Props = {
  balance: HeroBalance | null
  futuresMissing: boolean
  ordersOn: boolean
  clockSkew: number | null
  openCount: number
  loading: boolean
  onRefresh: () => void
}

export default function CoinbaseAccountHero({balance, futuresMissing, ordersOn, clockSkew, openCount, loading, onRefresh}: Props) {
  const danger = balance && !['BASE', ''].includes(String(balance.worst_margin_level ?? '').split('_').pop()?.toUpperCase() ?? '')
  return <section className="cbHero" aria-label="Coinbase vadeli hesap özeti">
    <header className="cbHeroTop">
      <span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b><i className="cbDot" aria-hidden="true"/><small>Bağlı</small></span>
      <button type="button" className="cbHeroRefresh" onClick={onRefresh} disabled={loading} aria-label="Yenile"><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/></button>
    </header>

    {balance && <>
      <div className="cbHeroMain"><small>Alım gücü</small><b>{fmt(balance.futures_buying_power)}</b></div>
      <dl className="cbHeroGrid">
        <div><dt>Kullanılabilir marjin</dt><dd>{fmt(balance.available_margin)}</dd></div>
        <div><dt>Açık K/Z</dt><dd data-tone={tone(balance.unrealized_pnl)}>{fmt(balance.unrealized_pnl)}</dd></div>
        <div><dt>Bugün K/Z</dt><dd data-tone={tone(balance.daily_realized_pnl)}>{fmt(balance.daily_realized_pnl)}</dd></div>
      </dl>
    </>}

    {futuresMissing && <div className="cbHeroEmpty" role="status">
      <b>Vadeli (Futures) hesabın henüz hazır görünmüyor</b>
      <p>Coinbase bu anahtar için vadeli bakiye bilgisi vermedi. Anahtar çalışıyor; büyük ihtimalle vadeli hesap açılmamış ya da içinde para yok.</p>
      <ol>
        <li>Coinbase’de vadeli işlemleri (Futures) etkinleştir.</li>
        <li>Vadeli cüzdana USD aktar.</li>
        <li>Buraya dönüp yenile.</li>
      </ol>
      <button type="button" onClick={onRefresh} disabled={loading}>{loading ? 'Kontrol ediliyor…' : 'Tekrar dene'}</button>
    </div>}

    <div className="cbHeroChips">
      <span className="cbChip" data-tone={ordersOn ? 'ok' : 'warn'}>{ordersOn ? <LockOpen aria-hidden="true"/> : <Lock aria-hidden="true"/>}{ordersOn ? 'Emir açık' : 'Emir kilitli'}</span>
      <span className="cbChip" data-tone="mute">{openCount > 0 ? `${openCount} açık pozisyon` : 'Açık pozisyon yok'}</span>
      {balance && <span className="cbChip" data-tone={danger ? 'bad' : 'ok'}>Marjin: {levelName(balance.worst_margin_level)}</span>}
      {clockSkew !== null && <span className="cbChip" data-tone="bad">Saat farkı {clockSkew} sn</span>}
      <span className="cbChip" data-tone="warn">Gerçek para</span>
    </div>
  </section>
}

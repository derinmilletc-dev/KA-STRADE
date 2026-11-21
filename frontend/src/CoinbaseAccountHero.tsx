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
  const ready = Boolean(balance) && !futuresMissing
  const live = ready && ordersOn
  return <section className="cbHero" aria-label="Coinbase vadeli hesap özeti">
    <header className="cbHeroTop">
      <span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span>
      <span className="cbHeroState" data-tone={live ? 'ok' : 'warn'}>{live ? 'AKTİF' : 'HAZIRLIK'}</span>
      <button type="button" className="cbHeroRefresh" onClick={onRefresh} disabled={loading} aria-label="Yenile"><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/></button>
    </header>

    <ul className="cbHeroRows" aria-label="Durum">
      <li data-tone="ok"><span>API bağlantısı</span><b><i aria-hidden="true"/>Bağlı</b></li>
      <li data-tone={ready ? 'ok' : balance === null && !futuresMissing ? 'mute' : 'warn'}><span>Futures hesabı</span><b><i aria-hidden="true"/>{ready ? 'Hazır' : balance === null && !futuresMissing ? 'Kontrol ediliyor…' : 'Hazır değil'}</b></li>
      {balance?.worst_margin_level && <li data-tone={danger ? 'bad' : 'ok'}><span>Marjin durumu</span><b><i aria-hidden="true"/>{levelName(balance.worst_margin_level)}</b></li>}
      {clockSkew !== null && <li data-tone="bad"><span>Saat farkı</span><b><i aria-hidden="true"/>{clockSkew} sn</b></li>}
    </ul>

    <dl className="cbHeroTiles">
      <div><dt>Bakiye</dt><dd>{balance ? fmt(balance.futures_buying_power) : futuresMissing ? '0,00 USD' : '—'}</dd></div>
      <div><dt>Pozisyon</dt><dd>{openCount} açık</dd></div>
      <div><dt>Emir</dt><dd data-tone={ordersOn ? 'up' : 'down'}>{ordersOn ? 'Aktif' : 'Kapalı'}</dd></div>
    </dl>

    {balance && (openCount > 0 || Number(balance.daily_realized_pnl?.value) !== 0) && <dl className="cbHeroGrid">
      <div><dt>Kullanılabilir marjin</dt><dd>{fmt(balance.available_margin)}</dd></div>
      <div><dt>Açık K/Z</dt><dd data-tone={tone(balance.unrealized_pnl)}>{fmt(balance.unrealized_pnl)}</dd></div>
      <div><dt>Bugün K/Z</dt><dd data-tone={tone(balance.daily_realized_pnl)}>{fmt(balance.daily_realized_pnl)}</dd></div>
    </dl>}

    {futuresMissing && <div className="cbHeroEmpty" role="status">
      <b>Futures bakiyesi bulunamadı</b>
      <p>API anahtarı çalışıyor, ama Coinbase Futures hesabından bakiye gelmedi. Futures’ı etkinleştirip vadeli cüzdana USD aktar.</p>
      <button type="button" onClick={onRefresh} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/>{loading ? 'Kontrol ediliyor…' : 'Tekrar kontrol et'}</button>
    </div>}

    <p className="cbHeroLive" data-tone={live ? 'ok' : 'warn'}>{live ? <LockOpen aria-hidden="true"/> : <Lock aria-hidden="true"/>}<span><small>CANLI İŞLEM · GERÇEK PARA</small><b>{live ? 'Emir gönderme aktif' : !ordersOn ? 'Emir gönderme sunucuda kapalı' : 'Hesap hazırlığı gerekli'}</b></span></p>
  </section>
}

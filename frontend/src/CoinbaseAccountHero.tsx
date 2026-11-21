// Coinbase ekranının üst özeti: bakiye, durum ve (vadeli hesap hazır değilse) sade yönlendirme.
// Yalnızca gösterim; hiçbir emir/ayar buradan değişmez.
import {useEffect, useState} from 'react'
import {RefreshCw, Wallet} from 'lucide-react'
import {pushState, type PushState} from '../../push-client'

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
  openSummary: string | null
  openOrders: number | null
  lastCheck: number | null
  loading: boolean
  onRefresh: () => void
}

const COINBASE_URL = 'https://www.coinbase.com/advanced-trade'

function useSecondsSince(at: number | null): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  return at === null ? null : Math.max(0, Math.round((now - at) / 1000))
}

export default function CoinbaseAccountHero({balance, futuresMissing, ordersOn, clockSkew, openCount, openSummary, openOrders, lastCheck, loading, onRefresh}: Props) {
  const [push, setPush] = useState<PushState | null>(null)
  useEffect(() => { let alive = true; void pushState().then(state => { if (alive) setPush(state) }).catch(() => undefined); return () => { alive = false } }, [])
  const since = useSecondsSince(lastCheck)
  const levelUpper = String(balance?.worst_margin_level ?? '').split('_').pop()?.toUpperCase() ?? ''
  const danger = Boolean(balance) && !['BASE', ''].includes(levelUpper)
  const power = Number(balance?.futures_buying_power?.value)
  const hasMoney = Number.isFinite(power) && power > 0
  const checking = balance === null && !futuresMissing
  // 3 adım: anahtar bağlı (bu ekrana gelindiyse hep tamam) → futures hesabı → bakiye
  const accountOk = Boolean(balance) && !futuresMissing
  const done = 1 + (accountOk ? 1 : 0) + (accountOk && hasMoney ? 1 : 0)
  const ready = done === 3
  const current = !accountOk ? 2 : !hasMoney ? 3 : 0
  const pnl = Number(balance?.daily_realized_pnl?.value)

  const refreshBtn = <button type="button" className="cbHeroRefresh" onClick={onRefresh} disabled={loading} aria-label="Yenile"><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/></button>

  if (checking) return <section className="cbHero cbHero2" aria-label="Coinbase vadeli hesap özeti" data-state="checking">
    <div className="cbHeroTop"><span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span><span className="cbHeroState" data-tone="mute">KONTROL</span>{refreshBtn}</div>
    <p className="cbHeroAuto"><RefreshCw className="spin" aria-hidden="true"/><span>Hesap kontrol ediliyor…</span></p>
  </section>

  if (!ready) return <section className="cbHero cbHero2" aria-label="Coinbase vadeli hesap özeti" data-state="setup">
    <div className="cbHeroTop"><span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span><span className="cbHeroState" data-tone="warn">{3 - done} ADIM KALDI</span>{refreshBtn}</div>
    <div className="cbHeroRing">
      <span className="cbRing" style={{background: `conic-gradient(#39d69a 0 ${done / 3 * 100}%, #2a3341 0)`}} role="img" aria-label={`${done} / 3 adım tamam`}><i>{done}/3</i></span>
      <span><b>İşleme hazır değil</b><small>Sıradaki adım: {current === 2 ? 'Futures hesabını aç' : 'Vadeli cüzdana USD ekle'}</small></span>
    </div>
    <ol className="cbSteps" aria-label="Hazırlık adımları">
      <li data-state="done"><i aria-hidden="true">✓</i><span>API anahtarı bağlı</span></li>
      <li data-state={accountOk ? 'done' : 'now'}><i aria-hidden="true">{accountOk ? '✓' : '2'}</i><span>Futures hesabını aç</span>{!accountOk && <a href={COINBASE_URL} target="_blank" rel="noreferrer">Coinbase’de aç ↗</a>}</li>
      <li data-state={current === 3 ? 'now' : 'todo'}><i aria-hidden="true">{current === 3 ? '3' : ''}</i><span>Vadeli cüzdana USD ekle</span>{current === 3 ? <a href={COINBASE_URL} target="_blank" rel="noreferrer">Para ekle ↗</a> : <small>{balance ? fmt(balance.futures_buying_power) : '0,00 USD'}</small>}</li>
    </ol>
    <p className="cbHeroAuto"><RefreshCw className={loading ? 'spin' : ''} aria-hidden="true"/><span>Her 30 sn kendisi kontrol ediyor{since !== null ? ` · son: ${since < 2 ? 'şimdi' : `${since} sn önce`}` : ''}</span><button type="button" onClick={onRefresh} disabled={loading}>Şimdi kontrol et</button></p>
    {clockSkew !== null && <p className="cbHeroWarn" role="alert">Sunucu saati {clockSkew} sn kaymış; emirler bekletilir.</p>}
  </section>

  return <section className="cbHero cbHero2" aria-label="Coinbase vadeli hesap özeti" data-state="ready">
    <div className="cbHeroTop"><span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span><span className="cbHeroState" data-tone={ordersOn ? 'ok' : 'warn'}>{ordersOn ? '● CANLI · HAZIR' : 'EMİR KAPALI'}</span>{refreshBtn}</div>
    <div className="cbHeroMoney"><small>Alım gücü</small><b>{fmt(balance!.futures_buying_power)}</b></div>
    <p className="cbHeroLine">Kullanılabilir {fmt(balance!.available_margin)}{Number.isFinite(pnl) ? <> · Bugün <em data-tone={tone(balance!.daily_realized_pnl)}>{pnl > 0 ? '+' : ''}{fmt(balance!.daily_realized_pnl)}</em></> : null}</p>
    <dl className="cbHeroTiles2">
      <div><dt>Açık pozisyon</dt><dd>{openCount === 0 ? 'Yok' : openSummary ?? `${openCount} açık`}</dd></div>
      <div><dt>Marjin</dt><dd data-tone={danger ? 'down' : 'up'}>{levelName(balance!.worst_margin_level) === '—' ? 'Güvenli' : levelName(balance!.worst_margin_level)}</dd></div>
    </dl>
    {openCount > 0 && <p className="cbHeroLine">Açık K/Z <em data-tone={tone(balance!.unrealized_pnl)}>{fmt(balance!.unrealized_pnl)}</em></p>}
    <div className="cbHeroChips2">
      <span>🛡 Stop zorunlu</span>
      <span>Emir {openOrders ?? 0}/5</span>
      <span data-off={push !== 'on' || undefined}>{push === 'on' ? '🔔 Bildirim açık' : '🔕 Bildirim kapalı'}</span>
      {clockSkew !== null && <span data-bad="">Saat farkı {clockSkew} sn</span>}
    </div>
  </section>
}

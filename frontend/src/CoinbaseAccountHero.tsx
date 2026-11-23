// Coinbase ekranının üst özeti: bakiye, durum ve (vadeli hesap hazır değilse) sade yönlendirme.
// Yalnızca gösterim; hiçbir emir/ayar buradan değişmez.
import {useEffect, useRef, useState} from 'react'
import {ArrowUpRight, Check, RefreshCw, Wallet} from 'lucide-react'
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
  return name === 'BASE' ? 'Güvenli' : name === 'WARNING' ? 'Uyarı' : name === 'DANGER' ? 'Tehlike' : name === 'LIQUIDATION' ? 'Tasfiye' : name === 'UNSPECIFIED' ? '—' : name || '—'
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
  // Coinbase'e gidip dönünce kart kendisi yeniden kontrol eder (kullanıcı düğmeye basmak zorunda kalmaz).
  const refreshRef = useRef(onRefresh)
  refreshRef.current = onRefresh
  const lastRef = useRef(lastCheck)
  lastRef.current = lastCheck
  useEffect(() => {
    const back = () => {
      if (document.visibilityState !== 'visible') return
      if (lastRef.current !== null && Date.now() - lastRef.current < 4000) return
      refreshRef.current()
    }
    document.addEventListener('visibilitychange', back)
    window.addEventListener('focus', back)
    return () => { document.removeEventListener('visibilitychange', back); window.removeEventListener('focus', back) }
  }, [])
  const levelUpper = String(balance?.worst_margin_level ?? '').split('_').pop()?.toUpperCase() ?? ''
  const danger = Boolean(balance) && !['BASE', '', 'UNSPECIFIED'].includes(levelUpper)
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

  if (!ready) {
    const steps: {key: string; label: string; state: 'done' | 'now' | 'todo'}[] = [
      {key: 'key', label: 'Anahtar', state: 'done'},
      {key: 'acct', label: 'Hesap', state: accountOk ? 'done' : 'now'},
      {key: 'fund', label: 'Bakiye', state: current === 3 ? 'now' : 'todo'},
    ]
    const nowTitle = current === 2 ? 'Futures hesabını aç' : 'Vadeli cüzdana USD ekle'
    const nowText = current === 2
      ? 'Coinbase’de vadeli işlemler (Futures) hesabını etkinleştir. Bitince buraya dön; kart kendisi algılar.'
      : 'Spot cüzdanından vadeli cüzdana USD aktar. Bitince buraya dön; kart kendisi algılar.'
    return <section className="cbHero cbHero2 cbSetup" aria-label="Coinbase vadeli hesap özeti" data-state="setup">
      <div className="cbHeroTop"><span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span><span className="cbHeroState" data-tone="warn">{done}/3</span>{refreshBtn}</div>
      <ol className="cbStepper" aria-label={`Hazırlık: ${done} / 3 adım tamam`}>
        {steps.map(step => <li key={step.key} data-state={step.state}><span className="cbStepBar" aria-hidden="true"/><span className="cbStepLabel">{step.state === 'done' ? <Check aria-hidden="true"/> : null}{step.label}</span></li>)}
      </ol>
      <div className="cbSetupNow">
        <small>SIRADAKİ ADIM · {current}/3</small>
        <b>{nowTitle}</b>
        <p>{nowText}</p>
        {current === 3 && <span className="cbSetupBal">Vadeli cüzdan <em>{balance ? fmt(balance.futures_buying_power) : '0,00 USD'}</em></span>}
        <a className="cbSetupCta" href={COINBASE_URL} target="_blank" rel="noreferrer">{current === 2 ? 'Coinbase’de aç' : 'Para ekle'}<ArrowUpRight aria-hidden="true"/></a>
      </div>
      <p className="cbHeroAuto"><i className="cbLiveDot" data-busy={loading || undefined} aria-hidden="true"/><span>Canlı izleniyor{since !== null ? ` · ${since < 2 ? 'şimdi kontrol edildi' : `${since} sn önce`}` : ''}</span><button type="button" onClick={onRefresh} disabled={loading}>Şimdi kontrol et</button></p>
      {clockSkew !== null && <p className="cbHeroWarn" role="alert">Sunucu saati {clockSkew} sn kaymış; emirler bekletilir.</p>}
    </section>
  }

  return <section className="cbHero cbHero2" aria-label="Coinbase vadeli hesap özeti" data-state="ready">
    <div className="cbHeroTop"><span className="cbHeroTitle"><Wallet aria-hidden="true"/><b>Coinbase Futures</b></span><span className="cbHeroState" data-tone={ordersOn ? 'ok' : 'warn'}>{ordersOn ? '● CANLI · HAZIR' : 'EMİR KAPALI'}</span>{refreshBtn}</div>
    <div className="cbHeroMoney"><small>Alım gücü</small><b>{fmt(balance!.futures_buying_power)}</b></div>
    <p className="cbHeroLine">Kullanılabilir {fmt(balance!.available_margin)}{Number.isFinite(pnl) ? <> · Bugün <em data-tone={tone(balance!.daily_realized_pnl)}>{pnl > 0 ? '+' : ''}{fmt(balance!.daily_realized_pnl)}</em></> : null}</p>
    <dl className="cbHeroTiles2">
      <div><dt>Açık pozisyon</dt><dd>{openCount === 0 ? 'Yok' : openSummary ?? `${openCount} açık`}</dd></div>
      <div><dt>Marjin</dt><dd data-tone={danger ? 'down' : levelUpper === 'UNSPECIFIED' ? 'mute' : 'up'}>{levelName(balance!.worst_margin_level) === '—' ? 'Belirtilmedi' : levelName(balance!.worst_margin_level)}</dd></div>
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

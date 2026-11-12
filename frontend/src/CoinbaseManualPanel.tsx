import {useCallback, useEffect, useRef, useState} from 'react'
import {AlertTriangle, CheckCircle2, RefreshCw, WalletCards} from 'lucide-react'
import {clearVisibleInterval, visibleInterval} from '../../visible-interval'
import type {ApiFailure} from './coinbase-manual'
import {CoinbaseApiError, coinbaseCall} from './coinbase-manual-api'
import CoinbaseManualKey, {type KeyMeta} from './CoinbaseManualKey'
import CoinbaseManualPositions, {type CbPosition, type CbProtection} from './CoinbaseManualPositions'
import CoinbaseManualTicket from './CoinbaseManualTicket'
import './coinbase-manual.css'

type Status = {enabled: boolean; orders_enabled?: boolean; vault_ready: boolean; vault_reason: string | null; key_saved: boolean; key: KeyMeta; clock?: {ok: boolean | null; skew_seconds: number | null}}
type Amount = {value: string | null; currency: string} | null
type Account = {balance: {futures_buying_power: Amount; available_margin: Amount; unrealized_pnl: Amount; daily_realized_pnl: Amount; worst_margin_level: string | null; worst_margin_window: string | null; liquidation_buffer_percentage: string | null}}
type Notice = {kind: 'ok' | 'warn' | 'error'; text: string; failure?: ApiFailure}

const amount = (value: Amount | undefined) => value?.value == null ? '—' : `${value.value} ${value.currency}`

export default function CoinbaseManualPanel() {
  const [status, setStatus] = useState<Status | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [positions, setPositions] = useState<CbPosition[]>([])
  const [protection, setProtection] = useState<CbProtection | null>(null)
  const [notice, setNotice] = useState<Notice>({kind: 'warn', text: 'Coinbase durumu kontrol ediliyor…'})
  const [loading, setLoading] = useState(false)
  const busy = useRef(false)

  const showFailure = (error: unknown): void => {
    if (error instanceof CoinbaseApiError) setNotice({kind: 'error', text: error.failure.message, failure: error.failure})
    else setNotice({kind: 'error', text: error instanceof Error ? error.message : 'Beklenmeyen hata.'})
  }

  const refresh = useCallback(async (quiet = false) => {
    if (busy.current) return
    busy.current = true
    setLoading(true)
    try {
      const next = await coinbaseCall<Status>('/status')
      setStatus(next)
      if (!next.key_saved) { setAccount(null); setPositions([]); setProtection(null); if (!quiet) setNotice({kind: 'warn', text: 'Coinbase anahtarı kayıtlı değil.'}); return }
      // Her okuma ayrı: biri bozulursa diğerleri gösterilir ve hata açıkça yazılır.
      const results = await Promise.allSettled([
        coinbaseCall<Account>('/account'),
        coinbaseCall<{positions: CbPosition[]}>('/positions'),
        next.orders_enabled ? coinbaseCall<CbProtection>('/orders/protection') : Promise.resolve(null),
      ])
      const [accountResult, positionResult, protectionResult] = results
      if (accountResult.status === 'fulfilled') setAccount(accountResult.value)
      if (positionResult.status === 'fulfilled') setPositions(positionResult.value.positions)
      if (protectionResult.status === 'fulfilled') setProtection(protectionResult.value)
      const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
      if (failed) showFailure(failed.reason)
      else if (!quiet) setNotice({kind: 'ok', text: 'Coinbase bilgileri yenilendi.'})
    } catch (error) {
      showFailure(error)
    } finally {
      busy.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh(true)
    const timer = visibleInterval(() => void refresh(true), 30000)
    return () => clearVisibleInterval(timer)
  }, [refresh])

  const done = (text: string) => { setNotice({kind: 'ok', text}); void refresh(true) }
  // Hata sonrası durum değişmiş olabilir (emir gitti ama cevap bozuk gibi): hatayı göster, sonra tazele.
  const failed = (error: CoinbaseApiError) => { setNotice({kind: 'error', text: error.failure.message, failure: error.failure}); void refresh(true) }

  const ordersOn = status?.orders_enabled === true
  const bal = account?.balance

  return <div className="cbPanel">
    <div className="cbTop">
      <div className="cbChips">
        <span className={status?.key_saved ? 'ok' : 'warn'}>Anahtar: {status?.key_saved ? 'kayıtlı' : 'yok'}</span>
        <span className={ordersOn ? 'warn' : 'ok'}>Emir gönderme: {ordersOn ? 'AÇIK (gerçek para)' : 'KAPALI (salt okunur)'}</span>
        {status?.clock?.ok === false && <span className="bad">Saat farkı {status.clock.skew_seconds} sn</span>}
      </div>
      <button onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? 'spin' : ''}/>Yenile</button>
    </div>

    <div className={`cbNotice ${notice.kind}${notice.failure?.critical ? ' critical' : ''}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
      {notice.kind === 'ok' ? <CheckCircle2/> : <AlertTriangle/>}
      <div>
        <b>{notice.failure?.critical ? 'ACİL: ' : ''}{notice.text}</b>
        {notice.failure?.reasons.length ? <ul>{notice.failure.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : null}
        {notice.failure?.retryAfterSeconds ? <small>Yaklaşık {notice.failure.retryAfterSeconds} sn sonra tekrar dene.</small> : null}
        {notice.failure?.code ? <small>Kod: {notice.failure.code}</small> : null}
      </div>
    </div>

    {status && <CoinbaseManualKey keySaved={status.key_saved} meta={status.key} vaultReady={status.vault_ready} vaultReason={status.vault_reason} onDone={done} onError={failed}/>}

    {bal && <section className="cbCard" aria-label="Hesap">
      <h3><WalletCards/>Vadeli hesap</h3>
      <dl className="cbDl">
        <div><dt>Alım gücü</dt><dd>{amount(bal.futures_buying_power)}</dd></div>
        <div><dt>Kullanılabilir marjin</dt><dd>{amount(bal.available_margin)}</dd></div>
        <div><dt>Gerçekleşmemiş K/Z</dt><dd>{amount(bal.unrealized_pnl)}</dd></div>
        <div><dt>Bugünkü gerçekleşen K/Z</dt><dd>{amount(bal.daily_realized_pnl)}</dd></div>
        <div><dt>Marjin seviyesi (en kötü pencere)</dt><dd>{bal.worst_margin_level ?? '—'}{bal.worst_margin_window ? ` · ${bal.worst_margin_window === 'overnight' ? 'gece' : 'gün içi'}` : ''}</dd></div>
        <div><dt>Likidasyon tamponu</dt><dd>{bal.liquidation_buffer_percentage ? `%${bal.liquidation_buffer_percentage}` : '—'}</dd></div>
      </dl>
    </section>}

    {status?.key_saved && <CoinbaseManualPositions positions={positions} protection={protection} ordersEnabled={ordersOn} onDone={done} onError={failed}/>}
    {status?.key_saved && ordersOn && <CoinbaseManualTicket onDone={done} onError={failed}/>}
    {status?.key_saved && !ordersOn && <p className="cbNote warn">Emir gönderme sunucuda kapalı. Bu ekran yalnızca izleme yapar; emir vermek için sunucuda <b>COINBASE_ORDERS_ENABLED</b> açılmalı.</p>}
  </div>
}

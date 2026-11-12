import {useCallback, useEffect, useState} from 'react'
import {CheckCircle2, TriangleAlert} from 'lucide-react'
import {coinbaseCall} from './coinbase-manual-api'
import CoinbaseManualKey, {type KeyMeta} from './CoinbaseManualKey'
import {noticeFromError, type Notice} from './CoinbaseManualNotice'
import './coinbase-manual.css'

type Status = {vault_ready: boolean; vault_reason: string | null; key_saved: boolean; key: KeyMeta}

/** Master Trade → Bağlantı sekmesinde Coinbase seçilince: Binance bağlantı kartıyla aynı görünüm. */
export default function CoinbaseManualSetup() {
  const [status, setStatus] = useState<Status | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const refresh = useCallback(async () => {
    try { setStatus(await coinbaseCall<Status>('/status')) } catch (error) { setNotice(noticeFromError(error)) }
  }, [])
  useEffect(() => { void refresh() }, [refresh])
  const test = useCallback(async () => {
    await coinbaseCall('/account')
    setNotice({kind: 'ok', text: 'Bağlantı doğrulandı: Coinbase hesabı okundu.'})
    await refresh()
  }, [refresh])
  const saved = status?.key_saved === true
  return <>
    {notice && <div className={`liveNotice masterTradeLiveNotice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
      {notice.kind === 'ok' ? <CheckCircle2/> : <TriangleAlert/>}<span>{notice.text}{notice.failure?.reasons.length ? ` ${notice.failure.reasons.join(' ')}` : ''}{notice.failure?.code ? ` (Kod: ${notice.failure.code})` : ''}</span>
    </div>}
    <section className="liveWsCard open"><div className="liveWsCardHead static"><span className={`liveWsBadge ${saved ? 'done' : 'current'}`}>{saved ? <CheckCircle2/> : '1'}</span><span><b>Coinbase bağlantısı</b><small>{saved ? 'Coinbase hesabın bağlı' : 'API anahtarını bağla'}</small></span></div>
      <div className="liveWsBody">{status
        ? <CoinbaseManualKey keySaved={saved} meta={status.key} vaultReady={status.vault_ready} vaultReason={status.vault_reason} onTest={test}
            onDone={text => { setNotice({kind: 'ok', text}); void refresh() }} onError={error => { setNotice(noticeFromError(error)); void refresh() }}/>
        : <p className="liveWsHint">Coinbase kasası kontrol ediliyor…</p>}</div></section>
  </>
}

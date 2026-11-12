import {useCallback, useEffect, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import {coinbaseCall} from './coinbase-manual-api'
import CoinbaseManualKey, {type KeyMeta} from './CoinbaseManualKey'
import CoinbaseManualNotice, {noticeFromError, type Notice} from './CoinbaseManualNotice'
import './coinbase-manual.css'

type Status = {vault_ready: boolean; vault_reason: string | null; key_saved: boolean; key: KeyMeta}

/** Borsa Bağlantıları sayfasında Coinbase seçilince görünen anahtar alanı. */
export default function CoinbaseManualSetup() {
  const [status, setStatus] = useState<Status | null>(null)
  const [notice, setNotice] = useState<Notice>({kind: 'warn', text: 'Coinbase kasası kontrol ediliyor…'})
  const refresh = useCallback(async () => {
    try {
      const next = await coinbaseCall<Status>('/status')
      setStatus(next)
      setNotice(next.vault_ready ? {kind: 'ok', text: next.key_saved ? 'Coinbase anahtarı kayıtlı.' : 'Coinbase anahtarı henüz yok.'} : {kind: 'warn', text: next.vault_reason || 'Kasa bekleniyor.'})
    } catch (error) { setNotice(noticeFromError(error)) }
  }, [])
  useEffect(() => { void refresh() }, [refresh])
  return <div className="cbPanel">
    <div className="cbTop"><span/><button onClick={() => void refresh()}><RefreshCw/>Yenile</button></div>
    <CoinbaseManualNotice notice={notice}/>
    {status && <CoinbaseManualKey keySaved={status.key_saved} meta={status.key} vaultReady={status.vault_ready} vaultReason={status.vault_reason}
      onDone={text => { setNotice({kind: 'ok', text}); void refresh() }} onError={error => { setNotice(noticeFromError(error)); void refresh() }}/>}
  </div>
}

import {useCallback, useEffect, useRef, useState} from 'react'
import LiveTradingPanel, {type SharedLiveStatus, type SharedConnectionStatus} from './frontend/src/LiveTradingPanel'
import {API_BASE} from './api'
import {visibleInterval, clearVisibleInterval} from './visible-interval'
import {useMemberAccess} from './premium-access'

// Master "Canlı İşlem" kokpitinin demo karşılığı: aynı Rehber / Kais Koç ekranı, ama Binance Futures Demo motoruna bağlı.
// Konuştuğu adresler /binance-demo/cockpit/*; canlı (/v25) adreslere hiç istek atmaz.
const TWIN = `${API_BASE}/binance-demo/cockpit`

const detailText = (payload: unknown, fallback: string) => {
  const detail = payload && typeof payload === 'object' ? (payload as {detail?: unknown}).detail : null
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) return detail.map(item => (item && typeof item === 'object' ? String((item as {msg?: unknown}).msg ?? '') : String(item))).filter(Boolean).join(' · ') || fallback
  return fallback
}

export default function DemoCockpit({symbol = 'BTCUSDT', analysis = null}: {onNavigate?: (tab: string) => void; onOpenManual?: () => void; symbol?: string; analysis?: React.ComponentProps<typeof LiveTradingPanel>['analysis']}) {
  const {userId} = useMemberAccess()
  const [status, setStatus] = useState<SharedLiveStatus | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${TWIN}/status`, {headers: {Accept: 'application/json'}})
      const payload: unknown = await response.json().catch(() => null)
      if (!response.ok || !payload || typeof payload !== 'object') throw new Error(detailText(payload, 'Demo durumu okunamadı.'))
      if (!alive.current) return
      setStatus(payload as SharedLiveStatus)
      setError('')
    } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : 'Demo durumu okunamadı.')
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = visibleInterval(() => void load(), 8000)
    return () => clearVisibleInterval(timer)
  }, [load])

  const connected = status?.connected === true
  const wallet = typeof status?.account?.wallet_balance === 'number' ? status.account.wallet_balance : null
  // Canlıdaki "bağlantı" bilgisinin demo karşılığı: demo anahtarı kayıtlıysa bağlı sayılır; engelleme/IP bilgisi yok.
  const connections: SharedConnectionStatus = {
    vault: {ready: true}, binance_ban: {active: false},
    connections: {LIVE: {configured: status?.credentials?.configured === true, active: connected, last_test_ok: connected, fingerprint: null, last_error: error || null, account: {wallet_balance: wallet ?? undefined}}},
  }
  // Canlı İşlem ekranının AYNISI; tek fark adreslerin demo motoruna gitmesi (demo prop'u). Gerçek hesaba hiçbir istek gitmez.
  return <LiveTradingPanel demo active masterTrade masterTradeTab="canli" key={userId} symbol={symbol} analysis={analysis}
    sharedStatus={status} sharedStatusVerified={status !== null && !error} sharedStatusError={error} sharedStatusLoading={loading}
    sharedConnections={connections} onRefreshStatus={async () => { await load() }}/>
}

import {useCallback, useEffect, useRef, useState} from 'react'
import LiveCockpit from './frontend/src/LiveCockpit'
import type {SharedLiveStatus} from './frontend/src/LiveTradingPanel'
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

type Plan = NonNullable<SharedLiveStatus['plans']>[number]

export default function DemoCockpit({onNavigate, onOpenManual}: {onNavigate?: (tab: string) => void; onOpenManual?: () => void}) {
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

  const request = useCallback(async (path: string, body?: Record<string, unknown>) => {
    const response = await fetch(`${TWIN}${path}`, {
      method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify(body ?? {}),
    })
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new Error(detailText(payload, 'İşlem tamamlanamadı.'))
    if (payload && typeof payload === 'object' && alive.current) setStatus(current => (payload as SharedLiveStatus).mode ? payload as SharedLiveStatus : current)
    return payload
  }, [])

  const runtime = status?.original_v2_live
  const connected = status?.connected === true
  const wallet = typeof status?.account?.wallet_balance === 'number' ? status.account.wallet_balance : null
  const available = Number(status?.account?.available_balance)
  const guideInput = {
    statusLoaded: status !== null, statusVerified: status !== null && !error, statusError: error || null,
    loading, freeTrial: false, credentialsConfigured: status?.credentials?.configured === true, connected,
    balance: connected ? wallet : null,
    emergency: false, recoveryRequired: false, accountRateLimited: false, marketBanSeconds: 0,
    fillLocked: false, dailyLocked: runtime?.daily?.locked === true,
    riskApproved: (runtime?.risk_revision ?? 0) > 0,
    authorizationValid: status?.authorization?.valid === true, policyAcknowledged: status?.policy_acknowledged === true,
    armed: status?.armed === true, running: runtime?.enabled === true, paused: false, pauseReason: null,
    entryBlocker: null, sessionRemainingSeconds: runtime?.session?.remaining_seconds ?? 0,
  }
  const terminal = ['KAPANDI', 'İPTAL', 'ACİL DURDURULDU', 'CLOSED', 'CANCELLED']
  const findPlan = (position: Record<string, unknown>): Plan | undefined => status?.plans?.find(plan =>
    String(plan.symbol || '').toUpperCase() === String(position.symbol || '').toUpperCase() && !terminal.includes(String(plan.status || '').toUpperCase()))

  return <LiveCockpit
    demo onNavigate={onNavigate} onOpenManual={onOpenManual} key={userId} status={status} verified={status !== null && !error} loading={loading} guideInput={guideInput}
    wallet={connected ? wallet : null} availableBalance={connected && Number.isFinite(available) ? available : null}
    marketBanSeconds={0} externalBusy={false} userId={userId}
    request={request} refresh={load} findPlan={findPlan}
  />
}

// Binance tarafı fiyat alarmı: /api/v22/price-alerts (Coinbase'dekiyle aynı ekran, farklı uç). Hatalar okunur metinle fırlatılır.
import {useCallback, useEffect, useState} from 'react'
import {API_BASE} from './api'
import {coinbaseCall} from './coinbase-manual-api'
import type {CbAlert} from './coinbase-alert'

export type AlertCall = <T>(path: string, options?: RequestInit) => Promise<T>

export const binanceAlertCall: AlertCall = async <T,>(path: string, options: RequestInit = {}) => {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${API_BASE}/v22/price-alerts${path}`, {...options, headers}) } catch { throw new Error('Bağlantı kurulamadı. Biraz sonra tekrar dene.') }
  let body: unknown = null
  try { body = await response.json() } catch { body = null }
  if (!response.ok) {
    const detail = body && typeof body === 'object' ? (body as {detail?: unknown}).detail : null
    throw new Error(typeof detail === 'string' && detail ? detail : `Alarm işlemi başarısız (HTTP ${response.status}).`)
  }
  return body as T
}

export const coinbaseAlertCall: AlertCall = <T,>(path: string, options: RequestInit = {}) => coinbaseCall<T>(path, options)

/** Alarm listesi: açılışta ve 30 sn'de bir yenilenir (tetiklenen alarm listeden düşer). */
export function usePriceAlerts(call: AlertCall, enabled = true): {alerts: CbAlert[]; reload: () => void} {
  const [alerts, setAlerts] = useState<CbAlert[]>([])
  const reload = useCallback(() => { call<{alerts: CbAlert[]}>('/alerts').then(data => setAlerts(Array.isArray(data.alerts) ? data.alerts : [])).catch(() => undefined) }, [call])
  useEffect(() => { if (!enabled) return; reload(); const timer = window.setInterval(reload, 30000); return () => window.clearInterval(timer) }, [reload, enabled])
  return {alerts, reload}
}

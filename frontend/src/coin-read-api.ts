import {useCallback, useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import type {AlarmMode, AlarmTf, CoinRead, ReadAlarm} from './coin-read-view'

// Akıllı yorum ve pozisyon yorum alarmı için istemci. Hatalar okunur Türkçe metinle fırlatılır; hiçbiri emir göndermez.
async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetchWithTimeout(`${API_BASE}/v22${path}`, {cache: 'no-store', ...init, headers}, timeoutMs) } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error('Bağlantı kurulamadı. Biraz sonra tekrar dene.')
  }
  let body: unknown = null
  try { body = await response.json() } catch { body = null }
  if (!response.ok) {
    const detail = body && typeof body === 'object' ? (body as {detail?: unknown}).detail : null
    throw new Error(typeof detail === 'string' && detail ? detail : `İşlem başarısız (HTTP ${response.status}).`)
  }
  return body as T
}

export type ReadOptions = {interval: string; side?: 'LONG' | 'SHORT' | null; entry?: number | null; enabled?: boolean}

export function fetchCoinRead(symbol: string, options: {interval: string; side?: string | null; entry?: number | null; signal?: AbortSignal}): Promise<CoinRead> {
  const query = new URLSearchParams({interval: options.interval})
  if (options.side) query.set('side', options.side)
  if (options.entry && options.entry > 0) query.set('entry', String(options.entry))
  return request<CoinRead>(`/coin-read/${encodeURIComponent(symbol)}?${query}`, {signal: options.signal}, 25000)
}

/** Seçili coinin yorumu: açılışta, coin/süre değişince ve 30 sn'de bir tazelenir. Eski sonuç yenisi gelene kadar ekranda kalır. */
export function useCoinRead(symbol: string, {interval, side = null, entry = null, enabled = true}: ReadOptions) {
  const [state, setState] = useState<{key: string; data: CoinRead | null; error: string}>({key: '', data: null, error: ''})
  const [tick, setTick] = useState(0)
  const key = `${symbol}|${interval}|${side ?? ''}|${entry ?? ''}`
  useEffect(() => {
    if (!enabled || !symbol) return
    let alive = true
    const controller = new AbortController()
    void fetchCoinRead(symbol, {interval, side, entry, signal: controller.signal}).then(data => { if (alive) setState({key, data, error: ''}) })
      .catch(error => { if (alive && error?.name !== 'AbortError') setState(old => ({key, data: old.key.split('|')[0] === symbol ? old.data : null, error: error instanceof Error ? error.message : 'Yorum alınamadı.'})) })
    return () => { alive = false; controller.abort() }
  }, [symbol, interval, side, entry, enabled, key, tick])
  useEffect(() => {
    if (!enabled || !symbol) return
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') setTick(value => value + 1) }, 30000)
    return () => window.clearInterval(timer)
  }, [enabled, symbol])
  const reload = useCallback(() => setTick(value => value + 1), [])
  const sameCoin = state.key.split('|')[0] === symbol
  return {data: sameCoin ? state.data : null, error: state.key === key ? state.error : '', loading: enabled && state.key !== key, reload}
}

export type AlarmInput = {product_id: string; symbol: string; side: 'LONG' | 'SHORT'; entry: number | null; mode: AlarmMode; min_tf: AlarmTf}

/** Bir borsanın pozisyon yorum alarmları. openIds: şu an AÇIK pozisyonların ürün kodları (bilinmiyorsa null: eşitleme yapılmaz).
 *  Eşitleme: kapanmış pozisyonun alarmı silinir, açık olanınki uzatılır. Liste okunamadıysa openIds null verilmeli (yanlışlıkla silmesin). */
export function useReadAlarms(exchange: 'binance' | 'coinbase', openIds: string[] | null) {
  const [alarms, setAlarms] = useState<ReadAlarm[]>([])
  const reload = useCallback(() => { void request<{alarms: ReadAlarm[]}>(`/read-alarms?exchange=${exchange}`).then(data => setAlarms(Array.isArray(data.alarms) ? data.alarms : [])).catch(() => undefined) }, [exchange])
  const idsKey = openIds === null ? null : [...openIds].sort().join(',')
  useEffect(() => {
    let alive = true
    const sync = () => {
      if (idsKey === null) { reload(); return }
      void request(`/read-alarms/keep`, {method: 'POST', body: JSON.stringify({exchange, product_ids: idsKey ? idsKey.split(',') : []})}).catch(() => undefined).finally(() => { if (alive) reload() })
    }
    sync()
    const timer = window.setInterval(sync, 30 * 60 * 1000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [exchange, idsKey, reload])
  const save = useCallback(async (input: AlarmInput): Promise<string | null> => {
    try {
      const data = await request<{alarm: ReadAlarm}>('/read-alarms', {method: 'POST', body: JSON.stringify({exchange, ...input})})
      setAlarms(current => [...current.filter(item => item.product_id !== input.product_id), data.alarm])
      return null
    } catch (error) { return error instanceof Error ? error.message : 'Alarm kurulamadı.' }
  }, [exchange])
  const remove = useCallback(async (productId: string): Promise<string | null> => {
    try {
      await request(`/read-alarms?exchange=${exchange}&product_id=${encodeURIComponent(productId)}`, {method: 'DELETE'})
      setAlarms(current => current.filter(item => item.product_id !== productId))
      return null
    } catch (error) { return error instanceof Error ? error.message : 'Alarm kaldırılamadı.' }
  }, [exchange])
  const byProduct = useMemo(() => new Map(alarms.map(item => [item.product_id, item])), [alarms])
  return {alarms, byProduct, save, remove, reload}
}

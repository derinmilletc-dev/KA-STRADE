import {useCallback, useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import type {Insights} from './coinbase-stats'
import type {AlarmMode, AlarmTf, CoinRead, PulseData, ReadAlarm, ReadStats, WatchDirection, WatchItem} from './coin-read-view'

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

export type AlarmInput = {product_id: string; symbol: string; side: 'LONG' | 'SHORT'; entry: number | null; mode: AlarmMode; min_tf: AlarmTf; stop?: number | null}

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


/** Yorumun son 7 gündeki yön isabeti (coin ve genel). Alınamazsa null: satır gizlenir. */
export function useReadStats(symbol: string) {
  const [state, setState] = useState<{symbol: string; stats: ReadStats | null}>({symbol: '', stats: null})
  useEffect(() => {
    if (!symbol) return
    let alive = true
    void request<ReadStats>(`/read-stats?symbol=${encodeURIComponent(symbol)}`).then(stats => { if (alive) setState({symbol, stats}) }).catch(() => { if (alive) setState({symbol, stats: null}) })
    return () => { alive = false }
  }, [symbol])
  return state.symbol === symbol ? state.stats : null
}

/** Giriş fırsatı izleme listesi (yalnız bildirim; emir göndermez). */
export function useEntryWatch() {
  const [items, setItems] = useState<WatchItem[]>([])
  const [max, setMax] = useState(12)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const reload = useCallback(() => {
    void request<{items: WatchItem[]; max: number}>('/entry-watch').then(data => { setItems(Array.isArray(data.items) ? data.items : []); if (data.max) setMax(data.max); setStatus('ready') }).catch(() => setStatus('error'))
  }, [])
  useEffect(() => { reload() }, [reload])
  const save = useCallback(async (symbol: string, direction: WatchDirection): Promise<string | null> => {
    try {
      const data = await request<{item: WatchItem}>('/entry-watch', {method: 'POST', body: JSON.stringify({symbol, direction})})
      setItems(current => [...current.filter(item => item.symbol !== data.item.symbol), data.item])
      return null
    } catch (error) { return error instanceof Error ? error.message : 'İzleme eklenemedi.' }
  }, [])
  const remove = useCallback(async (symbol: string): Promise<string | null> => {
    try {
      await request(`/entry-watch?symbol=${encodeURIComponent(symbol)}`, {method: 'DELETE'})
      setItems(current => current.filter(item => item.symbol !== symbol))
      return null
    } catch (error) { return error instanceof Error ? error.message : 'İzleme kaldırılamadı.' }
  }, [])
  return {items, max, status, reload, save, remove}
}

/** Binance işlem geçmişinden kişisel kalıplar (yalnız okuma). */
export const fetchMyInsights = () => request<Insights & {days: number; truncated: boolean; caveat: string}>('/my-insights?exchange=binance', {}, 30000)

// Kişisel kalıplar tüm yorum kartlarında aynı: 5 dk paylaşılır (sunucu da 10 dk önbellekliyor). Alınamazsa null; kart sessiz kalır.
let insightsShared: {at: number; value: Promise<Awaited<ReturnType<typeof fetchMyInsights>> | null>} | null = null
const sharedInsights = () => {
  const now = Date.now()
  if (!insightsShared || now - insightsShared.at > 300_000) insightsShared = {at: now, value: fetchMyInsights().catch(() => null)}
  return insightsShared.value
}
export function useMyInsights(enabled = true) {
  const [data, setData] = useState<Awaited<ReturnType<typeof fetchMyInsights>> | null>(null)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    void sharedInsights().then(value => { if (alive) setData(value) })
    return () => { alive = false }
  }, [enabled])
  return data
}

/** Açık pozisyonların kısa yorum nabzı; 45 sn'de bir (ekran görünürken) tazelenir. items null ise çalışmaz. Alınamazsa null: kart sessiz kalır. */
export function usePositionPulse(items: Array<{symbol: string; side: 'LONG' | 'SHORT'}> | null) {
  const [data, setData] = useState<PulseData | null>(null)
  const [failed, setFailed] = useState(false)
  const [tick, setTick] = useState(0)
  const key = items && items.length ? items.map(item => `${item.symbol}:${item.side}`).sort().join(',') : ''
  useEffect(() => {
    if (!key) { setData(null); return }
    let alive = true
    void request<PulseData>(`/positions-pulse?items=${encodeURIComponent(key)}`, {}, 25000).then(result => { if (alive) { setData(result); setFailed(false) } }).catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [key, tick])
  useEffect(() => {
    if (!key) return
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') setTick(value => value + 1) }, 45000)
    return () => window.clearInterval(timer)
  }, [key])
  return {data: key ? data : null, failed: Boolean(key) && failed && !data}
}

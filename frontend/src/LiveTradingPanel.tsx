// @ts-nocheck
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDollarSign, Info, Search, Eye, EyeOff, KeyRound, LockKeyhole, Power, RefreshCw, Send, ShieldAlert, ShieldCheck, TriangleAlert, UnlockKeyhole, Wallet } from 'lucide-react'
import { API_BASE } from './api'
import { fetchWithTimeout } from '../../master-trade-request'
import {CONNECTION_REQUEST_TIMEOUT_MS, connectionFailureMessage, connectionHttpMessage, redactConnectionMessage} from './exchange-connection-errors'
import { liveCopy } from '../../ui-copy'
import '../../live-ws.css'
import '../../manual-order.css'
import {useKaisErrorReaction} from '../../useKaisPageReactions'
import {useTradingPreferences} from '../../useTradingPreferences'
import {preferredRiskMargin} from '../../account-risk-sizing'
import {PremiumWorkspace, useMemberAccess} from '../../premium-access'
import OriginalV2LiveCard, {type OriginalV2EntryCheck, type OriginalV2LiveStatus, type OriginalPersistenceDiagnostics} from './OriginalV2LiveCard'
import OriginalLiveOverview from './OriginalLiveOverview'
import {readLiveStatusResponses, liveCredentialStatusMatches} from './live-status-read'
export {liveCredentialStatusMatches} from './live-status-read'

type LivePolicy = Record<string, unknown>
type LiveStatus = {
  original_v2_live?: OriginalV2LiveStatus
  credentials?: {configured?: boolean; fingerprint?: string | null; storage?: string}
  connected?: boolean
  connection?: {last_checked?: string | null; last_error?: string | null; clock_offset_ms?: number | null}
  stream?: {status?: string; last_error?: string | null}
  armed?: boolean
  armed_until?: string | null
  live_lock_reason?: {code: string; message: string; at: string} | null
  account_rate_limit?: {pending_verification?: boolean; locked?: boolean; message?: string} | null
  auto_session_until?: string | null
  real_trading_locked?: boolean
  live_auto_trade?: boolean
  auto?: {enabled?: boolean; status?: string; last_decision?: string; last_error?: string | null; last_scan?: string | null; last_skip_reason?: string | null; last_cycle_stage?: string | null; session_until?: string | null}
  scanner?: {last_scan_at?: string | null; scanned_symbol_count?: number; candidate_symbols?: string[]; candidate_count?: number; selected_symbols?: string[]; selected_symbols_count?: number; last_skip_reason?: string | null; last_cycle_stage?: string | null}
  policy?: LivePolicy
  policy_acknowledged?: boolean
  consent?: {active?: boolean; expires_at?: string | null; fingerprint?: string | null; reason?: string; scope_match?: boolean}
  authorization?: {valid?: boolean; expires_at?: string | null; reason?: string; scope_match?: boolean}
  readiness?: {ready?: boolean; gates?: Array<{key?: string; label?: string; passed?: boolean; detail?: string}>}
  account?: {wallet_balance?: number | null; available_balance?: number | null; margin_balance?: number | null; unrealized_pnl?: number | null; positions?: Array<Record<string, unknown>>; open_orders?: Array<Record<string, unknown>>}
  daily?: {realized_pnl?: number; remaining_loss_budget?: number; entries?: number; consecutive_losses?: number; unverified_closures?: number}
  plans?: Array<Record<string, unknown> & {targets?: unknown[]; stop_loss?: unknown; protection_state?: string; protection_status?: string; exchange_protection?: {stop: boolean; tp1: boolean; tp3: boolean}}>
  events?: Array<{kind?: string; message?: string; created_at?: string; symbol?: string; failures?: string[]}>
  emergency?: {active?: boolean; reason?: string | null}
  recovery_ready?: boolean
  recovery_error?: string | null
  execution_state?: string
  reconciliation_required?: boolean
  reconciliation_diagnostic?: {exception_type?: string; exception_message?: string; reconciliation_stage?: string; source?: string; function?: string; line?: number; request_method?: string | null; request_path?: string | null; timestamp?: string | null}
  last_order_error?: {message?: string; symbol?: string | null; created_at?: string; failed_check?: string | null; checks?: Array<{check?: string; passed?: boolean; detail?: string}>} | null
}

type ConnectionStatus = {
  vault?: {ready?: boolean}
  connections?: {LIVE?: {configured?: boolean; active?: boolean; last_test_ok?: boolean; fingerprint?: string | null; last_error?: string | null; account?: {wallet_balance?: number}}}
  binance_ban?: {
    active?: boolean
    blocked_until?: string | null
    remaining_seconds?: number
    message?: string | null
    source?: string | null
    egress_route?: string | null
    status_code?: number | null
    exchange_code?: number | null
    used_weight_1m?: number | null
  }
  safety?: {secrets_returned_to_browser?: boolean; connection_test_creates_orders?: boolean; live_orders_require_v25_gates?: boolean}
}

type ConnectionTestResult = {testedAt?: string; fingerprint?: string | null}
type ConnectionSaveResult = ConnectionStatus & {
  verification?: {readback_verified?: boolean; submitted_fingerprint?: string; stored_fingerprint?: string}
}
type ExternalHistory = {read_only?: boolean; label?: string; external_trades?: Array<Record<string, unknown>>; external_income?: Array<Record<string, unknown>>; errors?: Array<Record<string, unknown>>}

type OrderDraft = {symbol: string; direction: 'LONG' | 'SHORT'; order_type: 'MARKET' | 'LIMIT'; margin_usdt: string; leverage: string; limit_price: string; stop_loss: string; tp1: string; tp2: string; tp3: string}

export type SharedLiveStatus = LiveStatus
export type SharedConnectionStatus = ConnectionStatus

const normalizeCredentialFingerprint = (value: string | null | undefined) => (value || '').trim().toUpperCase().replace(/^SHA256:/, '')

type Props = {active: boolean; symbol: string; analysis?: {direction?: string | null; confidence?: number; entry?: number; stop_loss?: number; tp1?: number; tp2?: number; tp3?: number} | null; masterTrade?: boolean; masterTradeTab?: 'analiz' | 'canli' | 'pozisyonlar' | 'baglanti'; sharedStatus?: LiveStatus | null; sharedStatusVerified?: boolean; sharedStatusError?: string; sharedStatusLoading?: boolean; sharedConnections?: ConnectionStatus | null; onRefreshStatus?: (force?: boolean) => Promise<void>}

const V25 = `${API_BASE}/v25`
const CONNECTIONS = `${API_BASE}/exchange-connections`
const initialOrder = (symbol: string): OrderDraft => ({symbol, direction: 'LONG', order_type: 'MARKET', margin_usdt: '10', leverage: '1', limit_price: '', stop_loss: '', tp1: '', tp2: '', tp3: ''})
type CoinRow = {symbol: string; display: string; price: number; change: number; volume: number}
const RECENT_COINS_KEY = 'kaistrade-recent-coins'
const readRecentCoins = (): string[] => { try { const v = JSON.parse(localStorage.getItem(RECENT_COINS_KEY) ?? '[]'); return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, 6) : [] } catch { return [] } }
const saveRecentCoin = (symbol: string) => { try { localStorage.setItem(RECENT_COINS_KEY, JSON.stringify([symbol, ...readRecentCoins().filter(item => item !== symbol)].slice(0, 6))) } catch { /* kayıt kapalı olabilir */ } }
const coinPrice = (value: number) => Number.isFinite(value) ? value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2}) : '—'
const orderKey = (o: OrderDraft) => JSON.stringify([o.symbol, o.direction, o.order_type, o.margin_usdt, o.leverage, o.limit_price, o.stop_loss, o.tp1, o.tp2, o.tp3])
const text = (value: unknown) => value === null || value === undefined || value === '' ? '—' : String(value)
const signalPrice = (value: number | null | undefined) => value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
const money = (value: unknown) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const decimals = Math.abs(value) < 10 ? 4 : 2
  return `${value.toLocaleString('tr-TR', {maximumFractionDigits: decimals, minimumFractionDigits: decimals})} USDT`
}
const date = (value: unknown) => value ? new Date(String(value)).toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '—'
const activityTime = (value: unknown) => value ? new Date(String(value)).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '--:--:--'
const entryCheckLabel = (key: string) => ({
  owner_entry_authorization: 'OWNER canlı giriş yetkisi',
  owner_session_authority: 'OWNER oturum yetkisi',
  live_key_vault: 'OWNER Binance key kasası eşleşmesi',
  api_key_validation: 'Binance API anahtarı doğrulaması',
  signed_account_access: 'Binance hesap erişimi',
  auto_session_authorization: 'otomasyon oturumu yetkisi',
  auto_session_active: 'otomasyon oturumunun aktif olması',
  live_consent: '24 saatlik LIVE risk onayı',
  execution_lock: 'gerçek işlem/acil durdurma kilidi',
  recovery_readiness: 'LIVE recovery hazırlığı',
  one_way_mode: 'One-way hesap modu',
  multi_assets_mode: 'Multi-Assets modu',
  available_collateral: 'kullanılabilir teminat',
  minimum_quantity: 'Binance minimum miktar/notional',
  maximum_quantity: 'Binance maksimum miktar',
  risk_budget: 'işlem başı risk bütçesi',
  risk_reward: 'net getiri/risk eşiği',
  symbol_occupancy: 'sembolde açık pozisyon ve emir kontrolü',
  protection_readiness: 'Stop/TP koruma hazırlığı',
  isolated_margin: 'ISOLATED marjin ayarı',
  leverage: 'kaldıraç doğrulaması',
  liquidation_risk: 'likidasyon riski',
  fresh_live_credentials: 'gönderim öncesi taze LIVE yetkisi',
  entry_submission: 'emir gönderimi',
} as Record<string, string>)[key] || key.replaceAll('_', ' ')

export function liveBlockerMessage(status: LiveStatus | null, liveArmed: boolean): string {
  if (status === null) return 'Canlı işlem durumu alınamıyor.'
  if (!liveArmed && status.live_lock_reason?.message) return status.live_lock_reason.message
  if (status.account_rate_limit?.pending_verification) return status.account_rate_limit.message || 'Binance hesap doğrulaması bekleniyor; yeni girişler kapalı.'
  if (status.emergency?.active) return status.reconciliation_diagnostic?.exception_message && status.emergency.reason === 'LIVE_EXCEPTION'
    ? `${status.emergency.reason}: ${status.reconciliation_diagnostic.exception_message}`
    : status.emergency.reason || 'Acil durdurma aktif.'
  if (status.reconciliation_required || status.recovery_error || status.execution_state === 'UNKNOWN') return status.recovery_error || (status.reconciliation_required
    ? 'Canlı işleme devam etmeden önce uzlaştırma gerekir.' : 'Canlı işleme devam etmeden önce kurtarma gerekir.')
  if (!status.connected) return 'Canlı hesap bağlantısı gerekli.'
  if (status.authorization?.valid !== true) {
    const reason = status.authorization?.reason || status.consent?.reason || 'EXPIRED'
    const messages: Record<string, string> = {
      EXPIRED: '24 saatlik izin sona erdi.',
      TRADING_ACCOUNT_CHANGED: 'Doğrulanmış trading account değişti.',
      CREDENTIAL_CHANGED: 'API credentials değişti.',
      POLICY_CHANGED: 'Risk policy değişti; policy acknowledgement ve authorization yenilenmeli.',
      USER_CHANGED: 'Kullanıcı değişti.',
      RECOVERY_REQUIRED: 'Recovery tamamlanmadan authorization kullanılamaz.',
      RECONCILIATION_REQUIRED: 'Reconciliation tamamlanmadan authorization kullanılamaz.',
      EMERGENCY_BLOCKED: 'Emergency block aktif.',
    }
    return Object.hasOwn(messages, reason) ? messages[reason] : '24 saatlik LIVE authorization gerekli.'
  }
  if (status.readiness?.ready !== true) return 'ARM öncesi gereken güvenlik kontrolleri tamamlanmalı.'
  return liveArmed ? 'Engel yok.' : 'Canlı işlem onayı bekleniyor.'
}

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) return payload
  if (payload && typeof payload === 'object') {
    const row = payload as {detail?: unknown; message?: unknown}
    if (typeof row.detail === 'string' && row.detail.trim()) return row.detail
    if (row.detail && typeof row.detail === 'object') {
      const detail = row.detail as {message?: unknown; msg?: unknown; reason?: unknown}
      const message = detail.message ?? detail.msg ?? detail.reason
      if (typeof message === 'string' && message.trim()) return message
    }
    if (Array.isArray(row.detail)) {
      const messages = row.detail.map(item => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && typeof (item as {msg?: unknown}).msg === 'string') return (item as {msg: string}).msg
        return ''
      }).filter(Boolean)
      if (messages.length) return messages.join(' · ')
    }
    if (typeof row.message === 'string') return row.message
  }
  return fallback
}

export default function LiveTradingPanel({active, symbol, analysis, masterTrade, masterTradeTab, sharedStatus, sharedStatusVerified = true, sharedStatusError = '', sharedStatusLoading, sharedConnections, onRefreshStatus}: Props) {
  const [originalNotice, setOriginalNotice] = useState('')
  const [connectionNotice, setConnectionNotice] = useState<{kind: 'info' | 'ok' | 'error'; text: string} | null>(null)
  const {freeTrial, userId} = useMemberAccess()
  const tradingDefaults = useTradingPreferences()
  const defaultsApplied = useRef(false)
  const [sizingNotice, setSizingNotice] = useState('')
  useEffect(() => {
    if (!active || (masterTrade && masterTradeTab !== 'canli') || defaultsApplied.current || !tradingDefaults.preferences) return
    defaultsApplied.current = true
    if (tradingDefaults.untouched() && tradingDefaults.preferences.trading_mode === 'AUTO') {
      document.getElementById('master-trade-auto-trade')?.focus({preventScroll: true})
    }
  }, [active, masterTrade, masterTradeTab, tradingDefaults.preferences, tradingDefaults.untouched])
  const [localStatus, setLocalStatus] = useState<LiveStatus | null>(null)
  const [localStatusVerified, setLocalStatusVerified] = useState(false)
  const [localStatusError, setLocalStatusError] = useState('')
  const [localConnections, setLocalConnections] = useState<ConnectionStatus | null>(null)
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [credentials, setCredentials] = useState({apiKey: '', secretKey: '', accepted: false})
  const [keyCode, setKeyCode] = useState('')
  const [showSecret, setShowSecret] = useState(false)
  const [order, setOrder] = useState<OrderDraft>(initialOrder(symbol))
  const [policyDraft, setPolicyDraft] = useState<LivePolicy | null>(null)
  const [confirm, setConfirm] = useState<{title: string;message: string;expected?: string;simple?: boolean;understanding?: boolean;mfa?: boolean;action: (code?: string) => Promise<void>} | null>(null)
  const [confirmUnderstood, setConfirmUnderstood] = useState(false)
  useEffect(() => setConfirmUnderstood(false), [confirm])
  const [confirmText, setConfirmText] = useState('')
  const [confirmCode, setConfirmCode] = useState('')
  const [busy, setBusy] = useState('')
  const [armPendingSync, setArmPendingSync] = useState(false)
  const [rateLimitSeconds, setRateLimitSeconds] = useState<number | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [openStep, setOpenStep] = useState<number | null>(null)
  const [universe, setUniverse] = useState<CoinRow[] | null>(null)
  const [universeError, setUniverseError] = useState(false)
  const [coinQuery, setCoinQuery] = useState('')
  const [coinOpen, setCoinOpen] = useState(false)
  const [recentCoins, setRecentCoins] = useState<string[]>(() => readRecentCoins())
  const [reviewedKey, setReviewedKey] = useState('')
  useEffect(() => {
    if (!active || universe !== null || !(openStep === 3 || !masterTrade)) return
    const controller = new AbortController()
    void fetchWithTimeout(`${API_BASE}/markets?all=true`, {signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('markets')
      const rows = await response.json() as CoinRow[]
      setUniverse(Array.isArray(rows) ? rows.filter(row => row && typeof row.symbol === 'string').sort((left, right) => Number(right.volume || 0) - Number(left.volume || 0)) : [])
      setUniverseError(false)
    }).catch(error => { if (error?.name !== 'AbortError') setUniverseError(true) })
    return () => controller.abort()
  }, [active, masterTrade, openStep, universe])
  const [riskPrev, setRiskPrev] = useState<{notional: number; margin: number; loss: number; stopPct: number; maxStopPct: number; capped: boolean} | {error: string} | null>(null)
  useEffect(() => {
    if (!active || !masterTrade || openStep !== 3) { setRiskPrev(null); return }
    const row = (universe ?? []).find(item => item.symbol === order.symbol)
    const entry = order.order_type === 'LIMIT' ? Number(order.limit_price) : order.symbol === symbol && Number.isFinite(Number(analysis?.entry)) ? Number(analysis?.entry) : Number(row?.price)
    const stop = Number(order.stop_loss)
    const margin = Number(order.margin_usdt)
    const leverage = Number(order.leverage)
    if (![entry, stop, margin, leverage].every(value => Number.isFinite(value) && value > 0) || entry === stop || margin < 5 || margin > 100 || !Number.isInteger(leverage) || leverage < 1 || leverage > 50) { setRiskPrev(null); return }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void fetchWithTimeout(`${V25}/risk/preview`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({entry, stop_loss: stop, margin_usdt: margin, leverage}), signal: controller.signal}).then(async response => {
        const payload: any = await response.json().catch(() => null)
        if (!response.ok) { setRiskPrev({error: errorMessage(payload, 'Risk hesabı yapılamadı.')}); return }
        setRiskPrev({notional: Number(payload.notional_usdt), margin: Number(payload.margin_usdt), loss: Number(payload.estimated_stop_loss_usdt), stopPct: Number(payload.stop_distance_pct), maxStopPct: Number(payload.max_stop_distance_pct), capped: Boolean(payload.capped)})
      }).catch(error => { if (error?.name !== 'AbortError') setRiskPrev(null) })
    }, 500)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [active, masterTrade, openStep, order.symbol, order.order_type, order.limit_price, order.stop_loss, order.margin_usdt, order.leverage, analysis?.entry, symbol, universe])
  const [strategyInfo, setStrategyInfo] = useState(false)
  const [externalHistory, setExternalHistory] = useState<ExternalHistory | null>(null)
  const [reviewResult, setReviewResult] = useState<{message: string; notional?: number; estimated_stop_loss_usdt?: number} | null>(null)
  const [credentialModalOpen, setCredentialModalOpen] = useState(false)
  const [refreshPending, setRefreshPending] = useState(true)
  const [adoptionCandidate, setAdoptionCandidate] = useState<{
    symbol: string
    candidatePlan: any
    confirmToken: string
  } | null>(null)
  const [notice, setNotice] = useState<{kind: 'info' | 'ok' | 'error'; text: string}>({kind: 'info', text: 'LIVE başlatılmadı. Gerçek emir kilidi varsayılan olarak kapalıdır.'})
  useKaisErrorReaction(notice)
  const refreshInFlight = useRef(false)
  const actionInFlight = useRef(false)
  const confirmationOpen = useRef(false)
  confirmationOpen.current = confirm !== null
  const status = sharedStatus !== undefined ? sharedStatus : localStatus
  const statusVerified = sharedStatus !== undefined ? sharedStatusVerified : localStatusVerified
  const connections = sharedConnections !== undefined ? sharedConnections : localConnections
  const statusLoading = sharedStatusLoading ?? refreshPending
  const backendBan = connections?.binance_ban

  useEffect(() => {
    if (rateLimitSeconds === null || rateLimitSeconds <= 0) return
    const timer = window.setInterval(() => setRateLimitSeconds(current => current === null ? null : Math.max(0, current - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [rateLimitSeconds])

  useEffect(() => {
    if (backendBan?.active !== true) return
    const seconds = Number(backendBan.remaining_seconds)
    if (Number.isFinite(seconds) && seconds > 0) {
      setRateLimitSeconds(Math.ceil(seconds))
    }
  }, [backendBan?.active, backendBan?.blocked_until, backendBan?.remaining_seconds])

  const isConnectionAction = (path: string) => ['/test', '/save', '/activate'].includes(path)
  const rateLimitMessage = (seconds: number | null) => seconds && seconds > 0
    ? `Binance bağlantısı geçici olarak hız sınırına ulaştı. Tekrar denemeden önce ${seconds} saniye bekleyin.`
    : 'Binance bağlantısı geçici olarak hız sınırına ulaştı.'

  const call = async <T,>(base: string, path: string, options: RequestInit = {}): Promise<T> => {
    if (rateLimitSeconds !== null && rateLimitSeconds > 0 && base === CONNECTIONS && isConnectionAction(path)) throw new Error(rateLimitMessage(rateLimitSeconds))
    const headers = new Headers(options.headers)
    if (options.body) headers.set('Content-Type', 'application/json')
    const connectionAction = base === CONNECTIONS && isConnectionAction(path)
    let response: Response
    try {
      response = await fetchWithTimeout(`${base}${path}`, {...options, headers}, connectionAction ? CONNECTION_REQUEST_TIMEOUT_MS : undefined)
    } catch (error) {
      if (connectionAction) throw new Error(connectionFailureMessage(error, [credentials.apiKey, credentials.secretKey]))
      throw error
    }
    let payload: unknown
    try {
      payload = await response.json()
    } catch (error) {
      if (connectionAction) throw new Error(response.ok
        ? connectionFailureMessage(error, [credentials.apiKey, credentials.secretKey])
        : connectionHttpMessage(response.status, 'Sunucu geçerli bir hata açıklaması döndürmedi.', [credentials.apiKey, credentials.secretKey]))
      throw error
    }
    if (response.status === 429) {
      const retryAfter = Number(response.headers.get('Retry-After'))
      const seconds = Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : 0
      setRateLimitSeconds(seconds)
      throw new Error(rateLimitMessage(seconds || null))
    }
    if (!response.ok) {
      if (connectionAction) throw new Error(connectionHttpMessage(response.status,
        errorMessage(payload, 'Sunucu bir sebep bildirmedi; bağlantı ve kasa durumunu kontrol edin.'),
        [credentials.apiKey, credentials.secretKey]))
      const statusLabel = response.status === 423
        ? 'LIVE KİLİTLİ / GATE BLOCKED'
        : response.status === 502
          ? 'BELİRSİZ YÜRÜTME / MANUEL İNCELEME GEREKLİ'
          : response.status === 503
            ? 'BAKIM MODU / YENİ GİRİŞLER KAPALI'
            : `LIVE HTTP ${response.status}`
      throw new Error(`${statusLabel}: ${errorMessage(payload, 'LIVE isteği reddedildi.')}`)
    }
    return payload as T
  }

  const refresh = async (quiet = true) => {
    if (!onRefreshStatus && (refreshInFlight.current || confirmationOpen.current)) return
    setRefreshPending(true)
    if (onRefreshStatus) {
      try {
        await onRefreshStatus(!quiet)
        return true
      } catch (error) {
        if (!quiet) setNotice({kind: 'error', text: error instanceof Error ? error.message : 'LIVE durumu okunamadı.'})
        return false
      }
      finally { setRefreshPending(false) }
    }
    refreshInFlight.current = true
    try {
      const [statusResponse, connectionResponse] = await readLiveStatusResponses(setLocalConnections)
      const connectionResult = await connectionResponse.json() as ConnectionStatus
      const nextStatus = await statusResponse.json() as LiveStatus
      if (confirmationOpen.current) return
      setLocalConnections(connectionResult)
      if (!statusResponse.ok) throw new Error(errorMessage(nextStatus, `Canlı hesap durumu alınamadı (HTTP ${statusResponse.status}).`))
      if (!liveCredentialStatusMatches(nextStatus, connectionResult)) {
        throw new Error('LIVE durumu ile kayıtlı Binance anahtarı uyuşmuyor; yeni girişler kapalı.')
      }
      setLocalStatus(nextStatus)
      setLocalStatusVerified(true)
      setLocalStatusError('')
      setPolicyDraft(current => current || nextStatus.policy || {})
      return true
    } catch (error) {
      setLocalStatusVerified(false)
      setLocalStatusError(error instanceof Error ? error.message : 'LIVE durumu okunamadı.')
      if (!quiet) setNotice({kind: 'error', text: error instanceof Error ? error.message : 'LIVE durumu okunamadı.'})
      return false
    } finally {
      refreshInFlight.current = false
      setRefreshPending(false)
    }
  }

  useEffect(() => {
    if (!active) return
    setOrder(initialOrder(symbol))
    setConfirm(null)
    setConfirmText('')
    setConfirmCode('')
    void refresh()
    const timer = window.setInterval(() => void refresh(), 10000)
    return () => { if (timer !== undefined) window.clearInterval(timer) }
  }, [active, symbol, Boolean(onRefreshStatus)])

  const run = async (key: string, action: () => Promise<void>, success: string) => {
    if (actionInFlight.current) return
    actionInFlight.current = true
    setBusy(key)
    const connectionAction = ['test', 'save', 'connect', 'connect-save'].includes(key)
    if (connectionAction) setConnectionNotice({kind: 'info', text: 'Binance bağlantısı doğrulanıyor; hiçbir emir gönderilmiyor.'})
    try {
      await action()
      if (!await refresh()) throw new Error('İşlem sonrasında LIVE durumu doğrulanamadı; tekrar göndermeden önce durumu kontrol edin.')
      setNotice({kind: 'ok', text: success})
      if (connectionAction) setConnectionNotice({kind: 'ok', text: success})
    }
    catch (error) {
      const message = connectionAction ? connectionFailureMessage(error, [credentials.apiKey, credentials.secretKey])
        : error instanceof Error ? error.message : 'LIVE işlemi tamamlanamadı.'
      setNotice({kind: 'error', text: message})
      if (connectionAction) setConnectionNotice({kind: 'error', text: message})
      await refresh()
      setNotice({kind: 'error', text: message})
    }
    finally { actionInFlight.current = false; setBusy('') }
  }

  const liveConnection = connections?.connections?.LIVE
  const globalBanActive = Boolean(backendBan?.active && (rateLimitSeconds ?? backendBan?.remaining_seconds ?? 0) > 0)
  const banCountdown = Math.max(0, rateLimitSeconds ?? backendBan?.remaining_seconds ?? 0)
  const banCountdownText = `${String(Math.floor(banCountdown / 3600)).padStart(2, '0')}:${String(Math.floor((banCountdown % 3600) / 60)).padStart(2, '0')}:${String(banCountdown % 60).padStart(2, '0')}`
  const banUntilText = backendBan?.blocked_until
    ? new Date(backendBan.blocked_until).toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'})
    : null
  const globalBanMessage = globalBanActive
    ? `Binance geçici olarak engelledi; bitiş saati: ${banUntilText ?? 'sunucudan alınamadı'}. Kalan süre: ${banCountdownText}.`
    : ''
  const configured = Boolean(liveConnection?.configured || status?.credentials?.configured)
  const connected = Boolean(status?.connected)
  const marketDataConnected = status?.stream?.status === 'CANLI'
  const emergency = Boolean(status?.emergency?.active)
  const recoveryRequired = Boolean(emergency || status?.reconciliation_required || status?.recovery_error || status?.execution_state === 'UNKNOWN')
  const protectionGate = status?.readiness?.gates?.find(gate => /protection|koruma/i.test(`${gate.key} ${gate.label}`))
  const recoveryState = recoveryRequired ? 'REQUIRED' : status?.recovery_ready === true ? 'READY' : 'UNKNOWN'
  const readinessReady = status?.readiness?.ready === true
  const authorizationValid = status?.authorization?.valid === true
  const authorizationReason = status?.authorization?.reason || status?.consent?.reason || 'EXPIRED'
  const authorizationReasonText = authorizationReason === 'EXPIRED'
    ? '24 saatlik izin sona erdi.'
    : authorizationReason === 'TRADING_ACCOUNT_CHANGED'
      ? 'Doğrulanmış trading account değişti.'
      : authorizationReason === 'CREDENTIAL_CHANGED'
        ? 'API credentials değişti.'
        : authorizationReason === 'POLICY_CHANGED'
          ? 'Risk policy değişti; policy acknowledgement ve authorization yenilenmeli.'
          : authorizationReason === 'USER_CHANGED'
            ? 'Kullanıcı değişti.'
            : authorizationReason === 'RECOVERY_REQUIRED'
              ? 'Recovery tamamlanmadan authorization kullanılamaz.'
              : authorizationReason === 'RECONCILIATION_REQUIRED'
                ? 'Reconciliation tamamlanmadan authorization kullanılamaz.'
                : authorizationReason === 'EMERGENCY_BLOCKED'
                  ? 'Emergency block aktif.'
                  : '24 saatlik LIVE authorization gerekli.'
  const riskGate = status?.readiness?.gates?.find(gate => /risk|policy|limit|safety|guven/i.test(`${gate.key} ${gate.label}`)) || {passed: false}
  const activePlans = status?.plans?.filter(plan => !['CLOSED', 'CANCELLED', 'CLOSED_CONFIRMED'].includes(String(plan.status || '').toUpperCase())).length ?? 0
  const noActivePositionOrPlan = (status?.account?.positions || []).length === 0 && activePlans === 0
  const protectionState = recoveryRequired ? 'BLOCKED' : noActivePositionOrPlan ? 'READY' : activePlans === 0 ? 'EXTERNAL' : protectionGate ? protectionGate.passed ? 'READY' : 'REQUIRED' : 'UNKNOWN'
  const protectionReady = protectionState === 'READY'
  const exposure = (status?.account?.positions || []).reduce((sum, item) => sum + Math.abs(Number(item.notional || item.positionAmt || 0)), 0)
  const executionLocked = !statusVerified || status === null || status.real_trading_locked !== false
  const gateState = (patterns: RegExp[]) => {
    if (!status) return 'UNKNOWN'
    const gate = status.readiness?.gates?.find(item => patterns.some(pattern => pattern.test(`${item.key} ${item.label}`)))
    return gate ? gate.passed ? 'READY' : 'BLOCKED' : 'UNKNOWN'
  }
  const connectionReady = status !== null && connections !== null && connected
  const connectionState = status === null || connections === null ? 'UNKNOWN' : connected ? 'CONNECTED' : 'NOT CONNECTED'
  const liveArmed = Boolean(status?.armed && status.real_trading_locked === false)
  const armState = !statusVerified || status === null ? 'UNKNOWN' : liveArmed ? 'READY' : 'LOCKED'
  const riskState = riskGate ? riskGate.passed ? 'READY' : 'BLOCKED' : gateState([/risk/i, /policy/i, /limit/i, /safety/i, /guven/i])
  const exposureGate = gateState([/exposure/i, /notional/i])
  const exposureState = exposureGate !== 'UNKNOWN' ? exposureGate : status === null ? 'UNKNOWN' : exposure === 0 ? 'READY' : 'BLOCKED'
  const activePlanState = status === null ? 'UNKNOWN' : activePlans ? 'CONFLICT' : 'READY'
  const emergencyState = status === null ? 'UNKNOWN' : emergency ? 'ACTIVE' : 'CLEAR'
  const policy = policyDraft || status?.policy || {}
  const selectedAutoTimeframe = String(policy.interval ?? status?.policy?.interval ?? '15m')
  const savedAutoTimeframe = String(status?.policy?.interval ?? '15m')
  const autoTimeframeSupported = selectedAutoTimeframe === '15m' && savedAutoTimeframe === '15m'
  const autoTimeframeMessage = "LIVE Auto Trade yalnızca 15m zaman diliminde çalışır. Başlatmak için timeframe'i 15m seçip politikayı kaydedin."
  const timeframeWarning = !autoTimeframeSupported ? <div className="liveNotice error" role="alert" data-testid="live-auto-timeframe-warning"><TriangleAlert aria-hidden="true"/><span>{autoTimeframeMessage} Timeframe: {selectedAutoTimeframe !== '15m' ? selectedAutoTimeframe : savedAutoTimeframe}.</span></div> : null
  const autoReady = Boolean(!freeTrial && autoTimeframeSupported && status && connections && authorizationValid && connectionReady && armState === 'READY' && !executionLocked && readinessReady && riskState === 'READY' && exposureState === 'READY' && activePlanState === 'READY' && protectionState === 'READY' && recoveryState === 'READY' && emergencyState === 'CLEAR')
  const originalEntryChecks: OriginalV2EntryCheck[] = []
  const originalRuntime = status?.original_v2_live
  if (status && originalRuntime) {
    const addCheck = (key: string, label: string, passed: boolean, reason: string) => {
      originalEntryChecks.push({key, label, passed, reason})
    }
    const originalSessionActive = originalRuntime.enabled === true
      && originalRuntime.paused !== true
      && (originalRuntime.session?.remaining_seconds ?? 0) > 0
    const autoSessionExpiresAt = status.auto_session_until ? Date.parse(status.auto_session_until) : Number.NaN
    const autoSessionActive = status.live_auto_trade === true
      && Number.isFinite(autoSessionExpiresAt)
      && autoSessionExpiresAt > Date.now()
    const accountRateLimited = status.account_rate_limit?.locked === true || status.account_rate_limit?.pending_verification === true
    const hasGlobalBanStatus = backendBan?.active === false
    const originalDaily = originalRuntime.daily
    const originalDailyLossReady = originalDaily?.locked === false
      && typeof originalDaily.loss === 'number'
      && Number.isFinite(originalDaily.loss)
      && typeof originalDaily.limit === 'number'
      && Number.isFinite(originalDaily.limit)
      && originalDaily.loss < originalDaily.limit
    const policyNumber = (key: string) => {
      const value = status.policy?.[key]
      return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
    }
    const dailyTradeLimit = policyNumber('daily_trade_limit')
    const dailyLossLimit = policyNumber('daily_loss_limit')
    const consecutiveLossLimit = policyNumber('consecutive_loss_limit')
    const dailyEntries = status.daily?.entries
    const realizedPnl = status.daily?.realized_pnl
    const consecutiveLosses = status.daily?.consecutive_losses
    const openPnl = status.account?.unrealized_pnl
    const unverifiedClosures = status.daily?.unverified_closures
    const releaseGateText: Record<string, {label: string; passed: string; failed: string}> = {
      credentials: {label: 'API anahtarının hesap kasasında olması', passed: 'LIVE anahtarı kayıtlı; değerleri gösterilmez.', failed: 'Hesap kasasında kullanılabilir LIVE API anahtarı yok.'},
      local_consent: {label: 'LIVE izin süresi', passed: 'LIVE izin kapısı geçti.', failed: 'LIVE izni geçerli değil.'},
      read_only: {label: 'Binance hesap snapshot’ı', passed: 'İmzalı hesap snapshot’ı hazır.', failed: 'İmzalı Binance hesap snapshot’ı hazır değil.'},
      one_way: {label: 'One-way pozisyon modu', passed: 'Hesap One-way modunda.', failed: 'Hesap One-way modunda değil.'},
      policy: {label: 'Risk politikası onayı', passed: 'Güncel risk politikası onaylı.', failed: 'Güncel risk politikası onayı yok.'},
      demo_certificate: {label: 'Demo sertifikası / LIVE izni', passed: 'Gerekli Demo sertifikası veya LIVE izni mevcut.', failed: 'Gerekli Demo sertifikası / LIVE izni tamamlanmamış.'},
    }
    addCheck('live_status', 'Güncel LIVE durumu', statusVerified === true, statusVerified ? 'Sunucu durumu doğrulandı.' : 'Sunucu durumu doğrulanmadı; eski bilgiyle girişe izin verilmez.')
    addCheck('global_binance_ban', 'Binance genel engeli', hasGlobalBanStatus, hasGlobalBanStatus
      ? 'Aktif Binance genel engeli yok.'
      : backendBan?.active === true
        ? globalBanMessage || `Binance geçici engel aktif${banUntilText ? ` – bitiş saati ${banUntilText}` : '; bitiş saati sunucudan alınamadı'}.`
        : 'Binance engel durumu doğrulanamadı; girişe izin verilmez.')
    const knownFillLock = originalRuntime.fill_lock === null || typeof originalRuntime.fill_lock?.active === 'boolean'
    const fillLockReady = knownFillLock && originalRuntime.fill_lock?.active !== true
    addCheck('fill_lock', 'Dolum güvenlik kilidi', fillLockReady, fillLockReady ? 'Dolum güvenlik kilidi aktif değil.' : originalRuntime.fill_lock?.active === true ? 'Dolum güvenlik kilidi aktif; OWNER incelemesi gerekiyor.' : 'Dolum kilidi durumu doğrulanamadı.')
    addCheck('original_session', 'Original V2 oturumu', originalSessionActive, originalSessionActive ? 'Original V2 oturumu açık ve süresi devam ediyor.' : 'Original V2 oturumu kapalı veya süresi dolmuş.')
    addCheck('auto_session', 'Canlı otomasyon oturumu', autoSessionActive, autoSessionActive ? 'Canlı otomasyon oturumu etkin.' : 'Canlı otomasyon oturumu etkin değil veya süresi dolmuş.')
    addCheck('connected', 'Binance hesap bağlantısı', status.connected === true, status.connected === true ? 'Hesap bağlantısı ve snapshot hazır.' : 'Binance hesap bağlantısı veya güncel hesap snapshot’ı hazır değil.')
    addCheck('account_rate_limit', 'Binance hesap hız sınırı', !accountRateLimited, accountRateLimited ? 'Binance hesap doğrulaması / hız sınırı beklemede.' : 'Hesap hız sınırı kilidi yok.')
    addCheck('credentials', 'Kayıtlı LIVE API anahtarı', status.credentials?.configured === true, status.credentials?.configured === true ? 'Hesap kasasında kayıtlı; anahtar veya secret gösterilmez.' : 'Hesap kasasında kullanılabilir LIVE API anahtarı yok.')
    const authorizationReady = status.authorization?.valid === true && status.authorization.scope_match === true
    addCheck('authorization', 'LIVE yetkilendirmesi', authorizationReady, authorizationReady ? 'LIVE yetkisi geçerli ve hesap kapsamı eşleşiyor.' : authorizationReasonText)
    addCheck('real_trading_lock', 'Gerçek işlem kilidi', status.real_trading_locked === false, status.real_trading_locked === false ? 'Gerçek işlem kilidi kapalı.' : 'Gerçek işlem kilidi açık veya durumu doğrulanamadı.')
    const executionKnown = typeof status.execution_state === 'string' && status.execution_state.length > 0
    const executionBlockedNow = status.execution_state === 'UNKNOWN' || status.reconciliation_required === true || status.emergency?.active === true
    const executionPassed = executionKnown && !executionBlockedNow
    const executionReason = status.emergency?.active === true
      ? 'Acil durdurma aktif.'
      : status.reconciliation_required === true || status.execution_state === 'UNKNOWN'
        ? 'Belirsiz emir veya uzlaştırma çözülmeden yeni giriş yapılamaz.'
        : executionPassed
          ? `Yürütme durumu belirsiz değil (${status.execution_state === 'LOCKED' ? 'beklemede' : status.execution_state}).`
          : 'Yürütme durumu sunucudan alınamadı.'
    addCheck('execution_state', 'Yürütme / uzlaştırma durumu', executionPassed, executionReason)
    addCheck('recovery', 'LIVE recovery', status.recovery_ready === true && !status.recovery_error, status.recovery_ready === true && !status.recovery_error ? 'LIVE recovery tamamlandı.' : 'LIVE recovery tamamlanmadı veya hata bekliyor.')
    addCheck('original_daily_loss', 'Original V2 günlük kayıp sınırı', originalDailyLossReady, originalDailyLossReady
      ? `Günlük kayıp ${money(originalDaily?.loss)} / limit ${money(originalDaily?.limit)}; kilit yok.`
      : originalDaily?.locked === true ? 'Original V2 günlük kayıp kilidi aktif.' : 'Original V2 günlük kayıp durumu doğrulanamadı veya limit doldu.')
    const dailyTradesReady = typeof dailyEntries === 'number' && dailyTradeLimit !== null && dailyEntries < dailyTradeLimit
    addCheck('daily_trades', 'Günlük işlem adedi', dailyTradesReady, dailyTradesReady
      ? `Bugün ${dailyEntries}/${dailyTradeLimit} işlem; günlük sınır içinde.`
      : typeof dailyEntries === 'number' && dailyTradeLimit !== null
        ? `Günlük işlem sınırı doldu (${dailyEntries}/${dailyTradeLimit}).`
        : 'Günlük işlem sayısı veya limit doğrulanamadı.')
    const dailyLossReady = typeof realizedPnl === 'number' && dailyLossLimit !== null && realizedPnl > -dailyLossLimit
    addCheck('daily_loss', 'Günlük gerçekleşen kayıp sınırı', dailyLossReady, dailyLossReady
      ? `Günlük gerçekleşen PnL ${money(realizedPnl)}; limit içinde.`
      : typeof realizedPnl === 'number' && dailyLossLimit !== null
        ? `Günlük kayıp limiti doldu (${money(realizedPnl)} / -${money(dailyLossLimit)}).`
        : 'Günlük gerçekleşen PnL veya limit doğrulanamadı.')
    const openLossReady = typeof openPnl === 'number' && dailyLossLimit !== null && openPnl > -dailyLossLimit
    addCheck('open_loss', 'Açık zarar sınırı', openLossReady, openLossReady
      ? `Açık PnL ${money(openPnl)}; sınır içinde.`
      : typeof openPnl === 'number' && dailyLossLimit !== null
        ? `Açık zarar sınırı doldu (${money(openPnl)} / -${money(dailyLossLimit)}).`
        : 'Açık PnL veya zarar limiti doğrulanamadı.')
    const consecutiveLossReady = typeof consecutiveLosses === 'number' && consecutiveLossLimit !== null && consecutiveLosses < consecutiveLossLimit
    addCheck('consecutive_losses', 'Ardışık kayıp sınırı', consecutiveLossReady, consecutiveLossReady
      ? `Ardışık kayıp ${consecutiveLosses}/${consecutiveLossLimit}; sınır içinde.`
      : typeof consecutiveLosses === 'number' && consecutiveLossLimit !== null
        ? `Ardışık kayıp sınırı doldu (${consecutiveLosses}/${consecutiveLossLimit}).`
        : 'Ardışık kayıp verisi veya limit doğrulanamadı.')
    const verifiedPnlReady = unverifiedClosures === 0
    addCheck('verified_closures', 'Kapanış / PnL doğrulaması', verifiedPnlReady, verifiedPnlReady ? 'Doğrulanmamış kapanış yok.' : typeof unverifiedClosures === 'number' ? `${unverifiedClosures} kapanışın PnL doğrulaması bekliyor.` : 'Kapanış doğrulama durumu alınamadı.')
    const readinessGates = status.readiness?.gates
    readinessGates?.forEach((gate, index) => {
      const gateText = releaseGateText[gate.key || '']
      const passed = gate.passed === true
      addCheck(`readiness_${gate.key || index}`, gateText?.label || 'Ek LIVE güvenlik kapısı', passed, gateText
        ? (passed ? gateText.passed : gateText.failed)
        : (passed ? 'Sunucunun ek LIVE güvenlik kapısı geçti.' : 'Sunucunun ek LIVE güvenlik kapısı geçmedi.'))
    })
    const readinessPassed = status.readiness?.ready === true
      && Array.isArray(readinessGates)
      && readinessGates.every(gate => gate.passed === true)
    addCheck('readiness', 'Genel LIVE hazırlık kapıları', readinessPassed, readinessPassed ? 'Sunucunun LIVE hazırlık kapıları tamam.' : 'Bir veya daha fazla LIVE hazırlık kapısı tamam değil.')
    addCheck('ui_request', 'Devam eden ekran işlemi', !busy, busy ? 'Başka bir ekran işlemi sürüyor; tamamlanmasını bekleyin.' : 'Bekleyen ekran işlemi yok.')
  }
  const manualOrderReady = Boolean(!freeTrial && statusVerified && status && connected && authorizationValid && liveArmed && readinessReady && !recoveryRequired && !emergency)
  const setPolicy = (key: string, value: unknown) => setPolicyDraft(current => ({...(current || {}), [key]: value}))
  const numericPolicy = (key: string, fallback: number) => Number(policy[key] ?? fallback)
  const updatePolicy = () => {
    setConfirm({
      title: 'LIVE risk politikasını kaydet',
      message: 'Politika değişikliği otomasyonu durdurur ve risk onayını geçersiz kılar. Sunucu limitleri ve diğer güvenlik kapıları korunur.',
      simple: true, understanding: true,
      action: async () => {
        await call<LiveStatus>(V25, '/policy', {method: 'PUT', body: JSON.stringify({...policy, understood: true})})
      },
    })
    setConfirmText('')
    setConfirmCode('')
  }

  const testConnection = () => run('test', async () => {
    const body = credentials.apiKey && credentials.secretKey ? {mode: 'LIVE', api_key: credentials.apiKey, secret_key: credentials.secretKey} : {mode: 'LIVE'}
    const result = await call<{ok?: boolean; fingerprint?: string | null; account?: {tested_at?: string}}>(CONNECTIONS, '/test', {method: 'POST', body: JSON.stringify(body)})
    if (result?.ok !== true) throw new Error('Binance bağlantı testi başarıyla doğrulanmadı; bağlantı kapalı kaldı.')
    setTestResult({testedAt: result.account?.tested_at, fingerprint: result.fingerprint})
  }, 'LIVE API erişimi doğrulandı; test bağlantısı emir oluşturmadı.')

  const connectAndSave = () => {
    if (!credentials.apiKey || !credentials.secretKey || keyCode.length < 6) {
      setNotice({kind: 'error', text: 'LIVE API Key, Secret Key ve güncel 2FA kodunu girin.'})
      return
    }
    setTestResult(null)
    void run('connect-save', async () => {
      let saved = false
      try {
        const result = await call<ConnectionSaveResult>(CONNECTIONS, '/save', {method: 'POST', body: JSON.stringify({
          mode: 'LIVE', api_key: credentials.apiKey, secret_key: credentials.secretKey,
          confirmation: 'CANLI KASAYA KAYDET',
          totp_code: keyCode,
        })})
        const connection = result.connections?.LIVE
        const fingerprint = normalizeCredentialFingerprint(connection?.fingerprint)
        if (
          connection?.configured !== true || connection.active !== true || connection.last_test_ok !== true
          || result.verification?.readback_verified !== true || !fingerprint
          || fingerprint !== normalizeCredentialFingerprint(result.verification.submitted_fingerprint)
          || fingerprint !== normalizeCredentialFingerprint(result.verification.stored_fingerprint)
        ) throw new Error('Sunucu yeni kasa kaydının anahtar kimliğini doğrulamadı; bağlantı açılmadı.')
        saved = true
        setCredentials({apiKey: '', secretKey: '', accepted: false})
        if (!await refresh()) throw new Error('Güncel LIVE durumu okunamadı; bağlantı isteği gönderilmedi.')
        const activation = await call<LiveStatus>(CONNECTIONS, '/activate', {method: 'POST', body: JSON.stringify({
          mode: 'LIVE', confirmation: 'CANLI SALT OKUNUR BAĞLANTIYI AÇ',
          expected_fingerprint: connection.fingerprint,
        })})
        if (activation.connected !== true) throw new Error('Sunucu salt-okunur hesap bağlantısının açıldığını doğrulamadı.')
      } catch (error) {
        const phase = saved ? 'Anahtar kasada kayıtlı; salt-okunur bağlantı doğrulanamadı.' : 'Yeni anahtar kaydı doğrulanamadı; bağlantı açılmadı.'
        throw new Error(`${phase} ${connectionFailureMessage(error, [credentials.apiKey, credentials.secretKey])}`)
      } finally {
        setKeyCode('')
      }
    }, 'API anahtarı şifreli kasaya kaydedildi ve LIVE hesabı salt-okunur bağlandı. Emir kilidi kapalı kaldı.')
  }

  const recoverSession = () => {
    setConfirm({
      title: 'LIVE oturumunu geri bağla', expected: 'LIVE OTURUMUNU GERİ BAĞLA', mfa: true,
      message: 'Aynı OWNER hesabının şifreli Binance anahtarıyla hesap durumu yenilenir. Anahtarı tekrar girmeniz gerekmez; açık pozisyonlar ve mevcut güvenlik kilitleri korunur. Emir gönderilmez.',
      action: async code => {
        const result = await call<{ok: boolean; entries_enabled: boolean; orders_created: boolean}>(V25, '/session/recover', {
          method: 'POST', body: JSON.stringify({confirmation: 'LIVE OTURUMUNU GERİ BAĞLA', totp_code: code}),
        })
        if (result.ok !== true || result.entries_enabled !== false || result.orders_created !== false) throw new Error('Oturum geri bağlama sonucu doğrulanamadı.')
      },
    })
    setConfirmText('')
    setConfirmCode('')
  }

  const deactivateCredentials = () => {
    setConfirm({
      title: 'LIVE bağlantısını kapat', expected: 'BAĞLANTIYI KAPAT',
      message: 'Yeni emir yetkileri ve otomasyon sıfırlanır. Açık pozisyonlar kapatılmaz; korumaları Binance üzerinden kontrol edin.',
      action: async () => {
        await call(CONNECTIONS, '/deactivate', {method: 'POST', body: JSON.stringify({mode:'LIVE', confirmation:'BAĞLANTIYI KAPAT'})})
      },
    })
    setConfirmText('')
    setConfirmCode('')
  }

  const deleteCredentials = () => {
    setConfirm({
      title: 'LIVE anahtarını sil', expected: 'ANAHTARI KALICI SİL',
      message: 'Önce bağlantı kapatılmalıdır. Doğrulanmış açık pozisyon varsa sunucu silmeyi reddeder. Şifreli anahtar bu oturumdan kalıcı silinir; gerçek emir gönderilmez.',
      action: async () => {
        await call(CONNECTIONS, '/credentials', {method:'DELETE', body:JSON.stringify({mode:'LIVE', confirmation:'ANAHTARI KALICI SİL'})})
        setCredentials({apiKey:'', secretKey:'', accepted:false})
        setTestResult(null)
      },
    })
    setConfirmText('')
    setConfirmCode('')
  }

  const armLive = () => {
    if (freeTrial) {setNotice({kind:'error',text:'LIVE işlem MASTER MODE ile açılır'});return}
    if (!authorizationValid) {
      setNotice({kind: 'error', text: authorizationReasonText})
      return
    }
    if (!readinessReady) {
      setNotice({kind: 'error', text: 'LIVE ARM için tüm backend readiness gate’leri PASS olmalıdır.'})
      return
    }
    setConfirm({title: 'LIVE ARM onayı', message: 'Gerçek hesapta yeni emir kilidi açılır. Sunucu güvenlik kontrolleri korunur; otomasyon başlamaz.', simple: true, understanding: true, action: async () => {
      await call(V25, '/arm', {method: 'POST', body: JSON.stringify({understood: true})})
      setArmPendingSync(true)
    }})
    setConfirmText('')
  }

  const grantConsent = () => {
    setConfirm({title: '24 saatlik LIVE risk izni', message: 'Bu izin OWNER hesabına ve etkin Binance anahtarına bağlıdır. Gerçek parayla işlemde kayıp olabilir.', simple: true, understanding: true, action: async () => {
      await call(V25, '/consent', {method: 'POST', body: JSON.stringify({understood: true})})
    }})
    setConfirmText('')
  }

  const revokeConsent = () => {
    setConfirm({title: 'LIVE consent kaldırma', message: '24 saatlik LIVE risk izni, ARM penceresi ve Auto Trade yetkisi kaldırılacak. Binance pozisyonlarına veya emirlerine dokunulmaz.', expected: '24 SAATLİK CONSENTİ KALDIR', action: async () => {
      await call(V25, '/consent/revoke', {method: 'POST', body: JSON.stringify({confirmation: '24 SAATLİK CONSENTİ KALDIR'})})
      setArmPendingSync(false)
    }})
    setConfirmText('')
  }

  const acknowledgePolicy = () => {
    setConfirm({title: 'LIVE risk limitleri onayı', message: 'Mevcut sunucu limitlerini onaylıyorsun. Politika değişirse onay geçersiz olur.', simple: true, understanding: true, action: async () => {
      await call(V25, '/policy/acknowledge', {method: 'POST', body: JSON.stringify({understood: true})})
    }})
    setConfirmText('')
  }

  const stopAutoTrade = () => run('auto-stop', () => call(V25, '/auto/stop', {method: 'POST'}).then(() => undefined), 'LIVE AUTO-TRADE kapatıldı; mevcut protection yönetimi backend’de devam eder.')
  const disarmLive = () => run('disarm', () => call(V25, '/disarm', {method: 'POST'}).then(() => { setArmPendingSync(false) }), 'LIVE disarmed; AUTO-TRADE kapalı.')
  const checkRecovery = () => {
    setConfirm({title: 'LIVE recovery kontrolü', message: 'Bu özel kurtarma işlemi güncel 2FA gerektirir. Binance hesap durumu yeniden doğrulanır; yeni işlemler ayrıca onay ve ARM gerektirir.', expected: 'LIVE RECOVERY CHECK', mfa: true, action: async code => {
      await call(V25, '/recovery/check', {method: 'POST', body: JSON.stringify({confirmation: 'LIVE RECOVERY CHECK', totp_code: code})})
    }})
    setConfirmText('')
    setConfirmCode('')
  }
  const autoToggle = () => {
    if (status?.live_auto_trade || status?.auto?.enabled) return stopAutoTrade()
    if (freeTrial) {setNotice({kind:'error',text:'LIVE işlem MASTER MODE ile açılır'});return}
    if (!autoTimeframeSupported) {
      setNotice({kind: 'error', text: autoTimeframeMessage})
      return
    }
    const confirmation = {
      title: 'LIVE AUTO-TRADE onayı', message: 'REAL MONEY WILL BE USED. Otomatik işlemler yalnız mevcut V25 live safety chain üzerinden ilerler.',
      simple: true, understanding: true,
      action: async () => {
        await call(V25, '/auto/start', {method: 'POST', body: JSON.stringify({understood: true})})
      },
    }
    setConfirm(confirmation)
    setConfirmText('')
    setConfirmCode('')
  }

  const emergencyStop = () => {
    setConfirm({title: 'EMERGENCY STOP', message: 'Yeni LIVE işlemler engellenir ve otomasyon durur. Bota ait giriş ve koruma emirleri iptal edilir; doğrulanmış açık bot pozisyonları reduce-only emirle kapatılır. Yalnız yeni girişleri durdurmak için Auto Trade’i veya Original V2’yi durdurun.', expected: 'CANLI ACİL DURDUR', action: async () => { await call(V25, '/emergency', {method: 'POST', body: JSON.stringify({confirmation: 'CANLI ACİL DURDUR', close_tracked_positions: true})}) }})
    setConfirmText('')
  }

  const clearEmergency = () => {
    setConfirm({title: 'Emergency Stop kaldırma', message: 'Güncel 2FA ve temiz backend recovery gerekir. Gerçek emir kilidi kapalı kalır; yeniden ARM ayrıca gerekir.', expected: 'ACİL DURDURMAYI KALDIRIYORUM', mfa: true, action: async code => {
      await call(V25, '/emergency/clear', {method: 'POST', body: JSON.stringify({confirmation: 'ACİL DURDURMAYI KALDIRIYORUM', totp_code: code})})
    }})
    setConfirmText('')
  }

  const previewAdoption = async (adoptionSymbol: string) => {
    setBusy('adoption-preview')
    try {
      const result = await call<{
        would_create_plan?: boolean
        candidate_plan?: any
        confirm_token?: string | null
      }>(V25, '/adopt-external-position/preview', {
        method: 'POST',
        body: JSON.stringify({symbol: adoptionSymbol}),
      })
      if (result.would_create_plan !== true || !result.candidate_plan || !result.confirm_token) {
        setAdoptionCandidate(null)
        setNotice({kind: 'info', text: `${adoptionSymbol} için read-only plan oluşturulamadı.`})
        return
      }
      const candidate = {
        symbol: adoptionSymbol,
        candidatePlan: result.candidate_plan,
        confirmToken: result.confirm_token,
      }
      setAdoptionCandidate(candidate)
      setConfirm({
        title: 'Adopt & Manage',
        message: `${adoptionSymbol} pozisyonu V25 read-only plan olarak sahiplenilecek. Mevcut emirler değiştirilmeyecek, yalnızca izlenecek.`,
        simple: true,
        action: async () => {
          const executeResult = await call<{ok?: boolean; code?: string}>(V25, '/adopt-external-position', {
            method: 'POST',
            body: JSON.stringify({symbol: candidate.symbol, confirm_token: candidate.confirmToken}),
          })
          if (executeResult.ok === false && executeResult.code === 'ADOPTION_PERSISTENCE_UNCERTAIN') {
            throw new Error('Pozisyon sahiplenilmiş olabilir ancak kayıt belirsiz; reconciliation gerekiyor.')
          }
          setAdoptionCandidate(null)
          if (onRefreshStatus) await onRefreshStatus(true)
        },
      })
      setConfirmText('')
    } catch (error) {
      setAdoptionCandidate(null)
      setNotice({kind: 'error', text: error instanceof Error ? error.message : 'Pozisyon sahiplenme önizlemesi alınamadı.'})
    } finally {
      setBusy('')
    }
  }

  const fillAnalysis = () => {
    if (!analysis || !Number.isFinite(Number(analysis.entry))) {
      setNotice({kind: 'error', text: liveCopy.noSelectedCoin})
      return
    }
    setOrder(current => ({...current, symbol, direction: analysis.direction === 'SHORT' ? 'SHORT' : 'LONG', limit_price: text(analysis.entry).replace('—', ''), stop_loss: text(analysis.stop_loss).replace('—', ''), tp1: text(analysis.tp1).replace('—', ''), tp2: text(analysis.tp2).replace('—', ''), tp3: text(analysis.tp3).replace('—', '')}))
    setNotice({kind: 'ok', text: 'Seçili analiz yönü ve seviyeleri forma dolduruldu.'})
  }
  const reviewOrder = (nextOrder = order) => {
    if (freeTrial) {setNotice({kind:'error',text:'LIVE işlem MASTER MODE ile açılır'});return}
    const values = [nextOrder.margin_usdt, nextOrder.leverage, nextOrder.stop_loss, nextOrder.tp1, nextOrder.tp2, nextOrder.tp3]
    if (!nextOrder.symbol.endsWith('USDT') || values.some(value => !Number.isFinite(Number(value)) || Number(value) <= 0)) {
      setNotice({kind: 'error', text: 'Sembol, miktar, kaldıraç, Stop Loss ve tüm TP seviyeleri pozitif olmalıdır.'}); return
    }
    if (Number(nextOrder.margin_usdt) < 5 || Number(nextOrder.margin_usdt) > 100) {
      setNotice({kind: 'error', text: 'Marjin 5 ile 100 USDT arasında olmalıdır.'}); return
    }
    const policyMargin = Number(status?.policy?.max_margin_per_trade)
    if (Number.isFinite(policyMargin) && policyMargin > 0 && Number(nextOrder.margin_usdt) > policyMargin) {
      setNotice({kind: 'error', text: `Marjin, risk ayarındaki üst sınırı (${policyMargin} USDT) aşıyor. Marjini düşür veya ayarı değiştir.`}); return
    }
    const policyLeverage = Number(status?.policy?.max_leverage)
    if (Number.isFinite(policyLeverage) && policyLeverage > 0 && Number(nextOrder.leverage) > policyLeverage) {
      setNotice({kind: 'error', text: `Kaldıraç, risk ayarındaki üst sınırı (${policyLeverage}x) aşıyor. Kaldıracı düşür veya ayarı değiştir.`}); return
    }
    const leverage = Number(nextOrder.leverage)
    if (!Number.isInteger(leverage) || leverage < 1 || leverage > 50) {
      setNotice({kind: 'error', text: 'Kaldıraç 1x ile 50x arasında tam sayı olmalıdır.'}); return
    }
    if (nextOrder.order_type === 'LIMIT' && (!Number.isFinite(Number(nextOrder.limit_price)) || Number(nextOrder.limit_price) <= 0)) {
      setNotice({kind: 'error', text: 'Limit emir için pozitif bir limit fiyatı girin.'}); return
    }
    const referencePrice = nextOrder.order_type === 'LIMIT' ? Number(nextOrder.limit_price) : Number(analysis?.entry)
    const stop = Number(nextOrder.stop_loss)
    const targets = [Number(nextOrder.tp1), Number(nextOrder.tp2), Number(nextOrder.tp3)]
    if (!Number.isFinite(referencePrice) || referencePrice <= 0) {
      setNotice({kind: 'error', text: 'Stop Loss yönünü doğrulamak için giriş fiyatını analizden doldurun veya Limit seçin.'}); return
    }
    const validDirection = nextOrder.direction === 'LONG' ? stop < referencePrice && targets[0] > referencePrice : stop > referencePrice && targets[0] < referencePrice
    const orderedTargets = nextOrder.direction === 'LONG' ? targets[0] < targets[1] && targets[1] < targets[2] : targets[0] > targets[1] && targets[1] > targets[2]
    if (!validDirection) {
      setNotice({kind: 'error', text: 'Stop Loss, seçilen yöne göre giriş fiyatının doğru tarafında olmalıdır.'}); return
    }
    if (!orderedTargets) {
      setNotice({kind: 'error', text: 'TP seviyeleri seçilen yöne göre sıralı olmalıdır.'}); return
    }
    const availableBalance = Number(status?.account?.available_balance)
    if (Number.isFinite(availableBalance) && availableBalance > 0 && Number(nextOrder.margin_usdt) > availableBalance) {
      setNotice({kind: 'error', text: `Seçilen marjin ${Number(nextOrder.margin_usdt).toFixed(2)} USDT; kullanılabilir bakiye yalnızca ${availableBalance.toFixed(2)} USDT.`}); return
    }
    const payload = {...nextOrder, margin_usdt: Number(nextOrder.margin_usdt), leverage, limit_price: nextOrder.order_type === 'LIMIT' ? Number(nextOrder.limit_price) : null, stop_loss: Number(nextOrder.stop_loss), tp1: Number(nextOrder.tp1), tp2: Number(nextOrder.tp2), tp3: Number(nextOrder.tp3), intent_id: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`}
    void run('review', async () => {
      const result = await call<{message?: string; notional_usdt?: number; estimated_stop_loss_usdt?: number}>(V25, '/order/test', {method: 'POST', body: JSON.stringify(payload)})
      setReviewResult({message: result.message || 'Dry-run tamamlandı; gerçek emir gönderilmedi.', notional: result.notional_usdt, estimated_stop_loss_usdt: result.estimated_stop_loss_usdt})
      setReviewedKey(orderKey(nextOrder))
    }, 'Dry-run tamamlandı; gerçek emir gönderilmedi.')
  }

  const confirmAction = () => {
    if (actionInFlight.current || !confirm || (!confirm.simple && confirmText.trim().toUpperCase() !== confirm.expected)
        || (confirm.understanding && !confirmUnderstood)
        || (confirm.mfa && confirmCode.length < 6)) return
    const action = confirm.action
    const code = confirmCode
    setConfirm(null); setConfirmText(''); setConfirmCode('')
    void run('confirmed', () => action(code), 'LIVE backend safety zinciri işlemi tamamlandı.')
  }

  const stateRows = [
    ['LIVE LOCK', executionLocked ? 'LOCKED' : 'READY', !executionLocked],
    ['BINANCE CONNECTION', connected ? 'CONNECTED' : 'DISCONNECTED', connected],
    ['ACCOUNT STATUS', configured ? 'CONFIGURED' : 'MISSING', configured],
    ['RISK GATE', riskGate ? riskGate.passed ? 'PASS' : 'BLOCKED' : 'UNKNOWN', Boolean(riskGate?.passed)],
    ['PROTECTION', protectionState, protectionReady],
    ['AUTO TRADE', status?.live_auto_trade ? 'ON' : 'OFF', Boolean(status?.live_auto_trade)],
  ] as const

  const positions = status?.account?.positions || []
  const openOrders = status?.account?.open_orders || []
  const liveExposure = positions.reduce((sum, position) => sum + Math.abs(Number(position.quantity || 0) * Number(position.mark_price || 0)), 0)
  const positionLeverage = (position: Record<string, unknown>) => position.leverage === null || position.leverage === undefined ? '—' : `${text(position.leverage)}x`
  const terminalPlanStatuses = ['CLOSED', 'CANCELLED', 'KAPANDI', 'İPTAL']
  const findActivePlanForPosition = (position: Record<string, unknown>) => status?.plans?.find(plan => String(plan.symbol || '').toUpperCase() === String(position.symbol || '').toUpperCase() && !terminalPlanStatuses.includes(String(plan.status || '').toUpperCase()))
  const recentOrder = status?.events?.find(event => ['LIVE_ENTRY', 'LIVE_ENTRY_RECOVERED', 'LIVE_ORDER_UPDATE'].includes(String(event.kind)))
  const protectionLabelForPlan = (plan: Record<string, unknown> | undefined) => {
    const protectionStatus = String(plan?.protection_status || plan?.protection_state || 'UNKNOWN').toUpperCase()
    return protectionStatus === 'MATCHED' ? 'PROTECTED' : protectionStatus === 'MISSING' ? 'NOT PROTECTED' : 'OWNERSHIP UNKNOWN'
  }
  const protectionPriority = ['BLOCKED', 'OWNERSHIP UNKNOWN', 'NOT PROTECTED', 'EXTERNAL / NOT MANAGED', 'PROTECTED']
  const positionProtectionStates = positions.map(position => {
    const activePlanForPosition = findActivePlanForPosition(position)
    return activePlanForPosition ? protectionLabelForPlan(activePlanForPosition) : 'EXTERNAL / NOT MANAGED'
  })
  const currentProtection = recoveryRequired
    ? 'BLOCKED'
    : positions.length === 0
      ? protectionState
      : positionProtectionStates.reduce((worst, current) => protectionPriority.indexOf(current) < protectionPriority.indexOf(worst) ? current : worst, 'PROTECTED')
  const blocker = !statusVerified ? `${sharedStatusError ? `${sharedStatusError} ` : ''}Güncel LIVE durumu doğrulanamadı; ${status ? 'son görülen onaylar gösteriliyor. ' : ''}Yeni emir ve otomasyon başlatma kapalı.` : freeTrial ? 'LIVE işlem MASTER MODE ile açılır' : liveBlockerMessage(status, liveArmed)
  const autoStatusLabel = status === null ? 'UNKNOWN' : status.live_auto_trade ? executionLocked ? 'SCANNING ONLY' : 'RUNNING' : 'OFF'
  const liveState = status === null ? 'UNKNOWN' : emergency || recoveryRequired ? 'BLOCKED' : status.live_auto_trade ? executionLocked ? 'SCANNING ONLY' : 'RUNNING' : liveArmed ? 'ARMED' : readinessReady ? 'READY' : 'LOCKED'
  const activeConfirm = confirm
  const statusValue = (value: string) => value === 'READY' || value === 'CLEAR' || value === 'CONNECTED' || value === 'ON' || value === 'PROTECTED' ? 'READY' : value === 'UNKNOWN' ? 'UNKNOWN' : 'BLOCKED'
  const statusItems = [
    [liveCopy.status.liveTrading, status === null ? 'UNKNOWN' : statusValue(liveState === 'READY' || liveState === 'ARMED' ? 'READY' : liveState)],
    [liveCopy.status.autoTrade, status === null ? 'UNKNOWN' : statusValue(status.live_auto_trade ? 'ON' : 'BLOCKED')],
    [liveCopy.status.marketData, status === null ? 'UNKNOWN' : statusValue(marketDataConnected ? 'CONNECTED' : 'DISCONNECTED')],
    [liveCopy.status.liveAccount, status === null ? 'UNKNOWN' : statusValue(connected ? 'CONNECTED' : 'DISCONNECTED')],
    [liveCopy.status.risk, status === null ? 'UNKNOWN' : statusValue(riskState)],
    [liveCopy.status.exposure, status === null ? 'UNKNOWN' : statusValue(exposureState)],
    [liveCopy.status.protection, status === null ? 'UNKNOWN' : statusValue(currentProtection)],
    [liveCopy.status.recovery, status === null ? 'UNKNOWN' : statusValue(recoveryState)],
    [liveCopy.status.emergency, status === null ? 'UNKNOWN' : statusValue(emergencyState)],
  ] as const
  const closeCredentialModal = () => {
    setCredentialModalOpen(false)
    setCredentials({apiKey: '', secretKey: '', accepted: false})
  }
  const confirmationModal = activeConfirm && typeof document !== 'undefined' ? createPortal(
    <div className="liveConfirmBackdrop"><section className="liveConfirm" role="dialog" aria-modal="true">
      <h2>{activeConfirm.title}</h2><p>{activeConfirm.message}</p>
      {activeConfirm.understanding && <label><input type="checkbox" checked={confirmUnderstood} onChange={event => setConfirmUnderstood(event.target.checked)}/> Gerçek işlem ve kayıp riskini anladım</label>}
      {!activeConfirm.simple && <label>TYPE TO CONFIRM<input autoFocus value={confirmText} onChange={event => setConfirmText(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') confirmAction()}} placeholder={activeConfirm.expected}/></label>}
      {activeConfirm.mfa && <label>2FA kodu<input data-private="true" type="password" autoComplete="one-time-code" maxLength={32} value={confirmCode} onChange={event => setConfirmCode(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') confirmAction()}}/></label>}
      <div><button type="button" onClick={() => {setConfirm(null);setConfirmText('');setConfirmCode('')}}>{activeConfirm.simple ? 'HAYIR' : 'CANCEL'}</button>
        <button type="button" disabled={(activeConfirm.understanding && !confirmUnderstood) || (!activeConfirm.simple && confirmText.trim().toUpperCase() !== activeConfirm.expected) || (activeConfirm.mfa && confirmCode.length < 6)} onClick={confirmAction}>{activeConfirm.simple ? 'EVET' : 'CONFIRM'}</button>
      </div>
    </section></div>,
    document.body,
  ) : null
  const credentialModal = credentialModalOpen && typeof document !== 'undefined' ? createPortal(
    <div className="liveCredentialBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) closeCredentialModal() }}>
      <section className="liveCredentialModal" role="dialog" aria-modal="true" aria-labelledby="live-credentials-title">
        <header><div><small>{liveCopy.configureCredentials}</small><h2 id="live-credentials-title">{liveCopy.credentialsTitle}</h2></div><button type="button" onClick={closeCredentialModal} aria-label={liveCopy.close}>×</button></header>
        <p>{liveCopy.credentialsHint} Gerçek emir gönderilmez.</p>
        <label>{liveCopy.apiKey}<input data-private="true" type="password" autoComplete="new-password" value={credentials.apiKey} onChange={event => setCredentials({...credentials, apiKey: event.target.value})}/></label>
        <label>{liveCopy.secretKey}<input data-private="true" type="password" autoComplete="new-password" value={credentials.secretKey} onChange={event => setCredentials({...credentials, secretKey: event.target.value})}/></label>
        <label className="liveCredentialCheck"><input type="checkbox" checked={credentials.accepted} onChange={event => setCredentials({...credentials, accepted: event.target.checked})}/><span>Secret yalnızca şifreli sunucu kasasında tutulur.</span></label>
        {globalBanActive && <div className="liveNotice error" role="alert"><TriangleAlert aria-hidden="true"/><span>{globalBanMessage}</span></div>}
        <div className="liveCredentialActions"><button type="button" onClick={testConnection} disabled={Boolean(busy) || globalBanActive}>{busy === 'test' ? 'TEST EDİLİYOR...' : liveCopy.testConnection}</button><button type="button" className="primary" onClick={connectAndSave} disabled={Boolean(busy) || globalBanActive}>{busy === 'connect-save' ? 'BAĞLANIYOR...' : liveCopy.saveAndConnect}</button></div>
        <small className="liveCredentialNote">Test ve kaydetme bağlantı doğrulaması yapar; bu işlemler emir oluşturmaz.</small>
      </section>
    </div>,
    document.body,
  ) : null

  const safetyStatus = (value: string) => value === 'READY' || value === 'CLEAR' || value === 'PASS' ? 'ready' : value === 'UNKNOWN' ? 'unknown' : 'blocked'
  const configuredSymbols = Array.isArray(policy.allowed_symbols) ? policy.allowed_symbols as string[] : []
  const approvedSymbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
  const toggleSymbol = (value: string) => {
    const next = configuredSymbols.includes(value) ? configuredSymbols.filter(item => item !== value) : [...configuredSymbols, value]
    setPolicy('allowed_symbols', next)
  }
  const activity = (status?.events || []).slice(-8).reverse()
  const externalDate = (value: unknown) => {
    const timestamp = Number(value)
    return Number.isFinite(timestamp) && timestamp > 0 ? new Date(timestamp).toLocaleString('tr-TR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : date(value)
  }
  useEffect(() => {
    if (!active || !status) return
    void call<ExternalHistory>(V25, '/history?limit=1000').then(setExternalHistory).catch(() => setExternalHistory(null))
  }, [active, status?.events?.length, status?.plans?.length])
  const ownershipUncertain = status?.events?.find(event => String(event.kind) === 'OWNERSHIP_UNCERTAIN')
  const latestSignal = activity.find(event => /SIGNAL|ENTRY|ORDER|SCAN|CANDIDATE/i.test(String(event.kind || '')))
  const signalStatus = status?.live_auto_trade ? autoStatusLabel : latestSignal ? text(latestSignal.kind).replaceAll('_', ' ') : 'WAITING'
  const originalActive = status?.original_v2_live?.enabled === true
  const originalScanner = status?.original_v2_live?.scanner
  const scannerStage = originalActive ? originalScanner?.stage : status?.scanner?.last_cycle_stage
  const originalScannerLabels: Record<string, string> = {
    UNIVERSE_PENDING: 'Günlük sembol listesi bekleniyor',
    UNIVERSE_REFRESH: 'Günlük sembol listesi yenileniyor',
    SCANNING: 'Sembol taraması sürüyor',
    SCAN_COMPLETE: 'Tarama tamamlandı',
    WAITING_NEXT_CANDLE: 'Sonraki 15m mum bekleniyor',
    WAITING_RISK_DATA: 'Günlük risk verisi bekleniyor',
    RATE_LIMIT_WAIT: 'Binance istek sınırı bekleniyor',
    NO_ELIGIBLE_SYMBOLS: 'Filtrelere uyan sembol yok',
    BLOCKED: 'Yeni girişler engellendi',
  }
  const scannerStatus = originalActive
    ? originalScannerLabels[scannerStage || ''] || scannerStage || 'İlk tarama bekleniyor'
    : scannerStage || (status?.scanner?.last_scan_at ? 'WAITING FOR NEXT SCAN' : 'WAITING')
  const candidateCount = originalActive ? originalScanner?.candidate_count ?? 0 : status?.scanner?.selected_symbols_count ?? status?.scanner?.candidate_count ?? 0
  const automationReason = originalActive ? status?.original_v2_live?.pause_reason || `Original V2 · ${status?.original_v2_live?.symbols.length} sembol · risk %${status?.original_v2_live?.risk_percent}` : status?.auto?.last_error || status?.auto?.last_skip_reason || status?.scanner?.last_skip_reason || status?.auto?.last_decision || blocker
  const safetyRows = [
    ['Account', configured && connected ? 'PASS' : 'BLOCKED', configured && connected ? 'LIVE account verified' : 'Connect and verify LIVE API'],
    ['Risk', riskState === 'READY' ? 'PASS' : riskState, riskGate?.detail || 'Risk policy is backend-controlled'],
    ['Exposure', exposureState === 'READY' ? 'PASS' : exposureState, activePlanState === 'CONFLICT' ? 'Active plan conflict' : `Current exposure ${money(liveExposure)}`],
    ['Protection', protectionState === 'READY' ? 'PASS' : protectionState, protectionGate?.detail || 'Protection readiness is backend-controlled'],
    ['LIVE lock', manualOrderReady ? 'MANUAL READY' : 'BLOCKED', manualOrderReady ? 'Manual LIVE orders require the active ARM window and final EVET confirmation.' : blocker],
    ['Execution', status?.execution_state === 'UNKNOWN' || status?.reconciliation_required ? 'BLOCKED' : readinessReady ? 'PASS' : 'WARNING', status?.reconciliation_required ? 'Reconciliation required' : blocker],
  ] as const

  const renderManualOrderCard = () => {
    const q = coinQuery.trim().toUpperCase().replace('/', '')
    const rows = universe ?? []
    const selectedRow = rows.find(row => row.symbol === order.symbol)
    const matches = (q ? rows.filter(row => row.symbol.includes(q) || String(row.display || '').toUpperCase().replace('/', '').includes(q)) : rows)
    const listed = matches.slice(0, 80)
    const recentRows = !q ? recentCoins.map(item => rows.find(row => row.symbol === item)).filter((row): row is CoinRow => Boolean(row)) : []
    const chooseCoin = (value: string) => { setOrder(current => ({...current, symbol: value})); setCoinOpen(false); setCoinQuery(''); saveRecentCoin(value); setRecentCoins(readRecentCoins()) }
    const marginValue = Number(order.margin_usdt)
    const leverageValue = Number(order.leverage)
    const entryRef = order.order_type === 'LIMIT' ? Number(order.limit_price) : order.symbol === symbol && Number.isFinite(Number(analysis?.entry)) ? Number(analysis?.entry) : Number(selectedRow?.price)
    const notional = marginValue * leverageValue
    const stopValue = Number(order.stop_loss)
    const tp1Value = Number(order.tp1)
    const lossAtStop = Number.isFinite(entryRef) && entryRef > 0 && stopValue > 0 && notional > 0 ? notional * Math.abs(entryRef - stopValue) / entryRef : null
    const gainAtTp1 = Number.isFinite(entryRef) && entryRef > 0 && tp1Value > 0 && notional > 0 ? notional * Math.abs(tp1Value - entryRef) / entryRef : null
    const rewardRisk = lossAtStop && gainAtTp1 ? gainAtTp1 / lossAtStop : null
    const risk = riskPrev && !('error' in riskPrev) ? riskPrev : null
    const riskError = riskPrev && 'error' in riskPrev ? `Bu stop kabul edilmez: ${riskPrev.error.replace(/^Risk preview reddedildi:\s*/i, '')}` : ''
    const realNotional = riskError ? NaN : risk ? risk.notional : notional
    const realLoss = riskError ? null : risk ? risk.loss : lossAtStop
    const realGain = riskError ? null : risk && Number.isFinite(entryRef) && entryRef > 0 && tp1Value > 0 ? risk.notional * Math.abs(tp1Value - entryRef) / entryRef : gainAtTp1
    const realRR = realLoss && realGain ? realGain / realLoss : null
    const reviewed = reviewedKey !== '' && reviewedKey === orderKey(order)
    const sendBlock = riskError ? riskError : !manualOrderReady ? (blocker && blocker !== 'Engel yok.' ? blocker : 'Önce 1-2. adımları tamamla ve ARM\'ı aç.') : !reviewed ? 'Önce "Dry-run ile kontrol et" düğmesine bas.' : ''
    const sendOrder = () => {
      if (!manualOrderReady || !reviewed) { setNotice({kind: 'error', text: sendBlock || 'Emir göndermeden önce dry-run kontrolü gerekir.'}); return }
      const payload = {...order, margin_usdt: marginValue, leverage: leverageValue, limit_price: order.order_type === 'LIMIT' ? Number(order.limit_price) : null, stop_loss: stopValue, tp1: tp1Value, tp2: Number(order.tp2), tp3: Number(order.tp3), intent_id: `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`}
      setConfirm({title: 'CANLI MANUEL EMİR', message: `GERÇEK PARA KULLANILACAK. ${order.symbol} · ${order.direction} · ${order.order_type === 'LIMIT' ? 'LİMİT' : 'PİYASA'} · marjin ${marginValue} USDT · ${leverageValue}x · stop ${order.stop_loss}. Emir tüm V25 güvenlik kontrollerinden geçerse gönderilir.`, expected: 'CANLI EMİR GÖNDER', action: async () => {
        await call(V25, '/order', {method: 'POST', body: JSON.stringify({...payload, confirmation: 'CANLI EMİR GÖNDER'})})
        setReviewedKey(''); setReviewResult(null)
      }})
      setConfirmText('')
    }
    const set = (key: keyof OrderDraft, value: string) => setOrder(current => ({...current, [key]: value}))
    const armable = authorizationValid && readinessReady && !liveArmed && !freeTrial
    const wallet = Number(status?.account?.wallet_balance)
    const walletShare = wallet > 0 && marginValue > 0 ? marginValue / wallet * 100 : null
    const presetPrice = (value: number) => String(Number(value.toPrecision(6)))
    const applyPreset = (pct: number) => {
      if (!(entryRef > 0)) return
      const sign = order.direction === 'LONG' ? 1 : -1
      setOrder(current => ({...current, stop_loss: presetPrice(entryRef * (1 - sign * pct / 100)), tp1: presetPrice(entryRef * (1 + sign * pct / 100)), tp2: presetPrice(entryRef * (1 + sign * 2 * pct / 100)), tp3: presetPrice(entryRef * (1 + sign * 3 * pct / 100))}))
    }
    return <div className="liveWsOrder liveUxOrderGrid">
      {!manualOrderReady && <div className="manualLockStrip" role="status"><div><b>Emir şu an kilitli</b><small>{sendBlock}</small></div>{armable && <button type="button" className="liveWsPrimary" onClick={armLive} disabled={Boolean(busy)}>İzni aç</button>}</div>}
      <section className="liveWsOrderForm liveUxManual" data-locked={masterTrade && executionLocked || undefined}>
        <div className="liveWsOrderHead"><Send/><div><b>Emir hazırla</b><small>Önce kontrol et, sonra gönder. Gerçek emir yazı onayı ister.</small></div><span className={`liveWsTag ${executionLocked ? 'warn' : ''}`}>{executionLocked ? 'KİLİTLİ' : 'HAZIR'}</span></div>

        <div className="liveWsBlock liveWsCoin">
          <span className="liveWsLabel">1 · Coin</span>
          <button type="button" className="liveWsCoinBtn" aria-haspopup="listbox" aria-expanded={coinOpen} onClick={() => setCoinOpen(value => !value)}>
            <b>{selectedRow?.display || order.symbol || 'Coin seç'}</b>
            <small>{selectedRow ? `${coinPrice(selectedRow.price)} USDT · ${Number(selectedRow.change) >= 0 ? '+' : ''}${Number(selectedRow.change).toFixed(2)}%` : universeError ? 'Liste alınamadı' : 'Listeden seç'}</small>
            <ChevronDown/>
          </button>
          {coinOpen && <div className="liveWsCoinPop">
            <label className="liveWsSearch"><Search/><input autoFocus value={coinQuery} placeholder="Coin ara: BTC, ETH, SOL…" aria-label="Coin ara" onChange={event => setCoinQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') setCoinOpen(false); if (event.key === 'Enter' && listed[0]) chooseCoin(listed[0].symbol); if (event.key === 'Enter' && !listed[0] && /^[A-Z0-9]{5,20}$/.test(q)) chooseCoin(q) }}/></label>
            {recentRows.length > 0 && <div className="liveWsRecent"><small>Son kullanılanlar</small><div>{recentRows.map(row => <button type="button" key={row.symbol} onClick={() => chooseCoin(row.symbol)}>{row.display || row.symbol}</button>)}</div></div>}
            {universe === null && !universeError && <p className="liveWsHint">Coin listesi yükleniyor…</p>}
            {universeError && <p className="liveWsWarn">Liste alınamadı. Coin kodunu yazıp Enter'a bas (ör. BTCUSDT).</p>}
            {universe !== null && <ul role="listbox" aria-label="Coin listesi" className="liveWsCoinList">{listed.map(row => <li key={row.symbol}><button type="button" role="option" aria-selected={row.symbol === order.symbol} onClick={() => chooseCoin(row.symbol)}><b>{row.display || row.symbol}</b><span>{coinPrice(row.price)}</span><em className={Number(row.change) >= 0 ? 'up' : 'down'}>{Number(row.change) >= 0 ? '+' : ''}{Number(row.change).toFixed(2)}%</em></button></li>)}{listed.length === 0 && <li className="liveWsHint">Sonuç yok.</li>}</ul>}
            {universe !== null && matches.length > listed.length && <small className="liveWsHint">{matches.length} sonuçtan ilk {listed.length} gösteriliyor; aramayı daralt.</small>}
          </div>}
          {order.symbol !== symbol && <small className="liveWsHint">Analiz seviyeleri {symbol} içindir; "Analizden doldur" coini de {symbol} yapar.</small>}
        </div>

        <div className="liveWsBlock">
          <span className="liveWsLabel">2 · Yön</span>
          <div className="liveChoice liveWsDir"><button type="button" aria-pressed={order.direction === 'LONG'} className={order.direction === 'LONG' ? 'selectedLong' : ''} onClick={() => set('direction', 'LONG')}>LONG</button><button type="button" aria-pressed={order.direction === 'SHORT'} className={order.direction === 'SHORT' ? 'selectedShort' : ''} onClick={() => set('direction', 'SHORT')}>SHORT</button></div>
        </div>

        <div className="liveWsBlock">
          <span className="liveWsLabel">3 · Giriş ve miktar</span>
          <div className="liveWsSeg" role="group" aria-label="Emir türü"><button type="button" aria-pressed={order.order_type === 'MARKET'} className={order.order_type === 'MARKET' ? 'on' : ''} onClick={() => set('order_type', 'MARKET')}>Piyasa</button><button type="button" aria-pressed={order.order_type === 'LIMIT'} className={order.order_type === 'LIMIT' ? 'on' : ''} onClick={() => set('order_type', 'LIMIT')}>Limit</button></div>
          <div className="liveWsTwo">
            <label className="liveWsField">Marjin (USDT)<input type="number" min="5" max="100" value={order.margin_usdt} onChange={event => set('margin_usdt', event.target.value)}/><span className="liveWsChips">{[10, 25, 50, 100].map(v => <button type="button" key={v} onClick={() => set('margin_usdt', String(v))}>{v}</button>)}</span><input type="range" className="manualRange" min="5" max="100" step="1" value={Number.isFinite(marginValue) && marginValue > 0 ? marginValue : 10} onChange={event => set('margin_usdt', event.target.value)} aria-label="Marjin kaydırıcı"/>{walletShare !== null && <small className="liveWsHint">Hesabının %{walletShare.toFixed(1)}'i kullanılır</small>}</label>
            <label className="liveWsField">Kaldıraç (x)<input type="number" min="1" max="50" value={order.leverage} onChange={event => set('leverage', event.target.value)}/><span className="liveWsChips">{[1, 2, 3, 5, 10].map(v => <button type="button" key={v} onClick={() => set('leverage', String(v))}>{v}x</button>)}</span><input type="range" className="manualRange" min="1" max="50" step="1" value={Number.isFinite(leverageValue) && leverageValue > 0 ? leverageValue : 1} onChange={event => set('leverage', event.target.value)} aria-label="Kaldıraç kaydırıcı"/><small className="liveWsHint">Düşük kaldıraç daha güvenlidir</small></label>
          </div>
          {order.order_type === 'LIMIT' && <label className="liveWsField">Limit fiyatı<input type="number" value={order.limit_price} onChange={event => set('limit_price', event.target.value)}/></label>}
        </div>

        <div className="liveWsBlock">
          <span className="liveWsLabel">4 · Stop ve hedefler</span>
          <div className="liveWsChips manualPresets" role="group" aria-label="Hazır stop ve hedef"><small className="liveWsHint">Hazır:</small>{[1, 2, 3].map(pct => <button type="button" key={pct} disabled={!(entryRef > 0)} onClick={() => applyPreset(pct)}>Stop %{pct} · hedef 1R/2R/3R</button>)}</div>
          <div className="liveWsTwo">
            <label className="liveWsField">Stop Loss<input type="number" value={order.stop_loss} onChange={event => set('stop_loss', event.target.value)}/></label>
            <label className="liveWsField">Hedef 1 (TP1)<input type="number" value={order.tp1} onChange={event => set('tp1', event.target.value)}/></label>
            <label className="liveWsField">Hedef 2 (TP2)<input type="number" value={order.tp2} onChange={event => set('tp2', event.target.value)}/></label>
            <label className="liveWsField">Hedef 3 (TP3)<input type="number" value={order.tp3} onChange={event => set('tp3', event.target.value)}/></label>
          </div>
          <div className="liveWsRow"><button type="button" className="liveWsGhost" onClick={fillAnalysis}><CircleDollarSign/> Analizden doldur</button></div>
          {tradingDefaults.preferences?.risk_per_trade && <div className="liveWsRow"><span className="liveWsHint">Kişisel risk tercihi: %{tradingDefaults.preferences.risk_per_trade}</span><button type="button" className="liveWsGhost" disabled={Boolean(busy) || !manualOrderReady} onClick={() => {
            const result = preferredRiskMargin({wallet: Number(status?.account?.wallet_balance), available: Number(status?.account?.available_balance), entry: order.order_type === 'LIMIT' ? Number(order.limit_price) : Number(analysis?.entry), stop: Number(order.stop_loss), leverage: Number(order.leverage), percent: tradingDefaults.preferences.risk_per_trade})
            setSizingNotice(result.reason)
            if (result.margin !== null) setOrder(current => ({...current, margin_usdt: String(result.margin)}))
          }}>Tercih edilen riskle marjı hesapla</button></div>}
          {(sizingNotice || tradingDefaults.error) && <p className="liveWsHint" role="status">{sizingNotice || `İşlem tercihleri alınamadı: ${tradingDefaults.error}`}</p>}
        </div>
      </section>

      <aside className="liveWsOrderSummary liveUxSummary" aria-label="Emir özeti">
        <div className="liveWsOrderHead"><ShieldCheck/><div><b>{selectedRow?.display || order.symbol || 'Coin seçilmedi'}</b><small>Emir özeti</small></div></div>
        <div className="liveUxSummaryLead"><strong className={order.direction === 'LONG' ? 'up' : 'down'}>{order.direction}</strong><span>{order.order_type === 'LIMIT' ? 'Limit' : 'Piyasa'}</span><span>{Number.isFinite(leverageValue) ? `${leverageValue}x` : '—'}</span></div>
        <div className="liveWsSumGrid">
          <span><small>Marjin</small><b>{text(order.margin_usdt)} USDT</b></span>
          <span><small>Pozisyon büyüklüğü</small><b>{Number.isFinite(realNotional) ? money(realNotional) : '—'}</b></span>
          <span><small>Stop Loss</small><b>{text(order.stop_loss)}</b></span>
          <span><small>TP1 / TP2 / TP3</small><b>{text(order.tp1)} / {text(order.tp2)} / {text(order.tp3)}</b></span>
          <span><small>Tahmini zarar (stopta)</small><b className="down">{realLoss === null || realLoss === undefined ? '—' : money(realLoss)}</b></span>
          <span><small>Tahmini kâr (TP1)</small><b className="up">{realGain === null || realGain === undefined ? '—' : money(realGain)}{realRR ? ` · 1:${realRR.toFixed(1)}` : ''}</b></span>
        </div>
        {risk && <small className="liveWsHint">Stop'ta kaybın en çok {money(risk.loss)} olur. {risk.capped ? `İşlem başı risk ayarın yüzünden pozisyon küçültüldü: girdiğin ${text(order.margin_usdt)} USDT marjinin tamamı değil, ${money(risk.margin)} kullanılır.` : 'Girdiğin marjin tamamen kullanılır.'}</small>}
        {riskError && <p className="liveWsWarn" role="alert">{riskError}</p>}
        <small className="liveWsHint">Tahminler giriş fiyatı ve kaldıraçla hesaplanır; komisyon ve kayma dahil değildir.</small>
        {reviewResult && reviewed && <p className="liveWsOk" role="status">{reviewResult.message} Gerçek emir gönderilmedi.</p>}
        <div className="liveWsSendRow">
          <button type="button" className="liveWsGhost" onClick={() => reviewOrder()} disabled={Boolean(busy)}>{busy === 'review' ? <RefreshCw className="spin"/> : <Send/>} Dry-run ile kontrol et</button>
          <button type="button" className="liveWsPrimary danger" onClick={sendOrder} disabled={Boolean(busy) || Boolean(sendBlock)}><Send/> Emri gönder</button>
        </div>
        {sendBlock && sendBlock !== riskError && <small className="liveWsHint">{sendBlock}</small>}
      </aside>
    </div>
  }

  if (!active) return null
  if (!masterTrade) return <section className="originalLiveWorkspace" aria-label="Canlı İşlem"><h2>Canlı İşlem</h2><p>Canlı strateji ve risk ayarları Master Trade’de, API anahtarları yalnız Bağlantı sekmesinde yönetilir.</p><a href="/master-trade?tab=canli">Canlı İşlem’i aç →</a><a href="/master-trade?tab=baglanti">Bağlantı ayarları →</a></section>
  const statusNotice = !statusVerified && statusLoading
    ? <p role="status">Kayıtlı anahtar ve güncel Binance bakiyesi okunuyor; emir yetkisi kapalı.</p>
    : !statusVerified
    ? <div className="liveNotice error" role="alert" data-testid="live-status-unverified"><TriangleAlert aria-hidden="true"/><span>{localStatusError && !sharedStatusError ? `${localStatusError} ` : ''}{blocker}</span>{[sharedStatusError, notice.text].some(message => message.includes('LIVE kontrolü başka bir kullanıcı veya oturuma ait.')) && <button type="button" className="masterTradeLiveSecondary" onClick={recoverSession} disabled={Boolean(busy)} title="Aynı OWNER ve hesap için 2FA ile oturumu geri bağla"><RefreshCw size={16} aria-hidden="true"/> LIVE oturumunu geri bağla</button>}</div>
    : !liveArmed && status?.live_lock_reason?.message
      ? <div className="liveNotice error" role="alert" data-testid="live-arm-cancellation"><TriangleAlert aria-hidden="true"/><span>{status.live_lock_reason.message}</span></div>
      : status?.account_rate_limit?.pending_verification
        ? <div className="liveNotice error" role="alert" data-testid="live-account-rate-limit-wait"><TriangleAlert aria-hidden="true"/><span>{blocker}</span></div>
      : null
  if (masterTrade && (masterTradeTab ?? 'canli') === 'canli' && status?.original_v2_live?.available) {
    const snapshotVerified = statusVerified && connected && !recoveryRequired
      && !status.account_rate_limit?.pending_verification
      && typeof status.account?.wallet_balance === 'number' && Number.isFinite(status.account.wallet_balance)
      && Array.isArray(status.account?.positions)
    const prepareOriginal = async () => {
      if (!authorizationValid) await call(V25, '/consent', {method: 'POST', body: JSON.stringify({understood: true})})
      if (!status.policy_acknowledged) await call(V25, '/policy/acknowledge', {method: 'POST', body: JSON.stringify({understood: true})})
      if (!liveArmed) await call(V25, '/arm', {method: 'POST', body: JSON.stringify({understood: true})})
    }
    return <PremiumWorkspace><section className="originalLiveWorkspace" aria-label="Canlı İşlem">
      <div className="originalConnectionStrip"><span data-connected={connected && statusVerified}>{connected && statusVerified ? '● Binance bağlı' : 'Binance bağlantısı doğrulanmadı'}</span><a href="/master-trade?tab=baglanti">Bağlantı ayarları →</a></div>
      {statusNotice}
      {globalBanActive && <p role="alert" data-testid="binance-global-ban">{globalBanMessage}</p>}
      <OriginalV2LiveCard
        key={userId}
        className="masterTradeLiveOriginalCard" status={status.original_v2_live}
        entryChecks={originalEntryChecks} armed={liveArmed} automationActive={status.live_auto_trade === true}
        blocked={Boolean(busy) || !statusVerified || !connected || recoveryRequired || freeTrial || globalBanActive}
        connected={connected && statusVerified} statusVerified={statusVerified} prepareStart={prepareOriginal}
        loading={statusLoading && !snapshotVerified} balanceError={sharedStatusError || localStatusError || status.connection?.last_error || status.recovery_error || status.account_rate_limit?.message} userId={userId}
        request={(path, body) => call(V25, path, {method: 'POST', ...(body ? {body: JSON.stringify(body)} : {})})}
        readDiagnostics={() => call(V25, '/original-v2/persistence-diagnostics', {method: 'GET', cache: 'no-store'})}
        refresh={() => refresh(false)} notice={originalNotice} onNotice={setOriginalNotice}
        walletBalance={snapshotVerified ? status.account?.wallet_balance : null}
        overview={<OriginalLiveOverview status={status} verified={snapshotVerified} findPlan={findActivePlanForPosition}/>}
      />
      <details className="originalSurface originalManualOrder"><summary>Manuel emir · gerçek para</summary>
        <div id="master-trade-live-terminal" className="liveWs">{renderManualOrderCard()}</div>
      </details>
      <details className="originalAdvanced"><summary>Gelişmiş · kurtarma ve eski V25</summary>
        <button type="button" onClick={checkRecovery} disabled={Boolean(busy)}>Kurtarma kontrolü</button>
        <button type="button" onClick={clearEmergency} disabled={Boolean(busy) || !emergency}>Acil durdurma kilidini kaldır</button>
        <button type="button" onClick={emergencyStop} disabled={Boolean(busy)}>Pozisyonları kapatan tam acil durdurma</button>
        <button type="button" onClick={autoToggle} disabled={Boolean(busy) || status.original_v2_live.enabled || (!status.live_auto_trade && !autoReady)}>Eski V25 otomasyonu</button>
      </details>
      {confirmationModal}
    </section></PremiumWorkspace>
  }
  if (masterTrade) {
    const running = Boolean(status?.live_auto_trade)
    const consentDone = authorizationValid && status?.policy_acknowledged === true
    const step1Done = configured && connected
    const stepDone = [step1Done, step1Done && consentDone && readinessReady, running, false]
    const currentStep = !step1Done ? 0 : !(consentDone && readinessReady) ? 1 : 2
    const shownStep = openStep ?? currentStep
    const stepOpen = (index: number) => shownStep === index
    const stepLocked = (index: number) => index === 1 ? !step1Done : false
    const focusApiKey = () => { setOpenStep(0); window.setTimeout(() => document.getElementById('live-api-key')?.focus({preventScroll: true}), 60) }
    const next = status === null
      ? {tone: 'wait', title: 'Durum yükleniyor', text: 'Canlı hesap bilgileri alınıyor.', label: 'Yenile', run: () => void refresh(false), off: Boolean(busy) || refreshPending}
      : emergency || recoveryRequired
        ? {tone: 'stop', title: 'Canlı işlem şu an durduruldu', text: status?.reconciliation_diagnostic?.exception_message || status?.recovery_error || 'Önce kurtarma kontrolü tamamlanmalı. Bu sırada yeni emir açılmaz.', label: 'Kurtarma kontrolünü yap', run: checkRecovery, off: Boolean(busy)}
        : !step1Done
          ? {tone: 'go', title: 'Binance API anahtarını bağla', text: 'İlk adım: hesabını güvenli şekilde bağla. Emir açmaz, sadece okur.', label: 'Bağlantıyı başlat', run: focusApiKey, off: false}
          : !authorizationValid
            ? {tone: 'go', title: '24 saatlik izni ver', text: 'Canlı işlem gerçek parayla çalışır. Devam etmek için riski 24 saatliğine kabul etmelisin.', label: '24 saatlik izni ver', run: grantConsent, off: Boolean(busy)}
            : status?.policy_acknowledged !== true
              ? {tone: 'go', title: 'Risk limitlerini onayla', text: 'Günlük zarar, pozisyon ve maruziyet limitlerini görüp onaylaman gerekiyor.', label: 'Limitleri onayla', run: acknowledgePolicy, off: Boolean(busy)}
              : !readinessReady
                ? {tone: 'wait', title: 'Güvenlik kontrolleri tamamlanıyor', text: blocker || 'Sistem hazır olunca bir sonraki adım açılır.', label: 'Durumu yenile', run: () => void refresh(false), off: Boolean(busy) || refreshPending}
                : !liveArmed
                  ? {tone: 'go', title: 'Canlı emir iznini aç (ARM)', text: 'Son güvenlik onayı. Bu, otomatik işlemin emir açabilmesi için gerekli.', label: 'Canlı emir iznini aç', run: armLive, off: Boolean(busy)}
                  : !running
                    ? {tone: 'go', title: 'Otomatik işlemi başlat', text: autoReady ? 'Her şey hazır. Başlattığında strateji seçili coinleri tarar ve kurallara uyan fırsatta emir açar.' : (blocker || 'Başlatma şartları henüz tamamlanmadı.'), label: 'Otomatik işlemi başlat', run: autoToggle, off: Boolean(busy) || !autoReady}
                    : {tone: 'ok', title: 'Otomatik işlem çalışıyor', text: 'Seçili coinler izleniyor. İstediğin an durdurabilirsin.', label: 'Durdur', run: stopAutoTrade, off: Boolean(busy)}
    const stateText = status === null ? 'Bilinmiyor' : emergency || recoveryRequired ? 'Durduruldu' : running ? (executionLocked ? 'Sadece tarıyor' : 'Çalışıyor') : liveArmed ? 'Hazır (ARM açık)' : readinessReady ? 'Hazır' : 'Kilitli'
    const gateTr = (value: string) => value === 'READY' || value === 'CLEAR' ? 'Tamam' : value === 'UNKNOWN' ? 'Bilinmiyor' : 'Engelli'
    const marketsText = configuredSymbols.length ? configuredSymbols.map(item => item.replace('USDT', '')).join(', ') : 'Varsayılan liste'
    const stepsMeta = [
      {key: 'baglanti', title: '1 · Bağlantı', sub: step1Done ? 'Binance hesabın bağlı' : 'API anahtarını bağla'},
      {key: 'izinler', title: '2 · İzinler', sub: consentDone ? (readinessReady ? 'İzinler tamam' : 'Güvenlik kontrolü bekleniyor') : 'Sırayla iki onay gerekiyor'},
      {key: 'auto', title: '3 · Otomatik işlem', sub: running ? 'Çalışıyor' : 'Strateji ve başlatma'},
      {key: 'manuel', title: '4 · Manuel emir', sub: manualOrderReady ? 'Hazır' : 'Önce 1-2. adımlar ve ARM'},
    ]
    const connectionBlock = <div className="liveWsConnect">
      <div className="liveWsConnectHead"><span className={`liveWsDot ${connected ? 'on' : ''}`}/><div><b>{connected ? 'Hesap bağlı' : 'Hesap bağlı değil'}</b><small>Binance · USDT-M Futures · Son kontrol: {date(testResult?.testedAt || status?.connection?.last_checked)}</small></div></div>
      {status?.connection?.last_error && !connected && <p className="liveWsWarn">Bağlantı hatası: {text(status.connection.last_error)}</p>}
      <div className="liveWsFields">
        <label>API anahtarı<input id="live-api-key" data-private="true" type="password" autoComplete="new-password" value={credentials.apiKey} onChange={event => setCredentials({...credentials, apiKey: event.target.value})} placeholder="API anahtarını yapıştır"/></label>
        <label>Gizli anahtar<div className="masterTradeLiveSecretField"><input data-private="true" type={showSecret ? 'text' : 'password'} autoComplete="new-password" value={credentials.secretKey} onChange={event => setCredentials({...credentials, secretKey: event.target.value})} placeholder="Gizli anahtarı yapıştır"/><button type="button" aria-label={showSecret ? 'Gizli anahtarı gizle' : 'Gizli anahtarı göster'} onClick={() => setShowSecret(value => !value)}>{showSecret ? <EyeOff/> : <Eye/>}</button></div></label>
        <label>2FA kodu<input data-private="true" type="password" autoComplete="one-time-code" inputMode="numeric" value={keyCode} onChange={event => setKeyCode(event.target.value)} maxLength={32} placeholder="Doğrulama uygulamasındaki 6 haneli kod"/></label>
      </div>
      <label className="liveWsCheck masterTradeLiveCheck"><input type="checkbox" checked={credentials.accepted} onChange={event => setCredentials({...credentials, accepted: event.target.checked})}/><span>Anahtarlarımın şifreli sunucu kasasında saklanmasını kabul ediyorum.</span></label>
      <div className="liveWsRow"><button type="button" className="liveWsGhost" onClick={testConnection} disabled={Boolean(busy) || !connections?.vault?.ready}>{busy === 'test' ? <RefreshCw className="spin"/> : <ShieldCheck/>} Bağlantıyı test et</button><button type="button" className="liveWsPrimary" onClick={connectAndSave} disabled={Boolean(busy)}>{busy === 'connect-save' ? <RefreshCw className="spin"/> : <LockKeyhole/>} Kaydet ve bağlan</button></div>
      <p className="liveWsHint">Futures izni olan, para çekme izni olmayan bir anahtar kullan. Bu adım emir açmaz.</p>
    </div>
    const manualOrderCard = renderManualOrderCard()
    return <PremiumWorkspace>
      {notice.kind !== 'info' && <div className={`liveNotice masterTradeLiveNotice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'error' ? <TriangleAlert/> : <CheckCircle2/>}<span>{notice.text}</span></div>}
      <section id="master-trade-live-terminal" className="liveWs" aria-label="Canlı işlem" hidden={masterTradeTab === 'pozisyonlar'}>
        {masterTradeTab === 'canli' && <>
          <header className={`liveWsHero ${next.tone}`}>
            <div className="liveWsHeroTop"><div><small>CANLI İŞLEM · GERÇEK PARA</small><h2>Canlı İşlem</h2></div><span className={`liveWsPill ${running ? 'on' : emergency || recoveryRequired ? 'stop' : ''}`}><i/>{stateText}</span></div>
            <div id="master-trade-auto-trade" tabIndex={-1} aria-label="Şimdi yapman gereken" className="liveWsNext">
              <small>ŞİMDİ YAPMAN GEREKEN</small>
              <strong>{next.title}</strong>
              <p>{next.text}</p>
              {timeframeWarning}
              {status?.reconciliation_diagnostic && <p className="liveWsWarn" role="alert">Uzlaştırma hatası: {text(status.reconciliation_diagnostic.exception_message)}</p>}
              <button type="button" className="liveWsPrimary big" onClick={next.run} disabled={next.off}>{next.label}</button>
            </div>
            <ol className="liveWsStepper" aria-label="Canlı işlem adımları">{stepsMeta.map((item, index) => <li key={item.key} className={stepDone[index] ? 'done' : index === currentStep ? 'current' : stepLocked(index) ? 'locked' : ''}><button type="button" disabled={stepLocked(index)} onClick={() => setOpenStep(index)} aria-current={index === currentStep ? 'step' : undefined}><span className="liveWsNum">{stepDone[index] ? <CheckCircle2/> : stepLocked(index) ? <LockKeyhole/> : index + 1}</span><span><b>{item.title.slice(4)}</b><small>{item.sub}</small></span></button></li>)}</ol>
          </header>

          <section className={`liveWsCard ${stepOpen(0) ? 'open' : ''}`}>
            <button type="button" className="liveWsCardHead" aria-expanded={stepOpen(0)} onClick={() => setOpenStep(stepOpen(0) ? -1 : 0)}><span className={`liveWsBadge ${step1Done ? 'done' : 'current'}`}>{step1Done ? <CheckCircle2/> : '1'}</span><span><b>Bağlantı</b><small>{stepsMeta[0].sub}</small></span><ChevronDown className="liveWsChev"/></button>
            {stepOpen(0) && <div className="liveWsBody">{connectionBlock}</div>}
          </section>

          <section className={`liveWsCard ${stepOpen(1) ? 'open' : ''} ${stepLocked(1) ? 'locked' : ''}`}>
            <button type="button" className="liveWsCardHead" aria-expanded={stepOpen(1)} disabled={stepLocked(1)} onClick={() => setOpenStep(stepOpen(1) ? -1 : 1)}><span className={`liveWsBadge ${stepDone[1] ? 'done' : stepLocked(1) ? 'locked' : 'current'}`}>{stepDone[1] ? <CheckCircle2/> : stepLocked(1) ? <LockKeyhole/> : '2'}</span><span><b>İzinler</b><small>{stepsMeta[1].sub}</small></span><ChevronDown className="liveWsChev"/></button>
            {stepOpen(1) && <div className="liveWsBody">
              <div className="liveWsChecks">
                <article className={authorizationValid ? 'done' : 'todo'}>
                  <div className="liveWsCheckText"><b>① 24 saatlik risk izni</b><small>{authorizationValid ? `Geçerli: ${date(status?.authorization?.expires_at || status?.consent?.expires_at)} tarihine kadar` : 'Gerçek parayla işlem riskini 24 saatliğine kabul edersin.'}</small></div>
                  <div className="liveWsRow"><button type="button" className="liveWsPrimary" onClick={grantConsent} disabled={Boolean(busy) || authorizationValid}>{authorizationValid ? 'İzin verildi' : '24 SAAT İZİN VER'}</button>{authorizationValid && <button type="button" className="liveWsGhost" onClick={revokeConsent} disabled={Boolean(busy)}>İzni geri al</button>}</div>
                </article>
                <article className={status?.policy_acknowledged ? 'done' : authorizationValid ? 'todo' : 'wait'}>
                  <div className="liveWsCheckText"><b>② Risk limitleri onayı</b><small>{status?.policy_acknowledged ? 'Mevcut limitler onaylandı.' : authorizationValid ? 'Limitleri gördüm ve kabul ediyorum.' : 'Önce 1. onayı ver.'}</small></div>
                  <label className="masterTradeLivePolicyCheck liveWsCheck"><input type="checkbox" aria-label="Mevcut risk limitlerini onaylıyorum" checked={status?.policy_acknowledged === true} onChange={event => { if (event.target.checked) acknowledgePolicy() }} disabled={Boolean(busy) || status?.policy_acknowledged === true}/><span>{status?.policy_acknowledged ? 'Limitler onaylandı' : 'Risk limitlerini onaylıyorum'}</span></label>
                  <div className="liveWsRow"><button type="button" className="liveWsPrimary" onClick={acknowledgePolicy} disabled={Boolean(busy) || status?.policy_acknowledged === true}>{status?.policy_acknowledged ? 'Onaylandı' : 'LİMİTLERİ ONAYLA'}</button></div>
                </article>
              </div>
              <p className="liveWsHint">Manuel emir için de ARM gerekir. ARM'ı 3. adımda açarsın.</p>
              <div className="liveWsGates">{(status?.readiness?.gates || []).map(gate => <span key={gate.key} className={gate.passed ? 'ok' : 'bad'}>{gate.passed ? <CheckCircle2/> : <AlertTriangle/>}{gate.label || gate.key}</span>)}</div>
            </div>}
          </section>

          <section className={`liveWsCard ${stepOpen(2) ? 'open' : ''} ${stepLocked(2) ? 'locked' : ''}`}>
            <button type="button" className="liveWsCardHead" aria-expanded={stepOpen(2)} disabled={stepLocked(2)} onClick={() => setOpenStep(stepOpen(2) ? -1 : 2)}><span className={`liveWsBadge ${stepDone[2] ? 'done' : stepLocked(2) ? 'locked' : 'current'}`}>{stepDone[2] ? <CheckCircle2/> : stepLocked(2) ? <LockKeyhole/> : '3'}</span><span><b>Otomatik işlem</b><small>{stepsMeta[2].sub}</small></span><ChevronDown className="liveWsChev"/></button>
            {stepOpen(2) && <div className="liveWsBody">
              <div className="liveWsStrategy">
                <div className="liveWsStrategyHead"><div className="liveWsStrategyIcon"><Power/></div><div><b>Kais Original · Gözetimli</b><small>15 dakikalık mum · {marketsText}</small></div><span className="liveWsTag">{running ? 'ÇALIŞIYOR' : 'SEÇİLİ'}</span><button type="button" className="liveWsInfo" aria-label="Strateji nasıl çalışır?" aria-expanded={strategyInfo} onClick={() => setStrategyInfo(value => !value)}><Info/></button></div>
                {strategyInfo && <div className="liveWsInfoBox" role="note">
                  <b>Bu strateji nasıl çalışır?</b>
                  <ul>
                    <li>Seçili coinleri her {numericPolicy('scan_seconds', 60)} saniyede bir tarar (yalnızca 15 dakikalık grafik).</li>
                    <li>Sinyal güveni en az %{numericPolicy('min_confidence', 85)} ve tuzak puanı en fazla {Math.min(35, numericPolicy('max_trap_score', 35))} olmalıdır.</li>
                    <li>Uygun fırsatta stop-loss ve 3 kâr hedefiyle emri kendisi açar; korumasız pozisyon bırakmaz.</li>
                    <li>Tek işlemde en fazla {numericPolicy('max_loss_per_trade', 1)} USDT, aynı anda en fazla {numericPolicy('max_positions', 2)} pozisyon ve toplam {numericPolicy('max_total_exposure_usdt', 350)} USDT maruziyet.</li>
                    <li>Günlük zarar {numericPolicy('daily_loss_limit', 20)} USDT'ye veya üst üste {numericPolicy('consecutive_loss_limit', 3)} zarara ulaşırsa yeni işlem açmaz.</li>
                  </ul>
                </div>}
                <div className="masterTradeLiveSetupGrid liveWsGrid">
                  <label>Zaman dilimi<select value={selectedAutoTimeframe} onChange={event => setPolicy('interval', event.target.value)}><option>1m</option><option>5m</option><option>15m</option><option>1h</option><option>4h</option></select></label>
                  <label>İşlem başı risk<div className="masterTradeLiveUnitField"><input type="number" min="0.5" max="25" step="0.1" value={numericPolicy('max_loss_per_trade', 1)} onChange={event => setPolicy('max_loss_per_trade', Number(event.target.value))}/><span>USDT</span></div></label>
                  <label>En fazla pozisyon<input type="number" min="1" max="5" value={numericPolicy('max_positions', 2)} onChange={event => setPolicy('max_positions', Number(event.target.value))}/></label>
                  <label>En fazla maruziyet<div className="masterTradeLiveUnitField"><input type="number" min="25" max="350" value={numericPolicy('max_total_exposure_usdt', 350)} onChange={event => setPolicy('max_total_exposure_usdt', Number(event.target.value))}/><span>USDT</span></div></label>
                </div>
                <div className="masterTradeLiveMarkets"><span>COİNLER</span><div className="masterTradeLiveMarketsScroll">{approvedSymbols.map(item => <button type="button" key={item} aria-pressed={configuredSymbols.includes(item)} className={configuredSymbols.includes(item) ? 'selected' : ''} onClick={() => toggleSymbol(item)}>{item.replace('USDT', '')}</button>)}</div><small>{configuredSymbols.length ? `${configuredSymbols.length} coin seçili` : 'Varsayılan liste'}</small></div>
                <button type="button" className="liveWsLink" onClick={() => setAdvancedOpen(value => !value)}>{advancedOpen ? 'Gelişmiş ayarları gizle' : 'Gelişmiş ayarlar'}</button>
                {advancedOpen && <div className="masterTradeLiveSetupGrid liveWsGrid">
                  <label>En düşük güven<div className="masterTradeLiveUnitField"><input type="number" min="70" max="95" value={numericPolicy('min_confidence', 85)} onChange={event => setPolicy('min_confidence', Number(event.target.value))}/><span>%</span></div></label>
                  <label>En yüksek kaldıraç<input type="number" min="1" max="50" value={numericPolicy('max_leverage', 2)} onChange={event => setPolicy('max_leverage', Number(event.target.value))}/></label>
                  <label>Tasfiye tamponu<div className="masterTradeLiveUnitField"><input type="number" min="0.05" max="5" step="0.05" value={numericPolicy('liquidation_buffer_pct', 0.5)} onChange={event => setPolicy('liquidation_buffer_pct', Number(event.target.value))}/><span>% giriş</span></div></label>
                  <label>Günlük zarar limiti<div className="masterTradeLiveUnitField"><input type="number" min="5" max="100" value={numericPolicy('daily_loss_limit', 20)} onChange={event => setPolicy('daily_loss_limit', Number(event.target.value))}/><span>USDT</span></div></label>
                  <label>Üst üste zarar<input type="number" min="1" max="10" value={numericPolicy('consecutive_loss_limit', 3)} onChange={event => setPolicy('consecutive_loss_limit', Number(event.target.value))}/></label>
                  <label>Tarama aralığı (sn)<input type="number" min="30" max="300" value={numericPolicy('scan_seconds', 60)} onChange={event => setPolicy('scan_seconds', Number(event.target.value))}/></label>
                  <div className="masterTradeLiveSide"><span>YÖN</span><button type="button" className={policy.allow_long !== false ? 'selected' : ''} onClick={() => setPolicy('allow_long', policy.allow_long === false)}>LONG</button><button type="button" className={policy.allow_short !== false ? 'selected' : ''} onClick={() => setPolicy('allow_short', policy.allow_short === false)}>SHORT</button></div>
                </div>}
                <div className="liveWsRow"><button type="button" className="liveWsGhost" onClick={updatePolicy} disabled={Boolean(busy)}>Ayarları kaydet</button></div>
                <p className="liveWsHint">Ayarlar sunucuda doğrulanır. Ayar değiştirirsen limit onayını yenilemen gerekebilir.</p>
              </div>
              <div className="liveWsLaunch">
                <div className="liveWsLaunchRow"><span><small>CANLI EMİR İZNİ (ARM)</small><b>{liveArmed ? 'Açık' : 'Kapalı'}</b></span><button type="button" className="liveWsGhost" onClick={liveArmed ? disarmLive : armLive} disabled={Boolean(busy) || (!liveArmed && (!authorizationValid || !configured || !connected || emergency || recoveryRequired || !readinessReady))}>{liveArmed ? 'ARM\'ı kapat' : 'ARM\'ı aç'}</button></div>
                <div className="masterTradeLiveAssistantActions" aria-label="Auto Trade controls">
                  <button type="button" className={`liveWsPrimary big ${running ? 'danger' : ''}`} onClick={running ? stopAutoTrade : autoToggle} disabled={Boolean(busy) || (!running && !autoReady)}>{!running && !autoReady ? <LockKeyhole/> : <Power/>} {running ? 'OTOMATİK İŞLEMİ DURDUR' : 'OTOMATİK İŞLEMİ BAŞLAT'}</button>
                </div>
                {!running && !autoReady && <p className="liveWsHint">Başlatılamıyor: {blocker || 'önceki adımları tamamla'}</p>}
                <div className="liveWsMeta"><span><small>Tarayıcı</small><b>{scannerStatus}</b></span><span><small>Taranan</small><b>{status?.scanner?.scanned_symbol_count ?? 0}</b></span><span><small>Aday</small><b>{candidateCount}</b></span><span><small>Son tarama</small><b>{date(status?.scanner?.last_scan_at || status?.auto?.last_scan)}</b></span></div>
                {running && <p className="liveWsHint">{automationReason}</p>}
              </div>
            </div>}
          </section>

          <section className={`liveWsCard ${stepOpen(3) ? 'open' : ''} ${stepLocked(3) ? 'locked' : ''}`}>
            <button type="button" className="liveWsCardHead" aria-expanded={stepOpen(3)} disabled={stepLocked(3)} onClick={() => setOpenStep(stepOpen(3) ? -1 : 3)}><span className={`liveWsBadge ${manualOrderReady ? 'current' : ''}`}>4</span><span><b>Manuel emir</b><small>{stepsMeta[3].sub}</small></span><ChevronDown className="liveWsChev"/></button>
            {stepOpen(3) && <div className="liveWsBody">{manualOrderCard}</div>}
          </section>

          <section className="liveWsSection masterTradeLiveAccountHud" aria-label="Hesap özeti">
            <h3>Hesap özeti</h3>
            <div className="masterTradeLiveAccountMetrics">
              <span><small>CÜZDAN</small><b>{money(status?.account?.wallet_balance)}</b><em>USDT</em></span>
              <span><small>KULLANILABİLİR</small><b>{money(status?.account?.available_balance)}</b><em>USDT</em></span>
              <span><small>AÇIK KÂR/ZARAR</small><b className={Number(status?.account?.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative'}>{money(status?.account?.unrealized_pnl)}</b><em>Açık pozisyonlar</em></span>
              <span><small>TOPLAM MARUZİYET</small><b>{money(liveExposure)}</b><em>Pozisyon değeri</em></span>
              <span><small>AÇIK POZİSYON</small><b>{positions.length}</b><em>Anlık</em></span>
              <span><small>AÇIK EMİR</small><b>{openOrders.length}</b><em>Anlık</em></span>
              <span><small>KALDIRAÇ</small><b>{positions.length ? positions.map(positionLeverage).join(' / ') : '--'}</b><em>Pozisyon başına</em></span>
              <span><small>RİSK</small><b>{gateTr(riskState)}</b><em>Risk kapısı</em></span>
              <span><small>KORUMA</small><b>{gateTr(currentProtection === 'PROTECTED' ? 'READY' : currentProtection === 'UNKNOWN' ? 'UNKNOWN' : 'BLOCKED')}</b><em>Stop/hedef emirleri</em></span>
              <span><small>KURTARMA</small><b>{gateTr(recoveryState)}</b><em>Uzlaştırma</em></span>
            </div>
          </section>

          <section className="liveWsSection liveWsSignal masterTradeLiveSignal">
            <h3>Son sinyal · {symbol}</h3>
            <div className="masterTradeLiveSignalGrid">
              <span><small>YÖN</small><b>{analysis?.direction || '—'}</b></span>
              <span><small>GÜVEN</small><b>{analysis?.confidence !== undefined ? `${analysis.confidence}%` : '—'}</b></span>
              <span><small>GİRİŞ</small><b title={text(analysis?.entry)}>{signalPrice(analysis?.entry)}</b></span>
              <span><small>STOP</small><b title={text(analysis?.stop_loss)}>{signalPrice(analysis?.stop_loss)}</b></span>
              <span><small>HEDEF 1</small><b title={text(analysis?.tp1)}>{signalPrice(analysis?.tp1)}</b></span>
              <span><small>DURUM</small><b>{signalStatus}</b></span>
            </div>
            <p className="liveWsHint">{latestSignal ? `${text(latestSignal.message)} · ${date(latestSignal.created_at)}` : 'Henüz yeni sinyal olayı yok.'}</p>
          </section>

          <section className="liveWsSection liveWsActivity">
            <h3>Son olaylar</h3>
            {activity.length ? <ol>{activity.map((event, index) => <li key={`${event.created_at}-${event.kind}-${index}`} className={/FAIL|BLOCK|LOCK|STALE|RATE|ERROR|UNKNOWN|EMERGENCY/i.test(String(event.kind)) ? 'warn' : ''}><time>{date(event.created_at)}</time><span>{text(event.message || event.kind).replaceAll('_', ' ')}</span></li>)}</ol> : <p className="liveWsHint">Henüz canlı olay yok.</p>}
          </section>

          <section className="liveWsSection liveWsDanger">
            <div><b>Acil durum</b><small>Yeni emirleri ve otomatik işlemi hemen durdurur.</small></div>
            <div className="liveWsRow"><button type="button" className="liveWsGhost danger" onClick={emergencyStop} disabled={Boolean(busy)}><ShieldAlert/> ACİL DURDUR</button>{emergency && <button type="button" className="liveWsGhost" onClick={clearEmergency} disabled={Boolean(busy)}>Kurtarma sonrası kaldır</button>}</div>
          </section>
        </>}

        {masterTradeTab === 'baglanti' && <>
          <section className="liveWsCard open"><div className="liveWsCardHead static"><span className={`liveWsBadge ${step1Done ? 'done' : 'current'}`}>{step1Done ? <CheckCircle2/> : '1'}</span><span><b>Binance bağlantısı</b><small>{stepsMeta[0].sub}</small></span></div><div className="liveWsBody">{connectionBlock}</div></section>
          <section className="liveWsSection liveWsActivity">
            <h3>Son olaylar</h3>
            {activity.length ? <ol>{activity.map((event, index) => <li key={`${event.created_at}-${event.kind}-${index}`} className={/FAIL|BLOCK|LOCK|STALE|RATE|ERROR|UNKNOWN|EMERGENCY/i.test(String(event.kind)) ? 'warn' : ''}><time>{date(event.created_at)}</time><span>{text(event.message || event.kind).replaceAll('_', ' ')}</span></li>)}</ol> : <p className="liveWsHint">Henüz canlı olay yok.</p>}
          </section>
          <section className="liveWsSection masterTradeLiveSafety">
            <h3>Güvenlik özeti</h3>
            <div className="liveWsGates">{safetyRows.map(([label, value, detail]) => <span key={label} className={safetyStatus(value) === 'ready' ? 'ok' : 'bad'} title={String(detail)}>{safetyStatus(value) === 'ready' ? <CheckCircle2/> : <AlertTriangle/>}{label}: {value}</span>)}</div>
          </section>
        </>}
      </section>
      {confirmationModal}
    </PremiumWorkspace>
  }
  return null
}

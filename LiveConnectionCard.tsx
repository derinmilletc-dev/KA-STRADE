import {useCallback, useEffect, useState} from 'react'
import {ArrowRight, RefreshCw} from 'lucide-react'
import {API_BASE} from './api'
import {fetchWithTimeout} from './master-trade-request'
import {accountDate} from './account-settings-api'
import './notify-settings.css'

type LiveEvent = {kind?: string; message?: string; created_at?: string}
type LiveStatus = {
  connected?: boolean
  credentials?: {configured?: boolean}
  authorization?: {valid?: boolean; expires_at?: string | null; reason?: string}
  armed?: boolean
  armed_until?: string | null
  live_auto_trade?: boolean
  real_trading_locked?: boolean
  events?: LiveEvent[]
}

const PROBLEM = /STALE|RATE|FAIL_CLOSED|STREAM_RECONNECT|AUTO_ERROR|CONSENT_GRACE_EXPIRED|^LIVE_DISARM$|CREDENTIAL/

function remaining(iso?: string | null): string {
  const time = iso ? Date.parse(iso) : NaN
  if (!Number.isFinite(time)) return ''
  const minutes = Math.floor((time - Date.now()) / 60000)
  if (minutes <= 0) return 'süresi doldu'
  const days = Math.floor(minutes / 1440), hours = Math.floor((minutes % 1440) / 60), rest = minutes % 60
  return days ? `${days} gün ${hours} sa kaldı` : hours ? `${hours} sa ${rest} dk kaldı` : `${rest} dk kaldı`
}

export default function LiveConnectionCard() {
  const [status, setStatus] = useState<LiveStatus | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'locked' | 'error'>('loading')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async (signal?: AbortSignal) => {
    setBusy(true)
    try {
      const response = await fetchWithTimeout(`${API_BASE}/v25/status`, {credentials: 'same-origin', signal})
      if (response.status === 401 || response.status === 402 || response.status === 403) { setState('locked'); return }
      if (!response.ok) { setState('error'); return }
      setStatus(await response.json() as LiveStatus); setState('ready')
    } catch (error) {
      if ((error as {name?: string})?.name !== 'AbortError') setState('error')
    } finally { setBusy(false) }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    const timer = window.setInterval(() => void load(), 30000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [load])

  const configured = status?.credentials?.configured === true
  const connected = status?.connected === true
  const consentValid = status?.authorization?.valid === true
  const problem = (status?.events ?? []).find(event => PROBLEM.test(String(event.kind ?? '')))
  const pill = state !== 'ready' ? null : !configured ? {tone: 'warn', text: 'Anahtar bağlı değil'} : connected ? {tone: 'ok', text: 'Bağlı'} : {tone: 'bad', text: 'Bağlantı yok'}
  const hint = state !== 'ready' ? '' : !configured ? 'Önce Binance API anahtarını bağla.'
    : !connected ? 'Master Trade > Bağlantı sekmesini açıp hesabı yenile. Sorun sürerse anahtarı yeniden bağla.'
    : !consentValid ? 'Canlı işlem izni bitmiş. Master Trade > Canlı İşlem bölümünden 24 saatlik izni yenile.' : 'Her şey yolunda.'
  const go = () => window.location.assign(state === 'locked' ? '/pricing' : '/master-trade?tab=baglanti')

  return <section className="accountCard nsCard" id="live-connection-card">
    <h2>Canlı bağlantı durumu <button type="button" onClick={() => void load()} disabled={busy} aria-label="Durumu yenile"><RefreshCw className={busy ? 'spin' : undefined}/></button></h2>
    {state === 'loading' && <p className="nsLead">Durum okunuyor…</p>}
    {state === 'error' && <p className="nsWarn" role="alert">Durum şu anda okunamadı. Biraz sonra tekrar dene.</p>}
    {state === 'locked' && <p className="nsLead">Canlı işlem aboneliğe dahildir. Aboneliğin yoksa abonelik sayfasından başlayabilirsin.</p>}
    {state === 'ready' && pill && <>
      <div className={`nsStatus ${pill.tone}`}><i/><b>{pill.text}</b><small>{hint}</small></div>
      <div className="nsList">
        <div className="nsRow"><span><b>Binance anahtarı</b><small>{configured ? 'Kayıtlı' : 'Kayıtlı değil'}</small></span></div>
        <div className="nsRow"><span><b>24 saatlik canlı izin</b><small>{consentValid ? `Geçerli · ${remaining(status?.authorization?.expires_at)} (${accountDate(status?.authorization?.expires_at)})` : 'Geçerli değil'}</small></span></div>
        <div className="nsRow"><span><b>Canlı emir izni (ARM)</b><small>{status?.armed ? `Açık · ${remaining(status?.armed_until)}` : 'Kapalı'}</small></span></div>
        <div className="nsRow"><span><b>Otomatik işlem</b><small>{status?.live_auto_trade ? 'Çalışıyor' : 'Kapalı'}</small></span></div>
        <div className="nsRow"><span><b>Son bağlantı sorunu</b><small>{problem ? `${String(problem.message || problem.kind).replaceAll('_', ' ')} · ${accountDate(problem.created_at)}` : 'Kayıtlı sorun yok'}</small></span></div>
      </div>
    </>}
    <div className="nsFoot"><small>Bu kart yalnızca okur; hiçbir ayarı değiştirmez.</small><button type="button" className="nsTest" onClick={go}>{state === 'locked' ? 'Abonelik sayfası' : 'Master Trade bağlantısı'}<ArrowRight/></button></div>
  </section>
}

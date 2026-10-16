import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Camera, Info, Loader2, Sparkles, Upload, X} from 'lucide-react'
import {ChartClientError, chartRequest, chartResult, chartUsage, captureChart, CHART_AI_DISCLAIMER, CHART_AI_PRIVACY,
  imageBase64, validateChartUpload, type ChartResult, type ChartSnapshot, type ChartUsage} from './chart-ai-client'
import './chart-ai.css'

type Pending = {request_key: string; symbol: string; timeframe: string}
type Preview = {blob: Blob; url: string; context: ChartSnapshot; source: 'screenshot' | 'upload'}
type Props = {userId: string; symbol: string; timeframe: string; snapshot: () => ChartSnapshot; chart: () => SVGSVGElement | null}
const QUESTIONS = ['Bu grafiği yorumla', 'Riskler neler?', 'Yukarı ve aşağı senaryolar']
const MODES = ['Yorum', 'Riskler', 'Senaryolar']
const sections = (text: string) => text.split(/\n{2,}/).map(block => {
  const [title, ...body] = block.split('\n')
  return {title, body: body.join('\n')}
})
const message = (error: unknown) => error instanceof Error ? error.message : 'Grafik AI işlemi tamamlanamadı.'

export default function ChartAiPanel(props: Props) {
  const [usage, setUsage] = useState<ChartUsage | null>(null)
  const [hidden, setHidden] = useState(false)
  const [loading, setLoading] = useState(true)
  const [accessDenied, setAccessDenied] = useState(false)
  const [showPricing, setShowPricing] = useState(false)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mode, setMode] = useState(0)
  const [custom, setCustom] = useState('')
  const [resultHidden, setResultHidden] = useState(false)
  const asked = custom.trim() || QUESTIONS[mode]
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [result, setResult] = useState<ChartResult | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [identity, setIdentity] = useState<Pending | null>(null)
  const [queryRevision, setQueryRevision] = useState(0)
  const dialog = useRef<HTMLDialogElement>(null)
  const controller = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const preparation = useRef(0)
  const alive = useRef(false)
  const storageKey = `kaistrade-chart-ai-request:${props.userId}`

  const refresh = async (signal?: AbortSignal) => {
    const next = await chartUsage(signal)
    if (alive.current && !signal?.aborted) {
      setUsage(next)
      setHidden(false)
      setAccessDenied(false)
      setShowPricing(false)
    }
    return next
  }
  const receive = (next: ChartResult, expected: Pending) => {
    if (next.request_key !== expected.request_key) throw new Error('Analiz istek kimliği uyuşmuyor.')
    if (!alive.current) return
    setResult(next)
    setResultHidden(false)
    if (next.status !== 'processing') {
      setPending(null)
      sessionStorage.removeItem(storageKey)
      setBusy(false)
      locked.current = false
      if (next.error) setError(next.error.message)
    }
  }
  useEffect(() => {
    alive.current = true
    const abort = new AbortController()
    controller.current = abort
    void refresh(abort.signal).then(() => {
      if (abort.signal.aborted) return
      const raw = sessionStorage.getItem(storageKey)
      if (!raw) return
      const saved: unknown = JSON.parse(raw)
      if (typeof saved !== 'object' || saved === null || !('request_key' in saved) || typeof saved.request_key !== 'string'
          || !/^[0-9a-f-]{36}$/i.test(saved.request_key) || !('symbol' in saved) || typeof saved.symbol !== 'string'
          || !('timeframe' in saved) || typeof saved.timeframe !== 'string') throw new Error('Kayıtlı analiz kimliği geçersiz.')
      const restored = {request_key: saved.request_key, symbol: saved.symbol, timeframe: saved.timeframe}
      setPending(restored)
      setIdentity(restored)
      setBusy(true)
      locked.current = false
    }).catch((failure: unknown) => {
      if (!alive.current || abort.signal.aborted) return
      if (failure instanceof ChartClientError && (failure.code === 'feature_disabled' || failure.status === 404)) setHidden(true)
      else {
        const code = failure instanceof ChartClientError ? failure.code : ''
        setAccessDenied(code === 'access_denied')
        setShowPricing(code === 'access_denied' || code === 'quota_exhausted')
        setError(message(failure))
      }
    }).finally(() => { if (alive.current && !abort.signal.aborted) setLoading(false) })
    return () => { alive.current = false; abort.abort(); preparation.current++ }
  }, [storageKey])

  useEffect(() => {
    if (!preview) return
    return () => URL.revokeObjectURL(preview.url)
  }, [preview])
  useEffect(() => {
    preparation.current++
    setPreparing(false)
    setPreview(null)
    setResultHidden(true)
  }, [props.symbol, props.timeframe])
  useEffect(() => {
    if (!open || !dialog.current) return
    const previous = document.activeElement
    const element = dialog.current
    element.showModal()
    return () => { element.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus() }
  }, [open])

  useEffect(() => {
    if (!pending) return
    const abort = new AbortController()
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const next = chartResult(await chartRequest(`/requests/${pending.request_key}`, {signal: abort.signal}))
        if (!active) return
        if (next.status !== 'processing') await refresh(abort.signal)
        if (!active) return
        receive(next, pending)
        if (next.status === 'processing') timer = setTimeout(() => void poll(), 2000)
      } catch (failure: unknown) {
        if (active && !abort.signal.aborted) { setError(`${message(failure)} Yeni üretim başlatılmadı; sonucu yeniden sorgulayabilirsin.`); setBusy(false) }
      }
    }
    // The initial POST owns its HTTP wait; polling starts only on a processing/unknown result.
    if (!locked.current || result?.status === 'processing') void poll()
    return () => { active = false; abort.abort(); if (timer) clearTimeout(timer) }
  }, [pending, result?.status, queryRevision])

  const prepare = async (file?: File) => {
    const revision = ++preparation.current
    setPreparing(true); setError(''); setPreview(null)
    try {
      const context = props.snapshot()
      const blob = file ?? await captureChart(props.chart())
      await validateChartUpload(blob)
      if (!alive.current || revision !== preparation.current) return
      setPreview({blob, url: URL.createObjectURL(blob), context, source: file ? 'upload' : 'screenshot'})
      setResult(null)
    } catch (failure: unknown) {
      if (alive.current && revision === preparation.current) setError(message(failure))
    } finally {
      if (alive.current && revision === preparation.current) setPreparing(false)
    }
  }
  const send = async () => {
    if (locked.current || !preview || !usage || !usage.analysis_enabled) return
    locked.current = true
    setBusy(true); setError(''); setResult(null)
    let started = false
    try {
      if (usage.acknowledgement.required) {
        if (!consent) throw new Error('Önce kullanım uyarısını okuyup onayla.')
        await chartRequest('/acknowledgement', {method: 'POST', signal: controller.current?.signal,
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({policy_version: usage.acknowledgement.policy_version, accepted: true})})
        const confirmed = await refresh(controller.current?.signal)
        if (confirmed.acknowledgement.required) throw new Error('Kullanım onayı sunucuda doğrulanamadı; analiz gönderilmedi.')
      }
      if (!alive.current || controller.current?.signal.aborted) return
      const data = await imageBase64(preview.blob)
      if (!alive.current || controller.current?.signal.aborted) return
      const job = {request_key: crypto.randomUUID(), symbol: preview.context.symbol, timeframe: preview.context.timeframe}
      sessionStorage.setItem(storageKey, JSON.stringify(job))
      setPending(job)
      setIdentity(job)
      started = true
      const next = chartResult(await chartRequest('/analyze', {
        method: 'POST', signal: controller.current?.signal, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...job, question: asked, image: {
          source: preview.source, media_type: preview.blob.type, data_base64: data,
        }, context: preview.context}),
      }))
      receive(next, job)
      if (alive.current) await refresh(controller.current?.signal)
    } catch (failure: unknown) {
      if (!alive.current) return
      const code = failure instanceof ChartClientError ? failure.code : ''
      setAccessDenied(code === 'access_denied')
      setShowPricing(code === 'access_denied' || code === 'quota_exhausted')
      setError(message(failure))
      setBusy(false)
      locked.current = false
      if (!started) setPending(null)
    }
  }
  if (hidden || loading) return null
  const inputLocked = busy || Boolean(pending)
  const exhausted = Boolean(usage && !usage.quota.unlimited && (usage.quota.remaining ?? 0) <= 0)
  const showResult = Boolean(result?.result_text) && !resultHidden
  const fileInput = (label: string, className: string, icon: boolean) => <label className={className} aria-label={icon ? label : undefined}>
    {icon ? <Upload aria-hidden="true"/> : <><Upload aria-hidden="true"/>{label}</>}
    <input className="chartAiVisuallyHidden" type="file" accept="image/png,image/jpeg,image/webp" disabled={inputLocked || preparing}
      onChange={event => {const file = event.target.files?.[0]; event.target.value = ''; if (file) void prepare(file)}}/>
  </label>
  return <>
    <button type="button" className="chartAiButton" disabled={loading} onClick={() => setOpen(true)} title="Grafiği açıkla; işlem veya emir göndermez">
      <Sparkles aria-hidden="true"/>Yapay Zekaya Sor
    </button>
    {open && createPortal(<dialog ref={dialog} className="chartAiDialog" aria-labelledby="chart-ai-title" onCancel={event => {event.preventDefault(); setOpen(false)}}>
      <header className="chartAiHeader">
        <h2 id="chart-ai-title">Grafik AI</h2>
        {usage && <span className="chartAiQuota">Kalan hak: <strong>{usage.quota.unlimited ? 'Sınırsız' : usage.quota.remaining}</strong></span>}
        <button type="button" className="chartAiIconButton chartAiClose" autoFocus aria-label="Grafik AI panelini kapat" onClick={() => setOpen(false)}><X aria-hidden="true"/></button>
      </header>
      <div className="chartAiBody">
        {error && <div role="alert" className="chartAiNotice chartAiNotice--error">{error}</div>}
        {exhausted && usage && <p role="status" className="chartAiNotice chartAiNotice--warn">
          {usage.access_type === 'trial'
            ? 'Deneme süresindeki 3 analiz hakkınızı kullandınız. MASTER MODE ile haftada 20 analiz kullanabilirsiniz.'
            : 'Haftalık 20 analiz hakkınız doldu. Yeni haklar Pazartesi yenilenir.'}
        </p>}
        {(showPricing || usage?.access_type === 'trial' && exhausted)
          && <a className="chartAiPricing" href="/pricing">MASTER MODE'a geç</a>}
        {!accessDenied && <>
        {!usage && <button type="button" className="chartAiSecondary" onClick={() => {setError(''); void refresh().catch(failure => setError(message(failure)))}}>Durumu yeniden oku</button>}
        {usage && !usage.analysis_enabled && <p role="status" className="chartAiNotice chartAiNotice--warn">{usage.availability.message}</p>}

        {showResult && result?.result_text ? <section className="chartAiResult" aria-label="Grafik AI sonucu">
          <div className="chartAiResultHead">
            {preview && <img src={preview.url} alt=""/>}
            <div><strong>{MODES[mode]}{identity ? ` · ${identity.symbol} ${identity.timeframe}` : ''}</strong>
              {preview && <span>{new Date(preview.context.captured_at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})}</span>}</div>
          </div>
          {sections(result.result_text).map((part, index) => part.body
            ? <details key={index} className="chartAiSection" open={index < 2}><summary>{part.title}{'\n'}</summary><p>{part.body}{'\n\n'}</p></details>
            : <p key={index} className="chartAiResultNote">{part.title}{'\n'}</p>)}
        </section> : <>
        <section className="chartAiImage" aria-label="Grafik görseli">
          {preview ? <figure>
            <img src={preview.url} alt="Gönderilecek grafik önizlemesi"/>
            <div className="chartAiImageTools">
              <button type="button" className="chartAiIconButton" aria-label="Grafiği yeniden yakala" disabled={inputLocked || preparing} onClick={() => void prepare()}><Camera aria-hidden="true"/></button>
              {fileInput('Görsel yükle', 'chartAiIconButton', true)}
            </div>
            <figcaption><span>{preview.context.symbol}</span><span>{preview.context.timeframe}</span>
              <span>{new Date(preview.context.captured_at).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})}</span></figcaption>
          </figure> : <div className="chartAiImageEmpty">
            <button type="button" className="chartAiPrimaryGhost" disabled={inputLocked || preparing} onClick={() => void prepare()}><Camera aria-hidden="true"/>Grafiği yakala</button>
            {fileInput('Görsel yükle', 'chartAiSecondary', false)}
          </div>}
          {preparing && <p className="chartAiHint">Görsel hazırlanıyor…</p>}
        </section>

        <div className="chartAiField">
          <span className="chartAiLabel" id="chart-ai-mode">Analiz türü</span>
          <div className="chartAiModes" role="radiogroup" aria-labelledby="chart-ai-mode">
            {MODES.map((label, index) => <button type="button" role="radio" key={label} aria-checked={mode === index}
              className={mode === index ? 'active' : ''} disabled={inputLocked} onClick={() => setMode(index)}>{label}</button>)}
          </div>
        </div>

        <label className="chartAiField">
          <span className="chartAiLabel">Sorunuz <em>(isteğe bağlı)</em></span>
          <textarea rows={2} value={custom} maxLength={800} disabled={inputLocked} placeholder="Örn. 1 saatlik trend ile uyumlu mu?" onChange={event => setCustom(event.target.value)}/>
        </label>
        </>}

        {pending && <p role="status" className="chartAiHint">{pending.symbol} · {pending.timeframe}: {busy ? 'Yanıt hazırlanıyor; en fazla iki üretim, toplam analiz sınırı 60 saniye.' : 'Analiz durumu doğrulanmalı.'}</p>}
        {pending && !busy && <div className="chartAiRow">
          <button type="button" className="chartAiSecondary" onClick={() => {locked.current = false; setBusy(true); setError(''); setQueryRevision(value => value + 1)}}>Sonucu yeniden sorgula</button>
          <button type="button" className="chartAiSecondary" onClick={() => {
            if (!window.confirm('Bu işlem yalnız tarayıcıdaki kayıt bilgisini bırakır; başlamış analizi iptal etmez. Devam edilsin mi?')) return
            sessionStorage.removeItem(storageKey); setPending(null); setResult(null); locked.current = false
          }}>Bekleyen kayıt bilgisini bırak</button>
        </div>}
        </>}
      </div>

      <footer className="chartAiFooter">
        {!accessDenied && (showResult ? <div className="chartAiRow chartAiRow--split">
          <button type="button" className="chartAiSecondary" onClick={() => setResultHidden(true)}>Soruyu değiştir</button>
          <button type="button" className="chartAiPrimary" onClick={() => {setResultHidden(true); setResult(null); setPreview(null); setCustom('')}}>Yeni analiz</button>
        </div> : <>
          {usage?.acknowledgement.required && <label className="chartAiConsent"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)}/><span>Kullanım uyarısını okudum, anladım.</span></label>}
          <button type="button" className="chartAiPrimary chartAiSend" aria-busy={busy} disabled={!preview || !asked || !usage?.analysis_enabled || preparing || busy || Boolean(pending)
            || usage.acknowledgement.required && !consent || exhausted} onClick={() => void send()}>
            {busy && <Loader2 className="chartAiSpin" aria-hidden="true"/>}Grafiği analiz et
          </button>
        </>)}
        <div className="chartAiLegal">
          <p>{CHART_AI_DISCLAIMER}</p>
          <p>{CHART_AI_PRIVACY}</p>
          <details>
            <summary><Info aria-hidden="true"/>Ayrıntılar</summary>
            <p>Yalnız grafiğin görseli ve gösterilen piyasa verileri gönderilir. Hesap, bakiye ve anahtar bilgileri gönderilmez. Aylık bütçe sınırı geçerlidir.</p>
            {usage?.acknowledgement.text && <p className="chartAiPre">{usage.acknowledgement.text}</p>}
          </details>
        </div>
      </footer>
    </dialog>, document.body)}
  </>
}

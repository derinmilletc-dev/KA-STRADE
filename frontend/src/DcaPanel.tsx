import {useEffect, useMemo, useState} from 'react'
import {computeDca, DCA_LIMITS, DCA_PRESETS, type DcaPreset, type DcaPresetForm, type DcaResult, type DcaSide} from './dcaMath'
import './dca.css'

type Call = <T = any>(base: string, path: string, options?: RequestInit & {timeoutMs?: number}) => Promise<T>
type ConfirmConfig = {title: string; message: string; expected?: string; simple?: boolean; action: () => Promise<void>}

export type DcaPanelProps = {
  symbol: string
  price: number
  side: DcaSide
  liveArmed: boolean
  call: Call
  v25Base: string
  demoBase: string
  askConfirm: (config: ConfirmConfig) => void
}

type Account = 'DEMO' | 'LIVE'
type Form = DcaPresetForm
type Preset = DcaPreset

const STATUS: Record<string, {text: string; tone: 'run' | 'wait' | 'stop' | 'bad'}> = {
  RUNNING: {text: 'Çalışıyor', tone: 'run'},
  PAUSED: {text: 'Durduruldu', tone: 'wait'},
  CYCLE_DONE: {text: 'Tur bitti', tone: 'run'},
  STOPPED: {text: 'Bitti', tone: 'stop'},
  COMPLETED: {text: 'Hedefe ulaştı', tone: 'stop'},
  EMERGENCY_CLOSED: {text: 'Acil kapatıldı', tone: 'stop'},
  BLOCKED: {text: 'Müdahale gerek', tone: 'bad'},
  INTERVENTION_REQUIRED: {text: 'Müdahale gerek', tone: 'bad'},
  EMERGENCY: {text: 'Acil durum', tone: 'bad'},
}
const FINAL = ['STOPPED', 'COMPLETED', 'EMERGENCY_CLOSED']

const num = (value: unknown) => { const n = Number(String(value).replace(',', '.')); return Number.isFinite(n) ? n : NaN }
export const fmtPrice = (value: number) => (Number.isFinite(value) && value !== 0 ? String(Number(value.toPrecision(6))) : '—')
export const fmtUsd = (value: number, sign = false) => {
  if (!Number.isFinite(value)) return '—'
  const text = Math.abs(value).toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return `${value < 0 ? '−' : sign && value > 0 ? '+' : ''}${text} USDT`
}

/** Merdiven grafiği: fiyat ekseni üstte yüksek; emirler, ortalama, hedef, stop ve tahmini likidasyon. `scrub` 0..1: fiyat başlangıçtan stopa iner. */
export function DcaLadder({result, price, side, scrub}: {result: DcaResult; price: number; side: DcaSide; scrub: number}) {
  const W = 384, H = 340, padT = 24, padB = 30
  const sign = side === 'LONG' ? 1 : -1
  const stages = result.stages
  const full = stages[stages.length - 1]
  const core = [price, result.stopPrice, stages[0].tpPrice, full.tpPrice, ...stages.map(s => s.entry)]
  const coreHi = Math.max(...core), coreLo = Math.min(...core)
  const room = (coreHi - coreLo) * 0.16
  // Likidasyon çoğu zaman çok uzaktır; ekseni ezmesin diye ancak yakınsa gerçek yerinde, değilse kenarda "uzakta" oku olarak çizilir.
  const liq = result.liquidation
  const liqPast = (side === 'LONG' ? result.stopPrice - liq : liq - result.stopPrice)
  const liqNear = Number.isFinite(liq) && liq > 0 && liqPast >= 0 && liqPast <= (coreHi - coreLo) * 0.6
  const hi = side === 'LONG' ? coreHi + room * 0.4 : coreHi + (liqNear ? liqPast : room)
  const lo = side === 'LONG' ? coreLo - (liqNear ? liqPast : room) : coreLo - room * 0.4
  const span = hi - lo || 1
  const plotH = H - padT - padB
  const y = (value: number) => padT + ((hi - value) / span) * plotH
  const yLiq = liqNear ? y(liq) : (side === 'LONG' ? H - padB + 6 : padT - 6)
  const maxNotional = Math.max(...stages.map(s => s.notional))
  const scrubPrice = price + (result.stopPrice - price) * scrub
  const filled = (entry: number) => sign * (scrubPrice - entry) <= 1e-9
  const barX = 74, barMax = 130, gutter = 262
  const baseIsPrice = Math.abs(stages[0].entry - price) < 1e-9
  // Sağ sütun etiketleri üst üste binmesin: y'ye göre sıralanır, yakın olanlar aşağı itilir.
  const tags = [
    {key: 'tp', y: y(full.tpPrice), text: `Hedef ${fmtPrice(full.tpPrice)}`, cls: 'dcaTagTp'},
    ...(stages[0].tpPrice !== full.tpPrice ? [{key: 'tp0', y: y(stages[0].tpPrice), text: `Taban hedefi ${fmtPrice(stages[0].tpPrice)}`, cls: 'dcaTagTp faint'}] : []),
    {key: 'avg', y: y(full.avgEntry), text: `Ort. ${fmtPrice(full.avgEntry)}`, cls: 'dcaTagAvg'},
    {key: 'stop', y: y(result.stopPrice), text: `STOP ${fmtPrice(result.stopPrice)}`, cls: 'dcaTagStop'},
    ...(baseIsPrice ? [] : [{key: 'px', y: y(price), text: `Fiyat ${fmtPrice(price)}`, cls: 'dcaTagPx'}]),
    {key: 'liq', y: yLiq, text: `Likid. ~${fmtPrice(liq)}${liqNear ? '' : side === 'LONG' ? ' ↓' : ' ↑'}`, cls: 'dcaTagLiq'},
  ].sort((a, b) => a.y - b.y)
  for (let i = 1; i < tags.length; i++) if (tags[i].y - tags[i - 1].y < 13) tags[i] = {...tags[i], y: tags[i - 1].y + 13}
  return (
    <svg className="dcaLadder" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="DCA merdiven grafiği">
      <defs>
        <linearGradient id="dcaZone" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3ddc97" stopOpacity="0.14" />
          <stop offset="1" stopColor="#ff6b6b" stopOpacity="0.20" />
        </linearGradient>
        <linearGradient id="dcaBar" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#3ddc97" />
          <stop offset="1" stopColor="#16c9a8" />
        </linearGradient>
        <pattern id="dcaHatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="7" height="7" fill="rgba(255,107,107,0.05)" />
          <line x1="0" y1="0" x2="0" y2="7" stroke="rgba(255,107,107,0.35)" strokeWidth="2" />
        </pattern>
        <filter id="dcaGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
      </defs>
      <rect x={barX - 8} y={Math.min(y(price), y(result.stopPrice))} width={gutter - barX + 4} height={Math.abs(y(result.stopPrice) - y(price))} rx="10" fill="url(#dcaZone)" />
      <rect x={barX - 8} y={Math.min(y(result.stopPrice), yLiq)} width={gutter - barX + 4} height={Math.abs(yLiq - y(result.stopPrice))} fill="url(#dcaHatch)" />
      {stages.map(s => {
        const on = filled(s.entry)
        const w = 26 + (s.notional / maxNotional) * (barMax - 26)
        return (
          <g key={s.index} className="dcaStageG" data-on={on ? '1' : '0'}>
            <line x1={barX - 8} x2={gutter - 4} y1={y(s.entry)} y2={y(s.entry)} className="dcaStageLine" />
            <rect x={barX} y={y(s.entry) - 7} width={w} height={14} rx="7" fill="url(#dcaBar)" className="dcaStageBar" />
            <circle cx={barX} cy={y(s.entry)} r="6" className="dcaStageNode" />
            <text x="8" y={y(s.entry) + 4} className="dcaLblL">{s.index === 0 ? (baseIsPrice ? 'Taban' : 'Taban') : `Ek ${s.index}`}</text>
            <text x={barX + w + 8} y={y(s.entry) + 4} className="dcaLblR">{fmtPrice(s.entry)}</text>
          </g>
        )
      })}
      <line x1={barX - 8} x2={gutter - 4} y1={y(full.avgEntry)} y2={y(full.avgEntry)} className="dcaAvgLine" />
      <line x1={barX - 8} x2={gutter - 4} y1={y(full.tpPrice)} y2={y(full.tpPrice)} className="dcaTpLine" />
      {stages[0].tpPrice !== full.tpPrice && <line x1={barX - 8} x2={gutter - 4} y1={y(stages[0].tpPrice)} y2={y(stages[0].tpPrice)} className="dcaTpLine faint" />}
      <line x1={barX - 8} x2={gutter - 4} y1={y(result.stopPrice)} y2={y(result.stopPrice)} className="dcaStopLine" />
      {liqNear && <line x1={barX - 8} x2={gutter - 4} y1={yLiq} y2={yLiq} className="dcaLiqLine" />}
      <line x1={barX - 8} x2={gutter - 4} y1={y(price)} y2={y(price)} className="dcaPriceLine" />
      {tags.map(tag => <text key={tag.key} x={gutter + 2} y={tag.y + 4} className={tag.cls}>{tag.text}</text>)}
      <g className="dcaScrub" style={{transform: `translateY(${y(scrubPrice)}px)`}}>
        <line x1={barX - 8} x2={gutter - 4} y1="0" y2="0" className="dcaScrubLine" />
        <circle cx={gutter - 14} cy="0" r="7" filter="url(#dcaGlow)" className="dcaScrubDot" />
      </g>
    </svg>
  )
}

function scenarioAt(result: DcaResult, price: number, side: DcaSide, scrub: number) {
  const sign = side === 'LONG' ? 1 : -1
  const at = price + (result.stopPrice - price) * scrub
  const filled = result.stages.filter(s => sign * (at - s.entry) <= 1e-9)
  const top = filled[filled.length - 1] ?? result.stages[0]
  const pnl = sign * (at - top.avgEntry) * top.cumQty
  return {at, count: filled.length, avg: top.avgEntry, tp: top.tpPrice, qty: top.cumQty, pnl, atStop: scrub >= 0.999}
}

export default function DcaPanel(props: DcaPanelProps) {
  const {symbol, price, side, liveArmed, call, v25Base, demoBase, askConfirm} = props
  const [account, setAccount] = useState<Account>('DEMO')
  const [form, setForm] = useState<Form>(DCA_PRESETS[1].form)
  const [presetId, setPresetId] = useState('balanced')
  const [filters, setFilters] = useState<{tickSize: number; stepSize: number; minNotional: number} | null>(null)
  const [filtersError, setFiltersError] = useState('')
  const [scrub, setScrub] = useState(0.55)
  const [status, setStatus] = useState<any>(null)
  const [statusError, setStatusError] = useState('')
  const [server, setServer] = useState<{state: 'idle' | 'busy' | 'ok' | 'error'; text: string; plan?: any}>({state: 'idle', text: ''})
  const [busy, setBusy] = useState('')

  const base = account === 'LIVE' ? `${v25Base}/dca/live` : `${demoBase}/dca`
  const filtersPath = account === 'LIVE' ? `${v25Base}/grid/filters` : `${demoBase}/grid/filters`

  useEffect(() => {
    if (!symbol) return
    let cancelled = false
    setFilters(null); setFiltersError('')
    call<any>(filtersPath, `?symbol=${encodeURIComponent(symbol)}`).then(data => {
      if (cancelled) return
      const tick = Number(data?.tickSize), step = Number(data?.stepSize), min = Number(data?.minNotional)
      if (tick > 0 && step > 0 && min >= 0) setFilters({tickSize: tick, stepSize: step, minNotional: min})
      else setFiltersError('Borsa kuralları geçersiz geldi.')
    }).catch(error => { if (!cancelled) setFiltersError(error?.message || 'Borsa kuralları okunamadı.') })
    return () => { cancelled = true }
  }, [symbol, filtersPath])

  const loadStatus = () => call<any>(base, '/status').then(data => { setStatus(data); setStatusError('') }).catch(error => { setStatus(null); setStatusError(error?.message || 'Durum okunamadı.') })
  useEffect(() => {
    let cancelled = false
    const tick = () => { if (!cancelled) void loadStatus() }
    tick()
    const timer = window.setInterval(tick, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [base])
  useEffect(() => { setServer({state: 'idle', text: ''}) }, [account, symbol, side, form])

  const input = useMemo(() => ({
    side, price, baseMargin: num(form.margin), safetyOrders: form.safety, stepPct: num(form.step), volumeMultiplier: num(form.volume),
    takeProfitPct: num(form.tp), stopGapPct: num(form.gap), leverage: form.leverage, filters: filters ?? undefined,
  }), [side, price, form, filters])
  const result = useMemo(() => computeDca(input), [input])
  const ready = price > 0 && filters !== null
  const scenario = result.valid ? scenarioAt(result, price, side, scrub) : null
  const set = <K extends keyof Form>(key: K, value: Form[K]) => { setPresetId(''); setForm(current => ({...current, [key]: value})) }
  const pick = (preset: Preset) => { setPresetId(preset.id); setForm(preset.form) }

  const body = (preview: boolean, confirmation = '') => JSON.stringify({
    symbol, direction: side, base_margin_usdt: input.baseMargin, safety_orders: input.safetyOrders, step_pct: input.stepPct, volume_multiplier: input.volumeMultiplier,
    take_profit_pct: input.takeProfitPct, stop_gap_pct: input.stopGapPct, leverage: input.leverage, recycle: form.recycle, preview, confirmation,
  })
  const checkOnServer = async () => {
    setServer({state: 'busy', text: 'Sunucu planı kendi kurallarıyla yeniden hesaplıyor…'})
    try {
      const data = await call<any>(base, '/start', {method: 'POST', body: body(true), timeoutMs: 60000})
      setServer({state: 'ok', plan: data.plan, text: 'Sunucu planı onayladı.'})
    } catch (error: any) { setServer({state: 'error', text: error?.message || 'Sunucu planı reddetti.'}) }
  }

  const liveEnabled = account === 'LIVE' ? status?.enabled !== false && status?.enabled !== undefined : true
  const whyBlocked = !ready ? (filtersError || 'Borsa kuralları yükleniyor…')
    : !result.valid ? 'Önce planı düzelt (aşağıdaki uyarılar).'
    : account === 'LIVE' && !liveEnabled ? 'Canlı DCA sunucuda kapalı. İlk testte kapalı gelir; bilinçli olarak açılması gerekir.'
    : account === 'LIVE' && !liveArmed ? 'Canlı emir kilidi (ARM) kapalı.'
    : account === 'LIVE' && result.totalMargin > (status?.live_max_margin_usdt ?? 10) + 1e-9 ? `Canlı DCA şu an en çok ${status?.live_max_margin_usdt ?? 10} USDT toplam marjinle çalışır.`
    : ''

  const start = () => {
    const live = account === 'LIVE'
    askConfirm({
      title: live ? 'CANLI DCA BAŞLAT' : 'DEMO DCA BAŞLAT',
      message: `${live ? 'GERÇEK PARA. ' : 'Demo hesap, gerçek para yok. '}${symbol} ${side}: taban + ${form.safety} ek emir, toplam marjin ${fmtUsd(result.totalMargin)}, kaldıraç ${form.leverage}x. `
        + `Tüm emirler dolup stop çalışırsa tahmini en kötü zarar ${fmtUsd(result.worstLoss)}. Taban emir hemen piyasadan girer; ek emirler limit olarak bekler, stop dolan her miktar için kurulur.`,
      expected: live ? 'CANLI DCA BAŞLAT' : undefined, simple: !live,
      action: async () => {
        await call(base, '/start', {method: 'POST', timeoutMs: 60000, body: body(false, live ? 'CANLI DCA BAŞLAT' : 'DEMO DCA BAŞLAT')})
        await loadStatus()
      },
    })
  }
  const act = (kind: 'stop' | 'emergency' | 'resume', gridId: string) => {
    const query = `?grid_id=${encodeURIComponent(gridId)}`
    if (kind === 'resume') { setBusy(gridId); void call(base, `/resume${query}`, {method: 'POST', timeoutMs: 60000}).then(loadStatus).catch(error => setStatusError(error?.message || 'Devam ettirilemedi.')).finally(() => setBusy('')); return }
    askConfirm({
      title: kind === 'stop' ? 'DCA botunu durdur' : 'DCA ACİL DURDUR',
      message: kind === 'stop' ? 'Bekleyen ek emirler iptal edilir. Açık pozisyon varsa stop ve kâr hedefiyle yerinde kalır.'
        : 'Bekleyen emirler iptal edilir, pozisyon piyasadan kapatılır ve kapandığı borsadan doğrulanır. Doğrulanamazsa koruyucu stoplara dokunulmaz.',
      expected: kind === 'emergency' ? 'ACİL DURDUR' : undefined, simple: kind === 'stop',
      action: async () => { await call(base, `/${kind}${query}`, {method: 'POST', timeoutMs: 90000}); await loadStatus() },
    })
  }

  const bots: any[] = (status?.grids ?? []).filter((g: any) => g?.kind === 'DCA' || g?.spec?.meta?.kind === 'DCA')
  const active = bots.filter(g => !FINAL.includes(g.status))
  const finished = bots.filter(g => FINAL.includes(g.status)).slice(0, 3)
  const dir = side === 'LONG'

  return (
    <section className="dca" aria-label="DCA botu">
      <header className="dcaHero">
        <div className="dcaHeroGlow" aria-hidden="true" />
        <div className="dcaHeroTop">
          <div>
            <small>OTOMATİK ORTALAMA</small>
            <h3>DCA Botu</h3>
            <p>Taban emir girer, fiyat aleyhine giderse ek emirlerle ortalamayı düşürür; ortalamanın biraz üstünde tek hedefle çıkar. Hep stoplu.</p>
          </div>
          <div className="dcaAccount" role="group" aria-label="Hesap">
            <button type="button" aria-pressed={account === 'DEMO'} className={account === 'DEMO' ? 'on' : ''} onClick={() => setAccount('DEMO')}>Demo</button>
            <button type="button" aria-pressed={account === 'LIVE'} className={account === 'LIVE' ? 'on live' : 'live'} onClick={() => setAccount('LIVE')}>Canlı</button>
          </div>
        </div>
        <div className="dcaChips">
          <span className="dcaChip coin">{symbol || 'Coin seç'}</span>
          <span className={`dcaChip ${dir ? 'up' : 'down'}`}>{dir ? 'LONG ▲' : 'SHORT ▼'}</span>
          <span className="dcaChip">Fiyat {fmtPrice(price)}</span>
          {account === 'LIVE' && <span className={`dcaChip ${liveEnabled && liveArmed ? 'up' : 'warn'}`}>{!liveEnabled ? 'Sunucuda kapalı' : liveArmed ? 'ARM açık' : 'ARM kapalı'}</span>}
        </div>
      </header>

      {active.length > 0 && <div className="dcaBots" aria-label="Çalışan botlar">{active.map(bot => <BotCard key={bot.grid_id} bot={bot} busy={busy === bot.grid_id} onAct={act} />)}</div>}

      <div className="dcaPresets" role="group" aria-label="Hazır ayarlar">
        {DCA_PRESETS.map(preset => (
          <button key={preset.id} type="button" className={presetId === preset.id ? 'on' : ''} aria-pressed={presetId === preset.id} onClick={() => pick(preset)}>
            <b>{preset.name}</b><small>{preset.hint}</small>
            <span className="dcaPresetMeta">{preset.form.safety} ek · %{preset.form.step} aralık · {preset.form.leverage}x</span>
          </button>
        ))}
      </div>

      <div className="dcaGrid">
        <div className="dcaCard dcaForm">
          <Field label="Taban marjin" suffix="USDT"><input inputMode="decimal" value={form.margin} onChange={e => set('margin', e.target.value)} /></Field>
          <div className="dcaField">
            <label>Ek emir sayısı</label>
            <div className="dcaSeg">{[1, 2, 3, 4, 5].map(n => <button key={n} type="button" className={form.safety === n ? 'on' : ''} onClick={() => set('safety', n)}>{n}</button>)}</div>
          </div>
          <div className="dcaField">
            <label>Kaldıraç</label>
            <div className="dcaSeg">{[1, 2, 3, 4, 5].map(n => <button key={n} type="button" className={form.leverage === n ? 'on' : ''} onClick={() => set('leverage', n)}>{n}x</button>)}</div>
            <small>DCA'da en fazla {DCA_LIMITS.maxLeverage}x.</small>
          </div>
          <Slider label="Ek emirler arası aralık" unit="%" min={DCA_LIMITS.stepPct[0]} max={DCA_LIMITS.stepPct[1]} step={0.1} value={form.step} onChange={v => set('step', v)} />
          <Slider label="Hacim çarpanı" unit="x" min={1} max={DCA_LIMITS.maxVolumeMultiplier} step={0.1} value={form.volume} onChange={v => set('volume', v)} hint="Her ek emir bir öncekinden bu kadar büyük" />
          <Slider label="Kâr hedefi (ortalamadan)" unit="%" min={DCA_LIMITS.takeProfitPct[0]} max={5} step={0.1} value={form.tp} onChange={v => set('tp', v)} />
          <Slider label="Stop mesafesi (son ek emrin ötesi)" unit="%" min={DCA_LIMITS.stopGapPct[0]} max={5} step={0.1} value={form.gap} onChange={v => set('gap', v)} />
          <label className="dcaToggle">
            <input type="checkbox" checked={form.recycle} onChange={e => { setPresetId(''); setForm(c => ({...c, recycle: e.target.checked})) }} />
            <span><b>Tur bitince yeniden başlat</b><small>Her yeni tur risk kapılarından baştan geçer; biri geçmezse bot durur.</small></span>
          </label>
        </div>

        <div className="dcaCard dcaVisual">
          {result.valid && ready ? <>
            <DcaLadder result={result} price={price} side={side} scrub={scrub} />
            <div className="dcaScrubBox">
              <div className="dcaScrubHead"><b>Fiyat gidersen ne olur?</b><span>{scenario ? fmtPrice(scenario.at) : '—'}</span></div>
              <input className="dcaRange" type="range" min={0} max={1000} value={Math.round(scrub * 1000)} onChange={e => setScrub(Number(e.target.value) / 1000)} aria-label="Fiyat senaryosu" />
              {scenario && <div className="dcaScenario" data-neg={scenario.pnl < 0 ? '1' : '0'}>
                <span><small>Dolan emir</small><b>{scenario.count}/{result.stages.length}</b></span>
                <span><small>Ortalama</small><b>{fmtPrice(scenario.avg)}</b></span>
                <span><small>Hedef</small><b>{fmtPrice(scenario.tp)}</b></span>
                <span><small>Anlık K/Z</small><b className={scenario.pnl >= 0 ? 'up' : 'down'}>{fmtUsd(scenario.pnl, true)}</b></span>
              </div>}
              {scenario?.atStop && <p className="dcaNote down">Stop çalışırsa tahmini en kötü zarar: {fmtUsd(-result.worstLoss)} (komisyon dahil).</p>}
            </div>
          </> : <div className="dcaEmpty">
            <div className="dcaEmptyArt" aria-hidden="true"><i /><i /><i /><i /></div>
            <b>{ready ? 'Plan henüz geçerli değil' : 'Hazırlanıyor'}</b>
            <small>{ready ? 'Aşağıdaki uyarıları düzelt, merdiven burada görünecek.' : (filtersError || 'Borsa kuralları okunuyor…')}</small>
          </div>}
        </div>
      </div>

      {result.errors.length > 0 && ready && <ul className="dcaErrors" role="alert">{result.errors.map(text => <li key={text}><i>!</i><span>{text}</span></li>)}</ul>}

      {result.valid && <div className="dcaStats">
        <Stat label="Toplam marjin" value={fmtUsd(result.totalMargin)} hint="Taban + tüm ek emirler" />
        <Stat label="En kötü zarar" value={fmtUsd(-result.worstLoss)} tone="down" hint="Hepsi dolar, stop çalışır" />
        <Stat label="Hedefte net kâr" value={fmtUsd(result.stages[result.stages.length - 1].tpNetProfit, true)} tone="up" hint="Hepsi dolmuşsa, ücret düşülmüş" />
        <Stat label="Tahmini likidasyon" value={fmtPrice(result.liquidation)} hint={`Stoptan %${result.liquidationGapPct.toFixed(1)} uzak · tahmin`} />
      </div>}

      <div className="dcaTable" role="table" aria-label="Aşamalar">
        {result.stages.length > 0 && <>
          <div role="row" className="dcaTHead"><span>#</span><span>Giriş</span><span>Büyüklük</span><span>Ortalama</span><span>Hedef</span></div>
          {result.stages.map(s => <div role="row" key={s.index}><span>{s.index === 0 ? 'T' : s.index}</span><span>{fmtPrice(s.entry)}</span><span>{s.notional.toFixed(1)}</span><span>{fmtPrice(s.avgEntry)}</span><span className="up">{fmtPrice(s.tpPrice)}</span></div>)}
        </>}
      </div>

      <div className={`dcaServer ${server.state}`} role="status">
        {server.state === 'ok' && server.plan && <><b>✓ Sunucu onayladı</b><small>En kötü zarar {fmtUsd(-Number(server.plan.worst_loss_usdt))} · toplam marjin {fmtUsd(Number(server.plan.total_margin_usdt))}</small></>}
        {server.state === 'error' && <><b>Sunucu reddetti</b><small>{server.text}</small></>}
        {server.state === 'busy' && <small>{server.text}</small>}
        {server.state === 'idle' && <small>Ekrandaki hesap tahmindir. Başlatmadan önce sunucuya doğrulat: bakiye, günlük zarar hakkı ve diğer botlar dikkate alınır.</small>}
      </div>

      <div className="dcaActions">
        <button type="button" className="dcaGhost" disabled={!result.valid || !ready || server.state === 'busy'} onClick={checkOnServer}>Sunucuyla doğrula</button>
        <button type="button" className={`dcaGo ${account === 'LIVE' ? 'live' : ''}`} disabled={Boolean(whyBlocked)} onClick={start}>{account === 'LIVE' ? 'Canlı başlat' : 'Demo başlat'}</button>
      </div>
      {whyBlocked && <p className="dcaWhy">{whyBlocked}</p>}
      {statusError && <p className="dcaWhy bad">{statusError}</p>}
      <p className="dcaFine">Hepsi tahmindir: funding, kayma ve borsanın gerçek teminat tablosu dahil değil. Likidasyon fiyatı varsayımsal bakım oranıyla hesaplanır. Geçmiş sonuç gelecek kazancı garanti etmez.</p>

      {finished.length > 0 && <div className="dcaPast"><b>Son botlar</b>{finished.map(bot => <div key={bot.grid_id}><span>{bot.symbol} {bot.side}</span><span>{STATUS[bot.status]?.text || bot.status}</span><span className={Number(bot.stats?.realized_usdt) >= 0 ? 'up' : 'down'}>{fmtUsd(Number(bot.stats?.realized_usdt || 0), true)}</span></div>)}</div>}
    </section>
  )
}

function Field({label, suffix, children}: {label: string; suffix?: string; children: React.ReactNode}) {
  return <div className="dcaField"><label>{label}</label><div className="dcaInput">{children}{suffix && <em>{suffix}</em>}</div></div>
}

function Slider({label, unit, min, max, step, value, onChange, hint}: {label: string; unit: string; min: number; max: number; step: number; value: string; onChange: (v: string) => void; hint?: string}) {
  const n = num(value)
  const pct = Number.isFinite(n) ? Math.max(0, Math.min(100, ((n - min) / (max - min)) * 100)) : 0
  return (
    <div className="dcaField dcaSlider">
      <label><span>{label}</span><b>{value}{unit}</b></label>
      <input type="range" min={min} max={max} step={step} value={Number.isFinite(n) ? Math.min(Math.max(n, min), max) : min} style={{['--p' as any]: `${pct}%`}} onChange={e => onChange(String(Number(e.target.value)))} />
      {hint && <small>{hint}</small>}
    </div>
  )
}

function Stat({label, value, tone, hint}: {label: string; value: string; tone?: 'up' | 'down'; hint?: string}) {
  return <div className="dcaStat"><small>{label}</small><b className={tone}>{value}</b>{hint && <em>{hint}</em>}</div>
}

function BotCard({bot, busy, onAct}: {bot: any; busy: boolean; onAct: (kind: 'stop' | 'emergency' | 'resume', id: string) => void}) {
  const stats = bot.stats || {}
  const info = STATUS[bot.status] || {text: bot.status, tone: 'wait' as const}
  const total = Number(stats.stages_total || 0), done = Number(stats.stages_filled || 0)
  const long = bot.side === 'LONG'
  const alerts: Array<{level: string; text: string}> = (bot.alerts || []).slice(-2)
  const hasPos = Math.abs(Number(bot.position_qty || 0)) > 1e-9
  return (
    <article className="dcaBot" data-tone={info.tone}>
      <div className="dcaBotTop">
        <div><b>{bot.symbol}</b><span className={long ? 'up' : 'down'}>{long ? 'LONG ▲' : 'SHORT ▼'}</span></div>
        <span className="dcaStatus" data-tone={info.tone}><i />{info.text}</span>
      </div>
      <div className="dcaRail" aria-label={`${done}/${total} emir doldu`}>
        {Array.from({length: total}).map((_, index) => <span key={index} className={index < done ? 'on' : ''}><i>{index === 0 ? 'T' : index}</i></span>)}
      </div>
      <div className="dcaBotGrid">
        <span><small>Pozisyon</small><b>{hasPos ? Number(bot.position_qty).toString() : '—'}</b></span>
        <span><small>Ortalama</small><b>{stats.avg_entry ? fmtPrice(Number(stats.avg_entry)) : '—'}</b></span>
        <span><small>Kâr hedefi</small><b className="up">{stats.tp_price ? fmtPrice(Number(stats.tp_price)) : '—'}</b></span>
        <span><small>Stop</small><b className="down">{fmtPrice(Number(stats.stop_price))}</b></span>
        <span><small>Tur</small><b>{stats.cycle ?? 1}{Number(stats.completed_cycles) > 0 ? ` · ${stats.completed_cycles} bitti` : ''}</b></span>
        <span><small>Gerçekleşen</small><b className={Number(stats.realized_usdt) >= 0 ? 'up' : 'down'}>{fmtUsd(Number(stats.realized_usdt || 0), true)}</b></span>
      </div>
      {bot.liq?.distance_pct != null && bot.liq.level !== 'ok' && <p className="dcaBotWarn">Likidasyona %{bot.liq.distance_pct} kaldı.</p>}
      {bot.reason && <p className="dcaBotReason">{bot.reason}</p>}
      {alerts.map((a, i) => <p key={i} className="dcaBotAlert" data-level={a.level}>{a.text}</p>)}
      <div className="dcaBotActions">
        {bot.status === 'PAUSED' && <button type="button" disabled={busy} onClick={() => onAct('resume', bot.grid_id)}>Devam ettir</button>}
        {bot.status !== 'PAUSED' && !bot.needs_user && <button type="button" onClick={() => onAct('stop', bot.grid_id)}>Durdur</button>}
        <button type="button" className="danger" onClick={() => onAct('emergency', bot.grid_id)}>Acil durdur</button>
      </div>
    </article>
  )
}

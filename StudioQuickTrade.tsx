import {useEffect, useMemo, useRef, useState, type CSSProperties} from 'react'
import {ChevronDown, ChevronUp, Check, TriangleAlert, X} from 'lucide-react'
import {API_BASE} from './api'
import {currentLang} from './i18n'
import {analysisPrice} from './master-trade-presentation'
import {requestPositionsRefresh} from './trade-intent'
import {liveBlockerMessage, type SharedLiveStatus} from './frontend/src/LiveTradingPanel'
import {
  DEMO_CLOSE_PHRASE, DEMO_PHRASE, DEMO_REDUCE_PHRASE, LIVE_CLOSE_PHRASE, LIVE_PHRASE, LIVE_REDUCE_PHRASE,
  activePlan, allowedLeverages, allowedMargins, isOpen, levelsFor, nearestChoice, newIntentId, planRights, postJson, riskSummary, setLanguageReader, sideOf, tx,
  type AnalysisLevels, type Levels, type PositionLike, type Side,
} from './studio-quick-trade'
import {HoldButton, useHold} from './studio-hold'
import StudioGrid from './StudioGrid'
import type {Candle, GridChartLine} from './studio-grid'
import './studio-quick-trade.css'

setLanguageReader(() => currentLang() === 'en')

export type StudioPosition = PositionLike & {stop_loss?: number; tp1?: number; tp2?: number; tp3?: number}
export type StudioDecision = {direction: string; confidenceScore: number | null; mtfScore: number | null; mtfConfirmed: number; mtfTotal: number; status: string}

const V25 = `${API_BASE}/v25`
const DEMO = `${API_BASE}/binance-demo`
const PREFS_KEY = 'kaistrade-studio-trade-prefs'
const HOLD_MS = {live: 1200, demo: 700}
/** Seviyeler dondurulduğunda fiyat bundan fazla oynarsa emir gönderilmez; seviyeler yenilenir. */
const MAX_DRIFT_PCT = .5

const locale = () => currentLang() === 'en' ? 'en-US' : 'tr-TR'
const fmt = (value: number, digits = 2) => value.toLocaleString(locale(), {minimumFractionDigits: digits, maximumFractionDigits: digits})
const signed = (value: number, digits = 2) => `${value >= 0 ? '+' : '−'}${fmt(Math.abs(value), digits)}`
const positive = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
const short = (symbol: string) => symbol.replace(/USDT$/, '')

type Prefs = {margin: number; leverage: number}
const readPrefs = (): Prefs => {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}')
    const value = typeof raw === 'object' && raw !== null ? raw as Record<string, unknown> : {}
    return {margin: Number(value.margin) || 25, leverage: Number(value.leverage) || 2}
  } catch { return {margin: 25, leverage: 2} }
}
const savePrefs = (prefs: Prefs) => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)) } catch { /* tarayıcı kaydı kapalı olabilir */ } }

/** Anlık kâr/zarar: açık pozisyonun coini için canlı fiyattan, diğerleri için hesabın son değerinden. */
export function positionPnl(position: StudioPosition, livePrice: number | null): {pnl: number | null; pct: number | null} {
  const qty = Math.abs(Number(position.quantity) || 0)
  const entry = positive(position.entry_price)
  const now = positive(livePrice) ?? positive(position.mark_price)
  const sign = sideOf(position) === 'LONG' ? 1 : -1
  const pnl = entry && now && qty > 0 ? (now - entry) * qty * sign : typeof position.unrealized_pnl === 'number' && Number.isFinite(position.unrealized_pnl) ? position.unrealized_pnl : null
  const lev = positive(position.leverage ?? undefined)
  const margin = entry && qty > 0 && lev ? entry * qty / lev : null
  return {pnl, pct: pnl !== null && margin ? pnl / margin * 100 : null}
}

/** Açık işlem varken sağ altta: pozisyonu kapatan tek tuş (LONG → SAT, SHORT → AL). Basılı tutarak çalışır; yeni işlem açmaz. */
function CloseDock({position, demo, disabled, onDone}: {position: StudioPosition; demo: boolean; disabled: boolean; onDone: () => void}) {
  const long = sideOf(position) === 'LONG'
  const ms = demo ? HOLD_MS.demo : HOLD_MS.live
  const {holding, bind} = useHold(ms, onDone, disabled) // yazı basılıyken değişmez: tuş boyutu sabit kalır
  const word = long ? tx('SAT', 'SELL') : tx('AL', 'BUY')
  const lev = positive(position.leverage ?? undefined)
  return <div className="sqtCloseDock" data-demo={demo || undefined} role="group" aria-label={tx('Pozisyonu kapat', 'Close position')}>
    <span className="sqtCloseInfo"><span className="sqtMode" data-demo={demo || undefined}>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span><b>{short(position.symbol)} · {sideOf(position)}{lev ? ` ${lev}x` : ''}</b></span>
    <button type="button" className="sqtSide sqtCloseBtn" data-side={long ? 'sell' : 'buy'} data-holding={holding} disabled={disabled} style={{'--sqt-hold': `${ms}ms`} as CSSProperties} {...bind}
      aria-label={tx(`${position.symbol} pozisyonunu kapatmak için ${word} tuşunu basılı tut`, `Press and hold ${word} to close the ${position.symbol} position`)}>
      <span className="sqtHoldFill" aria-hidden="true"/>
      <span className="sqtTx"><b>{word}</b><small>{tx('basılı tut · kapat', 'hold · close')}</small></span>
      <span className="sqtIc" aria-hidden="true">{long ? '▼' : '▲'}</span>
    </button>
  </div>
}

type Toast = {kind: 'ok' | 'warn' | 'error'; title: string; detail?: string}

/** Üst çubuktaki (OHLC satırı) açık pozisyon sekmeleri: yalnız açık işlemleri gösterir, dokununca grafik o coine geçer. */
export function StudioPositionTabs({positions, symbol, price, onSelect}: {positions: readonly StudioPosition[]; symbol: string; price: number | null; onSelect: (symbol: string) => void}) {
  const open = positions.filter(isOpen)
  const viewing = open.some(position => position.symbol === symbol)
  // Tek işlem ve zaten onun grafiğindeyken sekme gereksizdir; başka coine bakarken ya da 2+ işlemde görünür.
  if (!open.length || (open.length === 1 && viewing)) return null
  return <div className="sqtTabs" role="tablist" aria-label={tx('Açık işlemler', 'Open trades')}>
    {open.map(position => {
      const {pnl} = positionPnl(position, position.symbol === symbol ? price : null)
      const selected = position.symbol === symbol
      return <button key={position.symbol} type="button" role="tab" aria-selected={selected} className="sqtTab" data-on={selected} data-side={sideOf(position)} title={`${position.symbol} · ${sideOf(position)}`} onClick={() => onSelect(position.symbol)}>
        <i aria-hidden="true"/><b>{short(position.symbol)}</b>{pnl !== null && <span data-tone={pnl >= 0 ? 'up' : 'down'}>{signed(pnl)}</span>}
      </button>
    })}
  </div>
}

type Props = {
  symbol: string; price: number | null; demo: boolean; premium: boolean; onUpgrade: () => void
  live: SharedLiveStatus | null; analysis: AnalysisLevels; decision: StudioDecision
  positions: readonly StudioPosition[]; onSelect: (symbol: string) => void; drawerOpen: boolean; dataReady: boolean
  candles?: readonly Candle[]; onGridLines?: (lines: GridChartLine[]) => void
}

export default function StudioQuickTrade({symbol, price, demo, premium, onUpgrade, live, analysis, decision, positions, onSelect, drawerOpen, dataReady, candles, onGridLines}: Props) {
  const [prefs, setPrefs] = useState<Prefs>(readPrefs)
  const [side, setSide] = useState<Side | null>(null)
  const [frozen, setFrozen] = useState<{price: number; levels: Levels} | null>(null)
  const [intentId, setIntentId] = useState(newIntentId)
  const [busy, setBusy] = useState<string | null>(null)
  const [sheetError, setSheetError] = useState('')
  const [toast, setToast] = useState<Toast | null>(null)
  const [settings, setSettings] = useState(false)
  const [card, setCard] = useState(false)
  const [tray, setTray] = useState(false)
  const [action, setAction] = useState<'half' | 'close' | null>(null)
  const [actionError, setActionError] = useState('')
  const [lockedUntil, setLockedUntil] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [gridOpen, setGridOpen] = useState(false)
  const busyRef = useRef(false)

  const open = useMemo(() => positions.filter(isOpen), [positions])
  const current = open.find(position => position.symbol === symbol) ?? null
  const locked = demo ? false : live?.real_trading_locked !== false
  const armed = Boolean(live?.armed && !locked)
  const blocker = demo ? '' : liveBlockerMessage(live, armed)
  const liveBlocked = !demo && blocker !== 'Engel yok.'
  const policy = (live?.policy ?? {}) as Record<string, unknown>
  const margins = useMemo(() => allowedMargins(demo ? 100 : Number(policy.max_margin_per_trade)), [demo, policy.max_margin_per_trade])
  const leverages = useMemo(() => allowedLeverages(demo ? 3 : Number(policy.max_leverage)), [demo, policy.max_leverage])
  const margin = nearestChoice(prefs.margin, margins)
  const leverage = nearestChoice(prefs.leverage, leverages)
  const update = (change: Partial<Prefs>) => setPrefs(currentPrefs => { const next = {...currentPrefs, ...change}; savePrefs(next); return next })

  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id) }, [])
  // Coin değişince açık pencereler kapanır; yarım kalmış bir onay başka coine gitmesin.
  useEffect(() => { setSide(null); setFrozen(null); setSettings(false); setAction(null); setActionError(''); setSheetError(''); setTray(false) }, [symbol])
  useEffect(() => { if (!current) { setCard(false); setAction(null) } }, [current])
  useEffect(() => { if (!toast) return; const id = window.setTimeout(() => setToast(null), toast.kind === 'ok' ? 4500 : 9000); return () => window.clearTimeout(id) }, [toast])
  // Esc: önce pencereyi kapatır (tam ekranı kapatmadan).
  useEffect(() => {
    if (!side && !settings && !tray) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busyRef.current) { event.stopPropagation(); setSide(null); setFrozen(null); setSettings(false); setTray(false) } }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [side, settings, tray])

  const startOrder = (next: Side) => {
    if (!demo && !premium) { onUpgrade(); return }
    const fresh = levelsFor(next, price, analysis)
    const at = positive(price)
    if (at === null || !fresh) { setToast({kind: 'error', title: tx('Fiyat ya da analiz verisi hazır değil', 'Price or analysis data is not ready'), detail: tx('Birkaç saniye sonra tekrar dene.', 'Try again in a few seconds.')}); return }
    setSheetError(''); setIntentId(newIntentId()); setFrozen({price: at, levels: fresh}); setSide(next); setSettings(false)
  }
  const closeSheet = () => { if (busyRef.current) return; setSide(null); setFrozen(null); setSheetError('') }
  const changeParam = (change: Partial<Prefs>) => { update(change); setIntentId(newIntentId()); setSheetError('') }

  const sending = busy !== null
  const lockLeft = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const risk = frozen && side ? riskSummary(frozen.price, frozen.levels, margin, leverage) : null

  const placeOrder = async () => {
    if (!side || !frozen || busyRef.current) return
    const livePrice = positive(price)
    if (!livePrice || Math.abs(livePrice - frozen.price) / frozen.price * 100 > MAX_DRIFT_PCT) {
      const fresh = livePrice ? levelsFor(side, livePrice, analysis) : null
      if (livePrice && fresh) setFrozen({price: livePrice, levels: fresh})
      setIntentId(newIntentId())
      setSheetError(tx(`Fiyat %${MAX_DRIFT_PCT} üzerinde oynadı; seviyeler güncellendi. Kontrol edip tekrar basılı tut.`, `The price moved by more than ${MAX_DRIFT_PCT}%; levels were updated. Review them and hold again.`))
      return
    }
    busyRef.current = true
    const {levels} = frozen
    const base = {symbol, direction: side, order_type: 'MARKET', margin_usdt: margin, leverage, limit_price: null, stop_loss: levels.stop, tp1: levels.tp1, tp2: levels.tp2, tp3: levels.tp3}
    try {
      if (demo) {
        setBusy(tx('Demo emri gönderiliyor…', 'Sending demo order…'))
        const result = await postJson<{risk_adjusted?: boolean}>(`${DEMO}/order`, {...base, confirmation: DEMO_PHRASE}, 60000)
        finish(result, tx('Demo emri gönderildi', 'Demo order sent'))
      } else {
        setBusy(tx('Binance doğrulaması…', 'Checking with Binance…'))
        const payload = {...base, tp_split: '60-20-20', limit_expiry_minutes: null, intent_id: intentId}
        const test = await postJson<unknown>(`${V25}/order/test`, payload, 45000)
        if (!test.ok) { setSheetError(test.message); return }
        setBusy(tx('Emir gönderiliyor…', 'Sending order…'))
        const result = await postJson<{risk_adjusted?: boolean}>(`${V25}/order`, {...payload, confirmation: LIVE_PHRASE}, 60000)
        finish(result, tx('Canlı emir gönderildi', 'Live order sent'))
      }
    } finally {
      busyRef.current = false
      setBusy(null)
    }
  }
  const finish = (result: Awaited<ReturnType<typeof postJson<{risk_adjusted?: boolean}>>>, title: string) => {
    if (result.ok) {
      setSide(null); setFrozen(null); setSheetError('')
      setToast({kind: 'ok', title: `${title} · ${symbol}`, detail: result.data?.risk_adjusted ? tx('Risk limiti nedeniyle büyüklük otomatik azaltıldı. Stop ve hedefler emirle birlikte kuruldu.', 'The size was reduced automatically because of the risk limit. Stop and targets are placed with the order.') : tx('Stop ve hedefler emirle birlikte kuruluyor.', 'Stop and targets are being placed with the order.')})
      requestPositionsRefresh()
    } else if (result.uncertain) {
      // Emir gitmiş olabilir: aynı emri tekrar göndermeyiz; pozisyonlar yenilenir ve kısa süre yeni emir kapalı kalır.
      setSide(null); setFrozen(null)
      setLockedUntil(Date.now() + 20000)
      setToast({kind: 'warn', title: tx('Emir sonucu doğrulanamadı', 'Order result could not be confirmed'), detail: `${result.message} ${tx('Pozisyonların yenilenmesini bekle; açıldıysa sekmede görünür. Emri tekrar göndermeden önce kontrol et.', 'Wait for positions to refresh; if it opened it will appear as a tab. Check before sending the order again.')}`})
      requestPositionsRefresh()
    } else {
      setSheetError(result.message); setIntentId(newIntentId())
    }
  }

  // ---- Açık pozisyon: yarısını kapat / tamamen kapat ----
  const plan = !demo && current ? activePlan(live?.plans as Array<Record<string, unknown>> | undefined, current.symbol) : undefined
  const rights = demo ? {id: '', close: true, reduce: true} : planRights(plan)
  const runAction = async (kind: 'half' | 'close') => {
    if (!current || busyRef.current) return
    busyRef.current = true
    setBusy(kind === 'half' ? tx('Yarısı kapatılıyor…', 'Closing half…') : tx('Pozisyon kapatılıyor…', 'Closing position…')); setActionError('')
    try {
      let result
      if (demo) {
        const body = kind === 'half'
          ? {symbol: current.symbol, quantity: Math.abs(Number(current.quantity)) / 2, position_side: current.position_side || 'BOTH', confirmation: DEMO_REDUCE_PHRASE}
          : {symbol: current.symbol, position_side: current.position_side || 'BOTH', confirmation: DEMO_CLOSE_PHRASE}
        result = await postJson<unknown>(`${DEMO}/position/${kind === 'half' ? 'reduce' : 'close'}`, body, 60000)
      } else {
        const body = kind === 'half' ? {plan_id: rights.id, percent: 50, confirmation: LIVE_REDUCE_PHRASE} : {plan_id: rights.id, confirmation: LIVE_CLOSE_PHRASE}
        result = await postJson<unknown>(`${V25}/position/${kind === 'half' ? 'reduce' : 'close'}`, body, 60000)
      }
      if (result.ok) {
        setAction(null)
        setToast({kind: 'ok', title: kind === 'half' ? tx(`${current.symbol} pozisyonunun yarısı kapatıldı`, `Half of the ${current.symbol} position was closed`) : tx(`${current.symbol} kapatma emri gönderildi`, `${current.symbol} close order sent`), detail: kind === 'close' ? tx('Pozisyon kapanınca stop ve hedef emirleri otomatik temizlenir.', 'Stop and target orders are cleared automatically once the position is closed.') : undefined})
        requestPositionsRefresh()
      } else if (result.uncertain) {
        setAction(null)
        setToast({kind: 'warn', title: tx('Sonuç doğrulanamadı', 'Result could not be confirmed'), detail: `${result.message} ${tx('Pozisyonların yenilenmesini bekle.', 'Wait for positions to refresh.')}`})
        requestPositionsRefresh()
      } else setActionError(result.message)
    } finally { busyRef.current = false; setBusy(null) }
  }

  const dockDisabled = !dataReady || sending || lockLeft > 0
  const aligned = (target: Side) => decision.direction === target ? 'with' : decision.direction === 'LONG' || decision.direction === 'SHORT' ? 'against' : 'none'
  const price0 = positive(price)
  const confidenceText = tx(`${aligned(side ?? 'LONG') === 'with' ? 'Analiz seninle' : 'Analiz ters'} · ${decision.direction}${decision.confidenceScore !== null ? ` · güven %${Math.round(decision.confidenceScore)}` : ''}`, `${aligned(side ?? 'LONG') === 'with' ? 'Analysis agrees' : 'Analysis is opposite'} · ${decision.direction}${decision.confidenceScore !== null ? ` · confidence ${Math.round(decision.confidenceScore)}%` : ''}`)
  const pnl = current ? positionPnl(current, price) : null

  return <div className="sqtLayer" data-drawer={drawerOpen || undefined} data-grid={gridOpen || undefined}>
    {toast && <div className="sqtToast" role="status" data-kind={toast.kind}><span aria-hidden="true">{toast.kind === 'ok' ? <Check/> : <TriangleAlert/>}</span><div><b>{toast.title}</b>{toast.detail && <small>{toast.detail}</small>}</div><button type="button" aria-label={tx('Kapat', 'Close')} onClick={() => setToast(null)}><X/></button></div>}

    <StudioGrid symbol={symbol} price={price} demo={demo} premium={premium} onUpgrade={onUpgrade} analysis={analysis} decision={decision.direction} liveBlocker={liveBlocked ? blocker : ''}
      policy={policy} dataReady={dataReady} hasPosition={Boolean(current)} hidden={Boolean(side)} onSelect={onSelect} onToast={setToast} onOpenChange={setGridOpen} candles={candles} onLines={onGridLines}/>

    {!current && !side && <div className="sqtDock" data-demo={demo || undefined} role="group" aria-label={tx('Hızlı alım satım', 'Quick buy / sell')}>
      <button type="button" className="sqtMid" aria-expanded={settings} aria-label={tx('Teminat ve kaldıraç', 'Margin and leverage')} onClick={() => setSettings(open => !open)}>
        <span className="sqtMode" data-demo={demo || undefined}>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
        <b>{margin} USDT <em>· {leverage}x</em></b>
      </button>
      <button type="button" className="sqtSide" data-side="buy" disabled={dockDisabled} onClick={() => startOrder('LONG')} aria-label={`${symbol} ${tx('AL', 'BUY')} (LONG)`}><span className="sqtTx"><b>{tx('AL', 'BUY')}</b><small>{price0 ? analysisPrice(price0) : '—'}</small></span><span className="sqtIc" aria-hidden="true">▲</span></button>
      {settings && <div className="sqtPop" role="dialog" aria-label={tx('Teminat ve kaldıraç', 'Margin and leverage')}>
        <ParamPicker label={tx('Teminat (USDT)', 'Margin (USDT)')} choices={margins} value={margin} onPick={value => update({margin: value})}/>
        <ParamPicker label={tx('Kaldıraç', 'Leverage')} choices={leverages} value={leverage} suffix="x" onPick={value => update({leverage: value})}/>
        {liveBlocked && <p className="sqtPopWarn"><TriangleAlert aria-hidden="true"/>{blocker}</p>}
      </div>}
      {lockLeft > 0 && <p className="sqtLockNote" role="status">{tx(`Sonuç kontrol ediliyor · ${lockLeft} sn`, `Checking the result · ${lockLeft} s`)}</p>}
    </div>}

    {current && pnl && <div className="sqtPos" data-open={card} data-tray={tray || undefined} data-tone={pnl.pnl === null ? undefined : pnl.pnl >= 0 ? 'up' : 'down'}>
      {!card
        ? <button type="button" className="sqtChip" onClick={() => setCard(true)} aria-expanded="false" aria-label={tx(`${current.symbol} açık pozisyon detayı`, `${current.symbol} open position details`)}>
            <i aria-hidden="true"/><b>{sideOf(current)}{positive(current.leverage ?? undefined) ? ` ${current.leverage}x` : ''}</b><span className="sqtSep" aria-hidden="true"/>
            <strong>{pnl.pnl === null ? '—' : `${signed(pnl.pnl, Math.abs(pnl.pnl) < 1 ? 4 : 2)} USDT`}</strong>{pnl.pct !== null && <em>{signed(pnl.pct)}%</em>}<ChevronUp aria-hidden="true"/>
          </button>
        : <PositionCard position={current} price={price} pnl={pnl} rights={rights} demo={demo} busy={busy} action={action} actionError={actionError} onCollapse={() => { setCard(false); setAction(null); setActionError('') }}
            onAsk={kind => { setActionError(''); setAction(kind) }} onCancelAsk={() => { setActionError(''); setAction(null) }} onRun={runAction} onTray={open.length > 1 ? () => setTray(value => !value) : undefined}/>}
      {rights.close && !card && !tray && !side && <CloseDock position={current} demo={demo} disabled={sending || lockLeft > 0} onDone={() => void runAction('close')}/>}
      {tray && <div className="sqtTray" role="dialog" aria-label={tx('Açık pozisyonlar', 'Open positions')}>
        <header><b>{tx('Açık pozisyonlar', 'Open positions')}</b><button type="button" className="sqtRound" aria-label={tx('Listeyi kapat', 'Close list')} onClick={() => setTray(false)}><X aria-hidden="true"/></button><span>{tx(`${open.length} · toplam`, `${open.length} · total`)} <strong data-tone={open.reduce((sum, item) => sum + (positionPnl(item, item.symbol === symbol ? price : null).pnl ?? 0), 0) >= 0 ? 'up' : 'down'}>{signed(open.reduce((sum, item) => sum + (positionPnl(item, item.symbol === symbol ? price : null).pnl ?? 0), 0))} USDT</strong></span></header>
        {open.map(item => { const value = positionPnl(item, item.symbol === symbol ? price : null); return <button key={item.symbol} type="button" className="sqtTrayRow" data-on={item.symbol === symbol} onClick={() => { onSelect(item.symbol); setTray(false) }}>
          <i data-tone={(value.pnl ?? 0) >= 0 ? 'up' : 'down'} aria-hidden="true"/><b>{item.symbol}</b><em data-side={sideOf(item)}>{sideOf(item)}{positive(item.leverage ?? undefined) ? ` ${item.leverage}x` : ''}</em>
          <span data-tone={(value.pnl ?? 0) >= 0 ? 'up' : 'down'}>{value.pnl === null ? '—' : signed(value.pnl)}</span><small>{value.pct === null ? '' : `${signed(value.pct)}%`}</small>
        </button> })}
      </div>}
    </div>}

    {side && frozen && risk && <>
      <div className="sqtScrim" onClick={closeSheet} aria-hidden="true"/>
      <div className="sqtSheet" role="dialog" aria-modal="true" aria-label={tx(`${symbol} ${side === 'LONG' ? 'AL' : 'SAT'} onayı`, `${symbol} ${side === 'LONG' ? 'BUY' : 'SELL'} confirmation`)} data-side={side}>
        <header className="sqtHead">
          <span className="sqtTag" data-side={side}>{side === 'LONG' ? tx('AL · LONG', 'BUY · LONG') : tx('SAT · SHORT', 'SELL · SHORT')}</span><b>{symbol}</b>
          <span className="sqtMkt">{tx('Piyasa emri', 'Market order')} · {analysisPrice(frozen.price)}</span>
          <span className="sqtAcc" data-demo={demo || undefined}><i aria-hidden="true"/>{demo ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
          <button type="button" className="sqtX" aria-label={tx('Vazgeç', 'Cancel')} onClick={closeSheet} disabled={sending}><X/></button>
        </header>
        <div className="sqtBody">
          <div className="sqtLeft">
            <div className="sqtChips">
              {aligned(side) !== 'none' && <span data-tone={aligned(side) === 'with' ? 'ok' : 'bad'}>{confidenceText}</span>}
              {aligned(side) === 'none' && <span data-tone="warn">{tx('Analiz: net yön yok', 'Analysis: no clear direction')}</span>}
              {decision.mtfScore !== null && aligned(side) !== 'none' && <span data-tone={aligned(side) === 'with' ? 'ok' : 'bad'}>{tx(`MTF ${decision.mtfConfirmed}/${decision.mtfTotal} ${aligned(side) === 'with' ? 'uyumlu' : 'ters yönde'}`, `MTF ${decision.mtfConfirmed}/${decision.mtfTotal} ${aligned(side) === 'with' ? 'aligned' : 'opposite'}`)}</span>}
              {risk.rewardRisk !== null && <span data-tone={risk.rewardRisk >= 1 ? 'ok' : 'warn'}>{tx('R/Ö', 'R/R')} 1 : {fmt(risk.rewardRisk, 1)}</span>}
            </div>
            {aligned(side) === 'against' && <p className="sqtWarn"><b>{tx(`Analiz ${decision.direction} diyor.`, `The analysis says ${decision.direction}.`)}</b> {tx(`${side === 'LONG' ? 'AL' : 'SAT'} analizin tersine bir işlemdir.`, `${side === 'LONG' ? 'BUY' : 'SELL'} is a trade against the analysis.`)}</p>}
            {frozen.levels.source === 'mirror' && <p className="sqtNote">{tx('Analiz seviyeleri bu yöne ya da fiyata uymadığı için stop ve hedefler analizin mesafeleriyle yeniden hesaplandı.', 'The analysis levels do not fit this direction or price, so stop and targets were recalculated using the analysis distances.')}</p>}
            {frozen.levels.source === 'default' && <p className="sqtNote">{tx('Analiz seviyesi bulunamadı; standart mesafeler kullanıldı (stop %1,5).', 'No analysis levels found; standard distances were used (1.5% stop).')}</p>}
            <Ladder side={side} entry={frozen.price} levels={frozen.levels}/>
            <div className="sqtStats">
              <div data-tone="loss"><small>{tx('Stop olursa', 'If stopped out')}</small><b>−{fmt(risk.lossAtStop)} USDT</b></div>
              <div data-tone="gain"><small>{tx("TP1'de", 'At TP1')}</small><b>+{fmt(risk.gainAtTp1)} USDT</b></div>
              <div><small>{tx('Pozisyon büyüklüğü', 'Position size')}</small><b>{fmt(risk.notional, 0)} USDT</b></div>
            </div>
            <ul className="sqtChecks">
              <li data-ok="true"><Check aria-hidden="true"/>{tx('Stop ve 3 hedef emirle birlikte gider', 'Stop and 3 targets are sent with the order')}</li>
              {!demo && <li data-ok={!liveBlocked}>{liveBlocked ? <TriangleAlert aria-hidden="true"/> : <Check aria-hidden="true"/>}{liveBlocked ? blocker : tx('ARM açık · canlı kapılar geçti', 'ARM is on · live gates passed')}</li>}
              <li data-ok={risk.stopBeforeLiquidation}>{risk.stopBeforeLiquidation ? <Check aria-hidden="true"/> : <TriangleAlert aria-hidden="true"/>}{risk.stopBeforeLiquidation ? tx('Stop, likidasyondan önce çalışır', 'The stop triggers before liquidation') : tx('Stop likidasyona çok yakın; kaldıracı düşür', 'The stop is too close to liquidation; lower the leverage')}</li>
            </ul>
          </div>
          <div className="sqtRight">
            <ParamPicker label={tx('Teminat (USDT)', 'Margin (USDT)')} choices={margins} value={margin} onPick={value => changeParam({margin: value})} disabled={sending}/>
            <ParamPicker label={tx('Kaldıraç', 'Leverage')} choices={leverages} value={leverage} suffix="x" onPick={value => changeParam({leverage: value})} disabled={sending}/>
            {sheetError && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{sheetError}</p>}
            <HoldButton ms={demo ? HOLD_MS.demo : HOLD_MS.live} tone={side === 'LONG' ? 'buy' : 'sell'} disabled={sending || liveBlocked || !risk.stopBeforeLiquidation}
              label={sending ? busy ?? '' : tx(`Basılı tut · ${side === 'LONG' ? 'AL' : 'SAT'}`, `Hold · ${side === 'LONG' ? 'BUY' : 'SELL'}`)} onDone={() => void placeOrder()}
              hint={tx(`${symbol} ${side === 'LONG' ? 'AL' : 'SAT'} emrini göndermek için basılı tut`, `Press and hold to send the ${symbol} ${side === 'LONG' ? 'buy' : 'sell'} order`)}/>
            <button type="button" className="sqtCancel" onClick={closeSheet} disabled={sending}>{tx('Vazgeç', 'Cancel')}</button>
          </div>
        </div>
        <p className="sqtFine">{demo ? tx('Yalnız Demo hesaba gider · gerçek para yok.', 'Goes to the Demo account only · no real money.') : tx('Gerçek para kullanılır.', 'Real money is used.')} {tx('Analiz kazanç garantisi değildir.', 'The analysis is not a guarantee of profit.')}</p>
      </div>
    </>}
  </div>
}

function ParamPicker({label, choices, value, onPick, suffix = '', disabled = false}: {label: string; choices: readonly number[]; value: number; onPick: (value: number) => void; suffix?: string; disabled?: boolean}) {
  return <div className="sqtParam"><small>{label}</small><div role="radiogroup" aria-label={label}>{choices.map(choice => <button key={choice} type="button" role="radio" aria-checked={choice === value} data-on={choice === value} disabled={disabled} onClick={() => onPick(choice)}>{choice}{suffix}</button>)}</div></div>
}

/** Stop – giriş – hedefler: tek bakışta okunan seviye şeridi. SHORT'ta fiyat sırası tersine çevrilir. */
function Ladder({side, entry, levels}: {side: Side; entry: number; levels: Levels}) {
  const stopFirst = side === 'LONG'
  const rows = stopFirst
    ? [[tx('Stop', 'Stop'), levels.stop, 'loss'], [tx('Giriş', 'Entry'), entry, 'entry'], ['TP1', levels.tp1, 'gain'], ['TP2', levels.tp2, 'gain'], ['TP3', levels.tp3, 'gain']] as const
    : [['TP3', levels.tp3, 'gain'], ['TP2', levels.tp2, 'gain'], ['TP1', levels.tp1, 'gain'], [tx('Giriş', 'Entry'), entry, 'entry'], [tx('Stop', 'Stop'), levels.stop, 'loss']] as const
  const loss = Math.abs(entry - levels.stop), gain = Math.abs(levels.tp3 - entry), total = loss + gain || 1
  const lossW = Math.max(6, loss / total * 100), gainW = Math.max(6, gain / total * 100)
  return <div className="sqtLadder" aria-label={tx('Stop ve hedef seviyeleri', 'Stop and target levels')}>
    <div className="sqtBar" aria-hidden="true">{stopFirst ? <><i data-tone="loss" style={{flexBasis: `${lossW}%`}}/><i data-tone="entry"/><i data-tone="gain" style={{flexBasis: `${gainW}%`}}/></> : <><i data-tone="gain" style={{flexBasis: `${gainW}%`}}/><i data-tone="entry"/><i data-tone="loss" style={{flexBasis: `${lossW}%`}}/></>}</div>
    <div className="sqtLevels">{rows.map(([name, value, tone]) => <span key={`${name}-${tone}`} data-tone={tone}><small>{name}</small>{analysisPrice(value)}</span>)}</div>
  </div>
}

function PositionCard({position, price, pnl, rights, demo, busy, action, actionError, onCollapse, onAsk, onCancelAsk, onRun, onTray}: {
  position: StudioPosition; price: number | null; pnl: {pnl: number | null; pct: number | null}
  rights: {id: string; close: boolean; reduce: boolean}; demo: boolean; busy: string | null; action: 'half' | 'close' | null; actionError: string
  onCollapse: () => void; onAsk: (kind: 'half' | 'close') => void; onCancelAsk: () => void; onRun: (kind: 'half' | 'close') => void; onTray?: () => void
}) {
  const side = sideOf(position)
  const entry = positive(position.entry_price), stop = positive(position.stop_loss), tp1 = positive(position.tp1)
  const sending = busy !== null
  return <div className="sqtCard" aria-label={tx(`${position.symbol} açık pozisyon`, `${position.symbol} open position`)}>
    <div className="sqtCardTop"><i aria-hidden="true"/><b>{position.symbol}</b><span data-side={side}>{side}{positive(position.leverage ?? undefined) ? ` ${position.leverage}x` : ''}</span>
      {onTray && <button type="button" className="sqtLink" onClick={onTray}>{tx('Tümü', 'All')}</button>}
      <button type="button" className="sqtRound" aria-label={tx('Küçült', 'Collapse')} onClick={onCollapse}><ChevronDown/></button></div>
    <div className="sqtPnl"><strong>{pnl.pnl === null ? '—' : signed(pnl.pnl, Math.abs(pnl.pnl) < 1 ? 4 : 2)} <u>USDT</u></strong>{pnl.pct !== null && <em>{signed(pnl.pct)}%</em>}</div>
    <div className="sqtMeta">{entry && <span>{tx('Giriş', 'Entry')} <b>{analysisPrice(entry)}</b></span>}{stop && <span>Stop <b data-tone="down">{analysisPrice(stop)}</b></span>}{tp1 && <span>TP1 <b data-tone="up">{analysisPrice(tp1)}</b></span>}{!stop && !tp1 && <span>{tx('Anlık', 'Now')} <b>{analysisPrice(positive(price) ?? positive(position.mark_price))}</b></span>}</div>
    {action === null && <div className="sqtActs">
      {rights.reduce && <button type="button" className="sqtBtn" disabled={sending} onClick={() => onAsk('half')}>{tx('Yarısını kapat', 'Close half')}</button>}
      {rights.close && <HoldButton ms={demo ? HOLD_MS.demo : HOLD_MS.live} tone="danger" disabled={sending} label={tx('Kapat · basılı tut', 'Close · hold')} onDone={() => onRun('close')} hint={tx(`${position.symbol} pozisyonunu kapatmak için basılı tut`, `Press and hold to close the ${position.symbol} position`)}/>}
      {!rights.close && <p className="sqtNoteSmall">{tx('Bu pozisyon bot ya da dış kaynaklı; kapatma Pozisyonlar sekmesinden.', 'This position is bot-managed or external; close it from the Positions tab.')}</p>}
    </div>}
    {action === 'half' && <div className="sqtAsk" role="alertdialog" aria-label={tx('Yarısını kapat onayı', 'Close half confirmation')}><span>{tx('Pozisyonun yarısı piyasa fiyatından kapatılsın mı?', 'Close half of the position at market price?')}</span><div><button type="button" className="sqtBtn" data-tone="primary" disabled={sending} onClick={() => onRun('half')}>{sending ? busy : tx('Evet, kapat', 'Yes, close')}</button><button type="button" className="sqtBtn" disabled={sending} onClick={onCancelAsk}>{tx('Vazgeç', 'Cancel')}</button></div></div>}
    {actionError && <p className="sqtError" role="alert"><TriangleAlert aria-hidden="true"/>{actionError}</p>}
    {sending && action === null && <p className="sqtNoteSmall" role="status">{busy}</p>}
  </div>
}

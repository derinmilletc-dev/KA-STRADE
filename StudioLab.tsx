import {useEffect, useMemo, useRef, useState, type ReactNode} from 'react'
import {API_BASE} from './api'
import {fetchPublicMarketData} from './master-trade-request'
import {useKlines} from './StudioSplitReplay'
import {currentLang} from './i18n'
import {barsPerYear, DEFAULT_STRATEGY, runBacktest, splitCheck, type LevelMode, type Side, type SizingMode, type Strategy} from './studio-backtest'
import {readSaved, removeSaved, upsertSaved, writeSaved, type SavedStrategy} from './studio-strategies'
import {parseKlines} from './studio-klines'
import {MAX_RULES, OPERATORS, PRESETS, ruleText, ruleTextEn, sanitizeOperand, type Operand, type Operator, type Rule} from './studio-rules'
import {scanMarkets, type Fetcher, type ScanRow} from './studio-screener'

// Strateji laboratuvarı: kural kur → geçmişte dene (backtest) → kuralı tüm piyasada tara. Hiçbir emir göndermez; yalnız okunur veri.
type Kind = Operand['kind']
const KIND_LABELS: Array<[Kind | 'close' | 'open' | 'high' | 'low' | 'volume', string]> = [['close', 'Kapanış'], ['open', 'Açılış'], ['high', 'Yüksek'], ['low', 'Düşük'], ['volume', 'Hacim'], ['sma', 'SMA'], ['ema', 'EMA'], ['rsi', 'RSI'], ['macd', 'MACD'], ['macdsig', 'MACD sinyal'], ['bbup', 'Bollinger üst'], ['bblow', 'Bollinger alt'], ['atr', 'ATR'], ['volsma', 'Hacim SMA'], ['hh', 'En yüksek (önceki N)'], ['ll', 'En düşük (önceki N)'], ['num', 'Sayı']]
const DEFAULT_PERIOD: Record<string, number> = {sma: 20, ema: 20, rsi: 14, atr: 14, volsma: 20, hh: 20, ll: 20}
const selectValue = (operand: Operand) => operand.kind === 'price' ? operand.field : operand.kind

function OperandEditor({value, label, onChange}: {value: Operand; label: string; onChange: (next: Operand) => void}) {
  const choose = (key: string) => {
    const next = key in DEFAULT_PERIOD ? sanitizeOperand({kind: key, period: DEFAULT_PERIOD[key]}) : key === 'macd' || key === 'macdsig' || key === 'bbup' || key === 'bblow' ? sanitizeOperand({kind: key}) : key === 'num' ? sanitizeOperand({kind: 'num', value: 50}) : sanitizeOperand({kind: 'price', field: key})
    if (next) onChange(next)
  }
  const number = value.kind === 'num' ? value.value : 'period' in value ? value.period : null
  return <span className="labOperand">
    <select aria-label={label} value={selectValue(value)} onChange={event => choose(event.target.value)}>{KIND_LABELS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select>
    {number !== null && <input aria-label={`${label} değeri`} type="number" inputMode="decimal" value={number} step={value.kind === 'num' ? 'any' : 1}
      onChange={event => { const n = Number(event.target.value); const next = sanitizeOperand(value.kind === 'num' ? {kind: 'num', value: n} : {kind: value.kind, period: n}); if (next) onChange(next) }}/>}
  </span>
}

function RuleList({title, rules, onChange, note}: {title: string; rules: Rule[]; onChange: (rules: Rule[]) => void; note?: string}) {
  const set = (index: number, patch: Partial<Rule>) => onChange(rules.map((rule, i) => i === index ? {...rule, ...patch} : rule))
  return <fieldset className="labRules"><legend>{title}</legend>
    {rules.map((rule, index) => <div className="labRule" key={index}>
      <div className="labRuleMain">
        <OperandEditor value={rule.left} label="Sol taraf" onChange={left => set(index, {left})}/>
        <select aria-label="Koşul" value={rule.op} onChange={event => set(index, {op: event.target.value as Operator})}>{OPERATORS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
        <OperandEditor value={rule.right} label="Sağ taraf" onChange={right => set(index, {right})}/>
      </div>
      <button type="button" className="labRemove" aria-label="Kuralı sil" onClick={() => onChange(rules.filter((_, i) => i !== index))}>×</button>
    </div>)}
    {rules.length < MAX_RULES && <button type="button" className="labAdd" onClick={() => onChange([...rules, {left: {kind: 'price', field: 'close'}, op: 'crossUp', right: {kind: 'ema', period: 20}}])}>+ Kural ekle</button>}
    {note ? <small>{note}</small> : rules.length > 1 && <small>Tüm kurallar aynı anda sağlanmalı (VE).</small>}
  </fieldset>
}

const pct = (value: number, digits = 2) => `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
const money = (value: number) => value.toLocaleString('tr-TR', {maximumFractionDigits: value >= 100 ? 0 : 2})
const maybe = (value: number | null, digits = 2) => value === null ? '—' : value.toFixed(digits)
const REASONS: Record<string, string> = {stop: 'stop', take: 'hedef', trail: 'iz süren stop', kural: 'kural', süre: 'süre', son: 'son mum'}

function Equity({values}: {values: number[]}) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1
  const points = values.map((v, i) => `${(i / (values.length - 1) * 300).toFixed(1)},${(46 - (v - min) / span * 44).toFixed(1)}`).join(' ')
  return <svg className="labEquity" viewBox="0 0 300 48" preserveAspectRatio="none" role="img" aria-label="Sermaye eğrisi"><polyline points={points} fill="none" vectorEffect="non-scaling-stroke"/></svg>
}

type Flow = 'analysis' | 'test' | 'scan' | 'trade'

function Field({label, children}: {label: string; children: ReactNode}) { return <label className="labField">{label}{children}</label> }

export default function StudioLab({symbol, interval, symbols, onPick, onClose, onTrade}: {symbol: string; interval: string; symbols: readonly string[]; onPick: (symbol: string) => void; onClose: () => void; onTrade?: () => void}) {
  const [tab, setTab] = useState<'backtest' | 'scan'>('backtest')
  const [strategy, setStrategy] = useState<Strategy>({...DEFAULT_STRATEGY, entry: PRESETS[0].entry, exit: PRESETS[0].exit})
  const patch = (change: Partial<Strategy>) => setStrategy(current => ({...current, ...change}))
  const [saved, setSaved] = useState<SavedStrategy[]>(() => readSaved())
  const [name, setName] = useState('')
  const data = useKlines(symbol, interval, tab === 'backtest', 1000, 0)
  const perYear = barsPerYear(interval)
  const result = useMemo(() => data.candles.length ? runBacktest(data.candles, strategy, {barsPerYear: perYear}) : null, [data.candles, strategy, perYear])
  const split = useMemo(() => data.candles.length ? splitCheck(data.candles, strategy, {barsPerYear: perYear}) : null, [data.candles, strategy, perYear])
  const [rows, setRows] = useState<ScanRow[]>([])
  const [progress, setProgress] = useState<{done: number; total: number} | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  // Tarayıcı, girişi ve onayı aynı mumda arar (onay süresi taramada yoktur).
  const scanRules = useMemo(() => [...strategy.entry, ...strategy.confirm], [strategy.entry, strategy.confirm])
  const fetcher: Fetcher = async (market, signal) => {
    const response = await fetchPublicMarketData(`${API_BASE}/klines/${market}?interval=${encodeURIComponent(interval)}&limit=250`, {signal}, 20000)
    if (!response.ok) throw new Error('veri yok')
    const candles = parseKlines(await response.json())
    if (!candles) throw new Error('geçersiz')
    return candles
  }
  const scan = () => {
    abort.current?.abort()
    const controller = new AbortController()
    abort.current = controller
    setRows([]); setProgress({done: 0, total: Math.min(symbols.length, 40)})
    void scanMarkets(symbols, scanRules, fetcher, {signal: controller.signal, onRow: (row, done, total) => { setRows(current => [...current, row]); setProgress({done, total}) }}).then(() => { if (!controller.signal.aborted) setProgress(null) })
  }
  const go = (step: Flow) => {
    if (step === 'analysis') onClose()
    else if (step === 'test') setTab('backtest')
    else if (step === 'scan') setTab('scan')
    else if (onTrade) onTrade()
  }
  const save = () => {
    if (!strategy.entry.length) return
    const next = upsertSaved(saved, name, strategy, () => `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`)
    setSaved(next); writeSaved(next); setName('')
  }
  const matches = rows.filter(row => row.match)
  const levelInput = (mode: LevelMode, pctValue: number, atrValue: number, onMode: (mode: LevelMode) => void, onValue: (value: number) => void, label: string) => <Field label={label}>
    <span className="labPair">
      <select aria-label={`${label} türü`} value={mode} onChange={event => onMode(event.target.value === 'atr' ? 'atr' : 'pct')}><option value="pct">%</option><option value="atr">ATR ×</option></select>
      <input aria-label={label} type="number" inputMode="decimal" min={0} step={mode === 'atr' ? 0.5 : 0.5} value={mode === 'atr' ? atrValue : pctValue} onChange={event => onValue(Number(event.target.value))}/>
    </span>
  </Field>
  return <div className="labBackdrop" role="presentation" onPointerDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="labSheet" role="dialog" aria-modal="true" aria-label="Strateji laboratuvarı">
      <header><strong>Strateji laboratuvarı</strong><span>{symbol} · {interval}</span><button type="button" aria-label="Kapat" onClick={onClose}>×</button></header>
      <nav className="labFlow" aria-label="Akış">
        {([['analysis', 'Analiz'], ['test', 'Test'], ['scan', 'Tara'], ['trade', 'İşlem']] as Array<[Flow, string]>).map(([step, label], index) =>
          <button type="button" key={step} data-active={(step === 'test' && tab === 'backtest') || (step === 'scan' && tab === 'scan') || undefined} disabled={step === 'trade' && !onTrade} onClick={() => go(step)}><i>{index + 1}</i>{label}</button>)}
      </nav>
      <div className="labTabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'backtest'} onClick={() => setTab('backtest')}>Backtest</button>
        <button type="button" role="tab" aria-selected={tab === 'scan'} onClick={() => setTab('scan')}>Tarayıcı</button>
      </div>
      <div className="labBody">
        <div className="labPresets">{PRESETS.map(preset => <button type="button" key={preset.id} onClick={() => patch({entry: preset.entry, confirm: preset.confirm ?? [], confirmBars: preset.confirm ? 3 : 0, exit: preset.exit})}>{preset.label}</button>)}</div>
        <div className="labSaved">
          <input aria-label="Strateji adı" placeholder="Stratejine ad ver" maxLength={30} value={name} onChange={event => setName(event.target.value)}/>
          <button type="button" className="labAdd" disabled={!strategy.entry.length} onClick={save}>Kaydet</button>
          {saved.map(item => <span className="labSavedItem" key={item.id}>
            <button type="button" onClick={() => setStrategy(item.strategy)} title="Yükle">{item.name}</button>
            <button type="button" aria-label={`${item.name} sil`} onClick={() => { const next = removeSaved(saved, item.id); setSaved(next); writeSaved(next) }}>×</button>
          </span>)}
        </div>
        <RuleList title="1 · Giriş kuralları" rules={strategy.entry} onChange={entry => patch({entry})}/>
        <RuleList title="2 · Onay (isteğe bağlı)" rules={strategy.confirm} onChange={confirm => patch({confirm})} note={strategy.confirm.length ? (tab === 'scan' ? 'Tarayıcıda onay, girişle aynı mumda aranır.' : strategy.confirmBars > 0 ? `Giriş sinyalinden sonra en çok ${strategy.confirmBars} mum içinde sağlanmalı.` : 'Giriş sinyaliyle aynı mumda sağlanmalı.') : 'Onay kuralı sahte sinyalleri eler: örnek, kırılımda hacim ortalamanın üstünde olsun.'}/>
        {tab === 'backtest' && strategy.confirm.length > 0 && <div className="labParams"><Field label="Onay süresi (mum)"><input type="number" inputMode="numeric" min={0} max={20} step={1} value={strategy.confirmBars} onChange={event => patch({confirmBars: Number(event.target.value)})}/></Field></div>}
        {tab === 'backtest' ? <>
          <RuleList title="3 · Çıkış kuralları (biri yeter)" rules={strategy.exit} onChange={exit => patch({exit})}/>
          <fieldset className="labRules"><legend>4 · Stop / hedef</legend>
            <div className="labParams">
              <Field label="Yön"><select value={strategy.side} onChange={event => patch({side: (event.target.value === 'short' ? 'short' : 'long') as Side})}><option value="long">Long</option><option value="short">Short</option></select></Field>
              {levelInput(strategy.stopMode, strategy.stopPct, strategy.stopAtr, stopMode => patch({stopMode}), value => patch(strategy.stopMode === 'atr' ? {stopAtr: value} : {stopPct: value}), 'Stop')}
              {levelInput(strategy.takeMode, strategy.takePct, strategy.takeAtr, takeMode => patch({takeMode}), value => patch(strategy.takeMode === 'atr' ? {takeAtr: value} : {takePct: value}), 'Hedef')}
              <Field label="İz süren stop %"><input type="number" inputMode="decimal" min={0} max={50} step="0.5" value={strategy.trailPct} onChange={event => patch({trailPct: Number(event.target.value)})}/></Field>
              <Field label="En fazla mum"><input type="number" inputMode="numeric" min={0} step={1} value={strategy.maxBars} onChange={event => patch({maxBars: Number(event.target.value)})}/></Field>
            </div>
            <small>0 yazarsan o seviye kapalı olur. ATR, sinyal mumundaki 14 periyotluk ortalama gerçek aralıktır ve girişte sabitlenir.</small>
          </fieldset>
          <fieldset className="labRules"><legend>5 · Pozisyon boyutu</legend>
            <div className="labParams">
              <Field label="Yöntem"><select value={strategy.sizing} onChange={event => { const sizing = event.target.value as SizingMode; patch({sizing, sizingValue: sizing === 'risk' ? 1 : sizing === 'fixed' ? 25 : 100}) }}><option value="all">Tüm sermaye</option><option value="fixed">Sabit %</option><option value="risk">İşlem başı risk %</option></select></Field>
              {strategy.sizing !== 'all' && <Field label={strategy.sizing === 'risk' ? 'Risk % (sermaye)' : 'Sermaye %'}><input type="number" inputMode="decimal" min={0.01} max={100} step="0.25" value={strategy.sizingValue} onChange={event => patch({sizingValue: Number(event.target.value)})}/></Field>}
              {strategy.sizing === 'risk' && <Field label="Maks. kaldıraç ×"><input type="number" inputMode="decimal" min={1} max={10} step="0.5" value={strategy.maxLeverage} onChange={event => patch({maxLeverage: Number(event.target.value)})}/></Field>}
              <Field label="Sermaye (USDT)"><input type="number" inputMode="decimal" min={1} step="100" value={strategy.capital} onChange={event => patch({capital: Number(event.target.value)})}/></Field>
              <Field label="Komisyon % (tek yön)"><input type="number" inputMode="decimal" min={0} max={2} step="0.01" value={strategy.feePct} onChange={event => patch({feePct: Number(event.target.value)})}/></Field>
            </div>
            {strategy.sizing === 'risk' && <small>Stop vurulursa sermayenin yalnız bu kadarı kaybedilir; stop yakınsa pozisyon büyür, kaldıraç sınırına kadar.</small>}
          </fieldset>
          {data.error && <p className="labNote" role="alert">{data.error}</p>}
          {!data.candles.length && !data.error && <p className="labNote" role="status">Geçmiş mumlar yükleniyor…</p>}
          {result && <div className="labResult">
            {result.warning && <p className="labNote" role="status">{result.warning}</p>}
            <dl className="labStats">
              <div data-tone={result.netPct >= 0 ? 'up' : 'down'}><dt>Net getiri</dt><dd>{pct(result.netPct)}</dd></div>
              <div data-tone={result.finalCapital >= strategy.capital ? 'up' : 'down'}><dt>Son sermaye</dt><dd>{money(result.finalCapital)}</dd></div>
              <div><dt>Al-tut</dt><dd>{pct(result.buyHoldPct)}</dd></div>
              <div><dt>İşlem</dt><dd>{result.trades.length}</dd></div>
              <div><dt>Kazanma</dt><dd>{result.winRate.toFixed(0)}%</dd></div>
              <div><dt>Kâr faktörü</dt><dd>{maybe(result.profitFactor)}</dd></div>
              <div><dt>Maks. düşüş</dt><dd>-{result.maxDrawdownPct.toFixed(1)}%</dd></div>
              <div><dt>Beklenti (R)</dt><dd>{maybe(result.expectancyR)}</dd></div>
              <div><dt>Kazanç/kayıp</dt><dd>{maybe(result.payoff)}</dd></div>
              <div><dt>Üst üste kayıp</dt><dd>{result.maxConsecLosses}</dd></div>
              <div><dt>Piyasada kalma</dt><dd>{result.exposurePct.toFixed(0)}%</dd></div>
              <div><dt>Sharpe</dt><dd>{maybe(result.sharpe)}</dd></div>
            </dl>
            <Equity values={result.equity}/>
            {split && <p className="labSplit" data-tone={split.consistent ? 'ok' : 'warn'} role="status">
              <b>Tutarlılık testi:</b> ilk %70 {pct(split.early.netPct, 1)} ({split.early.trades.length} işlem), son %30 {pct(split.late.netPct, 1)} ({split.late.trades.length} işlem). {split.consistent ? 'İki dönemde de aynı yönde.' : 'Dönemler çelişiyor: kurallar geçmişe uydurulmuş olabilir.'}
            </p>}
            <ol className="labTrades">{result.trades.slice(-12).reverse().map((trade, index) => <li key={index} data-tone={trade.equityPct >= 0 ? 'up' : 'down'}><span>{trade.side === 'long' ? 'Long' : 'Short'} · {REASONS[trade.reason] ?? trade.reason} · {trade.bars} mum{trade.r !== null ? ` · ${trade.r.toFixed(1)}R` : ''}</span><b>{pct(trade.equityPct)}</b></li>)}</ol>
            <small className="labFine">Giriş, sinyalden sonraki mumun açılışında; aynı mumda stop ve hedef birlikte vurulursa stop sayılır. Kayma ve fonlama dahil değildir. Geçmiş sonuç gelecek garantisi değildir.</small>
            <div className="labNext">
              <button type="button" className="labRun" onClick={() => setTab('scan')}>Bu kuralları piyasada tara →</button>
              {onTrade && <button type="button" onClick={onTrade}>İşlem masasına geç</button>}
            </div>
          </div>}
        </> : <>
          <button type="button" className="labRun" disabled={!scanRules.length || progress !== null} onClick={scan}>{progress ? `Taranıyor ${progress.done}/${progress.total}` : `En hacimli ${Math.min(symbols.length, 40)} piyasayı tara`}</button>
          {currentLang() === 'en'
            ? <p className="labFine" translate="no">The rule is checked on the latest CLOSED {interval} candle. {scanRules.map(ruleTextEn).join(' and ')}</p>
            : <p className="labFine">Kural, son KAPANMIŞ {interval} mumunda aranır. {scanRules.map(ruleText).join(' ve ')}</p>}
          {!progress && rows.length > 0 && matches.length === 0 && <p className="labNote" role="status">Şu an kuralı sağlayan piyasa yok.</p>}
          <ul className="labMatches">{matches.map(row => row.match ? <li key={row.symbol}>
            <button type="button" onClick={() => { onPick(row.symbol); onClose() }}><b>{row.symbol}</b><span>{row.price}</span><em data-tone={row.changePct >= 0 ? 'up' : 'down'}>{pct(row.changePct, 1)}</em></button>
            <button type="button" className="labTestOne" onClick={() => { onPick(row.symbol); setTab('backtest') }}>Test et</button>
          </li> : null)}</ul>
          {rows.some(row => row.error) && <small className="labFine">{rows.filter(row => row.error).length} piyasanın verisi alınamadı.</small>}
        </>}
      </div>
    </section>
  </div>
}

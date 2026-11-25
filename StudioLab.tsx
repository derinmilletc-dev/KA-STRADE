import {useEffect, useMemo, useRef, useState} from 'react'
import {API_BASE} from './api'
import {fetchPublicMarketData} from './master-trade-request'
import {useKlines} from './StudioSplitReplay'
import {runBacktest, type Side} from './studio-backtest'
import {parseKlines} from './studio-klines'
import {MAX_RULES, OPERATORS, PRESETS, ruleText, sanitizeOperand, type Operand, type Operator, type Rule} from './studio-rules'
import {scanMarkets, type Fetcher, type ScanRow} from './studio-screener'

// Strateji laboratuvarı: kural kur → geçmişte dene (backtest) → kuralı tüm piyasada tara. Hiçbir emir göndermez; yalnız okunur veri.
type Kind = Operand['kind']
const KIND_LABELS: Array<[Kind | 'close' | 'open' | 'high' | 'low' | 'volume', string]> = [['close', 'Kapanış'], ['open', 'Açılış'], ['high', 'Yüksek'], ['low', 'Düşük'], ['volume', 'Hacim'], ['sma', 'SMA'], ['ema', 'EMA'], ['rsi', 'RSI'], ['num', 'Sayı']]
const selectValue = (operand: Operand) => operand.kind === 'price' ? operand.field : operand.kind

function OperandEditor({value, label, onChange}: {value: Operand; label: string; onChange: (next: Operand) => void}) {
  const choose = (key: string) => {
    const next = key === 'sma' || key === 'ema' || key === 'rsi' ? sanitizeOperand({kind: key, period: key === 'rsi' ? 14 : 20}) : key === 'num' ? sanitizeOperand({kind: 'num', value: 50}) : sanitizeOperand({kind: 'price', field: key})
    if (next) onChange(next)
  }
  const number = value.kind === 'num' ? value.value : value.kind !== 'price' ? value.period : null
  return <span className="labOperand">
    <select aria-label={label} value={selectValue(value)} onChange={event => choose(event.target.value)}>{KIND_LABELS.map(([id, text]) => <option key={id} value={id}>{text}</option>)}</select>
    {number !== null && <input aria-label={`${label} değeri`} type="number" inputMode="decimal" value={number} step={value.kind === 'num' ? 'any' : 1}
      onChange={event => { const n = Number(event.target.value); const next = sanitizeOperand(value.kind === 'num' ? {kind: 'num', value: n} : {kind: value.kind, period: n}); if (next) onChange(next) }}/>}
  </span>
}

function RuleList({title, rules, onChange}: {title: string; rules: Rule[]; onChange: (rules: Rule[]) => void}) {
  const set = (index: number, patch: Partial<Rule>) => onChange(rules.map((rule, i) => i === index ? {...rule, ...patch} : rule))
  return <fieldset className="labRules"><legend>{title}</legend>
    {rules.map((rule, index) => <div className="labRule" key={index}>
      <OperandEditor value={rule.left} label="Sol taraf" onChange={left => set(index, {left})}/>
      <select aria-label="Koşul" value={rule.op} onChange={event => set(index, {op: event.target.value as Operator})}>{OPERATORS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <OperandEditor value={rule.right} label="Sağ taraf" onChange={right => set(index, {right})}/>
      <button type="button" className="labRemove" aria-label="Kuralı sil" onClick={() => onChange(rules.filter((_, i) => i !== index))}>×</button>
    </div>)}
    {rules.length < MAX_RULES && <button type="button" className="labAdd" onClick={() => onChange([...rules, {left: {kind: 'price', field: 'close'}, op: 'crossUp', right: {kind: 'ema', period: 20}}])}>+ Kural ekle</button>}
    {rules.length > 1 && <small>Tüm kurallar aynı anda sağlanmalı (VE).</small>}
  </fieldset>
}

const pct = (value: number, digits = 2) => `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`

function Equity({values}: {values: number[]}) {
  if (values.length < 2) return null
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1
  const points = values.map((v, i) => `${(i / (values.length - 1) * 300).toFixed(1)},${(46 - (v - min) / span * 44).toFixed(1)}`).join(' ')
  return <svg className="labEquity" viewBox="0 0 300 48" preserveAspectRatio="none" role="img" aria-label="Sermaye eğrisi"><polyline points={points} fill="none" vectorEffect="non-scaling-stroke"/></svg>
}

export default function StudioLab({symbol, interval, symbols, onPick, onClose}: {symbol: string; interval: string; symbols: readonly string[]; onPick: (symbol: string) => void; onClose: () => void}) {
  const [tab, setTab] = useState<'backtest' | 'scan'>('backtest')
  const [entry, setEntry] = useState<Rule[]>(PRESETS[0].entry)
  const [exit, setExit] = useState<Rule[]>(PRESETS[0].exit)
  const [side, setSide] = useState<Side>('long')
  const [stopPct, setStopPct] = useState(3)
  const [takePct, setTakePct] = useState(6)
  const data = useKlines(symbol, interval, tab === 'backtest', 1000, 0)
  const result = useMemo(() => data.candles.length ? runBacktest(data.candles, {side, entry, exit, stopPct, takePct, feePct: 0.05}) : null, [data.candles, side, entry, exit, stopPct, takePct])
  const [rows, setRows] = useState<ScanRow[]>([])
  const [progress, setProgress] = useState<{done: number; total: number} | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
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
    void scanMarkets(symbols, entry, fetcher, {signal: controller.signal, onRow: (row, done, total) => { setRows(current => [...current, row]); setProgress({done, total}) }}).then(() => { if (!controller.signal.aborted) setProgress(null) })
  }
  const matches = rows.filter(row => row.match)
  return <div className="labBackdrop" role="presentation" onPointerDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="labSheet" role="dialog" aria-modal="true" aria-label="Strateji laboratuvarı">
      <header><strong>Strateji laboratuvarı</strong><span>{symbol} · {interval}</span><button type="button" aria-label="Kapat" onClick={onClose}>×</button></header>
      <div className="labTabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'backtest'} onClick={() => setTab('backtest')}>Backtest</button>
        <button type="button" role="tab" aria-selected={tab === 'scan'} onClick={() => setTab('scan')}>Tarayıcı</button>
      </div>
      <div className="labBody">
        <div className="labPresets">{PRESETS.map(preset => <button type="button" key={preset.id} onClick={() => { setEntry(preset.entry); setExit(preset.exit) }}>{preset.label}</button>)}</div>
        <RuleList title={tab === 'backtest' ? 'Giriş kuralları' : 'Tarama kuralları'} rules={entry} onChange={setEntry}/>
        {tab === 'backtest' ? <>
          <RuleList title="Çıkış kuralları (biri yeter)" rules={exit} onChange={setExit}/>
          <div className="labParams">
            <label>Yön<select value={side} onChange={event => setSide(event.target.value === 'short' ? 'short' : 'long')}><option value="long">Long</option><option value="short">Short</option></select></label>
            <label>Stop %<input type="number" inputMode="decimal" min={0} max={50} step="0.5" value={stopPct} onChange={event => setStopPct(Number(event.target.value))}/></label>
            <label>Hedef %<input type="number" inputMode="decimal" min={0} max={500} step="0.5" value={takePct} onChange={event => setTakePct(Number(event.target.value))}/></label>
          </div>
          {data.error && <p className="labNote" role="alert">{data.error}</p>}
          {!data.candles.length && !data.error && <p className="labNote" role="status">Geçmiş mumlar yükleniyor…</p>}
          {result && <div className="labResult">
            {result.warning && <p className="labNote" role="status">{result.warning}</p>}
            <dl className="labStats">
              <div data-tone={result.netPct >= 0 ? 'up' : 'down'}><dt>Net getiri</dt><dd>{pct(result.netPct)}</dd></div>
              <div><dt>Al-tut</dt><dd>{pct(result.buyHoldPct)}</dd></div>
              <div><dt>İşlem</dt><dd>{result.trades.length}</dd></div>
              <div><dt>Kazanma</dt><dd>{result.winRate.toFixed(0)}%</dd></div>
              <div><dt>Kâr faktörü</dt><dd>{result.profitFactor === null ? '—' : result.profitFactor.toFixed(2)}</dd></div>
              <div><dt>Maks. düşüş</dt><dd>-{result.maxDrawdownPct.toFixed(1)}%</dd></div>
            </dl>
            <Equity values={result.equity}/>
            <ol className="labTrades">{result.trades.slice(-12).reverse().map((trade, index) => <li key={index} data-tone={trade.returnPct >= 0 ? 'up' : 'down'}><span>{trade.side === 'long' ? 'Long' : 'Short'} · {trade.reason}</span><b>{pct(trade.returnPct)}</b></li>)}</ol>
            <small className="labFine">Komisyon %0,05 (giriş+çıkış), giriş sinyalden sonraki mumun açılışında. Geçmiş sonuç gelecek garantisi değildir; kayma ve fonlama dahil değildir.</small>
          </div>}
        </> : <>
          <button type="button" className="labRun" disabled={!entry.length || progress !== null} onClick={scan}>{progress ? `Taranıyor ${progress.done}/${progress.total}` : `En hacimli ${Math.min(symbols.length, 40)} piyasayı tara`}</button>
          <p className="labFine">Kural, son KAPANMIŞ {interval} mumunda aranır. {entry.map(ruleText).join(' ve ')}</p>
          {!progress && rows.length > 0 && matches.length === 0 && <p className="labNote" role="status">Şu an kuralı sağlayan piyasa yok.</p>}
          <ul className="labMatches">{matches.map(row => row.match ? <li key={row.symbol}><button type="button" onClick={() => { onPick(row.symbol); onClose() }}><b>{row.symbol}</b><span>{row.price}</span><em data-tone={row.changePct >= 0 ? 'up' : 'down'}>{pct(row.changePct, 1)}</em></button></li> : null)}</ul>
          {rows.some(row => row.error) && <small className="labFine">{rows.filter(row => row.error).length} piyasanın verisi alınamadı.</small>}
        </>}
      </div>
    </section>
  </div>
}

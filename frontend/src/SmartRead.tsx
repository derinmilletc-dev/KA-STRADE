import {useEffect, useState} from 'react'
import './smart-read.css'
import {useCoinRead, useReadStats} from './coin-read-api'
import type {AlertCall} from './price-alert-api'
import {
  accuracyLine, alertCandidates, arrowOf, bookPos, confidenceTone, EVENT_LABEL, fitTitle, fitTone, gaugePos, levelBar, priceText, rrText, signedPct, TF_ORDER, topEvent, usdText,
  type CoinRead, type ReadPlan, type ReadTf,
} from './coin-read-view'

// Akıllı yorum kartı: tüm zaman dilimlerini tek bakışta okur (uyum/çelişki), seçili sürenin ayrıntısını, destek-direnç,
// futures verisini ve bu kuralın coindeki geçmiş doğruluğunu gösterir. Pozisyon verilirse yorumu pozisyona bağlar.
// Yalnız okuma: kapanmış mumlardan otomatik tarif; tahmin ya da yatırım tavsiyesi değildir.
export type AlertTarget = {productId: string; call: AlertCall; onSet?: () => void}
type Props = {symbol: string; focus?: string; side?: 'LONG' | 'SHORT' | null; entry?: number | null; onApplyPlan?: (plan: ReadPlan) => string | null | void; alert?: AlertTarget}

const isTf = (value: string | undefined): value is typeof TF_ORDER[number] => !!value && (TF_ORDER as readonly string[]).includes(value)
const toneOf = (value: string): 'up' | 'down' | 'warn' | 'flat' => value === 'up' || value === 'good' ? 'up' : value === 'down' || value === 'danger' ? 'down' : value === 'warn' ? 'warn' : 'flat'

function Skeleton() {
  return <div className="srx" data-state="loading" role="status" aria-label="Akıllı yorum hazırlanıyor"><i className="srxSk" style={{width: '38%'}}/><i className="srxSk" style={{height: 54}}/><i className="srxSk" style={{height: 38}}/></div>
}

function Levels({data}: {data: CoinRead}) {
  const bar = levelBar(data.price, data.levels)
  if (!bar) return null
  const res = data.levels.resistance[0], sup = data.levels.support[0]
  return <div className="srxBlock" aria-label="Destek ve direnç">
    <h4>Destek / direnç</h4>
    <div className="srxLv" role="img" aria-label={`Fiyat ${priceText(data.price)}${sup ? `, destek ${priceText(sup.price)}` : ''}${res ? `, direnç ${priceText(res.price)}` : ''}`}>
      <i className="srxLvTrack"/>
      {bar.marks.map(mark => <i key={mark.key} className="srxLvMark" data-kind={mark.kind} style={{left: `${mark.pos}%`}}/>)}
      <i className="srxLvNow" style={{left: `${bar.pos}%`}}/>
    </div>
    <div className="srxLvRows">
      {res && <span data-kind="resistance"><small>Direnç</small><b>{priceText(res.price)}</b><em>{signedPct(res.dist_pct)}</em></span>}
      {sup && <span data-kind="support"><small>Destek</small><b>{priceText(sup.price)}</b><em>{signedPct(sup.dist_pct)}</em></span>}
    </div>
  </div>
}

function QuickAlerts({data, alert}: {data: CoinRead; alert: AlertTarget}) {
  const [note, setNote] = useState<{tone: 'ok' | 'warn'; text: string} | null>(null)
  const [busy, setBusy] = useState('')
  const items = alertCandidates(data)
  if (!items.length) return null
  const set = async (item: ReturnType<typeof alertCandidates>[number]) => {
    if (busy) return
    setBusy(item.key); setNote(null)
    try {
      await alert.call('/alerts', {method: 'POST', body: JSON.stringify({product_id: alert.productId, level: String(item.price)})})
      setNote({tone: 'ok', text: `${item.label} (${priceText(item.price)}) için alarm kuruldu. Fiyat oraya gelince bildirim gelir.`}); alert.onSet?.()
    } catch (error) { setNote({tone: 'warn', text: error instanceof Error ? error.message : 'Alarm kurulamadı.'}) }
    setBusy('')
  }
  return <div className="srxBlock" aria-label="Seviyeye alarm">
    <h4>Bu seviyeye gelince haber ver</h4>
    <div className="srxQa">{items.map(item => <button type="button" key={item.key} data-tone={item.tone} disabled={!!busy} onClick={() => void set(item)}><small>🔔 {item.label}</small><b>{priceText(item.price)}</b></button>)}</div>
    {note && <p className="srxNoteLine" data-tone={note.tone === 'ok' ? 'up' : 'warn'} role="status">{note.text}</p>}
  </div>
}

function Extras({data, onApplyPlan}: {data: CoinRead; onApplyPlan?: Props['onApplyPlan']}) {
  const {context, plan, book, liquidations} = data
  const [applied, setApplied] = useState<string | null>(null)
  useEffect(() => { setApplied(null) }, [data.symbol, plan?.side])
  return <>
    {context && (context.btc || context.regime_text) && <div className="srxBlock" aria-label="Piyasa bağlamı">
      <h4>Piyasa bağlamı</h4>
      {context.btc && <p className="srxCtx" data-tone={context.btc.lean === 'LONG' ? 'up' : context.btc.lean === 'SHORT' ? 'down' : 'flat'}><b>{context.btc.text}</b>{context.corr !== null && <em>BTC bağı {context.corr.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</em>}</p>}
      {context.notes.map(note => <p className="srxNoteLine" key={note} data-tone={note.startsWith('Uyarı') ? 'warn' : 'flat'}>{note}</p>)}
      {context.regime_text && <p className="srxNoteLine" data-tone="flat">{context.regime_text}</p>}
    </div>}
    {plan && <div className="srxBlock srxPlan" data-tone={plan.tone} aria-label="Stop ve hedef önerisi">
      <h4>{plan.side === 'LONG' ? 'LONG' : 'SHORT'} düşünülürse: stop ve hedef</h4>
      <div className="srxPlanRows">
        <span data-kind="stop"><small>Stop{plan.stop_basis === 'seviye' ? ' (seviyenin ötesi)' : ' (ATR)'}</small><b>{priceText(plan.stop)}</b><em>{signedPct(plan.stop_pct)}</em></span>
        {plan.targets.map((target, index) => <span key={index} data-kind="target"><small>Hedef {index + 1}{target.basis === '2R' ? ' (2 katı risk)' : ''}</small><b>{priceText(target.price)}</b><em>{rrText(target.rr)}</em></span>)}
      </div>
      <p className="srxPlanVerdict" data-tone={plan.tone}>{plan.verdict}</p>
      <small className="srxPlanNote">Öneri değil, hesap: giriş şu anki fiyat varsayıldı. Emir göndermez.</small>
      {onApplyPlan && <button type="button" className="srxApply" onClick={() => setApplied(onApplyPlan(plan) ?? 'Stop ve hedef forma yazıldı. Göndermeden önce kontrol et.')}>Stop ve hedefi forma yaz</button>}
      {applied && <p className="srxNoteLine" data-tone="flat" role="status">{applied}</p>}
    </div>}
    {book && <div className="srxBlock" aria-label="Emir defteri">
      <h4>Emir defteri (±%1)</h4>
      <div className="srxBook" role="img" aria-label={book.notes[0]}><i className="srxBookBid" style={{width: `${bookPos(book.imbalance)}%`}}/><i className="srxBookAsk" style={{width: `${100 - bookPos(book.imbalance)}%`}}/></div>
      <div className="srxGaugeEnds"><span>ALIŞ {usdText(book.bid_value)}</span><span>SATIŞ {usdText(book.ask_value)}</span></div>
      {book.notes.map(note => <p className="srxNoteLine" key={note} data-tone="flat">{note}</p>)}
      <small className="srxPlanNote">{book.caveat}</small>
    </div>}
    {liquidations && <div className="srxBlock" aria-label="Likidasyon">
      <h4>Likidasyonlar (son 15 dk)</h4>
      <p className="srxNoteLine" data-tone="warn">{liquidations.text}</p>
      <div className="srxFut"><span><small>Long tasfiye</small><b data-tone="down">{usdText(liquidations.long_usd)}</b></span><span><small>Short tasfiye</small><b data-tone="up">{usdText(liquidations.short_usd)}</b></span>
        {liquidations.zones[0] && <span><small>Yoğun bölge</small><b>{priceText(liquidations.zones[0].price)}</b></span>}</div>
    </div>}
  </>
}

function Detail({read, history}: {read: ReadTf; history: string | null}) {
  const chips: {key: string; text: string; tone: string}[] = [{key: 'net', text: signedPct(read.net_pct), tone: read.net_pct >= 0 ? 'up' : 'down'}]
  chips.push({key: 'rsi', text: `RSI ${Math.round(read.rsi)}${read.rsi_state === 'overbought' ? ' · aşırı alım' : read.rsi_state === 'oversold' ? ' · aşırı satım' : ''}`, tone: read.rsi_state === 'normal' ? 'flat' : 'warn'})
  if (read.ema !== 'mixed') chips.push({key: 'ema', text: read.ema === 'up' ? 'EMA yukarı dizilim' : 'EMA aşağı dizilim', tone: read.ema})
  if (read.divergence) chips.push({key: 'div', text: read.divergence === 'bearish' ? 'RSI uyumsuzluğu ↓' : 'RSI uyumsuzluğu ↑', tone: 'warn'})
  if (read.squeeze) chips.push({key: 'sq', text: 'Sıkışma', tone: 'flat'})
  if (read.volume_spike) chips.push({key: 'vol', text: 'Hacim yüksek', tone: 'flat'})
  return <div className="srxDetail" data-tone={read.tone}>
    <b>{read.head}</b>
    <p>{read.text}</p>
    <div className="srxChips">{chips.map(chip => <span key={chip.key} data-tone={chip.tone}>{chip.text}</span>)}</div>
    {read.patterns.map(pattern => <p className="srxPattern" key={pattern.name} data-tone={pattern.bias}><b>{pattern.name}</b> {pattern.text}</p>)}
    {history && <p className="srxHist"><span aria-hidden="true">🕘</span> {history}</p>}
  </div>
}

export default function SmartRead({symbol, focus, side = null, entry = null, onApplyPlan, alert}: Props) {
  const [tf, setTf] = useState<string>(isTf(focus) ? focus : '15m')
  useEffect(() => { if (isTf(focus)) setTf(focus) }, [focus])
  const {data, error, loading, reload} = useCoinRead(symbol, {interval: tf, side, entry})
  const stats = useReadStats(symbol)
  if (!data) return error ? <div className="srx" data-state="error" role="status"><p>{error}</p><button type="button" onClick={reload}>Tekrar dene</button></div> : <Skeleton/>
  const {summary, futures, position} = data
  const current = data.timeframes.find(item => item.tf === tf) ?? data.timeframes[0]
  const histText = data.history && data.history.tf === current?.tf ? data.history.text : null
  return <section className="srx" data-lean={summary.lean} aria-label="Akıllı yorum" aria-busy={loading || undefined}>
    <header className="srxHead">
      <span className="srxKick">AKILLI YORUM</span>
      <span className="srxConf" data-level={confidenceTone(summary.confidence)} title="Zaman dilimlerinin ne kadar aynı yönde olduğuna göre">Güven: {summary.confidence}</span>
    </header>
    {(() => { const event = topEvent(data.events); return event && event.level !== 'info' ? <p className="srxEventBar" data-level={event.level} role="status"><span aria-hidden="true">{event.kind === 'funding' ? '⏱' : '🗓'}</span>{event.text}</p> : null })()}
    <div className="srxHero">
      <b className="srxTitle" data-lean={summary.lean}>{summary.title}</b>
      <div className="srxGauge" role="img" aria-label={`Yön ölçeği: ${summary.title}`}><i style={{left: `${gaugePos(summary.bias)}%`}}/></div>
      <div className="srxGaugeEnds"><span>SHORT</span><span>NÖTR</span><span>LONG</span></div>
      <p>{summary.text}</p>
      <small className="srxAlign">{summary.alignment.up} ▲ · {summary.alignment.down} ▼ · {summary.alignment.flat} ■ ({summary.alignment.total} zaman dilimi)</small>
    </div>

    {position && <div className="srxPos" data-tone={fitTone(position.fit)}>
      <div className="srxPosHead"><b>{fitTitle(position.fit)}</b>{position.pnl_pct !== null && <span data-tone={position.pnl_pct >= 0 ? 'up' : 'down'}>{signedPct(position.pnl_pct)}</span>}</div>
      <p>{position.text}</p>
      {(position.guard || position.target) && <div className="srxPosRows">
        {position.guard && <span><small>{position.guard.label}</small><b>{priceText(position.guard.price)}</b><em>{signedPct(position.guard.dist_pct)}</em></span>}
        {position.target && <span><small>{position.target.label}</small><b>{priceText(position.target.price)}</b><em>{signedPct(position.target.dist_pct)}</em></span>}
      </div>}
      {Object.entries(position.events).map(([key, event]) => <p className="srxEvt" key={key} data-tone={toneOf(event.tone)}>{event.text}</p>)}
    </div>}

    <div className="srxTf" role="tablist" aria-label="Zaman dilimi">
      {data.timeframes.map(item => <button type="button" role="tab" key={item.tf} aria-selected={item.tf === current?.tf} data-tone={item.tone} onClick={() => setTf(item.tf)}>
        <small>{item.label}</small><b aria-hidden="true">{arrowOf(item.tone, item.score)}</b>{item.event && <i title={EVENT_LABEL[item.event]} aria-label={EVENT_LABEL[item.event]}/>}
      </button>)}
    </div>
    {current && <Detail read={current} history={histText}/>}

    <Levels data={data}/>
    <Extras data={data} onApplyPlan={onApplyPlan}/>
    {alert && <QuickAlerts data={data} alert={alert}/>}

    {futures && <div className="srxBlock" aria-label="Futures verisi">
      <h4>Futures</h4>
      <div className="srxFut">
        {futures.funding_pct !== null && <span><small>Fonlama</small><b>{signedPct(futures.funding_pct, 3)}</b></span>}
        {futures.oi_change_pct !== null && <span><small>Açık poz. (4 sa)</small><b data-tone={futures.oi_change_pct >= 0 ? 'up' : 'down'}>{signedPct(futures.oi_change_pct, 1)}</b></span>}
        {futures.taker_ratio !== null && <span><small>Alıcı / satıcı</small><b data-tone={futures.taker_ratio >= 1.15 ? 'up' : futures.taker_ratio <= 0.87 ? 'down' : undefined}>{futures.taker_ratio.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</b></span>}
      </div>
      {futures.notes.map(note => <p className="srxNoteLine" key={note.text} data-tone={toneOf(note.tone)}>{note.text}</p>)}
    </div>}

    {summary.flags.filter(flag => !futures?.notes.some(note => note.text === flag.text)).length > 0 && <div className="srxBlock" aria-label="Dikkat">
      <h4>Dikkat</h4>
      {summary.flags.filter(flag => !futures?.notes.some(note => note.text === flag.text)).map(flag => <p className="srxNoteLine" key={flag.text} data-tone={toneOf(flag.tone)}>{flag.text}</p>)}
    </div>}

    {(() => { const line = accuracyLine(stats); return line ? <p className="srxAcc"><span aria-hidden="true">🎯</span>{line} <small>Bu, yorumun kısa vadeli yön isabetidir; kâr garantisi değildir.</small></p> : null })()}
    <p className="srxFoot">Kapanmış mumlardan otomatik okuma. Tahmin ya da yatırım tavsiyesi değildir.</p>
  </section>
}

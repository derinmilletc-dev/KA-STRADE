import {useEffect, useState} from 'react'
import './smart-read.css'
import {useCoinRead} from './coin-read-api'
import {
  arrowOf, confidenceTone, EVENT_LABEL, fitTitle, fitTone, gaugePos, levelBar, priceText, signedPct, TF_ORDER,
  type CoinRead, type ReadTf,
} from './coin-read-view'

// Akıllı yorum kartı: tüm zaman dilimlerini tek bakışta okur (uyum/çelişki), seçili sürenin ayrıntısını, destek-direnç,
// futures verisini ve bu kuralın coindeki geçmiş doğruluğunu gösterir. Pozisyon verilirse yorumu pozisyona bağlar.
// Yalnız okuma: kapanmış mumlardan otomatik tarif; tahmin ya da yatırım tavsiyesi değildir.
type Props = {symbol: string; focus?: string; side?: 'LONG' | 'SHORT' | null; entry?: number | null}

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

export default function SmartRead({symbol, focus, side = null, entry = null}: Props) {
  const [tf, setTf] = useState<string>(isTf(focus) ? focus : '15m')
  useEffect(() => { if (isTf(focus)) setTf(focus) }, [focus])
  const {data, error, loading, reload} = useCoinRead(symbol, {interval: tf, side, entry})
  if (!data) return error ? <div className="srx" data-state="error" role="status"><p>{error}</p><button type="button" onClick={reload}>Tekrar dene</button></div> : <Skeleton/>
  const {summary, futures, position} = data
  const current = data.timeframes.find(item => item.tf === tf) ?? data.timeframes[0]
  const histText = data.history && data.history.tf === current?.tf ? data.history.text : null
  return <section className="srx" data-lean={summary.lean} aria-label="Akıllı yorum" aria-busy={loading || undefined}>
    <header className="srxHead">
      <span className="srxKick">AKILLI YORUM</span>
      <span className="srxConf" data-level={confidenceTone(summary.confidence)} title="Zaman dilimlerinin ne kadar aynı yönde olduğuna göre">Güven: {summary.confidence}</span>
    </header>
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

    <p className="srxFoot">Kapanmış mumlardan otomatik okuma. Tahmin ya da yatırım tavsiyesi değildir.</p>
  </section>
}

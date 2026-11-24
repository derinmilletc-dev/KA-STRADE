import {type ReactNode, useState} from 'react'
import {ArrowRight, Check, LockKeyhole, X} from 'lucide-react'
import './coin-detail-panel.css'

// Analist ekranında coine basınca açılan teknik görünüm. Yalnız gösterim: veriyi CoinAnalysisCenter verir, burada istek yok.
// Telefonda tam ekran açılır (kapatma düğmesi yalnız orada görünür); masaüstünde sağ sütunda durur.
type Dir = 'LONG' | 'SHORT' | 'BEKLE'
export type DetailData = {
  symbol: string; display: string; price: number; change: number; direction: Dir; trend: string
  score: number; scoreText: string; rsi?: number; riskReward?: number; volumeRatio?: number
  riskPct?: number; tp3Pct?: number; support?: number; resistance?: number
  entry?: number; stop?: number; tp1?: number; tp2?: number; tp3?: number
  mtf: {timeframe: string; direction: string}[]
  reasons: string[]; verdict: string
}
type Props = {data: DetailData; premium: boolean; chart: ReactNode; insight: ReactNode; showLevels: boolean; showEma: boolean; onLevels: (on: boolean) => void; onEma: (on: boolean) => void; onClose: () => void; onTrade?: () => void}

const ok = (value?: number): value is number => value !== undefined && Number.isFinite(value)
const num = (value?: number) => !ok(value) ? '—' : value.toLocaleString('tr-TR', {maximumFractionDigits: value >= 100 ? 2 : value >= 1 ? 4 : 6})
const pct = (value: number, signed = false) => `${value < 0 ? '−' : signed && value > 0 ? '+' : ''}%${Math.abs(value).toLocaleString('tr-TR', {maximumFractionDigits: 2})}`
const tone = (dir: string) => dir === 'LONG' ? 'up' : dir === 'SHORT' ? 'down' : 'flat'
const Lock = () => <span className="cdpLock"><LockKeyhole aria-hidden="true"/>Master</span>

export default function CoinDetailPanel({data, premium, chart, insight, showLevels, showEma, onLevels, onEma, onClose, onTrade}: Props) {
  const [allReasons, setAllReasons] = useState(false)
  const value = (v: number | undefined, render: (n: number) => string) => ok(v) ? render(v) : premium ? '—' : <Lock/>
  const away = (v?: number) => ok(v) && ok(data.entry) && data.entry > 0 ? pct((v - data.entry) / data.entry * 100, true) : ''
  const rungs = [{key: 'tp3', label: 'TP3', v: data.tp3}, {key: 'tp2', label: 'TP2', v: data.tp2}, {key: 'tp1', label: 'TP1', v: data.tp1}, {key: 'entry', label: 'Giriş', v: data.entry}, {key: 'stop', label: 'Stop', v: data.stop}]
  const up = data.change >= 0
  const reasons = allReasons ? data.reasons : data.reasons.slice(0, 3)
  const volume = ok(data.volumeRatio) ? pct((data.volumeRatio - 1) * 100, true) : '—'
  return <div className="cdp" data-dir={tone(data.direction)}>
    <header className="cdpHead">
      <div className="cdpTitle">
        <small>Seçilen coin · teknik görünüm</small>
        <h3>{data.display}</h3>
        <p><b>{num(data.price)}</b><em data-tone={up ? 'up' : 'down'}>{pct(data.change, true)}</em></p>
      </div>
      <div className="cdpSide">
        <span className="cdpDir" data-tone={tone(data.direction)}>{data.direction === 'BEKLE' ? 'NÖTR' : data.direction}</span>
        <button type="button" className="cdpClose" onClick={onClose} aria-label="Detay panelini kapat"><X aria-hidden="true"/></button>
      </div>
    </header>

    <section className="cdpVerdict" aria-label="Sinyal özeti">
      <div className="cdpScore" style={{'--cdp-p': `${Math.max(0, Math.min(100, data.score))}%`} as React.CSSProperties} role="img" aria-label={`Teknik sinyal skoru ${num(data.score)} / 100, ${data.scoreText}`}>
        <span><b>{num(data.score)}</b><small>/100</small></span>
      </div>
      <div className="cdpVerdictTxt">
        <strong>{data.scoreText}</strong>
        <p>{data.verdict}</p>
        <small>Teknik skor tahmin ya da olasılık değildir.</small>
      </div>
    </section>

    {data.mtf.length > 0 && <div className="cdpMtf" role="list" aria-label="Zaman dilimi uyumu">
      {data.mtf.map(item => <span role="listitem" key={item.timeframe} data-tone={tone(item.direction)}><i aria-hidden="true"/>{item.timeframe.toUpperCase()}</span>)}
    </div>}

    <section className="cdpCard" aria-label="İşlem planı">
      <header><h4>İşlem planı</h4>{(ok(data.riskReward) || !premium) && <span className="cdpPill">R/R {ok(data.riskReward) ? num(data.riskReward) : <Lock/>}</span>}</header>
      <ul className="cdpLadder">{rungs.map(rung => <li key={rung.key} data-kind={rung.key}>
        <span>{rung.label}</span><b>{value(rung.v, num)}</b><em>{rung.key === 'entry' ? '' : away(rung.v)}</em>
      </li>)}</ul>
    </section>

    <dl className="cdpStats" aria-label="Göstergeler">
      <div><dt>RSI</dt><dd>{num(data.rsi)}</dd></div>
      <div><dt>Hacim</dt><dd data-tone={ok(data.volumeRatio) && data.volumeRatio < 0.9 ? 'down' : undefined}>{volume}</dd></div>
      <div><dt>Destek</dt><dd>{value(data.support, num)}</dd></div>
      <div><dt>Direnç</dt><dd>{value(data.resistance, num)}</dd></div>
      <div><dt>Risk</dt><dd data-tone="down">{value(data.riskPct, n => pct(-Math.abs(n)))}</dd></div>
      <div><dt>Hedef 3 potansiyeli</dt><dd data-tone="up">{value(data.tp3Pct, n => pct(n, true))}</dd></div>
    </dl>

    <section className="cdpCard" aria-label="Grafik">
      <header><h4>Grafik</h4>
        <div className="cdpToggles" role="group" aria-label="Grafik katmanları">
          <button type="button" aria-pressed={showLevels} onClick={() => onLevels(!showLevels)}>Seviyeler</button>
          <button type="button" aria-pressed={showEma} onClick={() => onEma(!showEma)}>EMA</button>
        </div>
      </header>
      <div className="cdpChart">{chart}</div>
    </section>

    {insight}

    <section className="cdpCard" aria-label="Sinyal nedenleri">
      <header><h4>Bu sinyal neden oluştu?</h4><small>Gerçek market verisinden</small></header>
      <ul className="cdpWhy">{reasons.map(reason => <li key={reason}><Check aria-hidden="true"/>{reason}</li>)}</ul>
      {data.reasons.length > 3 && <button type="button" className="cdpMore" aria-expanded={allReasons} onClick={() => setAllReasons(open => !open)}>{allReasons ? 'Daha az göster' : `Diğer ${data.reasons.length - 3} nedeni göster`}</button>}
    </section>

    {onTrade && <footer className="cdpCta"><button type="button" className="cdpGo" onClick={onTrade}>{data.display} için işlem ekranı<ArrowRight aria-hidden="true"/></button></footer>}
  </div>
}

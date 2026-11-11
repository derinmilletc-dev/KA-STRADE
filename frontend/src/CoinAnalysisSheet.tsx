import {createPortal} from 'react-dom'
import {ChevronLeft, ChevronRight, X} from 'lucide-react'
import CoinLiveChart from './CoinLiveChart'
import {AI_DISCLAIMER, strengthLabel, type SignalMap} from './coin-ranking'

// Coin analiz penceresi (alttan açılır): Manuel işlemdeki pencerenin aynısı, DCA coin listesi için.
// Yalnız gösterim: listedeki sinyalden beslenir, yeni istek atmaz. Görünüm manual-analysis-sheet.css'teki mxsa- sınıflarından gelir.
export type AnalysisRow = {symbol: string; display?: string; price: number; change: number}

const coinPrice = (value: number) => Number.isFinite(value) ? value.toLocaleString('tr-TR', {maximumFractionDigits: value < 1 ? 6 : value < 100 ? 4 : 2}) : '—'
const signedChange = (value: number) => `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`

export default function CoinAnalysisSheet({rows, symbol, signals, onClose, onOpen, onPick}: {
  rows: AnalysisRow[]
  symbol: string | null
  signals: SignalMap | null | undefined
  onClose: () => void
  onOpen: (symbol: string) => void
  onPick: (symbol: string) => void
}) {
  const idx = symbol ? rows.findIndex(row => row.symbol === symbol) : -1
  const row = idx >= 0 ? rows[idx] : undefined
  const sig = row ? signals?.[row.symbol] : undefined
  if (!row || !sig || typeof document === 'undefined') return null
  const name = row.display || row.symbol
  const step = (delta: number) => { const next = rows[idx + delta]; if (next && signals?.[next.symbol]) onOpen(next.symbol) }
  const title = sig.lean === 'LONG' ? 'Yükseliş yönü ağır basıyor' : sig.lean === 'SHORT' ? 'Düşüş yönü ağır basıyor' : 'Belirgin yön yok'
  const pos = sig.long !== null && sig.short !== null && sig.long + sig.short > 0 ? sig.long / (sig.long + sig.short) * 100 : 50
  const strength = strengthLabel(sig.confidence)
  const read = sig.lean === 'NEUTRAL' ? 'LONG ve SHORT tarafları birbirine yakın. Net bir yön yok.'
    : `${sig.lean === 'SHORT' ? 'Düşüş' : 'Yükseliş'} tarafı ${strength === 'Güçlü' ? 'belirgin şekilde' : strength === 'Orta' ? 'kısmen' : 'hafifçe'} ağır basıyor.${strength === 'Zayıf' ? ' Tek başına yön için yeterli değil.' : ''}`
  return createPortal(<div className="mxsaRoot" data-open="1">
    <button type="button" className="mxsaScrim" aria-label="Analizi kapat" onClick={onClose}/>
    <section className="mxsaSheet" role="dialog" aria-modal="true" aria-label={`${name} analizi`} data-lean={sig.lean}
      onKeyDown={event => { if (event.key === 'Escape') onClose(); if (event.key === 'ArrowRight') step(1); if (event.key === 'ArrowLeft') step(-1) }}>
      <span className="mxsaGrab" aria-hidden="true"/>
      <header className="mxsaHead">
        <button type="button" className="mxsaIcon" aria-label="Önceki coin" disabled={idx <= 0 || !signals?.[rows[idx - 1]?.symbol]} onClick={() => step(-1)}><ChevronLeft aria-hidden="true"/></button>
        <span className="mxsaTitle"><small>ANALİZ SONUCU · {idx + 1} / {rows.length}</small><b>{name}</b></span>
        <button type="button" className="mxsaIcon" aria-label="Sonraki coin" disabled={idx >= rows.length - 1 || !signals?.[rows[idx + 1]?.symbol]} onClick={() => step(1)}><ChevronRight aria-hidden="true"/></button>
        <button type="button" className="mxsaIcon" aria-label="Analizi kapat" onClick={onClose}><X aria-hidden="true"/></button>
      </header>
      <div className="mxsaBody">
        <section className="mxsaHero" aria-label="Analiz özeti">
          <div className="mxsaHeroTxt">
            <span className="mxsaPrice">{coinPrice(Number(row.price))}{Number.isFinite(Number(row.change)) && <em data-tone={Number(row.change) >= 0 ? 'up' : 'down'}>{signedChange(Number(row.change))}</em>}</span>
            <b className="mxsaVerdictT" data-lean={sig.lean}>{title}</b>
            <p className="mxsaRead">{read}</p>
          </div>
          <div className="mxsaRing" data-lean={sig.lean} style={{'--mxsa-p': `${sig.confidence}%`} as React.CSSProperties} role="img" aria-label={`Güven yüzde ${sig.confidence}, ${strength}`}>
            <span><strong>%{sig.confidence}</strong><small>{strength}</small></span>
          </div>
        </section>
        <CoinLiveChart symbol={row.symbol}/>
        <section className="mxsaGauge" aria-label="LONG ve SHORT dengesi">
          <div className="mxsaTrack" data-lean={sig.lean}><i style={{left: `${pos}%`}}/></div>
          <div className="mxsaEnds"><span data-side="SHORT">SHORT</span><span data-side="BEKLE">NÖTR</span><span data-side="LONG">LONG</span></div>
        </section>
        <div className="mxsaPair">
          <div className="mxsaTile" data-side="SHORT" data-lead={sig.lean === 'SHORT' || undefined}><span>SHORT</span><strong>{sig.short === null ? '–' : `%${sig.short}`}</strong><span className="mxsaMeter"><i style={{width: `${sig.short ?? 0}%`}}/></span></div>
          <div className="mxsaTile" data-side="LONG" data-lead={sig.lean === 'LONG' || undefined}><span>LONG</span><strong>{sig.long === null ? '–' : `%${sig.long}`}</strong><span className="mxsaMeter"><i style={{width: `${sig.long ?? 0}%`}}/></span></div>
        </div>
        <p className="mxsaNote">{AI_DISCLAIMER}</p>
      </div>
      <footer className="mxsaFoot">
        {sig.lean === 'NEUTRAL'
          ? <button type="button" className="mxsaCta" data-lean="NEUTRAL" onClick={() => onPick(row.symbol)}>{name} seç</button>
          : <button type="button" className="mxsaCta" data-lean={sig.lean} onClick={() => onPick(row.symbol)}>{name} seç · analiz {sig.lean}</button>}
        <button type="button" className="mxsaBack" onClick={onClose}>Listeye dön</button>
      </footer>
    </section>
  </div>, document.body)
}

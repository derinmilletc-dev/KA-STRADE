import type {CandleRead} from './coin-live-read'

// Otomatik mum yorumu kutusu: analiz çekmecesindeki grafikte ve Coin Analist sayfasında ortak. Görünüm manual-analysis-sheet.css (mxlcRead).
export default function CandleReadBox({read, label}: {read: CandleRead; label: string}) {
  return <div className="mxlcRead" data-tone={read.tone} role="status" aria-live="polite">
    <small>OTOMATİK YORUM · {label}</small>
    <b>{read.head}</b>
    <p>{read.text}</p>
    <div className="mxlcReadChips">{read.chips.map(chip => <span key={chip}>{chip}</span>)}</div>
  </div>
}

import {useState} from 'react'
import {Crosshair, X} from 'lucide-react'
import './smart-read.css'
import {useEntryWatch} from './coin-read-api'
import {nextDirection, WATCH_LABEL, type WatchDirection} from './coin-read-view'

// Giriş fırsatı alarmı: izlenen coinde hacimli kırılım + yorum yönü + zaman dilimi uyumu birlikte olunca telefona bildirim.
// Yalnız bildirim: emir göndermez, "bakmaya değer" der. Şartlar sunucuda (entry_watch.py) sabittir.
export default function EntryWatch({symbol, display}: {symbol: string; display: string}) {
  const {items, max, save, remove} = useEntryWatch()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const mine = items.find(item => item.symbol === symbol)
  const run = async (task: () => Promise<string | null>) => {
    if (busy) return
    setBusy(true); setMessage('')
    const error = await task()
    setBusy(false)
    if (error) setMessage(error)
  }
  const toggle = () => run(() => mine ? remove(symbol) : save(symbol, 'both'))
  const cycle = (direction: WatchDirection) => run(() => save(symbol, nextDirection(direction)))
  return <section className="srx srxWatch" aria-label="Giriş fırsatı alarmı">
    <header className="srxHead">
      <span className="srxKick">GİRİŞ FIRSATI ALARMI</span>
      <span className="srxConf" data-level={mine ? 'high' : undefined}>{mine ? 'İzleniyor' : 'Kapalı'}</span>
    </header>
    <p className="srxWatchText">Hacimli kırılım, yorum yönü ve zaman dilimi uyumu <b>birlikte</b> oluşunca telefona haber verir. Emir vermez; karar senin.</p>
    <div className="srxWatchRow">
      <button type="button" className="srxWatchMain" data-on={mine ? 'true' : undefined} disabled={busy} onClick={() => void toggle()}><Crosshair aria-hidden="true"/>{mine ? `${display} izlemeyi bırak` : `${display} izle`}</button>
      {mine && <button type="button" className="srxWatchDir" disabled={busy} onClick={() => void cycle(mine.direction)} aria-label="Yönü değiştir">{WATCH_LABEL[mine.direction]}</button>}
    </div>
    {message && <p className="srxNoteLine" data-tone="warn" role="alert">{message}</p>}
    {items.filter(item => item.symbol !== symbol).length > 0 && <ul className="srxWatchList" aria-label="İzlenen coinler">
      {items.filter(item => item.symbol !== symbol).map(item => <li key={item.symbol}><b>{item.symbol.replace(/USDT$/, '')}</b><small>{WATCH_LABEL[item.direction]}</small>
        <button type="button" aria-label={`${item.symbol} izlemeyi kaldır`} disabled={busy} onClick={() => void run(() => remove(item.symbol))}><X aria-hidden="true"/></button></li>)}
    </ul>}
    <small className="srxPlanNote">{items.length}/{max} coin izleniyor. Bildirim en sık 30 dakikada bir gelir.</small>
  </section>
}

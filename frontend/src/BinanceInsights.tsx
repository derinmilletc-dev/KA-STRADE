import {useEffect, useState} from 'react'
import {Fingerprint, ChevronDown} from 'lucide-react'
import './smart-read.css'
import {fetchMyInsights} from './coin-read-api'
import {weakHourNote, type Insights} from './coinbase-stats'

// Binance işlem geçmişinden kişisel kalıplar (son 30 gün). Yalnız okuma. Veri yoksa / okunamazsa kart hiç görünmez.
type Data = Insights & {days: number; truncated: boolean; caveat: string}

export default function BinanceInsights() {
  const [data, setData] = useState<Data | null>(null)
  const [failed, setFailed] = useState(false)
  const [open, setOpen] = useState(false)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    void fetchMyInsights().then(result => { if (alive) { setData(result); setFailed(false) } }).catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [tick])
  // Okunamadıysa sessizce kaybolmak yerine kısa not + tekrar dene (anahtar yoksa da bu görünür; zararsız).
  if (failed && !data) return <section className="srx srxMine" data-state="error" aria-label="Senin kalıpların" role="status"><p>Kalıplar şu an okunamadı.</p><button type="button" onClick={() => { setFailed(false); setTick(value => value + 1) }}>Tekrar dene</button></section>
  if (!data || !data.insights.length) return null
  const weak = weakHourNote(data, new Date())
  return <section className="srx srxMine" aria-label="Senin kalıpların" data-open={open || undefined}>
    <button type="button" className="srxMineHead" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span className="srxMineIcon"><Fingerprint aria-hidden="true"/></span>
      <span><b>Senin kalıpların</b><small>Binance · son {data.days} gün · {data.n} işlem</small></span>
      <ChevronDown aria-hidden="true"/>
    </button>
    {weak && <p className="srxNoteLine" data-tone="warn" role="status">{weak}</p>}
    {open && <div className="srxMineBody">
      {data.insights.map(item => <p className="srxNoteLine" key={item.text} data-tone={item.tone === 'good' ? 'up' : item.tone === 'warn' ? 'warn' : 'flat'}>{item.text}</p>)}
      <small className="srxPlanNote">{data.caveat}{data.truncated ? ' Çok işlem olduğu için yalnız ilk 1000 kayıt okundu.' : ''} Geçmişin dağılımıdır; neden-sonuç göstermez.</small>
    </div>}
  </section>
}

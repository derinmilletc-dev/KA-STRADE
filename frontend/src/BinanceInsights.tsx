import {useEffect, useState, type ReactNode} from 'react'
import {Fingerprint, ChevronDown, TrendingUp, TriangleAlert, Clock, Info} from 'lucide-react'
import './kalip-card.css'
import {fetchMyInsights} from './coin-read-api'
import {weakHourNote, type Insights, type InsightBlock} from './coinbase-stats'

// Binance işlem geçmişinden kişisel kalıplar (son 30 gün). Yalnız okuma. Veri yoksa / okunamazsa kart hiç görünmez.
type Data = Insights & {days: number; truncated: boolean; caveat: string}
type Item = {tone: string; text: string}

const pad = (n: number) => String(n).padStart(2, '0')
const range = (b: InsightBlock) => `${pad(b.from)}–${pad(b.to)}`
const bestOf = (blocks: InsightBlock[]) => [...blocks].sort((a, b) => b.win_rate - a.win_rate || b.trades - a.trades)[0]
const worstOf = (blocks: InsightBlock[]) => [...blocks].sort((a, b) => a.win_rate - b.win_rate || b.trades - a.trades)[0]
const rateTone = (rate: number) => rate >= 55 ? 'up' : rate >= 40 ? 'warn' : 'down'

function Chip({tone, icon, label, value}: {tone: string; icon: ReactNode; label: string; value: string}) {
  return <div className="kpChip" data-tone={tone}>{icon}<span><small>{label}</small><b>{value}</b></span></div>
}

export default function BinanceInsights() {
  const [data, setData] = useState<Data | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let alive = true
    void fetchMyInsights().then(result => { if (alive) setData(result) }).catch(() => { if (alive) setData(null) })
    return () => { alive = false }
  }, [])
  if (!data || !data.insights.length) return null

  const now = new Date()
  const weakNow = weakHourNote(data, now)
  const hour = (now.getUTCHours() + 3) % 24
  const goodNow = !weakNow && data.good_blocks.find(b => hour >= b.from && hour < b.to)
  const best = data.good_blocks.length ? bestOf(data.good_blocks) : null
  const worst = data.bad_blocks.length ? worstOf(data.bad_blocks) : null
  const warns: Item[] = data.insights.filter(i => i.tone === 'warn')
  const goods: Item[] = data.insights.filter(i => i.tone === 'good')
  const others: Item[] = data.insights.filter(i => i.tone !== 'warn' && i.tone !== 'good')
  const headline = warns[0] ?? goods[0] ?? others[0]
  const hasRate = typeof data.win_rate === 'number'
  const rate = hasRate ? Math.round(data.win_rate as number) : 0
  const restCount = data.insights.length - 1

  const section = (title: string, tone: string, icon: ReactNode, items: Item[], skip?: Item) => {
    const list = items.filter(i => i !== skip)
    return list.length ? <div className="kpGroup" data-tone={tone}>
      <h4>{icon}{title}</h4>
      <ul>{list.map(i => <li key={i.text}>{i.text}</li>)}</ul>
    </div> : null
  }

  return <section className="kpCard" aria-label="Senin kalıpların" data-open={open || undefined}>
    <button type="button" className="kpHead" aria-expanded={open} onClick={() => setOpen(v => !v)}>
      <span className="kpIcon"><Fingerprint aria-hidden="true"/></span>
      <span className="kpTitle"><b>Senin kalıpların</b><small>Binance · son {data.days} gün · {data.n} işlem</small></span>
      {hasRate && <span className="kpRate" data-tone={rateTone(rate)} title="Son işlemlerinde kazanan oranı"><b>%{rate}</b><small>kazanma</small></span>}
      <ChevronDown className="kpChev" aria-hidden="true"/>
    </button>

    {(weakNow || goodNow) && <div className="kpNow" data-tone={weakNow ? 'warn' : 'up'} role="status">
      <Clock aria-hidden="true"/>
      <span>{weakNow ?? `Şu an senin güçlü saatlerinden biri (TSİ ${range(goodNow as InsightBlock)}): bu aralıkta %${(goodNow as InsightBlock).win_rate} kazandın.`}</span>
    </div>}

    {(best || worst) && <div className="kpChips">
      {best && <Chip tone="up" icon={<TrendingUp aria-hidden="true"/>} label="En iyi saatin" value={`${range(best)} · %${best.win_rate}`}/>}
      {worst && <Chip tone="down" icon={<TriangleAlert aria-hidden="true"/>} label="Zayıf saatin" value={`${range(worst)} · %${worst.win_rate}`}/>}
    </div>}

    {headline && <p className="kpHeadline" data-tone={headline.tone === 'good' ? 'up' : headline.tone === 'warn' ? 'warn' : 'flat'}>{headline.text}</p>}

    {open
      ? <div className="kpBody">
        {section('Dikkat et', 'warn', <TriangleAlert aria-hidden="true"/>, warns, headline)}
        {section('İyi giden', 'up', <TrendingUp aria-hidden="true"/>, goods, headline)}
        {section('Diğer', 'flat', <Info aria-hidden="true"/>, others, headline)}
        <small className="kpNote">{data.caveat}{data.truncated ? ' Çok işlem olduğu için yalnız ilk 1000 kayıt okundu.' : ''} Geçmişin dağılımıdır; neden-sonuç göstermez.</small>
      </div>
      : restCount > 0 && <button type="button" className="kpMore" onClick={() => setOpen(true)}>+{restCount} kalıp daha göster</button>}
  </section>
}

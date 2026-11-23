// Coinbase akıllı uyarı kartları (yalnızca gösterim): işlem öncesi değerlendirme, günlük zarar sayacı, kısmi kâr önerisi.
import {AlertTriangle, CheckCircle2, Info, ShieldAlert, Sparkles} from 'lucide-react'
import type {DailyMeter, ProfitHint, TradeAdvice} from './coinbase-advice'

const usd = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
const ICON = {bad: ShieldAlert, warn: AlertTriangle, info: Info, good: CheckCircle2} as const

export function TradeAdviceCard({advice, compact = false}: {advice: TradeAdvice; compact?: boolean}) {
  const items = compact ? advice.items.filter(item => item.tone === 'bad' || item.tone === 'warn') : advice.items
  if (items.length === 0) return null
  return <section className="cbAdvice" data-verdict={advice.verdict} aria-label="İşlem değerlendirmesi">
    <header><b>{advice.headline}</b>{!compact && advice.lossUsd !== null && <div className="cbAdviceSum">Stopta −{usd(advice.lossUsd)}{advice.gainUsd !== null ? ` · hedefte +${usd(advice.gainUsd)}` : ''} USD</div>}</header>
    <ul>{items.map(item => { const Icon = ICON[item.tone]; return <li key={item.id} data-tone={item.tone}><Icon aria-hidden="true"/><div className="cbAdviceText"><b>{item.title}</b><small>{item.text}</small></div></li> })}</ul>
  </section>
}

export function DailyMeterBar({meter}: {meter: DailyMeter}) {
  return <div className="cbDaily" data-tone={meter.tone} role="status" aria-label="Günlük zarar durumu">
    {meter.pct !== null && <div className="cbDailyTrack" aria-hidden="true"><i style={{width: `${Math.max(2, meter.pct)}%`}}/></div>}
    <span>{meter.text}</span>
  </div>
}

export function ProfitHintBanner({hint, onHalf, onBreakeven}: {hint: ProfitHint; onHalf: () => void; onBreakeven: () => void}) {
  return <div className="cbProfitHint" data-level={hint.level} role="status">
    <Sparkles aria-hidden="true"/>
    <div className="cbProfitHintText"><b>{hint.title}</b><small>{hint.text}</small></div>
    {hint.action === 'half' && <button type="button" onClick={onHalf}>Yarısını kapat</button>}
    {hint.action === 'breakeven' && <button type="button" onClick={onBreakeven}>Stopu girişe çek</button>}
  </div>
}

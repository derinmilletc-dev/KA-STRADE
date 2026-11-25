import {useMemo, useState} from 'react'
import {Clock, Hourglass, Target, X} from 'lucide-react'
import './pending-entries.css'
import {useLiveQuotes} from './useLiveQuotes'
import {midPrice} from './live-quote'
import {formatPrice, formatUsdt, num, type RawRecord} from './position-view'

// Dolum bekleyen (henüz fiyata ulaşmamış) manuel LIMIT girişleri. Emir göndermez: yalnız seçilen değişikliği
// üst bileşene iletir; sunucu yön/risk/sıra kurallarını yeniden doğrular. Stop ve hedefler, emir dolunca koruma
// kurulurken kullanılır; bu yüzden beklerken değiştirilebilir.
export type PendingUpdate = {stop_loss?: number; tp1?: number; tp2?: number; tp3?: number; profit_usdt?: number}

type Props = {
  plans: RawRecord[]
  busy: boolean
  now: number
  onUpdate: (planId: string, symbol: string, body: PendingUpdate) => Promise<boolean>
  onCancel: (planId: string, symbol: string) => Promise<boolean>
}

const PROFIT_CHIPS = [1, 2, 5, 10]

export function isPendingEntry(plan: RawRecord): boolean {
  return String(plan.status ?? '').toUpperCase() === 'DOLUM BEKLİYOR' && String(plan.source ?? '') === 'MANUAL'
    && String(plan.order_type ?? '').toUpperCase() === 'LIMIT'
}

function remaining(plan: RawRecord, nowMs: number): string | null {
  const until = num(plan.good_till_ms)
  if (until === null || until <= 0) return null
  const left = Math.max(0, Math.floor((until - nowMs) / 1000))
  if (left <= 0) return 'süre doluyor'
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60)
  return h > 0 ? `${h} sa ${m} dk` : m > 0 ? `${m} dk` : `${left} sn`
}

export default function PendingEntries({plans, busy, now, onUpdate, onCancel}: Props) {
  const symbols = useMemo(() => plans.map(plan => String(plan.symbol ?? '').toUpperCase()).filter(Boolean), [plans])
  const live = useLiveQuotes(symbols, symbols.length > 0)
  const [open, setOpen] = useState<string | null>(null)
  const [stop, setStop] = useState('')
  const [tps, setTps] = useState(['', '', ''])
  const [profit, setProfit] = useState('')
  const [confirmCancel, setConfirmCancel] = useState<string | null>(null)
  if (plans.length === 0) return null

  const edit = (plan: RawRecord) => {
    const targets = Array.isArray(plan.targets) ? plan.targets.map(String) : ['', '', '']
    setOpen(String(plan.id)); setConfirmCancel(null)
    setStop(String(plan.stop_loss ?? '')); setTps([targets[0] ?? '', targets[1] ?? '', targets[2] ?? '']); setProfit('')
  }
  const parse = (value: string) => { const n = Number(value.replace(',', '.')); return Number.isFinite(n) && n > 0 ? n : undefined }

  return <section className="lcCard pePending" aria-label="Bekleyen giriş emirleri" data-testid="pending-entries">
    <header className="lcCardHead"><div><small>POZİSYONLAR</small><h3>Bekleyen emirler</h3></div><small data-tone="warn">{plans.length} emir fiyata ulaşmayı bekliyor</small></header>
    {plans.map(plan => {
      const id = String(plan.id), symbol = String(plan.symbol ?? '').toUpperCase(), coin = symbol.replace('USDT', '')
      const long = String(plan.direction ?? '').toUpperCase() === 'LONG'
      const entry = num(plan.entry_price), qty = num(plan.quantity)
      const mark = midPrice(live.quotes[symbol], live.now)
      const away = entry !== null && mark !== null && mark > 0 ? (entry - mark) / mark * 100 : null
      const left = remaining(plan, now)
      const targets = Array.isArray(plan.targets) ? plan.targets.map(item => num(item)) : []
      const stopValue = num(plan.stop_loss)
      const money = (level: number | null) => entry !== null && qty !== null && level !== null ? qty * Math.abs(level - entry) : null
      const profitNumber = parse(profit)
      const profitPrice = entry !== null && qty && profitNumber ? (long ? entry + profitNumber / qty : entry - profitNumber / qty) : null
      const targetUsdt = num(plan.profit_target_usdt)
      const isOpen = open === id
      const submit = async () => {
        const body: PendingUpdate = {}
        const s = parse(stop); if (s !== undefined && s !== stopValue) body.stop_loss = s
        if (profitNumber !== undefined) body.profit_usdt = profitNumber
        else tps.forEach((value, index) => {
          const v = parse(value)
          if (v !== undefined && v !== targets[index]) (body as Record<string, number>)[`tp${index + 1}`] = v
        })
        if (Object.keys(body).length === 0) return
        if (await onUpdate(id, symbol, body)) setOpen(null)
      }
      return <article key={id} className="peItem" data-testid="pending-entry" data-open={isOpen || undefined}>
        <div className="peHead">
          <span className="peCoin"><b>{coin}</b><i data-side={long ? 'long' : 'short'}>{long ? 'LONG' : 'SHORT'}</i></span>
          <span className="peState"><Hourglass aria-hidden="true"/>Fiyata ulaşması bekleniyor</span>
        </div>
        <div className="peGrid">
          <span><small>Emir fiyatı</small><b>{formatPrice(entry)}</b></span>
          <span><small>Güncel fiyat</small><b>{formatPrice(mark)}</b></span>
          <span><small>Uzaklık</small><b>{away === null ? '—' : `${away > 0 ? '+' : ''}${away.toLocaleString('tr-TR', {maximumFractionDigits: 2})}%`}</b></span>
          <span><small>Kalan süre</small><b>{left ?? 'iptal edilene dek'}</b></span>
        </div>
        <div className="peLevels">
          <span data-kind="stop"><small>Stop</small><b>{stopValue !== null ? formatPrice(stopValue) : 'yok'}</b>{money(stopValue) !== null && <em>−{formatUsdt(money(stopValue))} USDT</em>}</span>
          {[0, 1, 2].map(index => <span key={index} data-kind="tp"><small>TP{index + 1}</small><b>{formatPrice(targets[index] ?? null)}</b>{money(targets[index] ?? null) !== null && <em>+{formatUsdt(money(targets[index] ?? null))} USDT</em>}</span>)}
        </div>
        {targetUsdt !== null && <p className="peNote"><Target aria-hidden="true"/>Kâr hedefi: +{formatUsdt(targetUsdt)} USDT olunca tamamı kapanır.</p>}
        {!isOpen && <div className="peActions">
          <button type="button" className="lcBtn" disabled={busy} onClick={() => edit(plan)}>Stop / kâr hedefi</button>
          {confirmCancel === id
            ? <button type="button" className="lcBtn lcDanger" disabled={busy} onClick={() => void onCancel(id, symbol).then(() => setConfirmCancel(null))}>Evet, emri iptal et</button>
            : <button type="button" className="lcBtn lcGhost" disabled={busy} onClick={() => { setConfirmCancel(id); window.setTimeout(() => setConfirmCancel(current => current === id ? null : current), 6000) }}><X aria-hidden="true"/>Emri iptal et</button>}
        </div>}
        {isOpen && <div className="peEdit">
          <label><small>Stop fiyatı</small><input inputMode="decimal" value={stop} onChange={event => setStop(event.target.value)}/></label>
          <div className="peProfit">
            <small><Target aria-hidden="true"/>Şu kadar kâr olunca çık (USDT)</small>
            <div className="peChips">{PROFIT_CHIPS.map(value => <button type="button" key={value} data-on={profitNumber === value || undefined} onClick={() => setProfit(String(value))}>+{value}</button>)}
              <input inputMode="decimal" placeholder="özel" value={profit} onChange={event => setProfit(event.target.value)}/></div>
            {profitPrice !== null && <em>≈ {formatPrice(profitPrice)} fiyatında tamamı kapanır</em>}
          </div>
          {profitNumber === undefined && <div className="peTps">{tps.map((value, index) => <label key={index}><small>TP{index + 1}</small><input inputMode="decimal" value={value} onChange={event => setTps(current => current.map((item, i) => i === index ? event.target.value : item))}/></label>)}</div>}
          <p className="peHint"><Clock aria-hidden="true"/>Emir dolunca bu seviyelerle Stop ve hedefler otomatik kurulur. Sunucu seviyeleri yeniden doğrular.</p>
          <div className="peActions">
            <button type="button" className="lcBtn lcPrimary" disabled={busy} onClick={() => void submit()}>Kaydet</button>
            <button type="button" className="lcBtn lcGhost" disabled={busy} onClick={() => setOpen(null)}>Vazgeç</button>
          </div>
        </div>}
      </article>
    })}
  </section>
}

import {useState} from 'react'
import {Bell, BellRing} from 'lucide-react'
import './smart-read.css'
import SmartRead from './SmartRead'
import {PushLine} from './CoinbaseAlerts'
import type {AlarmInput} from './coin-read-api'
import {ALARM_MODES, ALARM_TFS, alarmSummary, type AlarmMode, type AlarmTf, type ReadAlarm} from './coin-read-view'

// Açık pozisyona basınca açılan bölüm: yorum pozisyona bağlı gösterilir + "yorum alarmı".
// Alarm: pozisyonuna ters bir gelişme çıkınca (ters kırılım, tuzak, RSI uyumsuzluğu, zaman dilimlerinin yön değiştirmesi) telefona bildirim.
// Emir göndermez, pozisyonu değiştirmez. Aynı gelişme sürdüğü sürece tekrar bildirmez.
type Props = {
  productId: string; symbol: string; side: 'LONG' | 'SHORT'; entry: number | null
  alarm: ReadAlarm | undefined
  onSave: (input: AlarmInput) => Promise<string | null>
  onRemove: (productId: string) => Promise<string | null>
}

export default function PositionRead({productId, symbol, side, entry, alarm, onSave, onRemove}: Props) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const mode: AlarmMode = alarm?.mode ?? 'against'
  const minTf: AlarmTf = alarm?.min_tf ?? '15m'
  const run = async (task: () => Promise<string | null>) => { setBusy(true); setProblem(''); const result = await task(); setProblem(result ?? ''); setBusy(false) }
  const save = (nextMode: AlarmMode, nextTf: AlarmTf) => run(() => onSave({product_id: productId, symbol, side, entry, mode: nextMode, min_tf: nextTf}))
  return <div className="prx">
    <SmartRead symbol={symbol} focus="15m" side={side} entry={entry}/>
    <section className="prxAlarm" data-on={alarm ? '1' : undefined} aria-label="Yorum alarmı">
      <div className="prxHead">
        <span className="prxIcon" aria-hidden="true">{alarm ? <BellRing/> : <Bell/>}</span>
        <span className="prxTxt"><b>Yorum alarmı</b><small>{alarm ? alarmSummary(alarm) : 'Pozisyonuna ters bir şey çıkınca telefona haber ver'}</small></span>
        <button type="button" className="prxSwitch" role="switch" aria-checked={Boolean(alarm)} aria-label="Yorum alarmı" disabled={busy}
          onClick={() => void (alarm ? run(() => onRemove(productId)) : save('against', '15m'))}><i/></button>
      </div>
      {alarm && <>
        <div className="prxGroup" role="group" aria-label="Neyi bildirsin">
          {ALARM_MODES.map(item => <button type="button" key={item.id} aria-pressed={mode === item.id} disabled={busy} onClick={() => void save(item.id, minTf)}><b>{item.label}</b><small>{item.hint}</small></button>)}
        </div>
        <div className="prxGroup" role="group" aria-label="Hassasiyet" data-cols="3">
          {ALARM_TFS.map(item => <button type="button" key={item.id} aria-pressed={minTf === item.id} disabled={busy} onClick={() => void save(mode, item.id)}><b>{item.label}</b><small>{item.hint}</small></button>)}
        </div>
        <p className="prxHow">Sunucu her dakika yorumu yeniden hesaplar. Yeni bir gelişme çıkınca tek bildirim gelir; aynı gelişme sürerse tekrar etmez. Pozisyon kapanınca alarm kendiliğinden kalkar.</p>
        <PushLine/>
      </>}
      {problem && <p className="prxErr" role="alert">{problem}</p>}
    </section>
  </div>
}

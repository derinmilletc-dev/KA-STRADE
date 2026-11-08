import {Check, MessageCircle, Zap} from 'lucide-react'
import {GUIDE_STEPS, type GuideInput} from './live-guide'

export type LiveStrategy = 'long' | 'dca'
export const STRATEGY_LABEL: Record<LiveStrategy, string> = {long: 'Kais Long', dca: 'DCA'}
const STORAGE_KEY = 'kt-live-strategy-v1'

export function readStrategy(): LiveStrategy {
  try { return window.localStorage.getItem(STORAGE_KEY) === 'dca' ? 'dca' : 'long' } catch { return 'long' }
}
export function writeStrategy(value: LiveStrategy) {
  try { window.localStorage.setItem(STORAGE_KEY, value) } catch { /* depolama kapalıysa seçim yalnız bu oturumda kalır */ }
}

/** Kais Rehber'in üstündeki iki seçenekli strateji seçici. Seçim yalnız rehberin hangi adımları göstereceğini belirler; emir göndermez. */
export function StrategyPicker({value, onChange}: {value: LiveStrategy; onChange: (next: LiveStrategy) => void}) {
  const items: Array<{id: LiveStrategy; label: string; sub: string}> = [
    {id: 'long', label: 'Kais Long', sub: 'Otomatik tarama ve işlem'},
    {id: 'dca', label: 'DCA', sub: 'Kademeli alım botu'},
  ]
  return <div className="lcPicker" role="radiogroup" aria-label="Strateji seç" data-testid="strategy-picker">
    {items.map(item => <button key={item.id} type="button" role="radio" aria-checked={value === item.id} data-on={value === item.id || undefined} data-testid={`strategy-${item.id}`} onClick={() => onChange(item.id)}>
      <b>{item.label}</b><small>{item.sub}</small>
    </button>)}
  </div>
}

/** DCA seçilince Kais Rehber'in yerine geçen kart. Yalnız doğrulanabilen şeyleri (bağlantı, ARM, acil durum) tik olarak gösterir. */
export function DcaGuideCard({input, stale, onOpen, onAsk, onConnect}: {input: GuideInput; stale: boolean; onOpen: () => void; onAsk: () => void; onConnect: () => void}) {
  const verified = input.statusVerified && !stale
  const blocked = verified && input.emergency
  const needsAccount = verified && !input.connected
  const step = needsAccount || blocked ? 0 : 1
  const title = !verified ? 'Durum okunuyor' : blocked ? 'Önce acil durumu çözelim' : needsAccount ? 'Önce Binance hesabını bağla' : 'DCA planını kur, önce Demo’da dene'
  const message = blocked ? (input.emergencyText || 'Acil durum açık. Çözülene kadar yeni DCA başlatılmaz.')
    : needsAccount ? 'Canlı DCA için bağlı bir Binance hesabı gerekir. Demo’yu hesap bağlamadan da deneyebilirsin.'
    : 'DCA, fiyat düştükçe (LONG) ya da yükseldikçe (SHORT) önceden belirlediğin kademelerde ek alım yapar ve ortalama maliyeti düşürür. Önce Demo’da dene; canlıda toplam marj varsayılan olarak en çok 10 USDT.'
  const checks: Array<{text: string; ok: boolean}> = [
    {text: 'Binance hesabı bağlı', ok: input.connected},
    {text: input.armed ? 'Canlı emir kilidi (ARM) açık' : 'Canlı emir kilidi (ARM) kapalı · Demo için gerekmez', ok: input.armed},
    {text: 'Acil durum yok', ok: !input.emergency},
  ]
  const steps = ['Hesap', 'Plan', 'Doğrula', 'Başlat']
  return <section className="lcGuide lcGuideDca" data-tone={blocked ? 'stop' : needsAccount ? 'warn' : 'go'} data-phase="setup" data-testid="dca-guide" aria-live="polite">
    <header className="lcGuideHead">
      <span className="lcEye" aria-hidden="true" data-tone={blocked ? 'stop' : 'go'}/>
      <div><b>KAIS REHBER</b><small>{`Adım ${step + 1} / ${steps.length} · ${steps[step]}`}</small><em className="lcStrat" data-testid="strategy-name">Seçili strateji: DCA</em></div>
      <span className="lcHeadBtns"><button type="button" className="lcAsk" onClick={onAsk}><MessageCircle aria-hidden="true"/>Kais’e sor</button></span>
    </header>
    <ol className="lcSteps" aria-label="DCA adımları">{steps.map((label, index) => <li key={label} data-state={index < step ? 'done' : index === step ? 'current' : 'todo'} aria-current={index === step ? 'step' : undefined}><i/><span>{label}</span></li>)}</ol>
    <h2 className="lcGuideTitle">{title}</h2>
    <p className="lcGuideMsg" data-open>{message}</p>
    {verified && <ul className="lcCoachChecks lcDcaChecks">{checks.map(item => <li key={item.text} data-ok={item.ok || undefined}><i aria-hidden="true">{item.ok ? '✓' : '○'}</i>{item.text}</li>)}</ul>}
    <ol className="lcDcaHow" aria-label="Nasıl ilerler">
      <li><b>Plan:</b> hazır paketi seç (Temkinli, Dengeli, Agresif) ya da kaydırıcılarla ayarla.</li>
      <li><b>Doğrula:</b> “Sunucuyla doğrula” planı sunucunun kurallarıyla yeniden hesaplar.</li>
      <li><b>Başlat:</b> önce Demo, sonra istersen Canlı. Durdur ve Acil durdur her zaman yanında.</li>
    </ol>
    <div className="lcGuideActions">
      {needsAccount ? <button type="button" className="lcBtn lcPrimary" onClick={onConnect}>Bağlantıyı aç</button> : null}
      <button type="button" className={needsAccount ? 'lcBtn lcGhost' : 'lcBtn lcPrimary'} data-testid="dca-open" disabled={blocked} onClick={onOpen}><Zap aria-hidden="true"/>DCA ekranını aç</button>
    </div>
  </section>
}

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
export function DcaGuideCard({input, stale, onOpen, onAsk, onConnect, onManual, onClearEmergency}: {input: GuideInput; stale: boolean; onOpen: () => void; onAsk: () => void; onConnect: () => void; onManual?: () => void; onClearEmergency?: () => void}) {
  const verified = input.statusVerified && !stale
  const blocked = verified && input.emergency
  const needsAccount = verified && !input.connected
  const step = needsAccount || blocked ? 0 : 1
  const title = !verified ? 'Durum okunuyor' : blocked ? 'Önce acil durumu çözelim' : needsAccount ? 'Önce Binance hesabını bağla' : 'DCA planını kur'
  const message = blocked ? (input.emergencyText || 'Acil durum açık. Çözülene kadar yeni DCA başlatılmaz.')
    : needsAccount ? 'Canlı DCA için bağlı bir Binance hesabı gerekir.'
    : 'Coin ve yönü seç, hazır paketle planı kur, sunucuyla doğrula ve başlat. DCA için ayrı bir marj sınırı yok; yalnız platformun genel güvenlik tavanı ve hesap risk kontrolleri geçerli.'
  const checks: Array<{text: string; ok: boolean}> = [
    {text: 'Hesap bağlı', ok: input.connected},
    {text: input.armed ? 'ARM açık' : 'ARM kapalı · DCA ekranında tek dokunuşla verilir', ok: input.armed},
    {text: input.emergency ? 'Acil durum var' : 'Acil durum yok', ok: !input.emergency},
  ]
  const steps = ['Hesap', 'Plan', 'Doğrula', 'Başlat']
  return <section className="lcGuide lcGuideDca" data-tone={blocked ? 'stop' : needsAccount ? 'warn' : 'go'} data-phase="setup" data-testid="dca-guide" aria-live="polite">
    <header className="lcGuideHead">
      <span className="lcEye" aria-hidden="true" data-tone={blocked ? 'stop' : 'go'}/>
      <div><b>KAIS REHBER</b><small>{`Adım ${step + 1} / ${steps.length} · ${steps[step]}`}</small><em className="lcStrat" data-testid="strategy-name">Seçili strateji: DCA</em></div>
      <span className="lcHeadBtns">
        <button type="button" className="lcAsk" onClick={onAsk}><MessageCircle aria-hidden="true"/>Kais’e sor</button>
        {onManual && <button type="button" className="lcManualBtn" data-testid="dca-manual" onClick={onManual}><Zap aria-hidden="true"/>Manuel işlem aç</button>}
      </span>
    </header>
    <ol className="lcSteps" aria-label="DCA adımları">{steps.map((label, index) => <li key={label} data-state={index < step ? 'done' : index === step ? 'current' : 'todo'} aria-current={index === step ? 'step' : undefined}><i/><span>{label}</span></li>)}</ol>
    <h2 className="lcGuideTitle">{title}</h2>
    <p className="lcGuideMsg" data-open>{message}</p>
    {verified && <ul className="lcDcaPills" aria-label="Durum">{checks.map(item => <li key={item.text} data-ok={item.ok || undefined}><i aria-hidden="true">{item.ok ? '✓' : '○'}</i>{item.text}</li>)}</ul>}
    <div className="lcGuideActions">
      {needsAccount ? <button type="button" className="lcBtn lcPrimary" onClick={onConnect}>Bağlantıyı aç</button> : null}
      {blocked && onClearEmergency ? <button type="button" className="lcBtn lcPrimary" data-testid="dca-clear-emergency" onClick={onClearEmergency}>Acil durdurmayı kaldır</button> : null}
      <button type="button" className={needsAccount || blocked ? 'lcBtn lcGhost' : 'lcBtn lcPrimary'} data-testid="dca-open" disabled={blocked} onClick={onOpen}><Zap aria-hidden="true"/>DCA ekranını aç</button>
    </div>
  </section>
}

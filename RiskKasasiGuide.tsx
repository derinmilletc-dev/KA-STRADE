import {useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import {CheckCircle2, Save, ShieldCheck, TriangleAlert} from 'lucide-react'
import './risk-kasasi-guide.css'

/** Risk Kasası'nı düz dille anlatan parçalar. Yalnız görünüm: değerler ve kayıt (PUT /settings) BinanceDemo'da önceki gibi. */
export type RiskDraft = {daily_loss_limit: number; daily_trade_limit: number; max_loss_per_trade: number; max_margin_per_trade: number
  max_positions: number; min_confidence: number; breakeven_enabled: boolean; trailing_enabled: boolean}
type RiskDaily = {realized_pnl?: number; remaining_loss_budget?: number}

const KEYS: (keyof RiskDraft)[] = ['daily_loss_limit', 'daily_trade_limit', 'max_loss_per_trade', 'max_margin_per_trade', 'max_positions', 'min_confidence', 'breakeven_enabled', 'trailing_enabled']
const usdt = (value: number) => `${(Math.abs(value) < 0.005 ? 0 : value).toLocaleString('tr-TR', {maximumFractionDigits: 2})} USDT`

export const RISK_PRESETS: {id: string; name: string; note: string; values: RiskDraft}[] = [
  {id: 'calm', name: 'Temkinli', note: 'Az işlem, küçük kayıp', values: {daily_loss_limit: 10, daily_trade_limit: 4, max_loss_per_trade: 2, max_margin_per_trade: 25, max_positions: 1, min_confidence: 82, breakeven_enabled: true, trailing_enabled: true}},
  {id: 'balanced', name: 'Dengeli', note: 'Çoğu kişi için uygun', values: {daily_loss_limit: 20, daily_trade_limit: 6, max_loss_per_trade: 5, max_margin_per_trade: 50, max_positions: 2, min_confidence: 78, breakeven_enabled: true, trailing_enabled: true}},
  {id: 'bold', name: 'Atak', note: 'Daha çok işlem, daha çok risk', values: {daily_loss_limit: 40, daily_trade_limit: 10, max_loss_per_trade: 10, max_margin_per_trade: 100, max_positions: 3, min_confidence: 72, breakeven_enabled: true, trailing_enabled: true}},
]

export const riskDirty = (draft: RiskDraft | null, saved: RiskDraft | null | undefined) =>
  Boolean(draft && saved && KEYS.some(key => draft[key] !== saved[key]))

export const FIELD_HELP: Record<string, string> = {
  daily_loss_limit: 'Bugünkü toplam kayıp buna ulaşınca o gün yeni işlem açılmaz.',
  daily_trade_limit: 'Bir günde açılabilecek en fazla işlem sayısı.',
  max_loss_per_trade: 'Stop olursa tek bir işlemde kaybedeceğin en fazla tutar.',
  max_margin_per_trade: 'Bir işleme bağlanacak en fazla teminat (kendi paran).',
  max_positions: 'Aynı anda en fazla kaç işlem açık kalabilir.',
  min_confidence: 'Sinyal bu puanın altındaysa işlem açılmaz. Yükselirse daha az ama daha seçici işlem açılır.',
}

export function riskWarnings(draft: RiskDraft): string[] {
  const out: string[] = []
  if (draft.max_loss_per_trade > draft.daily_loss_limit) out.push('İşlem başı kayıp, günlük zarar kilidinden büyük. Tek bir stop bile günü kapatır; işlem başı kaybı düşür.')
  else if (draft.max_loss_per_trade * draft.max_positions > draft.daily_loss_limit) out.push(`${draft.max_positions} işlem aynı anda stop olursa ${usdt(draft.max_loss_per_trade * draft.max_positions)} kaybedersin; bu, günlük kilidin (${usdt(draft.daily_loss_limit)}) üstünde. Kilit yine devreye girer ama aynı anda açık işlem sayısını azaltmayı düşünebilirsin.`)
  if (draft.max_loss_per_trade > draft.max_margin_per_trade) out.push('İşlem başı kayıp, işlem başı teminattan büyük olamaz.')
  return out
}

export function RiskPlainSummary({draft, daily, repairs, duplicateBlocks}: {draft: RiskDraft | null; daily?: RiskDaily; repairs?: number; duplicateBlocks?: number}) {
  if (!draft) return <section className="rkSummary" aria-label="Risk özeti"><p className="rkSentence">Risk ayarların yükleniyor…</p></section>
  const limit = draft.daily_loss_limit
  const left = daily?.remaining_loss_budget
  const used = left === undefined ? null : Math.max(0, Math.min(limit, limit - left))
  const pct = used === null || limit <= 0 ? 0 : Math.round(used / limit * 100)
  const raw = daily?.realized_pnl
  const pnl = raw === undefined ? undefined : Math.abs(raw) < 0.005 ? 0 : raw
  const tone = left !== undefined && left <= 0 ? 'stop' : pct >= 75 ? 'warn' : 'ok'
  return <section className="rkSummary" data-tone={tone} aria-label="Risk özeti">
    <small className="rkKicker">Bugünkü durumun</small>
    <p className="rkSentence">Bugün en fazla <b>{usdt(limit)}</b> kaybedebilirsin. Her işlemde en fazla <b>{usdt(draft.max_loss_per_trade)}</b> riske girer. Aynı anda en fazla <b>{draft.max_positions} işlem</b> açık olur.</p>
    <div className="rkBudget">
      <div className="rkBudgetTop"><span>Bugün kullanılan zarar hakkı</span><b>{used === null ? '—' : `${usdt(used)} / ${usdt(limit)}`}</b></div>
      <i className="rkBar" aria-hidden="true"><u style={{width: `${pct}%`}}/></i>
      <p className="rkBudgetNote">{tone === 'stop' ? 'Günlük sınıra ulaşıldı. Bugün yeni işlem açılmaz; yarın sıfırlanır.' : tone === 'warn' ? `Sınıra yaklaşıyorsun. Kalan hak: ${usdt(left ?? 0)}.` : left === undefined ? 'Bugünkü kullanım okunuyor.' : `Kalan hak: ${usdt(left)}. Gün sonunda sıfırlanır.`}</p>
    </div>
    <dl className="rkGuards">
      <div><dt>Bugünkü sonuç</dt><dd data-sign={pnl === undefined || pnl === 0 ? undefined : pnl > 0 ? 'up' : 'down'}>{pnl === undefined ? '—' : `${pnl > 0 ? '+' : ''}${usdt(pnl)}`}</dd></div>
      <div><dt>Düzeltilen stop emri</dt><dd>{repairs === undefined ? '—' : repairs === 0 ? <span className="rkOk"><CheckCircle2 aria-hidden="true"/>Sorun yok</span> : repairs}</dd><small>Eksik stop emri bulunup yeniden konduysa sayılır.</small></div>
      <div><dt>Engellenen tekrar giriş</dt><dd>{duplicateBlocks === undefined ? '—' : duplicateBlocks === 0 ? <span className="rkOk"><CheckCircle2 aria-hidden="true"/>Sorun yok</span> : duplicateBlocks}</dd><small>Aynı işleme ikinci kez girilmesi engellendiyse sayılır.</small></div>
    </dl>
  </section>
}

export function RiskPresets({draft, onPick}: {draft: RiskDraft; onPick: (values: RiskDraft) => void}) {
  const active = RISK_PRESETS.find(preset => KEYS.every(key => preset.values[key] === draft[key]))?.id
  return <section className="rkPresets" aria-label="Hazır ayarlar">
    <div className="rkPresetsHead"><b>Hazır ayar seç</b><small>Bir karta dokun, değerler dolsun. Sonra istediğin gibi değiştirip kaydet.</small></div>
    <div className="rkPresetRow" role="radiogroup" aria-label="Hazır risk ayarı">
      {RISK_PRESETS.map(preset => <button key={preset.id} type="button" role="radio" aria-checked={active === preset.id} className="rkPreset" data-id={preset.id} onClick={() => onPick(preset.values)}>
        <b>{preset.name}</b><small>{preset.note}</small><em>Günde en fazla {usdt(preset.values.daily_loss_limit)}</em>
      </button>)}
    </div>
  </section>
}

export function RiskWarnings({draft}: {draft: RiskDraft}) {
  const list = riskWarnings(draft)
  if (!list.length) return <p className="rkFine"><ShieldCheck aria-hidden="true"/>Ayarlar birbiriyle uyumlu.</p>
  return <ul className="rkWarn" aria-live="polite">{list.map(text => <li key={text}><TriangleAlert aria-hidden="true"/>{text}</li>)}</ul>
}

/** Kaydedilmemiş değişiklik varken ve asıl Kaydet düğmesi ekranda değilken altta beliren küçük kaydet çubuğu. */
export function RiskDirtyBar({dirty, busy, onSave}: {dirty: boolean; busy: boolean; onSave: () => void}) {
  const [inlineVisible, setInlineVisible] = useState(true)
  useEffect(() => {
    if (!dirty) return
    const target = document.querySelector('.rkSaveWrap')
    if (!target || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(([entry]) => setInlineVisible(entry.isIntersecting), {threshold: 0.6})
    observer.observe(target)
    return () => observer.disconnect()
  }, [dirty])
  if (!dirty || inlineVisible || typeof document === 'undefined') return null
  return createPortal(<div className="rkDirtyBar" role="status"><span>Kaydedilmemiş değişiklik var</span><button type="button" disabled={busy} onClick={onSave}><Save aria-hidden="true"/>Kaydet</button></div>, document.body)
}

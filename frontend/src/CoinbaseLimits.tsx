// "Risk limitlerim": kullanıcı kendi zarar/kaldıraç/stop payı sınırlarını seçer; sunucu tavanı içinde kalır ve emir öncesi
// sunucuda uygulanır. Ayrıca açık emir kapasitesini (5 emir sınırı) görsel olarak gösterir.
import {useCallback, useEffect, useState} from 'react'
import {Gauge, ShieldCheck} from 'lucide-react'
import {coinbaseCall, CoinbaseApiError} from './coinbase-manual-api'
import {limitFormValue, limitLabel, limitsPayload, orderCapacity, type LimitField, type LimitsView} from './coinbase-suggest'

type Props = {openOrders: number | null; onChanged?: () => void}
const FIELDS: Array<{key: LimitField; label: string; unit: string; hint: string; chips: string[]}> = [
  {key: 'max_loss_usd', label: 'İşlem başı en çok zarar', unit: 'USD', hint: 'Stopta kaybedebileceğin üst sınır; sözleşme sayısı buna göre kısılır.', chips: ['5', '10', '25', '50']},
  {key: 'daily_loss_limit_usd', label: 'Günlük zarar limiti', unit: 'USD', hint: 'Bugünkü zarar bu sınıra ulaşınca yeni emir verilmez.', chips: ['25', '50', '100', '250']},
  {key: 'leverage_max', label: 'En yüksek kaldıraç', unit: 'x', hint: 'Bu kaldıracı aşan emir verilmez.', chips: ['3', '5', '10', '20']},
  {key: 'stop_limit_gap_pct', label: 'Stop payı (hızlı düşüşte tampon)', unit: '%', hint: 'Stop-limit’te limit, stoptan bu kadar uzaktadır. Büyütürsen hızlı hareketlerde stopun dolma şansı artar, kayma da artar.', chips: ['0.3', '0.5', '1']},
]
const EMPTY = {max_loss_usd: '', daily_loss_limit_usd: '', leverage_max: '', stop_limit_gap_pct: ''} as Record<LimitField, string>

export default function CoinbaseLimits({openOrders, onChanged}: Props) {
  const [view, setView] = useState<LimitsView | null>(null)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<Record<LimitField, string>>(EMPTY)
  const [fieldError, setFieldError] = useState<{field: LimitField; message: string} | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = useCallback(async () => {
    try {
      const next = await coinbaseCall<LimitsView>('/orders/limits')
      setView(next); setError('')
      setForm(Object.fromEntries((Object.keys(EMPTY) as LimitField[]).map(key => [key, limitFormValue(key, next.custom)])) as Record<LimitField, string>)
    } catch (caught) {
      // Sunucu henüz güncellenmemişse (rota yok) kartı hiç gösterme; hata gibi görünmesin.
      if (caught instanceof CoinbaseApiError && (caught.failure.status === 404 || caught.failure.status === 405)) { setMissing(true); return }
      setError(caught instanceof CoinbaseApiError ? caught.failure.message : 'Limitler alınamadı.')
    }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (!saved) return; const timer = window.setTimeout(() => setSaved(false), 2800); return () => window.clearTimeout(timer) }, [saved])

  if (missing) return null
  if (!view) {
    return <section className="cbLimits" aria-label="Risk limitlerim" data-state={error ? 'error' : 'loading'}>
      <header><span className="cbLimIcon"><ShieldCheck aria-hidden="true"/></span><div><b>Risk limitlerim</b><small>{error || 'Yükleniyor…'}</small></div>
        {error && <button type="button" onClick={() => void load()}>Yeniden dene</button>}</header></section>
  }
  const cap = orderCapacity(openOrders, view.max_open_orders)
  const save = async () => {
    const built = limitsPayload(form, view)
    if (!built.ok) { setFieldError({field: built.field, message: built.message}); return }
    setFieldError(null); setSaving(true)
    try {
      const next = await coinbaseCall<LimitsView>('/orders/limits', {method: 'PUT', body: JSON.stringify(built.body)})
      setView(next); setSaved(true); setOpen(false); onChanged?.()
    } catch (caught) { setError(caught instanceof CoinbaseApiError ? caught.failure.message : 'Limitler kaydedilemedi.') }
    finally { setSaving(false) }
  }
  const set = (key: LimitField, value: string) => { setForm(current => ({...current, [key]: value})); setFieldError(null); setError('') }

  return <section className="cbLimits" aria-label="Risk limitlerim" data-open={open || undefined}>
    <header>
      <span className="cbLimIcon"><ShieldCheck aria-hidden="true"/></span>
      <div><b>Risk limitlerim</b><small>Emir vermeden önce sunucuda uygulanır</small></div>
      <button type="button" aria-expanded={open} onClick={() => { setOpen(value => !value); setFieldError(null); setError('') }}>{open ? 'Kapat' : 'Düzenle'}</button>
    </header>
    <div className="cbLimCap" data-tone={cap.tone} role="img" aria-label={`Açık emir ${cap.used}/${cap.max}. ${cap.text}`}>
      <span className="cbLimSegs">{Array.from({length: cap.max}, (_, index) => <i key={index} data-on={index < cap.used || undefined}/>)}</span>
      <span className="cbLimCapText"><Gauge aria-hidden="true"/><b>{cap.used}/{cap.max} açık emir</b><small>{cap.text} Stop ve hedef tek emir sayılır.</small></span>
    </div>
    <dl className="cbLimGrid">{FIELDS.map(field => <div key={field.key} data-custom={view.custom[field.key] !== undefined || undefined}>
      <dt>{field.label}</dt><dd>{limitLabel(field.key, view.effective[field.key])}</dd>
      <small>{view.custom[field.key] !== undefined ? 'Senin limitin' : 'Varsayılan'}</small></div>)}</dl>
    {saved && <p className="cbLimSaved" role="status">✓ Limitlerin kaydedildi; yeni emirler ve öneriler bunlara göre hesaplanır.</p>}
    {open && <form className="cbLimForm" onSubmit={event => { event.preventDefault(); void save() }} noValidate>
      {FIELDS.map(field => <div className="cbLimField" key={field.key} data-bad={fieldError?.field === field.key || undefined}>
        <label htmlFor={`cb-lim-${field.key}`}>{field.label}</label>
        <div className="cbLimInput"><input id={`cb-lim-${field.key}`} inputMode="decimal" autoComplete="off" placeholder="Varsayılan" value={form[field.key]}
          onChange={event => set(field.key, event.target.value.replace(/[^0-9.,]/g, ''))} aria-describedby={`cb-lim-h-${field.key}`}/><span>{field.unit}</span></div>
        <div className="cbLimChips" role="group" aria-label={`${field.label} kısayolları`}>
          {field.chips.map(chip => <button type="button" key={chip} data-on={form[field.key].replace(',', '.') === chip || undefined} onClick={() => set(field.key, chip)}>{field.key === 'stop_limit_gap_pct' ? `%${chip.replace('.', ',')}` : field.key === 'leverage_max' ? `${chip}x` : chip}</button>)}
          <button type="button" data-on={form[field.key] === '' || undefined} onClick={() => set(field.key, '')}>Varsayılan</button>
        </div>
        <small id={`cb-lim-h-${field.key}`}>{fieldError?.field === field.key ? fieldError.message : field.hint}</small>
      </div>)}
      {error && <p className="cbLimErr" role="alert">{error}</p>}
      <div className="cbLimActs"><button type="submit" className="primary" disabled={saving}>{saving ? 'Kaydediliyor…' : 'Limitleri kaydet'}</button>
        <button type="button" disabled={saving} onClick={() => setForm(EMPTY)}>Hepsini varsayılana al</button></div>
    </form>}
  </section>
}

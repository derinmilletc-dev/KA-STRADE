import {useEffect, useRef, useState} from 'react'
import {Check, ChevronDown, Search} from 'lucide-react'
import {LANGUAGES, type Lang} from './i18n'
import './language-picker.css'

/** Açılır dil seçici. Dil sayısı arttıkça listeye arama kutusu gelir ve liste kayar. */
export default function LanguagePicker({value, onChange, label, disabled}: {value: Lang; onChange: (lang: Lang) => void; label?: string; disabled?: boolean}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const current = LANGUAGES.find(item => item.code === value) ?? LANGUAGES[0]
  const q = query.trim().toLowerCase()
  const rows = LANGUAGES.filter(item => !q || `${item.native} ${item.en} ${item.code}`.toLowerCase().includes(q))
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) { if (event.key === 'Escape') setOpen(false); return }
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])
  return <div className="langPicker" ref={root} data-i18n-skip="true" translate="no">
    {label && <span className="langPickerLabel">{label}</span>}
    <button type="button" className="langPickerTrigger" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => { setOpen(value => !value); setQuery('') }}>
      <i className="langPickerBadge">{current.code}</i>
      <span><b>{current.native}</b><small>{current.en}</small></span>
      <ChevronDown className={open ? 'isOpen' : ''} aria-hidden="true"/>
    </button>
    {open && <div className="langPickerPanel">
      {LANGUAGES.length > 6 && <label className="langPickerSearch"><Search aria-hidden="true"/><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Search / Ara…" aria-label="Search language"/></label>}
      <div className="langPickerList" role="listbox">
        {rows.map(item => <button type="button" role="option" aria-selected={item.code === value} className={item.code === value ? 'on' : ''} key={item.code} onClick={() => { setOpen(false); if (item.code !== value) onChange(item.code) }}>
          <i className="langPickerBadge">{item.code}</i><span><b>{item.native}</b><small>{item.en}</small></span>{item.code === value && <Check aria-hidden="true"/>}
        </button>)}
        {!rows.length && <p>—</p>}
      </div>
    </div>}
  </div>
}

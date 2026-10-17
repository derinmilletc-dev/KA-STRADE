import {useEffect, useRef, useState} from 'react'
import {Check, ChevronDown, Search} from 'lucide-react'
import {LANGUAGES, type Lang} from './i18n'
import './language-picker.css'


/** Windows'ta emoji bayraklar harf olarak göründüğü için bayraklar SVG çizilir. */
function Flag({code}: {code: string}) {
  if (code === 'tr') return <i className="langPickerFlag" aria-hidden="true"><svg viewBox="0 0 30 20" preserveAspectRatio="xMidYMid slice"><rect width="30" height="20" fill="#e30a17"/><circle cx="10.6" cy="10" r="5" fill="#fff"/><circle cx="11.9" cy="10" r="4" fill="#e30a17"/><polygon points="11.90,10.00 13.63,9.44 13.63,7.62 14.70,9.09 16.42,8.53 15.36,10.00 16.42,11.47 14.70,10.91 13.63,12.38 13.63,10.56" fill="#fff"/></svg></i>
  if (code === 'en') return <i className="langPickerFlag" aria-hidden="true"><svg viewBox="0 0 60 30" preserveAspectRatio="xMidYMid slice"><rect width="60" height="30" fill="#012169"/><path d="M0,0 60,30M60,0 0,30" stroke="#fff" strokeWidth="6"/><path d="M0,0 60,30M60,0 0,30" stroke="#c8102e" strokeWidth="3"/><path d="M30,0V30M0,15H60" stroke="#fff" strokeWidth="10"/><path d="M30,0V30M0,15H60" stroke="#c8102e" strokeWidth="6"/></svg></i>
  return <i className="langPickerBadge">{code}</i>
}

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
      <Flag code={current.code}/>
      <span><b>{current.native}</b><small>{current.en}</small></span>
      <ChevronDown className={open ? 'isOpen' : ''} aria-hidden="true"/>
    </button>
    {open && <div className="langPickerPanel">
      {LANGUAGES.length > 6 && <label className="langPickerSearch"><Search aria-hidden="true"/><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Search / Ara…" aria-label="Search language"/></label>}
      <div className="langPickerList" role="listbox">
        {rows.map(item => <button type="button" role="option" aria-selected={item.code === value} className={item.code === value ? 'on' : ''} key={item.code} onClick={() => { setOpen(false); if (item.code !== value) onChange(item.code) }}>
          <Flag code={item.code}/><span><b>{item.native}</b><small>{item.en}</small></span>{item.code === value && <Check aria-hidden="true"/>}
        </button>)}
        {!rows.length && <p>—</p>}
      </div>
    </div>}
  </div>
}

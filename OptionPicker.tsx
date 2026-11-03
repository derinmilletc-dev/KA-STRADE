import {createPortal} from 'react-dom'
import {useEffect, useRef, useState} from 'react'
import {Check, ChevronDown, X} from 'lucide-react'

type Option = {value: string; label: string}
type Props = {value: string; onChange: (value: string) => void; options: Option[]; mobile: boolean; label: string; placeholder: string}

/** Ülke seçici ile aynı görünüm: telefonda alttan açılan liste, bilgisayarda açılır liste. */
export default function OptionPicker({value, onChange, options, mobile, label, placeholder}: Props) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const selected = options.find(option => option.value === value)

  useEffect(() => {
    if (!open) return
    setActive(Math.max(0, options.findIndex(option => option.value === value)))
    window.setTimeout(() => list.current?.focus({preventScroll: true}), 30)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open || mobile) return
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open, mobile])

  const pick = (next: string) => { onChange(next); setOpen(false) }
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => Math.min(options.length - 1, index + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => Math.max(0, index - 1)) }
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(options[active].value) }
    else if (event.key === 'Escape') { setOpen(false) }
  }

  const panel = <div className={mobile ? 'fdCountrySheet fdOptionSheet' : 'fdCountryPop fdOptionPop'} role="dialog" aria-label={label} onClick={event => event.stopPropagation()}>
    {mobile && <div className="fdOptionHead"><b>{label}</b><button type="button" onClick={() => setOpen(false)} aria-label="×"><X aria-hidden="true"/></button></div>}
    <ul ref={list} role="listbox" aria-label={label} tabIndex={-1} onKeyDown={onKey}>
      {options.map((option, index) => <li key={option.value} role="option" aria-selected={option.value === value} className={`${index === active ? 'act' : ''} ${option.value === value ? 'sel' : ''}`} onMouseMove={() => active !== index && setActive(index)} onClick={() => pick(option.value)}>
        <span className="fdCName">{option.label}</span>{option.value === value && <Check aria-hidden="true"/>}
      </li>)}
    </ul>
  </div>

  return <div className="fdCountry" ref={root}>
    <button type="button" className={`fdCountryBtn ${selected ? '' : 'empty'}`} aria-haspopup="listbox" aria-expanded={open} aria-label={label} onClick={() => setOpen(current => !current)}>
      <span>{selected ? selected.label : placeholder}</span><ChevronDown aria-hidden="true"/>
    </button>
    {open && (mobile
      // Telefonda alt sayfa body'ye taşınır: form/kart katmanlarının (backdrop-filter, z-index) altında kalmaz
      ? createPortal(<div className="fdCountry fdPortal" data-i18n-skip translate="no"><div className="fdSheetBack" onClick={() => setOpen(false)}>{panel}</div></div>, document.body)
      : panel)}
  </div>
}

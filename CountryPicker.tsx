import {createPortal} from 'react-dom'
import {useEffect, useMemo, useRef, useState} from 'react'
import {Check, ChevronDown, Search, X} from 'lucide-react'

/** Tüm ülkeler (ISO 3166-1 alpha-2). Adlar tarayıcının Intl.DisplayNames'inden, dile göre gelir. */
export const COUNTRY_CODES = 'AF AX AL DZ AS AD AO AI AQ AG AR AM AW AU AT AZ BS BH BD BB BY BE BZ BJ BM BT BO BQ BA BW BV BR IO BN BG BF BI CV KH CM CA KY CF TD CL CN CX CC CO KM CG CD CK CR CI HR CU CW CY CZ DK DJ DM DO EC EG SV GQ ER EE SZ ET FK FO FJ FI FR GF PF TF GA GM GE DE GH GI GR GL GD GP GU GT GG GN GW GY HT HM VA HN HK HU IS IN ID IR IQ IE IM IL IT JM JP JE JO KZ KE KI KP KR KW KG LA LV LB LS LR LY LI LT LU MO MG MW MY MV ML MT MH MQ MR MU YT MX FM MD MC MN ME MS MA MZ MM NA NR NP NL NC NZ NI NE NG NU NF MK MP NO OM PK PW PS PA PG PY PE PH PN PL PT PR QA RE RO RU RW BL SH KN LC MF PM VC WS SM ST SA SN RS SC SL SG SX SK SI SB SO ZA GS SS ES LK SD SR SJ SE CH SY TW TJ TZ TH TL TG TK TO TT TN TR TM TC TV UG UA AE GB US UM UY UZ VU VE VN VG VI WF EH YE ZM ZW'.split(' ')

const fold = (value: string) => value.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ı/g, 'i').replace(/İ/g, 'i').toLowerCase()

export function countryName(code: string, lang: string): string {
  try { return new Intl.DisplayNames([lang], {type: 'region'}).of(code) || code } catch { return code }
}

/** Tarayıcı dilindeki bölge ya da saat diliminden ülkeyi tahmin eder (yalnız öneri). */
export function guessCountry(): string {
  try {
    const region = new Intl.Locale(navigator.language).region
    if (region && COUNTRY_CODES.includes(region)) return region
  } catch { /* desteklenmiyor */ }
  const zone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone } catch { return '' } })()
  const byZone: Record<string, string> = {'Africa/Johannesburg': 'ZA', 'Europe/Istanbul': 'TR', 'Europe/London': 'GB'}
  return byZone[zone] ?? ''
}

function flag(code: string) {
  return String.fromCodePoint(...[...code].map(char => 0x1f1e6 + char.charCodeAt(0) - 65))
}

type Props = {value: string; onChange: (code: string) => void; lang: 'tr' | 'en'; mobile: boolean; label: string; placeholder: string; searchPlaceholder: string; emptyText: string; suggestedLabel: string; suggested?: string}

export default function CountryPicker({value, onChange, lang, mobile, label, placeholder, searchPlaceholder, emptyText}: Props) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const search = useRef<HTMLInputElement>(null)

  const all = useMemo(() => COUNTRY_CODES.map(code => ({code, name: countryName(code, lang)})).sort((a, b) => a.name.localeCompare(b.name, lang)), [lang])
  const rows = useMemo(() => {
    const needle = fold(query.trim())
    const rank = (item: {code: string; name: string}) => {
      const name = fold(item.name)
      if (item.code.toLowerCase() === needle || name.startsWith(needle)) return 0
      if (name.split(/[\s-]+/).some(word => word.startsWith(needle))) return 1
      return name.includes(needle) ? 2 : 3
    }
    const found = needle ? all.filter(item => rank(item) < 3).sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name, lang)) : all
    return found
  }, [all, query, lang])

  useEffect(() => { setActive(open && !query ? Math.max(0, rows.findIndex(item => item.code === value)) : 0) }, [query, open]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (open) window.setTimeout(() => search.current?.focus({preventScroll: true}), 30) }, [open])
  useEffect(() => {
    if (!open || mobile) return
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open, mobile])
  useEffect(() => { list.current?.children[active]?.scrollIntoView({block: 'nearest'}) }, [active])

  const pick = (code: string) => { onChange(code); setOpen(false); setQuery('') }
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(index => Math.min(rows.length - 1, index + 1)) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(index => Math.max(0, index - 1)) }
    else if (event.key === 'Enter') { event.preventDefault(); const row = rows[active] ?? rows[0]; if (row) pick(row.code) }
    else if (event.key === 'Escape') { setOpen(false) }
  }

  const panel = <div className={mobile ? 'fdCountrySheet' : 'fdCountryPop'} role="dialog" aria-label={label} onClick={event => event.stopPropagation()}>
    <div className="fdCountrySearch"><Search aria-hidden="true"/><input ref={search} value={query} onChange={event => setQuery(event.target.value)} onKeyDown={onKey} placeholder={searchPlaceholder} aria-label={searchPlaceholder} autoComplete="off" autoCorrect="off" spellCheck={false} enterKeyHint="search"/>
      {mobile && <button type="button" onClick={() => setOpen(false)} aria-label="×"><X aria-hidden="true"/></button>}</div>
    <ul ref={list} role="listbox" aria-label={label}>
      {rows.length === 0 && <li className="fdCountryEmpty">{emptyText}</li>}
      {rows.map((item, index) => <li key={item.code} role="option" aria-selected={item.code === value} className={`${index === active ? 'act' : ''} ${item.code === value ? 'sel' : ''}`} onMouseMove={() => active !== index && setActive(index)} onClick={() => pick(item.code)}>
        <span className="fdFlag" aria-hidden="true">{flag(item.code)}</span><span className="fdCName">{item.name}</span>
        {item.code === value && <Check aria-hidden="true"/>}
      </li>)}
    </ul>
  </div>

  return <div className="fdCountry" ref={root}>
    <button type="button" className={`fdCountryBtn ${value ? '' : 'empty'}`} aria-haspopup="listbox" aria-expanded={open} aria-label={label} onClick={() => setOpen(current => !current)}>
      {value ? <><span className="fdFlag" aria-hidden="true">{flag(value)}</span><span>{countryName(value, lang)}</span></> : <span>{placeholder}</span>}
      <ChevronDown aria-hidden="true"/>
    </button>
    {open && (mobile
      // Telefonda alt sayfa body'ye taşınır: form/kart katmanlarının (backdrop-filter, z-index) altında kalmaz
      ? createPortal(<div className="fdCountry fdPortal" data-i18n-skip translate="no"><div className="fdSheetBack" onClick={() => setOpen(false)}>{panel}</div></div>, document.body)
      : panel)}
  </div>
}

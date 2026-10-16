import { currentLang, setLang } from './i18n'
import './language-toggle.css'

export default function LanguageToggle() {
  const lang = currentLang()
  return (
    <div className="langToggle" data-i18n-skip="true" role="group" aria-label="Language / Dil">
      <button type="button" className={lang === 'tr' ? 'on' : ''} aria-pressed={lang === 'tr'} onClick={() => lang !== 'tr' && setLang('tr')}>TR</button>
      <button type="button" className={lang === 'en' ? 'on' : ''} aria-pressed={lang === 'en'} onClick={() => lang !== 'en' && setLang('en')}>EN</button>
    </div>
  )
}

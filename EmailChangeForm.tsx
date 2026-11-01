import {useState, type Dispatch, type FormEvent, type SetStateAction} from 'react'
import {Check, LoaderCircle, Mail, Send} from 'lucide-react'
import type {SensitiveProof} from './account-settings-api'
import {ProofCard, SecurityDialogHeader, proofComplete} from './PasswordChangeForm'

/** E-posta değiştirme penceresinin içeriği. Yalnız görünüm: istek (/account/email/request), doğrulama e-postası
 * ve kimlik kanıtı ProfileSettings'te önceki gibi çalışır; adres doğrulanana kadar mevcut adres değişmez. */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)*\.[^\s@.]{2,}$/
export const EMAIL_MAX_LENGTH = 180
export const emailLooksValid = (value: string) => value.length <= EMAIL_MAX_LENGTH && EMAIL_PATTERN.test(value.trim())

export function EmailDialogHeader() {
  return <SecurityDialogHeader icon={<i className="pwMailIco" aria-hidden="true"><Mail/></i>} title="E-posta Adresini Değiştir" subtitle="Hesap güvenliğin için e-posta adresini güncelle."/>
}

export default function EmailChangeForm(props: {
  newEmail: string; setNewEmail: (value: string) => void; currentEmail: string
  proof: SensitiveProof; setProof: Dispatch<SetStateAction<SensitiveProof>>
  hasPassword: boolean; twoFactor: boolean; emailDelivery: boolean; busy: boolean
  onRequestEmailCode: () => void; onSubmit: (event: FormEvent) => void
}) {
  const {newEmail, busy} = props
  const [touched, setTouched] = useState(false)
  const value = newEmail.trim()
  const valid = emailLooksValid(value)
  const same = valid && value.toLowerCase() === props.currentEmail.trim().toLowerCase()
  const showError = Boolean(value) && (touched || value.includes('@')) && (!valid || same)
  const state = !value ? undefined : valid && !same ? 'ok' : showError ? 'bad' : undefined
  const ready = valid && !same && proofComplete(props.proof, props.hasPassword, props.twoFactor)
  return <form onSubmit={props.onSubmit} className="accountForm pwForm">
    <div className="pwField">
      <label htmlFor="em-new">Yeni e-posta</label>
      <div className="pwInput" data-state={state}>
        <Mail aria-hidden="true"/>
        <input id="em-new" type="email" inputMode="email" autoComplete="email" autoCapitalize="none" spellCheck={false} required maxLength={EMAIL_MAX_LENGTH}
          placeholder="Yeni e-posta adresinizi girin" value={newEmail} aria-invalid={showError || undefined} aria-describedby={showError ? 'em-new-err' : undefined}
          onBlur={() => setTouched(true)} onChange={event => props.setNewEmail(event.target.value)}/>
        {state === 'ok' && <span className="pwOk" aria-hidden="true"><Check/></span>}
      </div>
      {showError && <p id="em-new-err" className="pwErr" aria-live="polite">{same ? 'Bu zaten mevcut e-posta adresiniz.' : 'Geçerli bir e-posta adresi girin (ör. ad@alanadi.com).'}</p>}
    </div>
    <ProofCard proof={props.proof} setProof={props.setProof} hasPassword={props.hasPassword} twoFactor={props.twoFactor} emailDelivery={props.emailDelivery} busy={busy}
      onRequestEmailCode={props.onRequestEmailCode} description="Hesabınızı korumak için kimliğinizi doğrulayın."/>
    <p className="pwHint">Yeni adrese bir doğrulama bağlantısı gönderilir. Bağlantıyı onaylayana kadar mevcut adresiniz değişmez.</p>
    <div className="pwFoot"><button className="pwSubmit" disabled={busy || !ready}>{busy ? <LoaderCircle className="spin" aria-hidden="true"/> : <Send aria-hidden="true"/>}Doğrulama gönder</button></div>
  </form>
}

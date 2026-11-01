import {useState, type Dispatch, type FormEvent, type ReactNode, type SetStateAction} from 'react'
import {Check, Eye, EyeOff, Info, KeyRound, LoaderCircle, Lock, QrCode, Shield, ShieldCheck} from 'lucide-react'
import type {SensitiveProof} from './account-settings-api'
import {PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, passwordRules} from './password-policy'
import './password-change.css'

/** Parola değiştirme penceresinin içeriği. Yalnız görünüm: doğrulama kuralları, sunucu isteği ve
 * kimlik kanıtı (mevcut parola / 2FA / e-posta kodu) ProfileSettings'te önceki gibi çalışır. */
const RULE_LABELS = ['', 'Büyük harf (A–Z)', 'Küçük harf (a–z)', 'Rakam (0–9)', 'Sembol (!@#$% vb.)']

export function passwordStrength(password: string) {
  if (!password) return {level: 0, label: ''}
  const rules = passwordRules(password)
  const passed = rules.filter(([, ok]) => ok).length
  if (passed <= 2) return {level: 1, label: 'Zayıf'}
  if (passed <= 4) return {level: 2, label: 'Orta'}
  return Array.from(password).length >= 16 ? {level: 4, label: 'Çok güçlü'} : {level: 3, label: 'Güçlü'}
}

export function PasswordDialogHeader() {
  return <SecurityDialogHeader icon={<i className="pwShield" aria-hidden="true"><Shield/><Lock/></i>} title="Parola Değiştir" subtitle="Hesabının güvenliğini güncel tut."/>
}

export function SecurityDialogHeader({icon, title, subtitle}: {icon: ReactNode; title: string; subtitle: string}) {
  return <div className="pwHead">{icon}<div><h2 id="account-dialog-title">{title}</h2><p>{subtitle}</p></div></div>
}

/** Kimlik doğrulama kartı: mevcut parola (veya Google hesapları için e-posta kodu) ve açıksa 2FA. Parola ve e-posta pencerelerinde ortak. */
export function ProofCard({proof, setProof, hasPassword, twoFactor, emailDelivery, busy, onRequestEmailCode, description}: {
  proof: SensitiveProof; setProof: Dispatch<SetStateAction<SensitiveProof>>; hasPassword: boolean; twoFactor: boolean; emailDelivery: boolean; busy: boolean
  onRequestEmailCode: () => void; description?: string
}) {
  return <section className="pwProof" aria-labelledby="pw-proof-title">
    <header><i aria-hidden="true"><ShieldCheck/></i><div><h3 id="pw-proof-title">Kimlik Doğrulama</h3>
      <p>{hasPassword ? description ?? `Güvenlik nedeniyle mevcut parolanızı${twoFactor ? ' ve 2FA kodu' : ''} girmeniz gerekir.` : 'Google hesabınız için mevcut doğrulanmış e-posta adresine kod göndererek devam edin.'}</p></div></header>
    {hasPassword
      ? <PasswordInput id="pw-current" label="Mevcut parola" icon="lock" value={proof.current_password || ''} onChange={value => setProof(current => ({...current, current_password: value}))}
        autoComplete="current-password" placeholder="Mevcut parolanız"/>
      : <>
        <button type="button" className="pwGhost" disabled={busy || !emailDelivery} onClick={onRequestEmailCode}>Doğrulama kodu gönder</button>
        <div className="pwField"><label htmlFor="pw-email-code">E-posta kodu</label>
          <div className="pwInput"><QrCode aria-hidden="true"/><input id="pw-email-code" data-private="true" autoComplete="one-time-code" required placeholder="E-postadaki kod" value={proof.email_code || ''} onChange={event => setProof(current => ({...current, email_code: event.target.value}))}/></div></div>
      </>}
    {twoFactor && <div className="pwField"><label htmlFor="pw-totp">2FA veya kurtarma kodu</label>
      <div className="pwInput"><QrCode aria-hidden="true"/><input id="pw-totp" data-private="true" autoComplete="one-time-code" required placeholder="6 haneli kod veya kurtarma kodu" value={proof.totp_code || ''} onChange={event => setProof(current => ({...current, totp_code: event.target.value}))}/></div></div>}
  </section>
}

export const proofComplete = (proof: SensitiveProof, hasPassword: boolean, twoFactor: boolean) =>
  (hasPassword ? Boolean(proof.current_password) : Boolean(proof.email_code)) && (!twoFactor || Boolean(proof.totp_code))

export function PasswordInput({id, label, ariaLabel, icon, value, onChange, autoComplete, placeholder, minLength, maxLength, state, children}: {
  id: string; label: string; ariaLabel?: string; icon: 'key' | 'lock'; value: string; onChange: (value: string) => void; autoComplete: string
  placeholder: string; minLength?: number; maxLength?: number; state?: 'ok' | 'bad';
}) {
  const [shown, setShown] = useState(false)
  return <div className="pwField">
    <label htmlFor={id}>{label}</label>
    <div className="pwInput" data-state={state}>
      {icon === 'key' ? <KeyRound aria-hidden="true"/> : <Lock aria-hidden="true"/>}
      <input id={id} data-private="true" aria-label={ariaLabel} type={shown ? 'text' : 'password'} autoComplete={autoComplete} placeholder={placeholder}
        minLength={minLength} maxLength={maxLength} required value={value} onChange={event => onChange(event.target.value)} autoCapitalize="none" spellCheck={false}/>
      <button type="button" className="pwEye" aria-label={shown ? 'Parolayı gizle' : 'Parolayı göster'} aria-pressed={shown} onClick={() => setShown(value => !value)}>{shown ? <EyeOff aria-hidden="true"/> : <Eye aria-hidden="true"/>}</button>
      {state === 'ok' && <span className="pwOk" aria-hidden="true"><Check/></span>}
    </div>
  </div>
}

export default function PasswordChangeForm(props: {
  newPassword: string; setNewPassword: (value: string) => void
  confirmPassword: string; setConfirmPassword: (value: string) => void
  proof: SensitiveProof; setProof: Dispatch<SetStateAction<SensitiveProof>>
  hasPassword: boolean; twoFactor: boolean; emailDelivery: boolean; busy: boolean
  onRequestEmailCode: () => void; onSubmit: (event: FormEvent) => void
}) {
  const {newPassword, confirmPassword, proof, hasPassword, twoFactor, busy} = props
  const rules = passwordRules(newPassword)
  const strength = passwordStrength(newPassword)
  const matchState = !confirmPassword ? undefined : confirmPassword === newPassword ? 'ok' : 'bad'
  const ready = Boolean(newPassword && confirmPassword) && proofComplete(proof, hasPassword, twoFactor)
  return <form onSubmit={props.onSubmit} className="accountForm pwForm">
    <PasswordInput id="pw-new" label="Yeni parola" ariaLabel="Yeni parola" icon="key" value={newPassword} onChange={props.setNewPassword} autoComplete="new-password"
      placeholder="Yeni parolanızı girin" minLength={PASSWORD_MIN_LENGTH} maxLength={PASSWORD_MAX_LENGTH}/>
    <div className="pwStrength" data-level={strength.level} aria-live="polite">
      <span className="pwBars" aria-hidden="true"><i/><i/><i/><i/></span>
      <span className="pwStrengthLabel">Parola gücü{strength.label && <>: <b>{strength.label}</b></>}</span>
    </div>
    <ul className="pwRules" aria-label="Parola şartları">
      {rules.map(([label, passed], index) => <li key={label} data-ok={passed || undefined}>
        <i aria-hidden="true">{passed && <Check/>}</i><span>{RULE_LABELS[index] || label}</span><span className="pwSr">{passed ? ' karşılandı' : ' eksik'}</span>
      </li>)}
    </ul>
    <PasswordInput id="pw-confirm" label="Yeni parola tekrar" ariaLabel="Yeni parola tekrar" icon="key" value={confirmPassword} onChange={props.setConfirmPassword} autoComplete="new-password"
      placeholder="Parolanızı tekrar girin" minLength={PASSWORD_MIN_LENGTH} maxLength={PASSWORD_MAX_LENGTH} state={matchState}/>
    {matchState === 'bad' && <p className="pwErr" aria-live="polite">Parolalar eşleşmiyor.</p>}

    <ProofCard proof={proof} setProof={props.setProof} hasPassword={hasPassword} twoFactor={twoFactor} emailDelivery={props.emailDelivery} busy={busy} onRequestEmailCode={props.onRequestEmailCode}/>

    <div className="pwNote"><p><Info aria-hidden="true"/>Bu cihazdaki oturumunuz korunur; yalnızca diğer cihazlardaki oturumlar kapatılır.</p><a href="/forgot-password">Parolamı unuttum?</a></div>
    <div className="pwFoot"><button className="pwSubmit" disabled={busy || !ready}>{busy ? <LoaderCircle className="spin" aria-hidden="true"/> : <Check aria-hidden="true"/>}Parolayı güncelle</button></div>
  </form>
}

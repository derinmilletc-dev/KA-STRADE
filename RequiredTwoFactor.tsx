import {useEffect, useState, type FormEvent} from 'react'
import {ShieldCheck} from 'lucide-react'
import {accountRequest, type AccountOverview} from './account-settings-api'
import TwoFactorSetup, {type EnrollmentResult} from './TwoFactorSetup'
import {RecoveryCodes} from './verification-ui'

type Enrollment = {secret: string; otpauth_uri: string}
type Proof = {current_password?: string; challenge_id?: string; email_code?: string}

/** Zorunlu 2FA: 2FA'sı olmayan hesap kurulum bitene kadar uygulamaya geçemez. */
export default function RequiredTwoFactor({onDone, onLogout}: {onDone: () => void; onLogout: () => void}) {
  const [hasPassword, setHasPassword] = useState<boolean | null>(null)
  const [proof, setProof] = useState<Proof>({})
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [codes, setCodes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  useEffect(() => {
    let live = true
    accountRequest<AccountOverview>('/account/overview')
      .then(data => {if (live) setHasPassword(data.user.auth_methods.includes('password'))})
      .catch(reason => {if (live) setError(reason instanceof Error ? reason.message : 'Hesap bilgisi okunamadı.')})
    return () => {live = false}
  }, [])
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('')
    try {await action()} catch (reason) {setError(reason instanceof Error ? reason.message : 'İşlem tamamlanamadı.')}
    finally {setBusy(false)}
  }
  const start = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      setEnrollment(await accountRequest<Enrollment>('/account/2fa/setup', {method: 'POST', body: JSON.stringify(proof)}))
      setProof({})
    })
  }
  const sendEmailCode = () => void run(async () => {
    const result = await accountRequest<{challenge_id: string}>('/account/reauth/email', {method: 'POST', body: '{}'})
    setProof({challenge_id: result.challenge_id, email_code: ''})
    setInfo('Doğrulanmış e-posta adresinize kod gönderildi.')
  })
  const setupForm = hasPassword === null ? <p role="status">Hesap bilgisi okunuyor…</p> : <form onSubmit={start} className="requiredTwoFactorForm">
    {hasPassword
      ? <label>Mevcut parola<input data-private="true" type="password" autoComplete="current-password" required value={proof.current_password || ''} onChange={event => setProof({current_password: event.target.value})}/></label>
      : <>
        <button type="button" disabled={busy} onClick={sendEmailCode}>E-posta ile kod gönder</button>
        {proof.challenge_id && <label>E-posta kodu<input data-private="true" inputMode="numeric" autoComplete="one-time-code" required value={proof.email_code || ''} onChange={event => setProof({...proof, email_code: event.target.value})}/></label>}
      </>}
    <button className="verificationPrimary" type="submit" disabled={busy || (hasPassword ? !proof.current_password : !(proof.challenge_id && proof.email_code))}>Kurulumu başlat</button>
    {info && <p role="status">{info}</p>}
  </form>
  return <main className="accountSettings"><div className="accountWorkspace"><section className="accountCard requiredTwoFactor">
    <header><ShieldCheck aria-hidden="true"/><h1>İki aşamalı doğrulama zorunlu</h1>
      <p>Hesabınızı korumak için devam etmeden önce doğrulama uygulamanızı bağlamanız gerekiyor. Bundan sonra her girişte uygulamadaki kod istenecek.</p></header>
    {codes.length
      ? <div className="verificationFlow"><h2>Yedek kodlarınız</h2><p>Telefonunuzu kaybederseniz bu kodlarla girebilirsiniz. Güvenli bir yere kaydedin; bir daha gösterilmeyecek.</p><RecoveryCodes codes={codes} onSaved={onDone}/></div>
      : <TwoFactorSetup enrollment={enrollment} setupForm={setupForm} onBusyChange={setBusy} onEnabled={(result: EnrollmentResult) => setCodes(result.recovery_codes)}/>}
    {error && <p role="alert">{error}</p>}
    <button type="button" className="requiredTwoFactorLogout" onClick={onLogout}>Çıkış yap</button>
  </section></div></main>
}

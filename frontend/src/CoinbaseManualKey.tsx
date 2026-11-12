import {useState} from 'react'
import {KeyRound, Trash2} from 'lucide-react'
import {CONFIRM, confirmMatches} from './coinbase-manual'
import {CoinbaseApiError, coinbaseCall, postJson} from './coinbase-manual-api'

export type KeyMeta = {key_hint?: string; fingerprint?: string; created_at?: string; updated_at?: string} | null
type Props = {keySaved: boolean; meta: KeyMeta; vaultReady: boolean; vaultReason: string | null; onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void}

export default function CoinbaseManualKey({keySaved, meta, vaultReady, vaultReason, onDone, onError}: Props) {
  const [keyName, setKeyName] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [confirm, setConfirm] = useState('')
  const [totp, setTotp] = useState('')
  const [busy, setBusy] = useState(false)

  const secrets = [privateKey]
  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    try { await action() } catch (error) {
      if (error instanceof CoinbaseApiError) onError(error)
      else throw error
    } finally { setBusy(false) }
  }

  const save = () => run(async () => {
    await coinbaseCall('/key', {method: 'PUT', body: JSON.stringify({key_name: keyName.trim(), private_key: privateKey, confirm, totp_code: totp.trim()})}, secrets)
    setPrivateKey(''); setConfirm(''); setTotp('')
    onDone('Coinbase anahtarı doğrulandı ve şifreli kaydedildi.')
  })
  const remove = () => run(async () => {
    await coinbaseCall('/key', {method: 'DELETE', body: JSON.stringify({confirm, totp_code: totp.trim()})})
    setConfirm(''); setTotp('')
    onDone('Coinbase anahtarı silindi.')
  })

  const canSave = !busy && vaultReady && keyName.trim().length >= 10 && privateKey.length >= 40 && confirmMatches(confirm, CONFIRM.saveKey) && totp.trim().length >= 6
  const canDelete = !busy && keySaved && confirmMatches(confirm, CONFIRM.deleteKey) && totp.trim().length >= 6

  return <section className="cbCard" aria-label="Coinbase API anahtarı">
    <h3><KeyRound/>API anahtarı</h3>
    {!vaultReady && <p className="cbNote bad">Anahtar kasası hazır değil. {vaultReason || 'COINBASE_VAULT_MASTER_KEY ayarını kontrol et.'}</p>}
    {keySaved
      ? <p className="cbNote ok">Kayıtlı anahtar: <b>{meta?.key_hint || '—'}</b>{meta?.updated_at ? ` · ${new Date(meta.updated_at).toLocaleString('tr-TR')}` : ''}. Gizli kısım bir daha gösterilmez.</p>
      : <p className="cbNote warn">Henüz anahtar yok. Coinbase'de anahtarı yalnızca <b>görüntüleme</b> ve <b>işlem</b> izniyle oluştur. <b>Transfer izni açık olmasın</b> (açıksa kaydedilmez).</p>}
    <label>Anahtar adı<input value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="organizations/…/apiKeys/…" autoComplete="off"/></label>
    <label>Gizli anahtar (PEM)<textarea value={privateKey} onChange={e => setPrivateKey(e.target.value)} rows={4} placeholder="-----BEGIN EC PRIVATE KEY-----" autoComplete="off" spellCheck={false}/></label>
    <label>Onay metni<input value={confirm} onChange={e => setConfirm(e.target.value)} placeholder={`Kaydetmek için: ${CONFIRM.saveKey}`} autoComplete="off"/></label>
    <label>2FA kodu<input value={totp} onChange={e => setTotp(e.target.value)} inputMode="numeric" maxLength={12} autoComplete="one-time-code"/></label>
    <div className="cbButtons">
      <button disabled={!canSave} onClick={save}>Doğrula ve kaydet</button>
      <button className="danger" disabled={!canDelete} onClick={remove}><Trash2/>Anahtarı sil ({CONFIRM.deleteKey} yaz)</button>
    </div>
  </section>
}

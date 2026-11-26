import {useState} from 'react'
import {Eye, EyeOff, LockKeyhole, RefreshCw, ShieldCheck, Trash2} from 'lucide-react'
import {CONFIRM} from './coinbase-manual'
import {CoinbaseApiError, coinbaseCall} from './coinbase-manual-api'
import './coinbase-manual.css'
import '../../connection-modern.css'

// Coinbase anahtarı oluşturma adımları: "?" düğmesine basınca açılır. Metin sade tutulur; anahtar değerleri burada gösterilmez.
export const COINBASE_KEY_STEPS: string[] = [
  'Coinbase hesabına gir: Ayarlar > API bölümünü aç ve "API anahtarı oluştur" de.',
  'Bir ad yaz (örneğin KaisTrade) ve işlem yapacağın portföyü seç.',
  'İzinlerde yalnız "View" ve "Trade" açık kalsın. "Transfer" ve "Receive" kutularının işaretini kaldır; para taşıma izni verme.',
  'İmza algoritmasında "ECDSA (Legacy SDKs)" seç. "Ed25519" seçersen çalışmaz.',
  '"Create & download" de. İnen dosyada iki bilgi var: "name" ve "privateKey".',
  'Aşağıdaki "API anahtarı" alanına, "name" değerini yapıştır (organizations/ ile başlar).',
  '"Gizli anahtar" alanına, "privateKey" değerini yapıştır (-----BEGIN EC PRIVATE KEY----- ile başlar). Tırnak ya da virgül kalırsa sorun olmaz.',
  '2FA kodunu yaz, kutuyu işaretle ve "Kaydet ve bağlan" de. Sonra "Bağlantıyı test et" ile kontrol et.',
]

export type KeyMeta = {key_hint?: string; fingerprint?: string; created_at?: string; updated_at?: string} | null
type Props = {lastChecked?: string; keySaved: boolean; meta: KeyMeta; vaultReady: boolean; vaultReason: string | null; onDone: (text: string) => void; onError: (error: CoinbaseApiError) => void; onTest: () => Promise<void>}

/** Binance bağlantı kartıyla aynı yapı (liveWsConnect…): API anahtarı, gizli anahtar, 2FA, onay kutusu, test + kaydet. */
export default function CoinbaseManualKey({lastChecked, keySaved, meta, vaultReady, vaultReason, onDone, onError, onTest}: Props) {
  const [keyName, setKeyName] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [totp, setTotp] = useState('')
  const [accepted, setAccepted] = useState(false)
  const [showSecret, setShowSecret] = useState(false)
  const [armedDelete, setArmedDelete] = useState(false)
  const [busy, setBusy] = useState<'' | 'save' | 'test' | 'delete'>('')
  const [helpOpen, setHelpOpen] = useState(false)

  const run = async (kind: 'save' | 'test' | 'delete', action: () => Promise<void>) => {
    setBusy(kind)
    try { await action() } catch (error) {
      if (error instanceof CoinbaseApiError) onError(error)
      else throw error
    } finally { setBusy('') }
  }

  const save = () => run('save', async () => {
    const result = await coinbaseCall<{check?: {warning?: string | null; permissions_checked?: boolean}}>('/key', {method: 'PUT', body: JSON.stringify({key_name: keyName.trim(), private_key: privateKey, confirm: CONFIRM.saveKey, totp_code: totp.trim()})}, [privateKey])
    setPrivateKey(''); setTotp(''); setAccepted(false)
    // İzinler okunamadıysa sessiz geçilmez: kullanıcıya açık uyarı gösterilir.
    const warning = result?.check?.warning
    onDone('Coinbase anahtarı doğrulandı ve şifreli kaydedildi.' + (warning ? ` UYARI: ${warning}` : ''))
  })
  const remove = () => run('delete', async () => {
    await coinbaseCall('/key', {method: 'DELETE', body: JSON.stringify({confirm: CONFIRM.deleteKey, totp_code: totp.trim()})})
    setTotp(''); setArmedDelete(false)
    onDone('Coinbase anahtarı silindi.')
  })

  const totpOk = totp.trim().length >= 6
  const canSave = !busy && vaultReady && accepted && keyName.trim().length >= 10 && privateKey.length >= 40 && totpOk

  return <div className="liveWsConnect">
    <div className="liveWsConnectHead"><span className={`liveWsDot ${keySaved ? 'on' : ''}`}/><div><b>{keySaved ? 'Hesap bağlı' : 'Hesap bağlı değil'}</b><small>Coinbase · Futures · Son kontrol: {lastChecked || '—'}</small></div><button type="button" className="cbHelpBtn" aria-label="Coinbase bağlantısı nasıl yapılır?" aria-expanded={helpOpen} aria-controls="coinbase-key-help" onClick={() => setHelpOpen(value => !value)}>?</button></div>
    {helpOpen && <div id="coinbase-key-help" className="cbHelp" role="region" aria-label="Coinbase anahtarı oluşturma adımları">
      <b>Coinbase'e nasıl bağlanırım?</b>
      <ol>{COINBASE_KEY_STEPS.map(step => <li key={step}>{step}</li>)}</ol>
      <p>Anahtarını kimseyle paylaşma, ekran görüntüsü atma. Sadece bu sitedeki alana yapıştır.</p>
    </div>}
    {keySaved && <div className="cbDisconnect">
      {!armedDelete
        ? <button type="button" className="liveWsGhost cbDeleteKey" disabled={Boolean(busy)} onClick={() => setArmedDelete(true)}><Trash2/> Bağlantıyı kes</button>
        : <div className="cbDisconnectArm" role="group" aria-label="Bağlantıyı kes">
          <p>Kayıtlı Coinbase anahtarı sunucudan silinir. Onaylamak için 2FA kodunu yaz.</p>
          <input data-private="true" type="password" autoComplete="one-time-code" inputMode="numeric" value={totp} onChange={event => setTotp(event.target.value)} maxLength={12} placeholder="6 haneli 2FA kodu" aria-label="2FA kodu"/>
          <div className="liveWsRow">
            <button type="button" className="liveWsGhost cbDeleteKey" disabled={Boolean(busy) || !totpOk} onClick={() => void remove()}>{busy === 'delete' ? <RefreshCw className="spin"/> : <Trash2/>} Evet, bağlantıyı kes</button>
            <button type="button" className="liveWsGhost" onClick={() => { setArmedDelete(false); setTotp('') }}>Vazgeç</button>
          </div>
        </div>}
    </div>}
    {!vaultReady && <p className="liveWsWarn">Anahtar kasası hazır değil. {vaultReason || 'COINBASE_VAULT_MASTER_KEY ayarını kontrol et.'}</p>}
    {keySaved && <p className="liveWsHint">Kayıtlı anahtar: <b>{meta?.key_hint || '—'}</b>. Gizli kısım bir daha gösterilmez. Yenisini kaydedersen eskisinin yerine geçer.</p>}
    <div className="liveWsFields">
      <label>API anahtarı<input id="coinbase-api-key" data-private="true" type="password" autoComplete="new-password" value={keyName} onChange={event => setKeyName(event.target.value)} placeholder="API anahtarını yapıştır"/></label>
      <label>Gizli anahtar<div className="masterTradeLiveSecretField"><input data-private="true" type={showSecret ? 'text' : 'password'} autoComplete="new-password" value={privateKey} onChange={event => setPrivateKey(event.target.value)} placeholder="Gizli anahtarı yapıştır"/><button type="button" aria-label={showSecret ? 'Gizli anahtarı gizle' : 'Gizli anahtarı göster'} onClick={() => setShowSecret(value => !value)}>{showSecret ? <EyeOff/> : <Eye/>}</button></div></label>
      <label>2FA kodu<input data-private="true" type="password" autoComplete="one-time-code" inputMode="numeric" value={totp} onChange={event => setTotp(event.target.value)} maxLength={12} placeholder="Doğrulama uygulamasındaki 6 haneli kod"/></label>
    </div>
    <label className="liveWsCheck masterTradeLiveCheck"><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)}/><span>Anahtarlarımın şifreli sunucu kasasında saklanmasını kabul ediyorum.</span></label>
    <div className="liveWsRow">
      <button type="button" className="liveWsGhost" disabled={Boolean(busy) || !keySaved} onClick={() => void run('test', onTest)}>{busy === 'test' ? <RefreshCw className="spin"/> : <ShieldCheck/>} Bağlantıyı test et</button>
      <button type="button" className="liveWsPrimary" disabled={!canSave} onClick={save}>{busy === 'save' ? <RefreshCw className="spin"/> : <LockKeyhole/>} Kaydet ve bağlan</button>
    </div>
    <p className="liveWsHint">Futures izni olan, para çekme izni olmayan bir anahtar kullan. Bu adım emir açmaz.</p>
  </div>
}

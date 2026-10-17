import {useEffect, useState} from 'react'
import {Bell, BellRing, Smartphone, Volume2} from 'lucide-react'
import {NOTIFY_KINDS, browserNotificationState, fireNotification, loadNotifyPrefs, saveNotifyPrefs, type NotifyKind, type NotifyPrefs} from './notification-prefs'
import {disablePush, enablePush, pushState, sendTestPush, syncPushKinds, type PushState} from './push-client'
import './notify-settings.css'

function Switch({checked, onChange, label}: {checked: boolean; onChange: (value: boolean) => void; label: string}) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`nsSwitch${checked ? ' on' : ''}`} onClick={() => onChange(!checked)}><i/></button>
}

export default function NotificationSettings() {
  const [prefs, setPrefs] = useState<NotifyPrefs>(() => loadNotifyPrefs())
  const [permission, setPermission] = useState(() => browserNotificationState())
  const [message, setMessage] = useState('')
  const [phone, setPhone] = useState<PushState | 'loading'>('loading')
  useEffect(() => { void pushState().then(setPhone) }, [])
  const update = (next: NotifyPrefs) => { setPrefs(next); saveNotifyPrefs(next); if (phone === 'on') void syncPushKinds(next) }
  const togglePhone = async (value: boolean) => {
    setMessage('')
    if (!value) { await disablePush(); setPhone('off'); return }
    const error = await enablePush(prefs)
    if (error) { setMessage(error); setPhone(await pushState()); return }
    setPhone('on')
  }
  const testPhone = async () => { const error = await sendTestPush(); setMessage(error ?? '') }
  const phoneHint = phone === 'loading' ? 'Kontrol ediliyor…' : phone === 'on' ? 'Site kapalıyken de bu cihaza haber gelir'
    : phone === 'denied' ? 'Bu cihazda engelli; ayarlardan izin ver' : phone === 'ios-install' ? 'iPhone: önce Paylaş → Ana Ekrana Ekle, sonra oradan aç'
    : phone === 'unsupported' ? 'Bu tarayıcıda desteklenmiyor' : phone === 'not-ready' ? 'Sunucuda henüz hazır değil' : 'Site kapalıyken de telefonuna haber gelsin'
  const toggleKind = (id: NotifyKind, value: boolean) => update({...prefs, enabled: {...prefs.enabled, [id]: value}})
  const toggleBrowser = async (value: boolean) => {
    if (!value) { update({...prefs, browser: false}); setMessage(''); return }
    if (typeof Notification === 'undefined') { setMessage('Bu tarayıcı bildirimleri desteklemiyor.'); return }
    let state = Notification.permission
    if (state === 'default') { try { state = await Notification.requestPermission() } catch { /* eski tarayıcı */ } }
    setPermission(state)
    if (state === 'granted') { update({...prefs, browser: true}); setMessage('') }
    else { update({...prefs, browser: false}); setMessage(state === 'denied' ? 'Tarayıcı bildirimleri engellenmiş. Adres çubuğundaki kilit simgesinden izin ver.' : 'İzin verilmedi.') }
  }
  const sendTest = () => fireNotification('Test bildirimi', 'Bildirimler bu cihazda çalışıyor.', 'ok', prefs, 'test')
  const activeCount = NOTIFY_KINDS.filter(item => prefs.enabled[item.id]).length
  return <section className="accountCard nsCard" id="notify-settings">
    <h2>Bildirimler</h2>
    <p className="nsLead">Önemli canlı işlem olaylarını Master Trade sayfası açıkken ekranda ve tarayıcı bildirimi olarak görürsün. Telefona bildirimi açarsan site kapalıyken de haber gelir; hangi olayların geleceğini aşağıdaki anahtarlar belirler.</p>
    <div className="nsList">
      {NOTIFY_KINDS.map(item => <div className="nsRow" key={item.id}>
        <span><b>{item.title}</b><small>{item.hint}</small></span>
        <Switch checked={prefs.enabled[item.id]} onChange={value => toggleKind(item.id, value)} label={item.title}/>
      </div>)}
    </div>
    <div className="nsChannels">
      <div className="nsRow">
        <span><b><BellRing/>Tarayıcı bildirimi</b><small>{permission === 'unsupported' ? 'Bu tarayıcıda desteklenmiyor' : permission === 'denied' ? 'Tarayıcıda engelli' : 'Başka sekmedeyken de haber verir'}</small></span>
        <Switch checked={prefs.browser && permission === 'granted'} onChange={value => void toggleBrowser(value)} label="Tarayıcı bildirimi"/>
      </div>
      <div className="nsRow">
        <span><b><Smartphone/>Telefona bildirim</b><small>{phoneHint}</small></span>
        <Switch checked={phone === 'on'} onChange={value => void togglePhone(value)} label="Telefona bildirim"/>
      </div>
      <div className="nsRow">
        <span><b><Volume2/>Ses</b><small>Bildirimle birlikte kısa bir ses çalar</small></span>
        <Switch checked={prefs.sound} onChange={value => update({...prefs, sound: value})} label="Ses"/>
      </div>
    </div>
    {message && <p className="nsWarn" role="alert">{message}</p>}
    <div className="nsFoot">
      <small>{activeCount} bildirim türü açık</small>
      <button type="button" className="nsTest" onClick={sendTest}><Bell/>Test bildirimi gönder</button>
      {phone === 'on' && <button type="button" className="nsTest" onClick={() => void testPhone()}><Smartphone/>Telefona test gönder</button>}
    </div>
  </section>
}

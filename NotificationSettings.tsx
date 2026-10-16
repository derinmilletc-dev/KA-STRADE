import {useState} from 'react'
import {Bell, BellRing, Volume2} from 'lucide-react'
import {NOTIFY_KINDS, browserNotificationState, fireNotification, loadNotifyPrefs, saveNotifyPrefs, type NotifyKind, type NotifyPrefs} from './notification-prefs'
import './notify-settings.css'

function Switch({checked, onChange, label}: {checked: boolean; onChange: (value: boolean) => void; label: string}) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className={`nsSwitch${checked ? ' on' : ''}`} onClick={() => onChange(!checked)}><i/></button>
}

export default function NotificationSettings() {
  const [prefs, setPrefs] = useState<NotifyPrefs>(() => loadNotifyPrefs())
  const [permission, setPermission] = useState(() => browserNotificationState())
  const [message, setMessage] = useState('')
  const update = (next: NotifyPrefs) => { setPrefs(next); saveNotifyPrefs(next) }
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
    <p className="nsLead">Önemli canlı işlem olaylarını Master Trade sayfası açıkken ekranda ve tarayıcı bildirimi olarak görürsün. Ayarlar yalnızca bu cihazda geçerlidir. Sayfa kapalıyken bildirim gelmez.</p>
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
        <span><b><Volume2/>Ses</b><small>Bildirimle birlikte kısa bir ses çalar</small></span>
        <Switch checked={prefs.sound} onChange={value => update({...prefs, sound: value})} label="Ses"/>
      </div>
    </div>
    {message && <p className="nsWarn" role="alert">{message}</p>}
    <div className="nsFoot">
      <small>{activeCount} bildirim türü açık</small>
      <button type="button" className="nsTest" onClick={sendTest}><Bell/>Test bildirimi gönder</button>
    </div>
  </section>
}

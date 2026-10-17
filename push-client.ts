import {API_BASE} from './api'
import {NOTIFY_KINDS, type NotifyPrefs} from './notification-prefs'

export type PushState = 'unsupported' | 'ios-install' | 'not-ready' | 'denied' | 'off' | 'on'

const base = `${API_BASE}/v22/push`
const isIos = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
const standalone = () => window.matchMedia('(display-mode: standalone)').matches || (navigator as unknown as {standalone?: boolean}).standalone === true
const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window

const keyBytes = (value: string) => {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=')
  return Uint8Array.from(atob(padded), char => char.charCodeAt(0))
}
export const enabledKinds = (prefs: NotifyPrefs) => NOTIFY_KINDS.filter(item => prefs.enabled[item.id]).map(item => item.id)

async function registration() {
  return navigator.serviceWorker.register('/sw.js', {scope: '/'}).then(() => navigator.serviceWorker.ready)
}
async function serverStatus(): Promise<{ready: boolean; public_key: string | null}> {
  const response = await fetch(`${base}/status`)
  if (!response.ok) return {ready: false, public_key: null}
  return response.json()
}
async function post(path: string, body?: object) {
  return fetch(`${base}/${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})})
}

export async function pushState(): Promise<PushState> {
  if (!supported()) return isIos() && !standalone() ? 'ios-install' : 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  try {
    if (!(await serverStatus()).ready) return 'not-ready'
    const existing = await (await registration()).pushManager.getSubscription()
    return existing && Notification.permission === 'granted' ? 'on' : 'off'
  } catch { return 'not-ready' }
}

/** Turns phone/browser push on for this device. Returns a user-facing error text, or null on success. */
export async function enablePush(prefs: NotifyPrefs): Promise<string | null> {
  if (!supported()) return isIos() && !standalone() ? 'iPhone\'da önce siteyi Ana Ekrana ekle (Paylaş → Ana Ekrana Ekle), sonra oradan aç.' : 'Bu tarayıcı telefon bildirimini desteklemiyor.'
  const permission = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission
  if (permission !== 'granted') return permission === 'denied' ? 'Bildirimler engellenmiş. Tarayıcı veya telefon ayarlarından bu site için izin ver.' : 'İzin verilmedi.'
  try {
    const status = await serverStatus()
    if (!status.ready || !status.public_key) return 'Telefon bildirimi sunucuda henüz hazır değil.'
    const worker = await registration()
    const subscription = (await worker.pushManager.getSubscription())
      ?? await worker.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: keyBytes(status.public_key)})
    const json = subscription.toJSON()
    const response = await post('subscribe', {endpoint: json.endpoint, keys: json.keys, kinds: enabledKinds(prefs)})
    if (!response.ok) { await subscription.unsubscribe().catch(() => false); return 'Cihaz kaydedilemedi. Biraz sonra tekrar dene.' }
    return null
  } catch { return 'Telefon bildirimi açılamadı. Biraz sonra tekrar dene.' }
}

export async function syncPushKinds(prefs: NotifyPrefs): Promise<void> {
  try {
    if (!supported() || Notification.permission !== 'granted') return
    const subscription = await (await registration()).pushManager.getSubscription()
    if (!subscription) return
    const json = subscription.toJSON()
    await post('subscribe', {endpoint: json.endpoint, keys: json.keys, kinds: enabledKinds(prefs)})
  } catch { /* ayar bir sonraki açılışta yeniden eşitlenir */ }
}

export async function disablePush(): Promise<void> {
  try {
    const subscription = await (await registration()).pushManager.getSubscription()
    if (!subscription) return
    await post('unsubscribe', {endpoint: subscription.endpoint})
    await subscription.unsubscribe()
  } catch { /* cihaz zaten kayıtlı değil */ }
}

export async function sendTestPush(): Promise<string | null> {
  const response = await post('test')
  if (response.ok) return null
  return response.status === 409 ? 'Bu cihaz kayıtlı görünmüyor. Telefon bildirimini kapatıp tekrar aç.' : 'Test bildirimi gönderilemedi.'
}

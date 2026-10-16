import {useEffect, useRef} from 'react'

export type NotifyKind = 'entry' | 'close' | 'protection' | 'connection' | 'arm' | 'emergency'
export type NotifyPrefs = {enabled: Record<NotifyKind, boolean>; browser: boolean; sound: boolean}

export const NOTIFY_KINDS: {id: NotifyKind; title: string; hint: string; pattern: RegExp; tone: 'ok' | 'warn' | 'bad'}[] = [
  {id: 'entry', title: 'Emir açıldı', hint: 'Yeni bir canlı pozisyon açıldığında', pattern: /^LIVE_ENTRY$|^LIVE_ENTRY_RECOVERED$/, tone: 'ok'},
  {id: 'close', title: 'Pozisyon kapandı', hint: 'Pozisyon kapatıldığında veya emir iptal edildiğinde', pattern: /^LIVE_CLOSE$|^LIVE_ENTRY_CANCELLED$|STOP_HIT|TP_HIT/, tone: 'ok'},
  {id: 'protection', title: 'Stop / koruma sorunu', hint: 'Stop kurulamadığında veya koruma onarıldığında', pattern: /^PROTECTION_(FAIL|BLOCK|REPAIR)$|LIVE_STOP_VERIFICATION_RETRY|OWNERSHIP_UNCERTAIN|LIVE_FOREIGN_ORDER|LIVE_ENTRY_REMAINDER_UNCERTAIN/, tone: 'bad'},
  {id: 'connection', title: 'Bağlantı sorunu', hint: 'Binance bağlantısı koptuğunda veya hız sınırına takıldığında', pattern: /STALE|RATE_LIMIT|RATE_LIMITED|AUTO_ERROR|MONITORING/, tone: 'warn'},
  {id: 'arm', title: 'İzin süresi / kilit', hint: '24 saatlik izin bittiğinde veya canlı işlem kapandığında', pattern: /LIVE_CONSENT_REAUTH_REQUIRED|LIVE_CONSENT_GRACE_EXPIRED|LIVE_AUTO_FAIL_CLOSED|^LIVE_DISARM$/, tone: 'warn'},
  {id: 'emergency', title: 'Acil durum', hint: 'Acil durdurma veya belirsiz emir durumunda', pattern: /^LIVE_EMERGENCY$|UNKNOWN_ORDER/, tone: 'bad'},
]

const KEY = 'kais-notify-prefs:v1'
export const DEFAULT_NOTIFY_PREFS: NotifyPrefs = {
  enabled: {entry: true, close: true, protection: true, connection: true, arm: true, emergency: true},
  browser: false,
  sound: true,
}

export function loadNotifyPrefs(): NotifyPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (raw && typeof raw === 'object') {
      return {
        enabled: {...DEFAULT_NOTIFY_PREFS.enabled, ...(raw.enabled && typeof raw.enabled === 'object' ? raw.enabled : {})},
        browser: raw.browser === true,
        sound: raw.sound !== false,
      }
    }
  } catch { /* depolama kapalı olabilir */ }
  return {...DEFAULT_NOTIFY_PREFS, enabled: {...DEFAULT_NOTIFY_PREFS.enabled}}
}

export function saveNotifyPrefs(prefs: NotifyPrefs) {
  try { localStorage.setItem(KEY, JSON.stringify(prefs)) } catch { /* depolama kapalı olabilir */ }
}

export const browserNotificationState = (): 'granted' | 'denied' | 'default' | 'unsupported' =>
  typeof Notification === 'undefined' ? 'unsupported' : Notification.permission

function beep() {
  try {
    const Context = window.AudioContext || (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext
    if (!Context) return
    const context = new Context()
    const tone = (frequency: number, start: number) => {
      const oscillator = context.createOscillator()
      const gain = context.createGain()
      oscillator.frequency.value = frequency
      gain.gain.setValueAtTime(0.0001, context.currentTime + start)
      gain.gain.exponentialRampToValueAtTime(0.15, context.currentTime + start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + start + 0.22)
      oscillator.connect(gain); gain.connect(context.destination)
      oscillator.start(context.currentTime + start); oscillator.stop(context.currentTime + start + 0.25)
    }
    tone(880, 0); tone(660, 0.2)
    window.setTimeout(() => void context.close(), 900)
  } catch { /* ses desteklenmiyor */ }
}

function toast(title: string, body: string, tone: 'ok' | 'warn' | 'bad') {
  const color = tone === 'bad' ? '#f87171' : tone === 'warn' ? '#f5b942' : '#34d399'
  const element = document.createElement('div')
  element.setAttribute('role', 'status')
  element.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483000;max-width:min(380px,calc(100vw - 32px));padding:14px 16px;border-radius:16px;background:#141d28;color:#edf2f8;font:600 14px/1.45 system-ui,sans-serif;box-shadow:0 12px 32px rgba(0,0,0,.45);display:grid;gap:3px;'
  const head = document.createElement('b')
  head.textContent = title
  head.style.color = color
  const text = document.createElement('span')
  text.textContent = body
  text.style.cssText = 'font-weight:500;color:#cbd5e1;overflow-wrap:anywhere'
  element.append(head, text)
  document.body.appendChild(element)
  window.setTimeout(() => element.remove(), 8000)
}

export function fireNotification(title: string, body: string, tone: 'ok' | 'warn' | 'bad', prefs = loadNotifyPrefs(), tag?: string) {
  toast(title, body, tone)
  if (prefs.sound) beep()
  if (prefs.browser && browserNotificationState() === 'granted') {
    try { new Notification(title, {body, tag}) } catch { /* bazı tarayıcılar engeller */ }
  }
}

type LiveEvent = {kind?: unknown; message?: unknown; created_at?: unknown}

/** Master Trade açıkken yeni canlı olayları izler; seçili olaylar için bildirim gösterir. */
export function useLiveEventNotifications(events: unknown) {
  const seen = useRef<Set<string> | null>(null)
  useEffect(() => {
    if (!Array.isArray(events)) return
    const keyOf = (event: LiveEvent) => `${String(event.created_at)}|${String(event.kind)}|${String(event.message)}`
    if (seen.current === null) { seen.current = new Set((events as LiveEvent[]).map(keyOf)); return }
    const prefs = loadNotifyPrefs()
    for (const event of (events as LiveEvent[]).slice().reverse()) {
      const key = keyOf(event)
      if (seen.current.has(key)) continue
      seen.current.add(key)
      const kind = String(event.kind ?? '')
      const match = NOTIFY_KINDS.find(item => item.pattern.test(kind))
      if (!match || !prefs.enabled[match.id]) continue
      fireNotification(match.title, String(event.message || kind).replaceAll('_', ' '), match.tone, prefs, key)
    }
  }, [events])
}

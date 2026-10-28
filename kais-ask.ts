// Sayfalardaki "Kais'e sor" düğmeleri sohbeti açıp hazır bir soruyu gönderir; asistan bileşenine doğrudan bağlanmaz.
export const KAIS_ASK_EVENT = 'kais:ask'
export const KAIS_ASK_MAX = 400

export function askKais(message: string): boolean {
  const text = String(message || '').trim().slice(0, KAIS_ASK_MAX)
  if (!text || typeof window === 'undefined') return false
  window.dispatchEvent(new CustomEvent(KAIS_ASK_EVENT, {detail: {message: text}}))
  return true
}

export function kaisAskMessage(event: Event): string | null {
  if (!(event instanceof CustomEvent)) return null
  const detail: unknown = event.detail
  if (!detail || typeof detail !== 'object') return null
  const text = String((detail as {message?: unknown}).message ?? '').trim().slice(0, KAIS_ASK_MAX)
  return text || null
}

// "Hey Kais'i dene": sohbeti sesli (göz ekranı, dinleyen) modda açar; asistan bileşenine doğrudan bağlanmaz.
export const KAIS_VOICE_EVENT = 'kais:voice'
export function startKaisVoice(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(KAIS_VOICE_EVENT))
}

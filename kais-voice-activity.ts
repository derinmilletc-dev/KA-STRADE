/** Sesli sohbetin GERÇEK etkinlik olayları (yalnız görsel tepki için; ses verisi tutulmaz, gönderilmez).
 *  input  = konuşma tanıyıcı kullanıcının sesinden yeni bir sonuç üretti (SpeechRecognition onresult).
 *  output = Kais'in sesli okuması başladı ya da bir kelime sınırına geldi (SpeechSynthesisUtterance start/boundary).
 *  Tarayıcı konuşma sentezi ses akışını dışarı vermediği için genlik ölçülemez; bu olaylar gerçek konuşma ilerleyişidir.
 *  Bazı sesler boundary olayı üretmez: o durumda görsel tepki yalnız konuşma durumuna göre sakin kalır (taklit edilmez). */
export type VoiceActivitySource = 'input' | 'output'
export const VOICE_ACTIVITY_EVENT = 'kais:voice-activity'

export function emitVoiceActivity(source: VoiceActivitySource) {
  if (typeof window === 'undefined') return
  try { window.dispatchEvent(new CustomEvent<VoiceActivitySource>(VOICE_ACTIVITY_EVENT, {detail: source})) } catch { /* görsel olay; sesli sohbeti asla etkilemez */ }
}

import { currentLang, type Lang } from './i18n'

type Pool = {any: string[]; morning: string[]; afternoon: string[]; evening: string[]; night: string[]}

const TR: Pool = {
  any: ['Kahve ve KaisTrade vakti mi?', 'Yine buradayız, hoş geldin.', 'Bugün neye bakıyoruz?', 'Hazırsan, başlayalım.', 'Geri döndün, iyi ki.',
    'Piyasalar ne diyor bakalım?', 'Nasıl gidiyor bugün?', 'Bir göz atalım mı?', 'Sakin kafayla, adım adım.', 'Tekrar merhaba.',
    'Grafikler seni bekliyordu.', 'Bot çalışıyor, sen rahat ol.', 'Su içtin mi? Şimdi bakalım.', 'Acele yok, buradayız.'],
  morning: ['Günaydın, kahven hazır mı?', 'Güne güzel başla.', 'Sabah sabah burada, helal.', 'Erken kalkan piyasayı yakalar.'],
  afternoon: ['Tünaydın, günün nasıl geçiyor?', 'Öğleden sonra molası mı?', 'Gün ortası, bir kontrol zamanı.', 'Çayını al, bakalım.'],
  evening: ['İyi akşamlar, günün özeti zamanı.', 'Akşam çayı ve grafikler.', 'Gün biterken bir bakalım.', 'Akşam keyfi, sakin kafa.'],
  night: ['Gece kuşu, hoş geldin.', 'Hâlâ ayakta mısın? Biz de.', 'Gece vardiyası mı?', 'Piyasa uyumaz ama sen biraz dinlen.'],
}

const EN: Pool = {
  any: ['Coffee and KaisTrade time?', 'Back again, welcome.', 'What are we looking at today?', 'Ready when you are.', 'You\'re back, glad to see you.',
    'What are the markets saying?', 'How\'s it going today?', 'Shall we take a look?', 'Calm mind, one step at a time.', 'Hello again.',
    'The charts were waiting for you.', 'The bot is running, take it easy.', 'Had some water? Now let\'s look.', 'No rush, we\'re here.'],
  morning: ['Good morning, is your coffee ready?', 'Start the day right.', 'Up early, nice.', 'The early bird catches the market.'],
  afternoon: ['Good afternoon, how\'s your day going?', 'Afternoon break?', 'Midday, time for a check-in.', 'Grab your tea, let\'s see.'],
  evening: ['Good evening, time for the daily recap.', 'Evening tea and charts.', 'Day\'s winding down, let\'s look.', 'Evening mood, calm mind.'],
  night: ['Night owl, welcome.', 'Still up? Us too.', 'Night shift?', 'Markets never sleep, but you can rest a bit.'],
}

const LAST_KEY = 'kt_greeting_last'

/** Saate göre uygun havuzdan, bir öncekinden farklı bir cümle seçer. */
export function pickGreeting(lang: Lang = currentLang(), now: Date = new Date(), random: () => number = Math.random): string {
  const pool = lang === 'tr' ? TR : EN
  const hour = now.getHours()
  const part = hour >= 5 && hour < 12 ? pool.morning : hour >= 12 && hour < 17 ? pool.afternoon : hour >= 17 && hour < 22 ? pool.evening : pool.night
  const all = [...pool.any, ...part]
  let last = ''
  try { last = window.localStorage.getItem(LAST_KEY) || '' } catch { /* depolama kapalı olabilir */ }
  const choices = all.filter(item => item !== last)
  const picked = choices[Math.floor(random() * choices.length)] ?? all[0]
  try { window.localStorage.setItem(LAST_KEY, picked) } catch { /* ignore */ }
  return picked
}

import { currentLang, type Lang } from './i18n'

type Pool = {any: string[]; morning: string[]; afternoon: string[]; evening: string[]; night: string[]
  friday: string[]; weekend: string[]; newYear: string[]; winter: string[]; comeback: string[]
  moodUp: string[]; moodDown: string[]; moodCalm: string[]; moodWild: string[]}
export type GreetingKind = 'morning' | 'afternoon' | 'evening' | 'night' | 'newYear' | 'winter' | 'mood'
export type Mood = 'up' | 'down' | 'calm' | 'wild'

const TR: Pool = {
  any: ['Kahve ve KaisTrade vakti mi?', 'Yine buradayız, hoş geldin.', 'Bugün neye bakıyoruz?', 'Hazırsan, başlayalım.', 'Geri döndün, iyi ki.',
    'Piyasalar ne diyor bakalım?', 'Nasıl gidiyor bugün?', 'Bir göz atalım mı?', 'Sakin kafayla, adım adım.', 'Tekrar merhaba.',
    'Grafikler seni bekliyordu.', 'Bot çalışıyor, sen rahat ol.', 'Su içtin mi? Şimdi bakalım.', 'Acele yok, buradayız.'],
  morning: ['Günaydın, kahven hazır mı?', 'Güne güzel başla.', 'Sabah sabah burada, helal.', 'Erken kalkan piyasayı yakalar.'],
  afternoon: ['Tünaydın, günün nasıl geçiyor?', 'Öğleden sonra molası mı?', 'Gün ortası, bir kontrol zamanı.', 'Çayını al, bakalım.'],
  evening: ['İyi akşamlar, günün özeti zamanı.', 'Akşam çayı ve grafikler.', 'Gün biterken bir bakalım.', 'Akşam keyfi, sakin kafa.'],
  night: ['Gece kuşu, hoş geldin.', 'Hâlâ ayakta mısın? Biz de.', 'Gece vardiyası mı?', 'Piyasa uyumaz ama sen biraz dinlen.'],
  friday: ['Hayırlı cumalar.', 'Cuma geldi, haftayı güzel kapatalım.', 'Haftanın son iş günü, sakin kalalım.'],
  weekend: ['İyi hafta sonları, acele yok.', 'Hafta sonu keyfi, bir göz atıp çıkalım.', 'Dinlenmeyi de ihmal etme, hafta sonu bu.'],
  newYear: ['Yeni yıl yaklaşıyor, iyi ki buradasın.', 'Yeni yılın güzel başlasın.', 'Mutlu yıllar, hoş geldin.'],
  winter: ['Kış havası, sıcak bir şeyler al.', 'Dışarısı soğuk, burası sıcak.', 'Kar yağarken grafikler de bakalım.'],
  comeback: ['Seni özlettik, hoş geldin.', 'Uzun zaman oldu, iyi ki döndün.', 'Nerelerdeydin? Hoş geldin.'],
  moodUp: ['Piyasada yeşil bir gün, keyfini sakin çıkar.', 'Bugün hava güneşli, yine de ölçülü ol.'],
  moodDown: ['Bugün piyasa biraz kırmızı, sakin kal.', 'Düşüş günleri de geçer, acele etme.'],
  moodCalm: ['Piyasa bugün sakin.', 'Sakin bir gün, sakin bir kafa.'],
  moodWild: ['Piyasa bugün hareketli, acele etme.', 'Sert dalgalar var, sakin kalmak sende.'],
}

const EN: Pool = {
  any: ['Coffee and KaisTrade time?', 'Back again, welcome.', 'What are we looking at today?', 'Ready when you are.', 'You\'re back, glad to see you.',
    'What are the markets saying?', 'How\'s it going today?', 'Shall we take a look?', 'Calm mind, one step at a time.', 'Hello again.',
    'The charts were waiting for you.', 'The bot is running, take it easy.', 'Had some water? Now let\'s look.', 'No rush, we\'re here.'],
  morning: ['Good morning, is your coffee ready?', 'Start the day right.', 'Up early, nice.', 'The early bird catches the market.'],
  afternoon: ['Good afternoon, how\'s your day going?', 'Afternoon break?', 'Midday, time for a check-in.', 'Grab your tea, let\'s see.'],
  evening: ['Good evening, time for the daily recap.', 'Evening tea and charts.', 'Day\'s winding down, let\'s look.', 'Evening mood, calm mind.'],
  night: ['Night owl, welcome.', 'Still up? Us too.', 'Night shift?', 'Markets never sleep, but you can rest a bit.'],
  friday: ['Happy Friday.', 'Friday\'s here, let\'s close the week well.', 'Last working day of the week, stay calm.'],
  weekend: ['Happy weekend, no rush.', 'Weekend mode, a quick look and we\'re off.', 'Remember to rest, it\'s the weekend.'],
  newYear: ['New year is coming, glad you\'re here.', 'Wishing you a great new year.', 'Happy New Year, welcome.'],
  winter: ['Winter mood, grab something warm.', 'Cold outside, warm in here.', 'Snow is falling, let\'s check the charts.'],
  comeback: ['We missed you, welcome back.', 'It\'s been a while, glad you\'re back.', 'Where have you been? Welcome.'],
  moodUp: ['A green day on the markets, enjoy it calmly.', 'Sunny out there, stay measured anyway.'],
  moodDown: ['Markets are a bit red today, stay calm.', 'Down days pass too, no rush.'],
  moodCalm: ['Markets are quiet today.', 'A calm day, a calm mind.'],
  moodWild: ['Markets are lively today, don\'t rush.', 'Big waves out there, staying calm is up to you.'],
}

const LAST_KEY = 'kt_greeting_last'
const VISIT_KEY = 'kt_last_visit'
const COMEBACK_DAYS = 3
let sessionGapDays: number | null = null

/** Son ziyaretten bu yana kaç gün geçti. Oturum başına bir kez okunur, sonra ziyaret zamanı güncellenir. */
export function daysSinceLastVisit(now: Date = new Date()): number {
  if (sessionGapDays !== null) return sessionGapDays
  let gap = 0
  try {
    const saved = Number(window.localStorage.getItem(VISIT_KEY))
    if (saved > 0) gap = Math.floor((now.getTime() - saved) / 86_400_000)
    window.localStorage.setItem(VISIT_KEY, String(now.getTime()))
  } catch { /* depolama kapalı olabilir */ }
  sessionGapDays = gap
  return gap
}

/** Piyasa ruh hali, BTC'nin 24 saatlik değişiminden. Yatırım tavsiyesi değil, yalnız hava durumu gibi bir ton. */
export function moodFromChange(changePercent: number): Mood {
  const abs = Math.abs(changePercent)
  if (abs >= 5) return 'wild'
  if (abs < 1.5) return 'calm'
  return changePercent > 0 ? 'up' : 'down'
}

function dayPart(hour: number): 'morning' | 'afternoon' | 'evening' | 'night' {
  return hour >= 5 && hour < 12 ? 'morning' : hour >= 12 && hour < 17 ? 'afternoon' : hour >= 17 && hour < 22 ? 'evening' : 'night'
}

export type Greeting = {text: string; kind: GreetingKind}

/** Önce uzun aradan dönüş, sonra özel gün (yarı yarıya), sonra saate göre; bir öncekinden farklı bir cümle seçer. */
export function pickGreeting(opts: {lang?: Lang; now?: Date; random?: () => number; mood?: Mood | null; gapDays?: number} = {}): Greeting {
  const lang = opts.lang ?? currentLang()
  const now = opts.now ?? new Date()
  const random = opts.random ?? Math.random
  const pool = lang === 'tr' ? TR : EN
  const part = dayPart(now.getHours())
  const month = now.getMonth(), day = now.getDate(), weekday = now.getDay()
  const gap = opts.gapDays ?? daysSinceLastVisit(now)
  let last = ''
  try { last = window.localStorage.getItem(LAST_KEY) || '' } catch { /* ignore */ }
  const choose = (list: string[]) => {
    const options = list.filter(item => item !== last)
    return options[Math.floor(random() * options.length)] ?? list[0]
  }
  let result: Greeting
  const newYear = (month === 11 && day >= 27) || (month === 0 && day <= 3)
  const winter = month === 11 && day >= 15 && !newYear
  if (opts.mood) {
    const list = opts.mood === 'up' ? pool.moodUp : opts.mood === 'down' ? pool.moodDown : opts.mood === 'wild' ? pool.moodWild : pool.moodCalm
    result = {text: choose(list), kind: 'mood'}
  } else if (gap >= COMEBACK_DAYS) result = {text: choose(pool.comeback), kind: part}
  else if (newYear && random() < 0.6) result = {text: choose(pool.newYear), kind: 'newYear'}
  else if (winter && random() < 0.4) result = {text: choose(pool.winter), kind: 'winter'}
  else if (weekday === 5 && random() < 0.4) result = {text: choose(pool.friday), kind: part}
  else if ((weekday === 0 || weekday === 6) && random() < 0.4) result = {text: choose(pool.weekend), kind: part}
  else result = {text: choose([...pool.any, ...pool[part]]), kind: part}
  try { window.localStorage.setItem(LAST_KEY, result.text) } catch { /* ignore */ }
  return result
}

// "Hey Kais" uyanma sözü: saf yardımcılar (React/DOM yok, birim testli).

/** Ses tanımanın "Kais" adını yazdığı yaygın biçimler (Kais, Kayış, Kaiz, Kays, Kaes, Ka is …). */
const NAME = String.raw`(?:ka[iİıI][sSşŞzZ]|kay[iİıI][sSşŞzZ]|ka[eE][sSzZ]|kay[sSzZ]|ka[ -][iİıI][sSşŞ]|kay[ -][iİıI][sSşŞ]|keys|kace)`
/** "Hey" ile birlikte gelince kabul edilen geniş yazılışlar (Kaiz, Kay, Kes, Case, Kış, Keyis …). Yalnız "hey" önekiyle: tek başına yanlış uyanmasın. */
const LOOSE_NAME = String.raw`(?:ka+[iıİy]*[sşzSŞZ]+|kay[iıİ]*[sşz]?|ke+[iy]*[sşz]+|ca[sz]e|kai|kei|ky[iı]?[sşz]|k[iıİ][sşz]|kaya)`
const HEY = String.raw`(?:hey|hay|hi|ey|he|hej|selam|merhaba)`
const WAKE = new RegExp(String.raw`^\s*(?:${HEY}[\s,]+)?${NAME}(?![\p{L}\p{N}])[\s,.:!?-]*(.*)$`, 'iu')
const WAKE_LOOSE = new RegExp(String.raw`^\s*${HEY}[\s,]*${LOOSE_NAME}(?![\p{L}\p{N}])[\s,.:!?-]*(.*)$`, 'iu')

/** Duyulan metin uyanma sözüyle başlıyorsa {question} döner (soru yoksa boş metin); değilse null.
 * Birden fazla tahmin (alternatif) verilirse hepsine bakılır; ses tanıma adı çoğu zaman ikinci/üçüncü tahminde doğru yazar. */
export function detectWake(heard: string | readonly string[]): {question: string} | null {
  const candidates = (Array.isArray(heard) ? heard : [heard]) as string[]
  for (const candidate of candidates) {
    const text = String(candidate || '').normalize('NFC')
    const match = WAKE.exec(text) ?? WAKE_LOOSE.exec(text)
    if (match) return {question: match[1].trim()}
  }
  return null
}

export const WAKE_KEY = 'kais-wake-word'
export const VOICE_PARAM = 'kais'
export const VOICE_PARAM_VALUE = 'voice'

/** Adres ?kais=voice ise (ikon kısayolu / Siri) true; parametre adresten silinir ki yenileyince tekrar açılmasın. */
export function takeVoiceLaunch(location: {search: string; pathname: string; hash: string}, replace: (url: string) => void): boolean {
  const params = new URLSearchParams(location.search)
  if (params.get(VOICE_PARAM) !== VOICE_PARAM_VALUE) return false
  params.delete(VOICE_PARAM)
  const query = params.toString()
  replace(`${location.pathname}${query ? `?${query}` : ''}${location.hash}`)
  return true
}

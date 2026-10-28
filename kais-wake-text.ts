// "Hey Kais" uyanma sözü: saf yardımcılar (React/DOM yok, birim testli).

/** Ses tanımanın "Kais" adını yazdığı yaygın biçimler (Kais, Kayış, Kaiz, Kays, Kaes, Ka is …). */
const NAME = String.raw`(?:ka[iİıI][sSşŞzZ]|kay[iİıI][sSşŞzZ]|ka[eE][sSzZ]|kay[sSzZ]|ka[ -][iİıI][sSşŞ]|kay[ -][iİıI][sSşŞ]|keys|kace)`
/** Cümlenin BAŞINDA "hey/hay/selam … Kais" ya da yalnız "Kais" aranır; cümle ortasında geçen ad uyandırmaz. */
const WAKE = new RegExp(String.raw`^\s*(?:(?:hey|hay|hi|ey|selam|merhaba)[\s,]+)?${NAME}(?![\p{L}\p{N}])[\s,.:!?-]*(.*)$`, 'iu')

/** Duyulan metin uyanma sözüyle başlıyorsa {question} döner (soru yoksa boş metin); değilse null. */
export function detectWake(heard: string): {question: string} | null {
  const match = WAKE.exec(String(heard || '').normalize('NFC'))
  if (!match) return null
  return {question: match[1].trim()}
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

/** Hukuki metinlerdeki yer tutucuların gerçek değerleri. Üç alan da doldurulunca Gizlilik Politikası ve Hesap Silme
 *  sayfasındaki köşeli parantezli yer tutucuların yerine otomatik geçer; boş bırakılan alanın yer tutucusu metinde kalır.
 *  Değerler site sahibi tarafından doğrulanmadan YAZILMAMALIDIR (hukuki unvan, e-posta, saklama süreleri). */
export const LEGAL_CONTACT = {
  /** Gizlilik/silme taleplerinin gönderileceği e-posta adresi. */
  privacyEmail: '',
  /** Veri sorumlusunun hukuki unvanı (şirket kurulmadıysa gerçek kişinin adı). */
  controller: '',
  /** Kayıt kategorilerine göre saklama süreleri (tek cümle). */
  retention: '',
}

const PLACEHOLDERS: ReadonlyArray<readonly [string, keyof typeof LEGAL_CONTACT]> = [
  ['[GİZLİLİK E-POSTASI]', 'privacyEmail'],
  ['[VERİ SORUMLUSU / HUKUKİ UNVAN]', 'controller'],
  ['[SAKLAMA SÜRELERİ]', 'retention'],
]

export function fillLegalPlaceholders(text: string, values: Record<keyof typeof LEGAL_CONTACT, string> = LEGAL_CONTACT): string {
  let result = text
  for (const [token, key] of PLACEHOLDERS) {
    const value = values[key].trim()
    if (value) result = result.split(token).join(value)
  }
  return result
}

/** Metinde hâlâ doldurulmamış yer tutucu kaldı mı (yayın öncesi kontrol için). */
export function unfilledLegalPlaceholders(text: string): string[] {
  return PLACEHOLDERS.map(([token]) => token).filter(token => text.includes(token))
}

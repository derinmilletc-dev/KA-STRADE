import {translateText} from '../../i18n'

// Onay yazısı: büyük/küçük harf ve Türkçe i/ı/İ/I farkından bağımsız karşılaştırılır.
// İngilizce arayüzde kutunun içindeki örnek yazı çevrilmiş görünür (ör. "START LIVE GRID"); kullanıcı
// ekranda gördüğünü yazınca da kabul edilir. Güvenlik değişmez: yalnız beklenen yazının kendisi veya
// onun ekranda görünen çevirisi geçerlidir.
const fold = (value: string) => value.trim().toLocaleUpperCase('tr-TR').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/İ/g, 'I').replace(/\s+/g, ' ')

export function sameConfirm(typed: string, expected?: string): boolean {
  if (!expected) return false
  const t = fold(typed)
  if (!t) return false
  if (t === fold(expected)) return true
  let shown = expected
  try { shown = translateText(expected) } catch { /* çeviri yoksa özgün yazı */ }
  return shown !== expected && t === fold(shown)
}

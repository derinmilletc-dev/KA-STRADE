/** Google Play (TWA) uygulaması içinde miyiz? Yalnızca Android uygulamasında satın alma yüzeylerini gizlemek için kullanılır.
 *  Algılama: TWA açılışının referrer'ı `android-app://<paket>` olur; Bubblewrap başlangıç adresine `?source=android-app` eklenir.
 *  sessionStorage kullanılır: kalıcı (localStorage) olsaydı aynı Chrome'daki normal web oturumlarına sızardı. */
const KEY = 'kt_android_app'

export function isAndroidApp(): boolean {
  if (typeof window === 'undefined') return false
  try {
    if (window.sessionStorage.getItem(KEY) === '1') return true
  } catch { /* sessionStorage yoksa yalnızca anlık işaretlere bakılır */ }
  const marked = document.referrer.startsWith('android-app://') || new URLSearchParams(window.location.search).get('source') === 'android-app'
  if (marked) { try { window.sessionStorage.setItem(KEY, '1') } catch { /* yok say */ } }
  return marked
}

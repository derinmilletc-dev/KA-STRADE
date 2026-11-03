/**
 * Sayfa yakınlaştırmasını kapatır (uygulama gibi davranır).
 * - Android/Chrome: viewport meta'daki maximum-scale=1, user-scalable=no yeterli.
 * - iOS Safari bu meta'yı iki parmakla yakınlaştırmada yok sayar; gesture olayları engellenir.
 * - Çift dokunarak yakınlaştırma CSS touch-action ile kapanır (index.html).
 * Grafiklerin kendi iki parmak hareketleri (touch olayları) etkilenmez.
 */
export function installNoZoom(): void {
  if (typeof document === 'undefined') return
  const block = (event: Event) => { if (event.cancelable) event.preventDefault() }
  for (const name of ['gesturestart', 'gesturechange', 'gestureend']) document.addEventListener(name, block, {passive: false})
}

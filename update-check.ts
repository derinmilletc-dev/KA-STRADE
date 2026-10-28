// Yeni sürüm denetimi (saf yardımcı, birim testli): sayfadaki ana betik dosyası ile yayındaki index.html'in betiği karşılaştırılır.
// Vite her derlemede dosya adına yeni bir özet ekler (assets/index-XXXX.js); ad değiştiyse yeni sürüm yayındadır.
export function entryScript(html: string): string | null {
  const match = /<script[^>]+type="module"[^>]+src="([^"]*\/assets\/[^"]+\.js)"/i.exec(html) ?? /<script[^>]+src="([^"]*\/assets\/[^"]+\.js)"[^>]*type="module"/i.exec(html)
  return match ? new URL(match[1], 'https://x.invalid').pathname : null
}

export function isNewVersion(current: string | null, deployed: string | null): boolean {
  return Boolean(current && deployed && current !== deployed)
}

export const CONNECTION_REQUEST_TIMEOUT_MS = 70000

export function redactConnectionMessage(message: string, credentials: readonly string[]): string {
  return credentials.filter(value => value.length > 0).sort((left, right) => right.length - left.length).reduce(
    (text, value) => text.split(value).join('[GİZLİ]'), message,
  )
}

export function connectionFailureMessage(error: unknown, credentials: readonly string[]): string {
  if (error instanceof TypeError) return 'Bağlantı servisine ulaşılamadı. İnternet bağlantını kontrol edip tekrar dene.'
  if (error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name)) {
    return 'Binance bağlantı doğrulaması zaman aşımına uğradı. Birkaç dakika sonra tekrar dene; önce bağlantı durumunu yenile.'
  }
  if (error instanceof SyntaxError) return 'Bağlantı servisi geçerli bir yanıt döndürmedi. Birkaç dakika sonra tekrar dene.'
  return redactConnectionMessage(
    error instanceof Error && error.message.trim() ? error.message : 'Binance bağlantısı kurulamadı. Sunucu ve bağlantı durumunu kontrol edin.',
    credentials,
  )
}

export function connectionHttpMessage(status: number, reason: string, credentials: readonly string[]): string {
  if (status >= 500 && status !== 502 && status !== 503) {
    return `Sunucu hatası (HTTP ${status}). Bağlantı şu anda kaydedilemedi veya doğrulanamadı; birkaç dakika sonra tekrar dene.`
  }
  return redactConnectionMessage(`Binance bağlantısı kurulamadı (HTTP ${status}): ${reason}`, credentials)
}

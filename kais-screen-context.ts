// Kais'in ekranda ne gördüğünü bilmesi için: ekranlar seçili coin / zaman dilimi / sekme gibi kısa bilgileri buraya yazar,
// sohbet isteği bunları sayfa bağlamına ekler. Yalnızca ekrandaki seçimler; hesap verisi, anahtar ya da kişisel bilgi yazılmaz.

const parts = new Map<string, string>()
const MAX_PART = 120

const clean = (text: string) => text.replace(/\s+/g, ' ').trim().slice(0, MAX_PART)

export function setKaisScreenContext(key: string, text: string | null) {
  const value = text ? clean(text) : ''
  if (value) parts.set(key, value); else parts.delete(key)
}

/** Ekranda seçili şeyleri tek satırda verir (boşsa boş metin). */
export function readKaisScreenContext(): string {
  return [...parts.values()].join(' · ')
}

/** Üyenin kayıtlı işlem tercihlerini tek satıra çevirir (Kais "benim coinlerim", "her zamanki ayarım" gibi sözleri anlasın). */
export function preferencesLine(prefs: {symbols?: readonly string[]; timeframe?: string; risk_per_trade?: number; trading_mode?: string} | null | undefined): string | null {
  if (!prefs) return null
  const bits: string[] = []
  const symbols = (prefs.symbols ?? []).filter(symbol => /^[A-Z0-9]{1,30}USDT$/.test(symbol)).slice(0, 6)
  if (symbols.length) bits.push(`sevdiği coinler ${symbols.join(',')}`)
  if (prefs.timeframe && /^[0-9]{1,2}[mhdw]$/.test(prefs.timeframe)) bits.push(`zaman dilimi ${prefs.timeframe}`)
  if (typeof prefs.risk_per_trade === 'number' && Number.isFinite(prefs.risk_per_trade)) bits.push(`işlem başı risk ${prefs.risk_per_trade}`)
  if (prefs.trading_mode === 'MANUAL' || prefs.trading_mode === 'AUTO') bits.push(`işlem bölümü ${prefs.trading_mode}`)
  return bits.length ? `Kayıtlı tercihleri: ${bits.join(' · ')}` : null
}

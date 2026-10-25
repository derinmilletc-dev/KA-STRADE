// Kullanıcıya gösterilecek grid uyarıları. Saf fonksiyonlar.
import type {GridResult} from './gridMath'

export type GridAlert = {level: 'bilgi' | 'uyari' | 'kritik'; text: string}
export type FiltersState = 'idle' | 'loading' | 'ok' | 'error'

const RISK_CHECKS = new Set(['exposure', 'loss_per_trade', 'daily_loss', 'portfolio_risk', 'margin', 'positions', 'liquidation'])

// Motorun (ileride canlı) durumunu kullanıcı diline çevirir.
export function engineStatusAlert(status: string, reason = ''): GridAlert | null {
  switch (status) {
    case 'INTERVENTION_REQUIRED': return {level: 'kritik', text: `MÜDAHALE GEREKLİ: grid durumu doğrulanamadı${reason ? ` (${reason})` : ''}. Koruyucu stoplar yerinde tutuluyor; borsayı elle kontrol edin.`}
    case 'BLOCKED': return {level: 'uyari', text: `Grid durduruldu: ${reason || 'tutarsızlık bulundu'}. Yeni emir gönderilmiyor.`}
    case 'EMERGENCY_CLOSED': return {level: 'bilgi', text: 'Acil durdurma tamamlandı: grid pozisyonu kapandı ve borsadan doğrulandı.'}
    case 'EMERGENCY': return {level: 'kritik', text: 'Acil durdurma sürüyor: kapanış henüz doğrulanmadı.'}
    case 'STOPPED': return {level: 'bilgi', text: 'Stop tetiklendi; bekleyen emirler iptal edildi, yeni emir açılmayacak.'}
    case 'PAUSED': return {level: 'bilgi', text: 'Grid durduruldu: yeni giriş yok, açık pozisyon stop ve hedefleriyle duruyor.'}
    default: return null
  }
}

export function buildGridAlerts(input: {result: GridResult; filters: FiltersState; filtersError?: string; openRisk: number | null; hasOpenPositions: boolean}): GridAlert[] {
  const out: GridAlert[] = []
  if (input.filters === 'error') out.push({level: 'uyari', text: `Bağlantı sorunu: borsa kuralları okunamadı${input.filtersError ? ` (${input.filtersError})` : ''}. Plan onaylanmaz.`})
  if (input.hasOpenPositions && input.openRisk === null) out.push({level: 'uyari', text: 'Açık pozisyonlardan en az birinin koruyucu stopu okunamadı; toplam risk bilinmiyor, plan onaylanmaz.'})
  for (const error of input.result.errors) out.push({level: 'uyari', text: error})
  for (const check of input.result.checks) {
    if (check.ok) continue
    if (check.id === 'exchange_filters' && input.filters === 'error') continue   // zaten yukarıda
    const unknown = /okunamadı|bilinmiyor|hesaplanamadı/.test(check.detail)   // aşım değil, doğrulanamadı
    const risky = RISK_CHECKS.has(check.id)
    out.push({level: risky ? 'kritik' : 'uyari', text: `${unknown ? 'Doğrulanamadı' : risky ? 'Risk aşımı' : 'Uyarı'} · ${check.label}: ${check.detail}`})
  }
  return out
}

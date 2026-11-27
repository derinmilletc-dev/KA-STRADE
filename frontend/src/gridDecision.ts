// Grid kurulum ekranının "karar" katmanı: SAF fonksiyonlar, ağ/emir yok. Hesabı değiştirmez; mevcut computeGrid sonucunu
// düz Türkçeye çevirir ve (yalnız limitleri aşan planlar için) limitlere sığan en yakın kademe/marjin kombinasyonunu önerir.
import type {GridInput, GridLimits, GridResult} from './gridMath'
import type {FiltersState} from './gridAlerts'

const GRID_MIN_COUNT = 2, GRID_MAX_COUNT = 20 // gridMath.ts ile aynı (node testinde değer import'u olmasın diye burada)
type ComputeGrid = (input: GridInput, limits: GridLimits) => GridResult

export type GridReason = {key: string; text: string; unknown: boolean}

const UNKNOWN = /okunamadı|bilinmiyor|hesaplanamadı/

/** Başarısız kontrolleri kısa, anlaşılır cümlelere çevirir. Aynı konuyu tekrar eden kontroller tek satıra iner. */
export function explainGrid(result: GridResult, filters: FiltersState = 'ok'): GridReason[] {
  const out: GridReason[] = []
  const seen = new Set<string>()
  const add = (key: string, text: string, unknown = false) => { if (!seen.has(key)) { seen.add(key); out.push({key, text, unknown}) } }
  if (filters === 'error') add('filters', 'Borsa kuralları okunamadı; biraz sonra tekrar dene.', true)
  for (const error of result.errors) add(`err:${error}`, error)
  for (const check of result.checks) {
    if (check.ok) continue
    const unknown = UNKNOWN.test(check.detail)
    switch (check.id) {
      case 'level_size': add('size', 'Her kademe çok küçük kalıyor; kademeyi azalt ya da marjini artır.'); break
      case 'exchange_filters':
        if (filters === 'error') break
        add('size', unknown ? 'Borsa kuralları okunamadı; biraz sonra tekrar dene.' : 'Kademeler borsanın en küçük emir tutarının altında kalıyor.', unknown); break
      case 'daily_loss': case 'portfolio_risk':
        add('daily', unknown ? 'Bugünkü kalan zarar hakkı okunamadı; sayfayı yenileyip tekrar dene.' : 'En kötü zarar, bugünkü kalan zarar hakkını aşıyor.', unknown); break
      case 'loss_per_trade': add('loss', 'En kötü zarar, tek işlem zarar sınırını aşıyor.'); break
      case 'exposure': add('exposure', 'Toplam açık büyüklük sınırı aşılıyor (açık pozisyonlar dahil).'); break
      case 'margin': add('margin', 'Toplam marjin sınırı aşılıyor.'); break
      case 'positions': add('positions', 'Açık pozisyon sayısı sınırda; yeni grid açılamaz.'); break
      case 'liquidation': add('liq', 'Stop, tahmini likidasyona çok yakın; kaldıracı düşür ya da stopu yakınlaştır.'); break
      case 'side': add('side', 'Bazı kademeler fiyatın yanlış tarafında; aralığı fiyatın doğru tarafına çek.'); break
      default: add(check.id, `${check.label}: ${check.detail}`, unknown)
    }
  }
  return out
}

export type GridFix = {count: number; margin: number; changedCount: boolean; changedMargin: boolean}

/** Plan limit aşıyorsa, mevcut kademe/marjine en yakın ve TÜM kontrolleri geçen kombinasyonu bulur (önce marjini koru, sonra kademeyi).
 *  Veri okunamadığı (bilinmiyor) için geçmeyen planlarda öneri yoktur (null). Yalnız öneridir; uygulamak kullanıcının tıklamasıdır. */
export function suggestGridFix(computeGrid: ComputeGrid, input: GridInput, limits: GridLimits): GridFix | null {
  if (computeGrid(input, limits).allOk) return null
  const baseCount = Math.floor(Number(input.count)), baseMargin = Number(input.totalMargin)
  if (!(baseCount >= GRID_MIN_COUNT) || !(baseMargin > 0)) return null
  for (const factor of [1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]) {
    const margin = baseMargin >= 10 ? Math.floor(baseMargin * factor) : Math.floor(baseMargin * factor * 100) / 100
    if (!(margin >= 5)) break
    for (let count = Math.min(baseCount, GRID_MAX_COUNT); count >= GRID_MIN_COUNT; count--) {
      if (factor === 1 && count === baseCount) continue
      if (computeGrid({...input, totalMargin: margin, count}, limits).allOk) return {count, margin, changedCount: count !== baseCount, changedMargin: margin !== baseMargin}
    }
  }
  return null
}

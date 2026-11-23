// Fiyat alarmı için SAF yardımcılar (ağ yok). Alarm emir göndermez; sunucu seviyeyi izler ve bildirir.

export type CbAlert = {id: number; product_id: string; level: string; direction: 'above' | 'below'; ref_price: string}
export type AlertPreset = {pct: number; level: number; dir: 'above' | 'below'}

export const ALERT_STEPS = [1, 3, 5, 10] as const

const pos = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null }
const nice = (value: number) => Number(value.toPrecision(6))

/** Yazılan seviyeyi sayıya çevirir (virgül/nokta); geçersiz ya da ≤0 ise null. */
export function parseAlertLevel(text: string): number | null {
  return pos(String(text).trim().replace(',', '.'))
}

/** Güncel fiyata göre hazır seviyeler: yukarı ve aşağı için ±%1/3/5/10. Fiyat geçersizse boş. */
export function alertPresets(price: number | null): {up: AlertPreset[]; down: AlertPreset[]} {
  const p = pos(price)
  if (p === null) return {up: [], down: []}
  return {
    up: ALERT_STEPS.map(pct => ({pct, level: nice(p * (1 + pct / 100)), dir: 'above' as const})),
    down: ALERT_STEPS.map(pct => ({pct, level: nice(p * (1 - pct / 100)), dir: 'below' as const})),
  }
}

/** Seviyenin güncel fiyata uzaklığı, % olarak (işaretli). Hesaplanamazsa null. */
export function distancePct(level: unknown, price: unknown): number | null {
  const l = pos(level), p = pos(price)
  return l === null || p === null ? null : (l - p) / p * 100
}

/** Yazılan seviye için tahmini yön ve uyarı: fiyata eşit/çok yakınsa kurulamaz. */
export function previewLevel(text: string, price: number | null): {ok: boolean; dir: 'above' | 'below' | null; pct: number | null; hint: string} {
  const level = parseAlertLevel(text)
  if (level === null) return {ok: false, dir: null, pct: null, hint: text.trim() === '' ? '' : 'Geçerli bir fiyat yaz.'}
  const pct = distancePct(level, price)
  if (pct === null) return {ok: true, dir: null, pct: null, hint: ''}
  if (Math.abs(pct) < 0.01) return {ok: false, dir: null, pct, hint: 'Fiyat zaten burada.'}
  return {ok: true, dir: pct > 0 ? 'above' : 'below', pct, hint: pct > 0 ? 'Yukarı çıkınca haber verir.' : 'Aşağı inince haber verir.'}
}

export const pctText = (pct: number | null) => pct === null ? '' : `${pct >= 0 ? '+' : '−'}%${Math.abs(pct).toFixed(Math.abs(pct) < 10 ? 2 : 1)}`

/** Ürüne ait alarmlar (aynı coin altındaki tüm vadeler `root` ile eşleşir). */
export function alertsFor(alerts: readonly CbAlert[], productId: string): CbAlert[] {
  return alerts.filter(item => item.product_id === productId)
}

export type FiredAlert = {id: number; product_id: string; level: string; direction: 'above' | 'below'; trigger_price?: string | null}

/** Çalan alarmın uygulama içi bildirim metni. */
export function firedText(item: FiredAlert): {title: string; body: string} {
  const name = item.product_id.split('-')[0].replace(/USDT$/, '')
  const side = item.direction === 'above' ? 'üstüne çıktı' : 'altına indi'
  return {title: `🔔 ${name} alarmı`, body: `${name} ${Number(item.level)} seviyesinin ${side}${item.trigger_price ? ` (şimdi ${Number(item.trigger_price)})` : ''}.`}
}

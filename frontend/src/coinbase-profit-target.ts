// "X USD kâr olunca otomatik kapat" için SAF yardımcılar (ağ yok). Sunucu net K/Z'yi (komisyon dahil) izler ve kapatır.

export const TARGET_CHIPS = [2, 5, 10, 25] as const

/** Yazılan tutarı sayıya çevirir; geçersiz/sıfır/negatif/çok büyükse null. Virgül ve nokta kabul. */
export function parseTarget(text: string): number | null {
  const n = Number(String(text).trim().replace(',', '.'))
  return Number.isFinite(n) && n > 0 && n <= 1_000_000 ? Math.round(n * 100) / 100 : null
}

/** Hedefe ilerleme: net K/Z hedefin yüzde kaçı (0-100) ve kalan USD. Net bilinmiyorsa null. */
export function targetProgress(net: number | null, target: number | null): {pct: number; left: number} | null {
  if (net === null || target === null || !(target > 0)) return null
  return {pct: Math.max(0, Math.min(100, Math.round(net / target * 100))), left: Math.max(0, Math.round((target - net) * 100) / 100)}
}

/** Sunucu yanıtından (ürün → {target_usd}) bu ürünün hedefi; yoksa null. */
export function targetOf(targets: Record<string, {target_usd?: string} | undefined> | null | undefined, product: string): number | null {
  const n = Number(targets?.[product]?.target_usd)
  return Number.isFinite(n) && n > 0 ? n : null
}

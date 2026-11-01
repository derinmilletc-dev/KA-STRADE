// Sayfa ipuçları: bir bölüme ilk girişte 3-4 kısa baloncuk, sonra bölümdeki "?" tuşuyla tekrar. Saf veri/yardımcılar (React/DOM yok, birim testli).

export const PAGE_TIPS_OPEN_EVENT = 'kais-page-tips:open'

export type PageTip = {
  /** Vurgulanacak öğe: ilk görünür eşleşme alınır; bulunamazsa vurgu olmadan, kart ekranın altında gösterilir. */
  targets: string[]
  title: string
  text: string
}
export type PageTipsDefinition = {id: string; label: string; tips: PageTip[]}

export const PAGE_TIPS: Record<'market' | 'risk', PageTipsDefinition> = {
  market: {id: 'market', label: 'Piyasa listesi', tips: [
    {targets: ['.refMarketSearch > label:not(.refMarketSort)'], title: 'Coin ara', text: 'Coinin adını ya da sembolünü yaz: BTC, ETH, Solana… Liste yazdıkça daralır.'},
    {targets: ['.refMarketFilters'], title: 'Filtrele', text: 'Favoriler, Yükselenler, Düşenler gibi filtrelerle listeyi daralt. Telefonda hepsini görmek için yana kaydır.'},
    {targets: ['.refMarketSort'], title: 'Sırala', text: 'Listeyi skora, 24 saatlik değişime, hacme ya da alfabetik sıraya göre dizebilirsin.'},
    {targets: ['.refMarketRow'], title: 'Coini seç', text: 'Bir coine dokununca grafik ve analiz o coine geçer. Yıldızla favorilerine ekleyebilirsin.'},
  ]},
  risk: {id: 'risk', label: 'Risk Kasası', tips: [
    {targets: ['.rkSummary'], title: 'Bugünkü durumun', text: 'Bugün en fazla ne kadar kaybedebileceğini ve ne kadarını kullandığını burada görürsün. Sınıra gelince bot o gün yeni işlem açmaz.'},
    {targets: ['.rkPresets'], title: 'Hazır ayarlar', text: 'Bir karta dokununca değerler dolar. Sonra istediğin gibi değiştirip kaydedebilirsin.'},
    {targets: ['.v21Settings'], title: 'Risk sınırların', text: 'Günlük zarar limiti, işlem başı kayıp ve açık işlem sayısı gibi sınırları buradan belirlersin. Değişiklik, Kaydet’e basana kadar uygulanmaz.'},
    {targets: ['.rkCalc'], title: 'Pozisyon hesaplayıcı', text: 'İsteğe bağlı: giriş ve stop fiyatına göre ne kadar büyüklükte işlem açabileceğini hesaplar. Emir açmaz.'},
  ]},
}

export const pageTipsKey = (userId: string, id: string) => `kais-page-tips:v1:${userId}:${id}`

/** Bir kutuda sırayla gezinirken sonraki indeks; son adımdan sonra null (bitti). */
export function nextTipIndex(index: number, total: number): number | null {
  return index + 1 < total ? index + 1 : null
}
export function previousTipIndex(index: number): number {
  return Math.max(0, index - 1)
}

/** Kartın nereye konacağı: hedefin altında yer varsa altına, üstünde varsa üstüne, yoksa ekranın altına. */
export function tipPlacement(box: {top: number; height: number} | null, cardHeight: number, viewportHeight: number): 'below' | 'above' | 'dock' {
  if (!box) return 'dock'
  const gap = 20
  if (viewportHeight - (box.top + box.height) >= cardHeight + gap + 8) return 'below'
  if (box.top >= cardHeight + gap + 8) return 'above'
  return 'dock'
}

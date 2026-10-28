// Site turu: saf yardımcılar (React/DOM yok, birim testli). Tur içeriği burada; görünüm SiteTour.tsx'te.

export const TOUR_START_EVENT = 'kais-site-tour:start'
export const TOUR_WAIT_DAYS = 2
export const TOUR_OPEN_ATTRIBUTE = 'data-kt-tour'

export type TourView = 'dashboard' | 'analyst' | 'master-trade' | 'performance' | 'billing'
export type TourStep = {
  id: string
  kicker: string
  title: string
  body: string
  bullets?: string[]
  view?: TourView
  /** Vurgulanacak öğe: ilk görünür eşleşme alınır; bulunamazsa vurgu olmadan, ortada gösterilir. */
  targets?: string[]
  /** Kullanıcı bu Master Trade sekmesine dokunana kadar "İleri" kapalı kalır. */
  press?: 'canli'
  /** Adımın gösterilmesi için Master Trade'de bu sekmede olunmalı. */
  need?: 'canli'
  extra?: 'plans' | 'voice'
}

const TAB_CANLI = ['#master-tab-canli', '#masterTradeTab-canli']
const NAV = '.v26MobileBottomNav'

export function buildTourSteps(premium: boolean): TourStep[] {
  const steps: TourStep[] = [
    {id: 'home', kicker: 'ANA SAYFA', title: 'Her şey buradan başlar',
      body: 'Ana sayfada bakiyeni, günün özetini ve piyasanın durumunu görürsün. Diğer sayfalara alttaki menüden ya da üstteki gezinmeden geçersin.',
      view: 'dashboard', targets: [`${NAV} button:first-child`]},
    {id: 'analysis', kicker: 'ANALİST VE TARAMA', title: 'Coini sen seç, Kais sadeleştirsin',
      body: 'Analist, seçtiğin coin için yönü, güven puanını ve olası hedefleri sade bir dille gösterir. Tarama, piyasadaki fırsatları senin yerine süzer.',
      bullets: ['Kais’e “BTC mi ETH mi?” diye sorup iki coini karşılaştırabilirsin.', 'Skorlar analiz çıktısıdır; kazanç garantisi değildir.'], view: 'analyst'},
  ]
  if (premium) {
    steps.push(
      {id: 'master-press', kicker: 'MASTER TRADE', title: 'Canlı İşlem sekmesine dokun',
        body: 'Master Trade, canlı işlem alanındır. Parlayan “Canlı İşlem” sekmesine dokunarak devam edelim; sen dokunana kadar bekliyorum.',
        view: 'master-trade', targets: TAB_CANLI, press: 'canli'},
      {id: 'master-guide', kicker: 'MASTER TRADE', title: 'Kais Rehber seninle',
        body: 'Rehber; risk, günlük sınır, teminat ve izinleri adım adım hazırlar. Her adımı sen onaylarsın, Kais senin yerine işlem açmaz.',
        bullets: ['Pozisyonlar sekmesinde açık işlemlerini ve kâr/zararını görürsün.', 'Bağlantı sekmesinde Binance anahtarını kaydedersin; para çekme iznini açma.'],
        targets: ['[data-testid="live-guide"]', '#master-panel-canli'], need: 'canli'},
    )
  } else {
    steps.push({id: 'master-locked', kicker: 'MASTER TRADE', title: 'Canlı işlem alanı Premium’da',
      body: 'Master Trade canlı işlem alanıdır ve Master Mode (Premium) üyelere açıktır. İçindeki Kais Rehber; risk, günlük sınır ve izinleri adım adım hazırlar, her adımı sen onaylarsın.',
      view: 'dashboard', targets: [`${NAV} button:nth-child(4)`]})
  }
  steps.push(
    {id: 'risk', kicker: 'RİSK VE PERFORMANS', title: 'Sınırlarını ve geçmişini izle',
      body: 'Risk sayfası pozisyon kontrollerini ve sınırlarını, Performans geçmiş sonuçlarını gösterir. Geçmiş sonuç, gelecek için garanti değildir.',
      view: 'performance'},
    {id: 'plans', kicker: 'PAKETLER', title: premium ? 'Master Mode açık' : 'Ücretsiz mi, Premium mu?',
      body: premium ? 'Premium üyeliğin aktif. Paketler sayfasından planını ve faturalarını yönetebilirsin.' : 'İkisiyle de Kais’i kullanırsın. Fark, ne kadar ve ne derinlikte baktığında:',
      view: 'billing', extra: 'plans'},
    {id: 'kais', kicker: 'KAİS AI', title: 'Sorularını Kais’e sor',
      body: 'Kais; analizleri, piyasayı ve (izin verdiysen) kendi hesabını okuyup sade bir dille anlatır. Yalnız okur: emir açamaz, kapatamaz, ayar değiştiremez.',
      bullets: ['“Bu hafta nasıl gitti?”', '“Neden işlem açmadın?”', '“BTC mi ETH mi?”'], view: 'dashboard',
      targets: ['.v26NavAssistantHost button', '.v26NavAssistantHost', '.assistantLauncher']},
    {id: 'voice', kicker: 'SON ADIM', title: 'Takılırsan Hey Kais de',
      body: 'Bir sorun olursa ya da merak edersen her zaman sorabilirsin. Uygulama açıkken “Hey Kais” demen yeter.', extra: 'voice'},
  )
  return steps
}

/** Hafıza: 'never' (kapattı ya da bitirdi) bir daha sorulmaz; 'later' TOUR_WAIT_DAYS gün sorulmaz. */
export type TourMemory = 'never' | 'later'
export const tourMemoryKey = (userId: string) => `kais-site-tour:v1:${userId}`

export function parseTourMemory(raw: string | null | undefined): {kind: TourMemory; at: number} | null {
  const [kind, at] = String(raw ?? '').split('|')
  return kind === 'never' || kind === 'later' ? {kind, at: Number(at) || 0} : null
}

export function shouldOfferTour(raw: string | null | undefined, now: number): boolean {
  const memory = parseTourMemory(raw)
  if (!memory) return true
  if (memory.kind === 'never') return false
  return !(now - memory.at < TOUR_WAIT_DAYS * 86_400_000 && now >= memory.at)
}

export function firstName(displayName: string | null | undefined): string {
  return String(displayName ?? '').trim().split(/\s+/)[0] ?? ''
}

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
  need?: 'canli' | 'analiz'
  extra?: 'plans' | 'voice'
}

const TAB_CANLI = ['#master-tab-canli', '#masterTradeTab-canli']
const NAV = '.v26MobileBottomNav'

/** Telefon düzeni (alt menü, Menü tuşu) ile bilgisayar düzeni (ana sayfada kartlar, üstte Kais gözü) farklıdır; tur ikisine de doğru anlatır. */
export const PHONE_QUERY = '(max-width: 700px)'
const CARDS = '.v26DashboardChoices'

export function buildTourSteps(premium: boolean, phone = false): TourStep[] {
  const steps: TourStep[] = [
    {id: 'home', kicker: 'ANA SAYFA', title: 'Her şey buradan başlar',
      body: phone
        ? 'Ana sayfada bakiyeni, günün özetini ve piyasanın durumunu görürsün. Diğer sayfalara alttaki menüden geçersin; sağdaki Menü tuşunda Tarama, Performans, Destek gibi sayfalar var.'
        : 'Ana sayfada çalışma alanlarını kartlar halinde görürsün: İşlem, Master Trade, Analist, Tarama, Performans, Destek ve diğerleri. Bir karta tıklayınca o sayfa açılır; geri dönmek için sayfadaki Geri tuşunu kullanırsın. En altta Piyasa Nabzı canlı fiyatları gösterir.',
      view: 'dashboard', targets: phone ? [`${NAV} button:first-child`] : [CARDS]},
    {id: 'analysis', kicker: 'ANALİST VE TARAMA', title: 'Coini sen seç, Kais sadeleştirsin',
      body: `Analist, seçtiğin coin için yönü, güven puanını ve olası hedefleri sade bir dille gösterir. Tarama, piyasadaki fırsatları senin yerine süzer. ${phone ? 'İkisini de alttaki Menü tuşundan açarsın.' : 'İkisi de ana sayfadaki kartlarda.'}`,
      bullets: ['Kais’e “BTC mi ETH mi?” diye sorup iki coini karşılaştırabilirsin.', 'Skorlar analiz çıktısıdır; kazanç garantisi değildir.'], view: 'analyst'},
  ]
  if (premium) {
    steps.push(
      {id: 'studio', kicker: 'ANALİZ STÜDYOSU', title: 'Grafik tam ekranda: Analiz Stüdyosu',
        body: phone
          ? 'Analiz sekmesindeki “Analysis Studio” düğmesi grafiği yatay tam ekranda açar. Düğmeye bas, telefonu yan çevir; kapatınca kaldığın yere dönersin.'
          : 'Analiz sekmesindeki “Analysis Studio” düğmesi grafiği tek tıkla tam ekranda açar. Kapatınca kaldığın yere dönersin.',
        bullets: ['Solda çizim araçları: yatay çizgi, trend çizgisi, fiyat alarmı ve silgi.', 'Üstte coin seçici, zaman dilimi ve göstergeler var; “Kais AI’a Sor” grafiği senin için yorumlar.', 'Analiz paneli giriş, stop ve hedefleri tek yerde toplar. Bunlar analiz çıktısıdır; kazanç garantisi değildir.'],
        view: 'master-trade', targets: ['.kfsStudioBtn'], need: 'analiz'},
      {id: 'studio-trade', kicker: 'ANALİZ STÜDYOSU', title: 'İşlem aç ve canlı takip',
        body: 'Grafikteki “İşlem aç” tuşu Manuel işlem panelini o coinle açar. Emir yine aynı güvenlik adımlarından geçer: dry-run, ARM kilidi ve yazı onayı. Kais senin yerine emir göndermez.',
        bullets: ['Emir gidince “Grafikte canlı takip et” seni stüdyoya geri getirir.', 'O coinde açık pozisyonun varsa grafikte giriş, stop ve hedef çizgileri ile anlık kâr/zarar kartı görünür. Kart yalnız gösterir; pozisyonu kapatmaz.'],
        view: 'master-trade', targets: phone ? ['.refTradeCta'] : ['.refTradeBtn'], need: 'analiz'},
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
      view: 'dashboard', targets: phone ? [`${NAV} button:nth-child(4)`] : [`${CARDS} button:nth-child(2)`]})
  }
  steps.push(
    {id: 'risk', kicker: 'RİSK VE PERFORMANS', title: 'Sınırlarını ve geçmişini izle',
      body: `Risk sayfası pozisyon kontrollerini ve sınırlarını, Performans geçmiş sonuçlarını gösterir. ${phone ? 'İkisi de Menü tuşunun içinde.' : 'İkisi de ana sayfadaki kartlarda.'} Geçmiş sonuç, gelecek için garanti değildir.`,
      view: 'performance'},
    {id: 'plans', kicker: 'PAKETLER', title: premium ? 'Master Mode açık' : 'Ücretsiz mi, Premium mu?',
      body: premium ? 'Premium üyeliğin aktif. Paketler sayfasından planını ve faturalarını yönetebilirsin.' : 'İkisiyle de Kais’i kullanırsın. Fark, ne kadar ve ne derinlikte baktığında:',
      view: 'billing', extra: 'plans'},
    {id: 'kais', kicker: 'KAİS AI', title: 'Sorularını Kais’e sor',
      body: `Kais; analizleri, piyasayı ve (izin verdiysen) kendi hesabını okuyup sade bir dille anlatır. ${phone ? 'Gözü alttaki menünün ortasında bulursun.' : 'Gözü sayfanın sağ üstünde bulursun.'} Yalnız okur: emir açamaz, kapatamaz, ayar değiştiremez.`,
      bullets: ['“Bu hafta nasıl gitti?”', '“Neden işlem açmadın?”', '“BTC mi ETH mi?”'], view: 'dashboard',
      targets: phone ? ['.v26NavAssistantHost button', '.v26NavAssistantHost'] : ['.v26HeaderActions .assistantLauncher', '.assistantLauncher']},
    {id: 'voice', kicker: 'SON ADIM', title: 'Takılırsan Hey Kais de',
      body: phone
        ? 'Bir sorun olursa ya da merak edersen her zaman sorabilirsin. Uygulama açıkken “Hey Kais” demen yeter. Ana ekrana eklediysen ikona basılı tutup “Kais’e sor”u da seçebilirsin.'
        : 'Bir sorun olursa ya da merak edersen her zaman sorabilirsin. Sayfa açıkken “Hey Kais” demen yeter; tarayıcı mikrofon izni sorarsa izin ver.',
      extra: 'voice'},
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

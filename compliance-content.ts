import registrationDocument from './backend/app/legal/registration-2026-11-28.json' with {type:'json'}

export const MARKETING_CONSENT_TEXT = registrationDocument.marketing_text

export const PUBLIC_POLICIES = {
  privacy: {
    title: 'Gizlilik Politikası',
    paragraphs: [
      'Son güncelleme: 28 Kasım 2026. Bu politika, KaisTrade hizmetlerindeki kişisel veri işleme faaliyetleri hakkında bilgilendirmedir. Veri sorumlusu: KaisTrade. Kayıtlı adres, iletişim adresi ve uygulanacak hukuk henüz belirlenmemiştir: Florida, ABD, privacykais@gmail.com, [UYGULANACAK HUKUK].',
      '1. İşlenen veriler: Hesap oluşturma ve hizmet sunma kapsamında ad, e-posta, parola özeti, hesap rolü, e-posta doğrulama durumu, oturum ve güvenlik kayıtları işlenir. Google ile giriş seçildiğinde doğrulanmış kimlik bilgileri kullanılır. Gereksiz kişisel bilgiler talep edilmemelidir.',
      '2. Kullanım amaçları: Kimlik doğrulama, hesap güvenliği, abonelik erişimi, hizmet sunumu, destek, hata inceleme ve kötüye kullanımın önlenmesi için gerekli veriler işlenir. Kullanıcı sözleşmesinin kabulü, isteğe bağlı pazarlama izni veya canlı işlem yetkisi yerine geçmez.',
      '3. Borsa bağlantısı: Binance API anahtarları sunucu tarafındaki şifreli kasada işlenir; ham anahtarlar tarayıcı depolamasında tutulmaz ve herkese açık yanıtlarda gösterilmez. Bağlantı etkinleştirme, emir yetkilendirme ve risk kontrolleri ayrıdır. Para çekme yetkisi açık veya doğrulanamayan LIVE anahtarları kabul edilmez.',
      '4. İşlem verileri: Seçilen özelliklere göre borsa bakiyesi, pozisyon ve emir durumu, risk ayarları, işlem sonuçları ve teknik olay kayıtları işlenebilir. Hesabın kapatılması veya API bağlantısının kaldırılması borsadaki açık pozisyonları kendiliğinden kapatmaz.',
      '5. Abonelik ve ödeme: Abonelik durumu ve ödeme işlemlerine ilişkin gerekli kayıtlar işlenir. Ödemeler kripto para ile, üçüncü taraf ödeme hizmeti NOWPayments (nowpayments.io) üzerinden alınır. Ödeme sayfası NOWPayments'a aittir; cüzdan veya borsa bilgilerin, özel anahtarların ve parolaların KaisTrade tarafından görülmesi ya da saklanması söz konusu değildir. KaisTrade ödeme için sipariş numarasını, tutarı, seçilen kripto varlığı ve ödemenin durumunu kaydeder. Ödemenin işlenmesi ve güvenliği NOWPayments'ın sorumluluğundadır ve NOWPayments'ın kendi gizlilik koşulları ayrıca geçerlidir. Ödeme sonrası üyelik bilgisi e-posta ile bildirilir.',
      '6. Çerezler ve depolama: Oturum, CSRF/OAuth akışı, kimlik doğrulama ve kullanıcı tercihleri için gerekli çerezler ve tarayıcı depolaması kullanılabilir. Bu metin, gerçekte uygulanmayan analiz veya reklam çerezleri için izin alındığı anlamına gelmez. Kullanılan çerezlerin adı, amacı ve süresi envanterde açıklanmalıdır: [ÇEREZ ENVANTERİ].',
      '7. Ticari iletiler: Kampanya, indirim, yeni özellik ve tanıtım iletileri için ayrı, isteğe bağlı onay alınır. Onay vermemek üyelik hizmetlerini kısıtlamaz. Bu onay hesap ayarlarından geri çekilebilir; ileti aboneliğinden çıkma bağlantısı da kullanılabilir. Güvenlik, doğrulama ve hizmetin yürütülmesi için gerekli operasyonel iletiler reklam izninden ayrı değerlendirilir.',
      '8. Onay kayıtları: Sözleşme kabulü ile ticari ileti tercihi ayrı kayıtlarla, sunucunun UTC zamanı, metin sürümü ve metnin SHA-256 özetiyle saklanır. Ticari ileti izninin geri çekilmesi yeni bir olay olarak kaydedilir; önceki sürümlerin onay geçmişi üzerine yazılmaz.',
      '9. Paylaşım ve aktarım: Hizmetin gerektirdiği ölçüde borsa, giriş, ödeme, e-posta ve altyapı sağlayıcılarıyla veri işlenebilir. Ödeme için NOWPayments, borsa bağlantısı için Binance, grafik yapay zekâ analizi için Anthropic ve e-posta gönderimi için e-posta sağlayıcımızla gerekli ölçüde veri paylaşılır. Bu sağlayıcılar yurt dışında olabilir. Aktarımın hukuki dayanakları ve işleme ülkeleri ayrıca belirlenecektir: [AKTARIM DAYANAKLARI].',
      '10. Saklama ve güvenlik: Erişim sınırlaması, şifreli anahtar kasası, oturum doğrulaması ve çok faktörlü kimlik doğrulama gibi teknik önlemler uygulanır; mutlak güvenlik garanti edilmez. Kişisel veriler, hesabın kapatılmasından veya hizmetin sona ermesinden itibaren 3 yıl boyunca saklanır; yasal bir saklama yükümlülüğü yoksa bu sürenin sonunda silinir veya anonim hale getirilir. Yedekler ve üçüncü taraf kayıtları için ayrı saklama koşulları olabilir.',
      '11. Haklar ve başvuru: Uygulanabilir mevzuat kapsamında bilgi alma, erişim, düzeltme, silme ve itiraz gibi haklara ilişkin talepler privacykais@gmail.com üzerinden iletilebilir. Geçerli haklar, başvuru yöntemi ve yanıt süreleri, gerçek veri sorumlusunun statüsüne ve uygulanacak hukuka göre tamamlanmalıdır.',
      '12. Değişiklikler: Politikanın güncelleme tarihi belirtilir. Hukuken gerekli önemli değişiklikler uygun yöntemle duyurulur. Bu taslak, gerekli aydınlatma veya açık rıza yükümlülüklerinin yerine getirildiği iddiası değildir; yayından önce profesyonel hukuki inceleme gerektirir.',
      '13. Grafik yapay zekâ analizi: Kais AI\'a sorarsan grafiğin görüntüsü ve ekranda görünen piyasa bilgisi analiz için yapay zekâ sağlayıcımız Anthropic\'e gönderilir. Görüntü sitemizde saklanmaz.',
    ],
  },
  terms: {
    title: 'Kullanım Şartları',
    paragraphs: registrationDocument.paragraphs,
  },
  risk: {
    title: 'Risk Uyarısı',
    paragraphs: [
      'This is a research and demo-first operating workspace. It is not a guarantee of profit and it does not promise financial returns.',
      'Market data, signal quality, order logic, and execution status can change rapidly. The platform uses fail-closed security gates by default. Live orders are never activated automatically and only proceed after explicit validation and safety checks.',
      'Users must understand that market exposure carries risk, including potential loss of capital. This platform is designed for education, simulation, risk review, and controlled testnet workflows unless a separate live trading authorization is explicitly completed.',
    ],
  },
} as const

export type PublicPolicy = keyof typeof PUBLIC_POLICIES

export function publicPolicyForPath(pathname:string):PublicPolicy|null {
  switch (pathname.replace(/\/$/, '')) {
    case '/privacy': return 'privacy'
    case '/terms': return 'terms'
    case '/risk': return 'risk'
    default: return null
  }
}

import registrationDocument from './backend/app/legal/registration-2026-11-28.json' with {type:'json'}

export const MARKETING_CONSENT_TEXT = registrationDocument.marketing_text

export const PUBLIC_POLICIES = {
  privacy: {
    title: 'Gizlilik Politikası',
    paragraphs: [
      'Son güncelleme: 28 Kasım 2026. Bu politika, KaisTrade hizmetlerindeki kişisel veri işleme faaliyetleri hakkında bilgilendirmedir. Veri sorumlusu: KaisTrade. Kayıtlı adres: Florida, ABD. İletişim: privacykais@gmail.com. Uygulanacak hukuk: Güney Afrika Cumhuriyeti.',
      '1. İşlenen veriler: Hesap oluşturma ve hizmet sunma kapsamında ad, e-posta, parola özeti, hesap rolü, e-posta doğrulama durumu, oturum ve güvenlik kayıtları işlenir. Google ile giriş seçildiğinde doğrulanmış kimlik bilgileri kullanılır. Gereksiz kişisel bilgiler talep edilmemelidir.',
      '2. Kullanım amaçları: Kimlik doğrulama, hesap güvenliği, abonelik erişimi, hizmet sunumu, destek, hata inceleme ve kötüye kullanımın önlenmesi için gerekli veriler işlenir. Kullanıcı sözleşmesinin kabulü, isteğe bağlı pazarlama izni veya canlı işlem yetkisi yerine geçmez.',
      '3. Borsa bağlantısı: Binance API anahtarları sunucu tarafındaki şifreli kasada işlenir; ham anahtarlar tarayıcı depolamasında tutulmaz ve herkese açık yanıtlarda gösterilmez. Bağlantı etkinleştirme, emir yetkilendirme ve risk kontrolleri ayrıdır. Para çekme yetkisi açık veya doğrulanamayan LIVE anahtarları kabul edilmez.',
      '4. İşlem verileri: Seçilen özelliklere göre borsa bakiyesi, pozisyon ve emir durumu, risk ayarları, işlem sonuçları ve teknik olay kayıtları işlenebilir. Hesabın kapatılması veya API bağlantısının kaldırılması borsadaki açık pozisyonları kendiliğinden kapatmaz.',
      '5. Abonelik ve ödeme: Abonelik durumu ve ödeme işlemlerine ilişkin gerekli kayıtlar işlenir. Ödemeler kripto para ile, üçüncü taraf ödeme hizmeti NOWPayments (nowpayments.io) üzerinden alınır. Ödeme sayfası NOWPayments\'a aittir; cüzdan veya borsa bilgilerin, özel anahtarların ve parolaların KaisTrade tarafından görülmesi ya da saklanması söz konusu değildir. KaisTrade ödeme için sipariş numarasını, tutarı, seçilen kripto varlığı ve ödemenin durumunu kaydeder. Ödemenin işlenmesi ve güvenliği NOWPayments\'ın sorumluluğundadır ve NOWPayments\'ın kendi gizlilik koşulları ayrıca geçerlidir. Ödeme sonrası üyelik bilgisi e-posta ile bildirilir.',
      '6. Çerezler ve depolama: Oturum, CSRF/OAuth akışı, kimlik doğrulama ve kullanıcı tercihleri için gerekli çerezler ve tarayıcı depolaması kullanılabilir. Bu metin, gerçekte uygulanmayan analiz veya reklam çerezleri için izin alındığı anlamına gelmez. Sitede analiz, reklam veya izleme çerezi kullanılmaz. Kullanılan çerezler yalnızca giriş ve güvenlik içindir: giriş oturumu çerezi (protrebot_session), Google ile giriş sırasındaki geçici doğrulama çerezleri (protrebot_google_binding, protrebot_google_pending) ve kayıt doğrulama çerezi (__Secure-kaistrade_registration). Bunlar hizmetin çalışması için gereklidir; oturum bittiğinde veya işlem tamamlandığında silinir ya da kısa sürede kendiliğinden sona erer. Tarayıcı depolamasında yalnızca dil, tema ve ekran tercihleri, Kais AI sohbet ve ipucu durumu, grafik çizimleri ve favori listeleri gibi senin ayarların tutulur; bunlar sunucuya reklam amacıyla gönderilmez.',
      '7. Ticari iletiler: Kampanya, indirim, yeni özellik ve tanıtım iletileri için ayrı, isteğe bağlı onay alınır. Onay vermemek üyelik hizmetlerini kısıtlamaz. Bu onay hesap ayarlarından geri çekilebilir; ileti aboneliğinden çıkma bağlantısı da kullanılabilir. Güvenlik, doğrulama ve hizmetin yürütülmesi için gerekli operasyonel iletiler reklam izninden ayrı değerlendirilir.',
      '8. Onay kayıtları: Sözleşme kabulü ile ticari ileti tercihi ayrı kayıtlarla, sunucunun UTC zamanı, metin sürümü ve metnin SHA-256 özetiyle saklanır. Ticari ileti izninin geri çekilmesi yeni bir olay olarak kaydedilir; önceki sürümlerin onay geçmişi üzerine yazılmaz.',
      '9. Paylaşım ve aktarım: Hizmetin gerektirdiği ölçüde borsa, giriş, ödeme, e-posta ve altyapı sağlayıcılarıyla veri işlenebilir. Ödeme için NOWPayments, borsa bağlantısı için Binance, grafik yapay zekâ analizi için Anthropic ve e-posta gönderimi için e-posta sağlayıcımızla gerekli ölçüde veri paylaşılır. Bu sağlayıcılar yurt dışında olabilir. Bu aktarımlar hizmetin sunulması için gereklidir ve ilgili sağlayıcıların kendi sözleşme ve gizlilik koşullarına tabidir.',
      '10. Saklama ve güvenlik: Erişim sınırlaması, şifreli anahtar kasası, oturum doğrulaması ve çok faktörlü kimlik doğrulama gibi teknik önlemler uygulanır; mutlak güvenlik garanti edilmez. Kişisel veriler, hesabın kapatılmasından veya hizmetin sona ermesinden itibaren 3 yıl boyunca saklanır; yasal bir saklama yükümlülüğü yoksa bu sürenin sonunda silinir veya anonim hale getirilir. Yedekler ve üçüncü taraf kayıtları için ayrı saklama koşulları olabilir.',
      '11. Haklar ve başvuru: Uygulanabilir mevzuat kapsamında bilgi alma, erişim, düzeltme, silme ve itiraz gibi haklara ilişkin talepler privacykais@gmail.com üzerinden iletilebilir. Geçerli haklar, başvuru yöntemi ve yanıt süreleri, gerçek veri sorumlusunun statüsüne ve uygulanacak hukuka göre tamamlanmalıdır.',
      '12. Değişiklikler: Politikanın güncelleme tarihi belirtilir. Hukuken gerekli önemli değişiklikler uygun yöntemle duyurulur.',
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
      '1. Kâr garantisi yoktur: KaisTrade bir analiz ve işlem destek platformudur. Kâr vaat etmez, yatırım tavsiyesi vermez. Analizler, sinyaller, yapay zekâ değerlendirmeleri ve geçmiş performans gelecekteki sonuçları garanti etmez.',
      '2. Sermaye kaybı: Kripto para ve özellikle kaldıraçlı vadeli işlemler çok yüksek risk taşır. Yatırdığın paranın tamamını, kaldıraçla bazen daha fazlasını kaybedebilirsin. Kaybetmeyi göze alamayacağın parayla işlem yapma.',
      '3. Canlı işlem sana aittir: Canlı işlemler kendi borsa hesabında, gerçek parayla yapılır. Hangi işlemi, hangi miktarla ve hangi kaldıraçla açacağına sen karar verirsin; kâr ve zararın tamamı sana aittir. Platform emirleri yalnızca senin açıkça verdiğin izinle iletir.',
      '4. Ücretsiz deneme: Deneme süresince canlı hesapta elle işlem açabilirsin; bu hak günde en fazla 5 yeni pozisyonla sınırlıdır. Denemede gerçek para kullanılır, deneme olması riski azaltmaz. Önce Demo hesapta denemeni öneririz.',
      '5. Demo sonuçları: Demo hesap sonuçları gerçek piyasa koşullarını, kayma, gecikme ve işlem maliyetlerini tam yansıtmayabilir. Demo kârı, canlıda aynı sonucun alınacağı anlamına gelmez.',
      '6. Piyasa ve teknik riskler: Fiyatlar çok hızlı değişebilir; borsa, internet, veri sağlayıcı veya platformda kesinti, gecikme ve hata olabilir. Stop ve koruma emirleri her koşulda istenen fiyattan gerçekleşmeyebilir.',
      '7. Otomatik işlem: Otomatik stratejiler ve Auto Trade hata yapabilir, zarar edebilir. Kullandığında ayarları, risk limitlerini ve hesabını düzenli kontrol etmek senin sorumluluğundadır.',
      '8. Güvenlik kapıları: Platform varsayılan olarak güvenli tarafta kalır; canlı emirler kendiliğinden açılmaz, gerekli onay ve kontroller tamamlanınca iletilir. Bu kontroller zarara karşı güvence değildir.',
      '9. Sorumluluk: Platform üzerinden alınan kararların sonuçlarından kullanıcı sorumludur. Gerekirse bağımsız bir mali danışmana başvur.',
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

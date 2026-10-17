// Otomatik üretildi: destek sayfası sıkça sorulan sorular (TR)
export type FaqCategory = 'account' | 'billing' | 'binance' | 'trading' | 'security'
export type Faq = {id: string; category: FaqCategory; q: string; a: string}
export const FAQ_CATEGORIES: {id: 'all' | FaqCategory; label: string}[] = [
  {
    "id": "all",
    "label": "Tümü"
  },
  {
    "id": "account",
    "label": "Hesap ve giriş"
  },
  {
    "id": "billing",
    "label": "Abonelik ve ödeme"
  },
  {
    "id": "binance",
    "label": "Binance bağlantısı"
  },
  {
    "id": "trading",
    "label": "İşlem ve strateji"
  },
  {
    "id": "security",
    "label": "Güvenlik ve bildirim"
  }
]
export const FAQS: Faq[] = [
  {
    "id": "f1",
    "category": "account",
    "q": "Şifremi unuttum, ne yapmalıyım?",
    "a": "Giriş ekranındaki “Şifremi unuttum” bağlantısına tıkla ve e-posta adresini yaz. Sana bir sıfırlama bağlantısı gönderilir. E-posta birkaç dakika içinde gelmezse gereksiz (spam) klasörünü kontrol et. Yine de gelmezse aşağıdaki formdan destek ekibine yaz."
  },
  {
    "id": "f2",
    "category": "account",
    "q": "Doğrulama e-postası gelmedi.",
    "a": "Önce gereksiz (spam) klasörünü kontrol et ve birkaç dakika bekle. Doğrulama ekranındaki “Tekrar gönder” seçeneğiyle yeni e-posta isteyebilirsin. E-posta adresini doğru yazdığından da emin ol."
  },
  {
    "id": "f3",
    "category": "account",
    "q": "İki adımlı doğrulamayı nasıl açarım?",
    "a": "Ayarlar sayfasındaki güvenlik bölümünden iki adımlı doğrulamayı açabilirsin. Kurulum sırasında verilen yedek kodları güvenli bir yere kaydet. Telefonunu kaybedersen hesabına bu kodlarla girebilirsin."
  },
  {
    "id": "f4",
    "category": "account",
    "q": "Hesabımı nasıl kapatırım?",
    "a": "Ayarlar sayfasındaki hesap bölümünden hesabını kapatabilirsin. Açık bir işlemin veya aktif bir aboneliğin varsa önce bunları sonlandırman gerekebilir. Bu işlem geri alınamaz."
  },
  {
    "id": "f5",
    "category": "billing",
    "q": "Ücretsiz deneme nasıl çalışır?",
    "a": "Yeni üyelere 7 günlük ücretsiz deneme sunulur. Deneme süresince Master Trade özelliklerini kullanabilirsin. Süre sonunda abonelik, abonelik sayfasında gösterilen koşullarla otomatik başlar. İstediğin zaman iptal edebilirsin."
  },
  {
    "id": "f6",
    "category": "billing",
    "q": "Aboneliğimi nasıl iptal ederim?",
    "a": "Abonelik sayfasındaki faturalama portalından aboneliğini iptal edebilirsin. İptal ettikten sonra ödediğin dönemin sonuna kadar erişimin devam eder."
  },
  {
    "id": "f7",
    "category": "billing",
    "q": "Ödeme bilgilerim güvende mi?",
    "a": "Ödemeler Stripe üzerinden güvenle işlenir. KaisTrade kart bilgilerini kendi sistemlerinde saklamaz."
  },
  {
    "id": "f8",
    "category": "billing",
    "q": "Ödemem başarısız oldu, ne yapmalıyım?",
    "a": "Abonelik sayfasından ödeme yöntemini güncelle. Ödeme yeniden denenirken Master Trade erişimin geçici olarak devam eder. Güncelleme yapmazsan abonelik sona erebilir."
  },
  {
    "id": "f9",
    "category": "binance",
    "q": "Binance API anahtarımı nasıl bağlarım?",
    "a": "Master Trade içindeki Bağlantı sekmesinden bağlarsın. Binance hesabında yeni bir API anahtarı oluştururken yalnızca Futures işlem iznini aç. Para çekme iznini kesinlikle açma, KaisTrade bu izne ihtiyaç duymaz. Mümkünse Binance tarafında IP kısıtlaması da kullan."
  },
  {
    "id": "f10",
    "category": "binance",
    "q": "Anahtarlarım güvende mi?",
    "a": "API anahtarların sunucuda şifreli olarak saklanır ve ekranda tekrar gösterilmez. Gizli anahtarını hiç kimseyle paylaşma. Destek ekibi senden asla şifre veya gizli anahtar istemez."
  },
  {
    "id": "f11",
    "category": "binance",
    "q": "Demo ile canlı işlem arasındaki fark nedir?",
    "a": "Demo, sanal bakiyeyle çalışır ve gerçek para kullanılmaz. Canlı işlem ise Binance hesabındaki gerçek parayla yapılır. Önce demoda denemeni öneririz. Demo hesapta üst sınır 100 USDT ve 2x kaldıraçtır."
  },
  {
    "id": "f12",
    "category": "binance",
    "q": "Bağlantı kuramıyorum veya API hatası alıyorum.",
    "a": "Anahtarı eksiksiz kopyaladığını ve Futures izninin açık olduğunu kontrol et. Binance tarafında IP kısıtlaması koyduysan geçici olarak kaldırıp tekrar dene. Sorun sürerse hata mesajının ekran görüntüsünü aşağıdaki formdan destek ekibine gönder."
  },
  {
    "id": "f13",
    "category": "trading",
    "q": "Kais Original V2 nasıl çalışır?",
    "a": "Kais Original V2, işlem hacmi yüksek vadeli işlem çiftlerini 15 dakikalık periyotta tarar. 1 saatlik ve 4 saatlik grafiklerin aynı yönü desteklemesini bekler ve belirlenen risk kurallarının dışına çıkmaz. Ayrıntılar için Master Trade içindeki “Strateji nasıl çalışır?” düğmesine bas."
  },
  {
    "id": "f14",
    "category": "trading",
    "q": "Bot neden işlem açmıyor?",
    "a": "Bot yalnızca sinyal kuralları ve risk limitlerinin hepsi sağlandığında işlem açar. Piyasa yatay veya belirsizse, günlük zarar limitine ulaşıldıysa ya da veri eksikse beklemesi normaldir. Botun son kararını Master Trade içinde görebilirsin."
  },
  {
    "id": "f15",
    "category": "trading",
    "q": "Stop-loss nedir, nasıl çalışır?",
    "a": "Stop-loss, işlem açılırken belirlenen zarar sınırıdır. Fiyat bu seviyeye gelirse pozisyon kapanır ve zararın daha fazla büyümesi engellenir. Ani fiyat sıçramalarında kapanış fiyatı belirlenen seviyeden farklı olabilir."
  },
  {
    "id": "f16",
    "category": "trading",
    "q": "Risk Kasası ne işe yarar?",
    "a": "Risk Kasası, işleme girmeden önce en fazla ne kadar kaybetmeyi göze aldığına göre güvenli pozisyon büyüklüğünü hesaplar. Ayrıca günlük zarar ve işlem sayısı için üst sınırlar koymanı sağlar."
  },
  {
    "id": "f17",
    "category": "trading",
    "q": "Kazanç garantisi var mı?",
    "a": "Hayır. Kripto vadeli işlemler yüksek risk taşır ve kazanç garantisi verilmez. Geçmiş performans gelecekteki sonuçların göstergesi değildir. Yalnızca kaybetmeyi göze alabileceğin tutarla işlem yap."
  },
  {
    "id": "f18",
    "category": "security",
    "q": "Bildirimler neden gelmiyor?",
    "a": "Bildirimler Master Trade sayfası açıkken çalışır, sayfa kapalıyken bildirim gelmez. Ayarlar sayfasındaki Bildirimler bölümünden istediğin bildirim türlerini ve tarayıcı bildirimini aç. Tarayıcı izin isterse “İzin ver” seçeneğini seç."
  },
  {
    "id": "f19",
    "category": "security",
    "q": "Destek ekibi şifremi veya gizli anahtarımı isteyebilir mi?",
    "a": "Hayır. KaisTrade ekibi senden hiçbir zaman şifre, gizli anahtar veya iki adımlı doğrulama kodu istemez. Böyle bir talep alırsan yanıt verme ve bize bildir."
  },
  {
    "id": "f20",
    "category": "security",
    "q": "Dili Türkçe veya İngilizce yapabilir miyim?",
    "a": "Evet. Ana sayfadaki dil düğmesiyle Türkçe ve İngilizce arasında geçiş yapabilirsin. Seçimin bu tarayıcıda hatırlanır."
  }
]

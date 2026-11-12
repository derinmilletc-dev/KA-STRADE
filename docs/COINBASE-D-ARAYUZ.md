# Coinbase — D aşaması: arayüz (Binance ile aynı ekran)

Ayrı bir sekme yok. **Aynı iki sayfa** kullanılır; üstte **Borsa: Binance | Coinbase** seçici çıkar. Seçici yalnızca sunucuda Coinbase açıksa ve üye izinliyse görünür (açılışta `/api/coinbase/status` sorulur; 404/401/403 → gizli; 424 yükleme hatası → görünür ve hata yazar).

## Borsa Bağlantıları sayfası
Coinbase seçilince **API anahtarı alanı** gelir: anahtar adı + gizli anahtar (PEM) + onay metni + 2FA. Binance seçiliyse eski anahtar kasası aynen durur.

## Canlı (manuel emir) sayfası
Coinbase seçilince Binance'in **manuel emir sihirbazıyla aynı görünüm** gelir (aynı bileşenler ve stiller, hiçbiri değiştirilmedi):
Ürün → Yön (LONG/SHORT kartları) → Tutar → Stop → Kontrol.
Arka planda Coinbase mantığı çalışır: "Ön kontrol yap" = dry-run (gerçek emir gitmez), sonra onay metniyle "Gerçek emri gönder": piyasa girişi → stop-limit koy → stop'u doğrula; doğrulanamazsa pozisyon kapanır.
Altında vadeli hesap özeti, pozisyonlar ve koruma durumu (stop koy/taşı, azalt, kapat, sahipsiz stop temizliği).

## Güvenlik
- Emir bölümü yalnızca sunucu `orders_enabled: true` derse açılır; yoksa ekran sadece izler.
- Her emir isteği tek seferlik `request_id` taşır. Cevap gelmeden bağlantı koparsa ekran "emrin gidip gitmediği BİLİNMİYOR" der ve durumu hemen yeniler.
- Her hata açık Türkçe; ACİL olanlar kırmızı. Her işlemden sonra ve 30 sn'de bir durum yenilenir.
- Bu sürümde Coinbase için hedef (TP) emri yok; yalnızca zorunlu stop.
- Binance ekranlarına ve botlara dokunulmadı. `TestnetFirstApp.tsx`'te yalnızca seçici ve iki yönlendirme eklendi.

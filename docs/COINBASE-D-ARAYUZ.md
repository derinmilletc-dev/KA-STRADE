# Coinbase — D aşaması: arayüz

Ayrı bir **COINBASE** sekmesi. Sunucuda Coinbase kapalıysa (veya bu üye izinli değilse) sekme **hiç görünmez**: ekran açılışta `/api/coinbase/status` sorar; 404/401/403 → gizli. Yükleme hatası (424) olursa sekme görünür ve hata yazar.

## Ekran
1. **Durum şeridi**: anahtar var mı, emir gönderme açık mı (açıksa "gerçek para" uyarısı), saat farkı.
2. **Bildirim kutusu**: her hata burada, açık Türkçe. `critical` olanlar kırmızı "ACİL". Ön kontrol sebepleri liste halinde.
3. **API anahtarı**: kaydet/sil (onay metni + 2FA). Gizli anahtar kutudan çıkınca temizlenir, hata mesajlarından da silinir.
4. **Vadeli hesap**: alım gücü, marjin, bugünkü K/Z, marjin seviyesi (gün içi/gece, kötü olan), likidasyon tamponu.
5. **Pozisyonlar ve koruma**: her pozisyon için stop durumu (yeşil/sarı/kırmızı); Stop koy/taşı, Azalt, Kapat (her biri onay metniyle); sahipsiz stop temizliği.
6. **Yeni emir**: ürün, yön, stop (zorunlu), teminat, sözleşme sayısı → **Ön kontrol** → plan (en kötü kayıp vurgulu) → onay metni → gönder. Alanlar değişirse ön kontrol sıfırlanır.

## Güvenlik
- Emir bölümü yalnızca sunucu `orders_enabled: true` derse görünür; yoksa ekran sadece izler.
- Her emir isteği tek seferlik `request_id` taşır (çift tıklama korunması). Başarısız olunca yenisi üretilir.
- Cevap gelmeden bağlantı koparsa ekran "emrin gidip gitmediği BİLİNMİYOR" der ve durumu hemen yeniler.
- Her işlemden (başarılı ya da hatalı) sonra pozisyon ve koruma durumu otomatik yenilenir; 30 sn'de bir de yenilenir.
- Binance ekranlarına ve botlara dokunulmadı. `TestnetFirstApp.tsx`'te yalnızca sekme eklendi.

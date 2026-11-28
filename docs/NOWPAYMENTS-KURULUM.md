# Kripto ile otomatik ödeme (NOWPayments) kurulumu

Müşteri "Kripto ile öde"ye basar, NOWPayments sayfasında ödemeyi yapar. Ödeme ağda onaylanınca
NOWPayments sunucumuza imzalı bildirim gönderir ve üyelik 30 gün **kendiliğinden** açılır.
Binance elle ödeme olduğu gibi çalışmaya devam eder.

## Senin yapacakların

1. **Hesap aç:** nowpayments.io'da hesap oluştur (kimlik doğrulama başlangıçta gerekmeyebilir).
2. **Para alacağın cüzdanı ekle** (Settings > Payments / Payout wallet): ödemelerin ineceği adres.
3. **API anahtarı al:** Store Settings > API keys > "Add new key". Anahtarı kopyala.
4. **IPN secret al:** Store Settings > Instant payment notifications > "Generate key". Kopyala.
5. **Veritabanı tablolarını kur** (bir kez):
   `psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/20261128_001_nowpayments.sql`
6. **Sunucuda** `/etc/kaistrade.env` dosyasına ekle:
   ```
   NOWPAYMENTS_API_KEY=...
   NOWPAYMENTS_IPN_SECRET=...
   ```
   `BINANCE_MANUAL_ACCESS_ENABLED=true` açık kalmalı (üyelik kontrolü bu anahtarın altından geçer).
7. Sunucuyu güncelle ve servisi yeniden başlat (açık pozisyon yokken).

## Denemeden canlıya

- Önce `NOWPAYMENTS_SANDBOX=true` ve NOWPayments **sandbox** hesabının anahtarlarıyla dene.
- Gerçek ödeme için sandbox satırını sil, canlı anahtarları yaz.
- Bildirim adresi varsayılan olarak `https://api.kaistrade.com/api/v22/payments/nowpayments/ipn`'dir.
  Sunucun bu adresten dışarıya açık olmalı (Cloudflare/Caddy bu yolu engellememeli).

## Güvenlik kuralları (kodda zorunlu)

- İmzası yanlış veya eksik bildirim reddedilir (401).
- Üyelik yalnız `finished` durumunda, tutar (119.90 veya 39.90 USD) taleple birebir eşleşirse açılır.
- Aynı bildirim tekrar gelse bile dönem **bir kez** verilir.
- Eksik ödeme (`partially_paid`) ve tutar uyuşmazlığı üyelik açmaz; kayıt `mismatch` olarak işaretlenir.
- Ödeme sayfası adresi yalnız `nowpayments.io` alan adlarına açılır.

## Takip

OWNER olarak `GET /api/v22/admin/nowpayments` ile son ödemeleri ve durumlarını görebilirsin
(`mismatch` olanlar elle incelenmelidir). Arayüz ekranı henüz yok.

## Bilinen sınırlar

- Üyelik yenilemesi elle: müşteri her ay yeniden öder (Binance akışıyla aynı).
- Binance elle ödeme onay ekranı, kripto dönemini kendi "üyelik bitişi" hesabında görmez;
  aynı anda iki yöntemle ödeme yapan üyede süreler üst üste biner, toplanmaz.
- Para NOWPayments'ta birikir; cüzdana çekim ayarını NOWPayments panelinden sen yönetirsin.

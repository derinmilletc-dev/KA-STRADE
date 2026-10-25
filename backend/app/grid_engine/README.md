# Grid motoru — Aşama 2 (sahte borsa) ve Testnet adaptörü

**Canlıya, API rotalarına, bota ya da gerçek borsaya BAĞLI DEĞİL.** Canlı adaptör yok. Gerçek para/anahtar yok.

Testler (ağ yok):
- `cd backend && python -B -m unittest tests.test_grid_engine_fake tests.test_grid_testnet_adapter tests.test_grid_filters -v`
- `node frontend/src/grid.test.mts` (hesap, risk, uyarılar; Node 22.18+)

## Kurallar
- Kademe sınırı 5 (önizleme, `manual_grid.py` ve motorla aynı).
- Aynı coinde başka pozisyon VEYA bekleyen emir varsa grid başlamaz.
- Emir kimliği = grid + yön + kademe + rol + sürüm. Güvenlik tek mekanizmaya bağlı değil:
  veritabanında benzersiz kimlik → göndermeden önce kayıt → yanıt kaybında borsadan sorgu →
  belirsiz emir ancak art arda 2 doğrulanmış "yok" sonrası, aynı kimlikle, tek sefer yeniden gönderilir.
  Biten (dolu/iptal) emrin kimliği asla yeniden gönderilmez.
- Koruma sırası: önce stop (yeni stop doğrulanmadan eskisi iptal edilmez), sonra hedef, sonra yeni emir.
  Stop ve hedef miktarı gerçek dolum miktarıdır (kısmi dolumda da).
- Stop kesin reddedilirse (fiyat zaten geçilmiş, -2021) kalan miktar bir kez piyasadan kapatılır; yanıt kaybı/belirsizlikte kapatma yok.
  Başka bir kesin ret olursa girişler iptal edilir, durum INTERVENTION_REQUIRED olur.
- Acil durdur başarı kriteri: pozisyon kapanırsa BORSADAN doğrulanır → EMERGENCY_CLOSED. Doğrulanamazsa
  INTERVENTION_REQUIRED, koruyucu stoplar iptal EDİLMEZ, kullanıcıya müdahale uyarısı. Kapandığı asla varsayılmaz.
- Testnet adresi koda sabit (`https://testnet.binancefuture.com`); kurucuda adres parametresi yok, ortam değişkeni okunmaz,
  her istekte adres yeniden doğrulanır, yönlendirme izlenmez. Anahtar yalnızca BINANCE_TESTNET_API_KEY/SECRET.

## Ekran tarafı (önizleme)
- Borsa kuralları: `GET /api/v25/grid/filters?symbol=` (yalnız herkese açık exchangeInfo, mevcut önbellekli kanal; imzalı istek/anahtar/emir yok).
  Okunamazsa ekran "Bağlantı sorunu" der ve plan ONAYLANMAZ; varsayılan değer uydurulmaz.
- Toplam portföy riski = açık pozisyonların STOPA KADAR zararı + grid'in en kötü zararı ≤ bugün kalan zarar hakkı.
  Herhangi bir açık pozisyonun stopu okunamazsa toplam "bilinmiyor" olur ve plan onaylanmaz.
- Uyarılar (`gridAlerts.ts`): Bağlantı sorunu, Risk aşımı, Doğrulanamadı, MÜDAHALE GEREKLİ (motor durumu → kullanıcı dili).
- Bakım teminatı imzalı veri olduğu için ekrana getirilmedi: likidasyon tahmini hâlâ %0,5 VARSAYIMIDIR. Testnet/canlı öncesi gerçek veriyle değiştirilmeden güvenilir sayılmaz.

## Gün 1 (Testnet) nasıl çalıştırılır
Sunucuda, kendi ekranında (anahtarı kimseye yazma, dosyaya koyma):
```
export BINANCE_TESTNET_API_KEY=...   # Testnet anahtarı, para çekme izni yok
export BINANCE_TESTNET_API_SECRET=...
cd backend && python -B scripts/grid_testnet_day1.py --report-dir ./grid_reports
```
Betik: tek yönlü mod, BTC/ETH kuralları (Testnet), fiyat/pozisyon okuma → (hepsi geçerse) uzak fiyatlı tek emir oluştur, oku, kimlik tekrarı (açıkken),
iptal, kimlik tekrarı (iptal sonrası), temizlik. İlk kontrol başarısızsa emir GÖNDERİLMEZ. Belirsiz yanıtta emir tekrar gönderilmez, kimlikle sorgulanır.
Rapor `grid_reports/` içine .md ve .json yazılır (anahtar içermez). Atlanan maddeler (sunucu yolu, açık pozisyon stopu) ELLE doğrulanır.

## Test planı (zorunlu; biri kırılırsa ilerleme yok)
Altı senaryo: (1) yanıt kaybı (2) yeniden başlatma (3) kısmi dolum (4) stop tetiklenmesi (5) bağlantı kopması (6) acil durdurma.
Dört güvenlik düzeltmesi: (a) acil durdurma başarı kriteri = borsadan doğrulama, yoksa INTERVENTION_REQUIRED + stoplar kalır
(b) emir kimliği çok katmanlı (kimlik tekrarını reddetmeyen / biten kimliği yeniden kullanan borsa varyantları dahil)
(c) kısmi dolumda %30/%70/%100 hiçbir an korumasız yok, stop+hedef = gerçek dolum (d) Testnet adresi kilidi.
Ek: aynı coinde pozisyon/bekleyen emir → başlamaz; stop kesin ret ≠ yanıt kaybı; çift kapanış yok.

## Testnet'te DOĞRULANACAK varsayımlar (henüz doğrulanmadı)
1. Açık emrin kimliği tekrar kullanılınca -4116 dönüyor mu; BİTEN (dolu/iptal) emrin kimliği yeniden kullanılabiliyor mu?
2. Stop (STOP_MARKET, reduceOnly) fiyat geçilmişse -2021 mi veriyor?
3. get_order "yok" için -2013, iptalde bilinmeyen emir için -2011 doğru mu?
4. HTTP 503 / -1007 (durum belirsiz) davranışı ve sonradan sorguda emrin bulunması.
5. Tek yönlü (one-way) mod açık mı (`assert_one_way_mode`), reduceOnly + kısmi dolum davranışı.
6. Fiyat/miktar adımı ve en düşük emir değeri (`symbol_filters`); gerçek bakım teminatı tablosu.

## Testnet'te çalıştırılacak sıra
Gün 1 küçük denemeler → Gün 2 altı zorunlu senaryo (yanıt kaybı, yeniden başlatma, kısmi dolum, stop, kopma, acil durdurma) →
Gün 3 kesintisiz çalışma (defter↔borsa karşılaştırması). Başarısız test / emir uyuşmazlığı / açıklanamayan pozisyon farkı varsa ilerleme yok.

## Henüz yok
Gerçek bakım teminatı tablosu, motor durumunun ekrana canlı akışı (motor henüz hiçbir yere bağlı değil),
kademe dolunca yeniden kurma, canlı adaptör (yazılmayacak).

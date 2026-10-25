# Grid motoru — Aşama 2 (sahte borsa) ve Testnet adaptörü

**Canlıya, API rotalarına, bota ya da gerçek borsaya BAĞLI DEĞİL.** Canlı adaptör yok. Gerçek para/anahtar yok.

Testler (ağ yok): `cd backend && python -B -m unittest tests.test_grid_engine_fake tests.test_grid_testnet_adapter -v`

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
Kullanıcıya uyarı gösterimi, borsa filtrelerinin/teminat tablosunun ekrana taşınması, açık pozisyonların stopa kadar zararının
portföy riskine eklenmesi, kademe dolunca yeniden kurma, canlı adaptör (yazılmayacak).

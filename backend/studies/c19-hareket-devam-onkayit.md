# C19 – "Hareket Başladıktan Sonra Gir, Hızlı Kâr Al" · ÖN KAYIT

Sürüm: c19-onkayit-v1 · Hazırlanma: 2026-11-09 · Dal: `feat/kais-farmer`
Durum: ONAYLANDI ve KİLİTLENDİ (2026-11-09 08:02 UTC+2). Veri indirilmedi, sonuç görülmedi.

Bu belge, veri indirilmeden ve hiçbir sonuç görülmeden yazıldı. Onaydan sonra SHA-256 değeri
`c19-hareket-devam-onkayit.sha256` dosyasına yazılır ve kilitlenir. Kilitten sonra her kural sabittir;
değişiklik gerekirse yeni sürüm açılır, gerekçesi ve o ana kadar görülen veri yazılır.

---

## 0. Soru ve kesin sınırlar

**Soru:** Bir coin zaten sert yükseliyor ya da düşüyorsa, hareket başladıktan **sonra** yönünde girip
kısa sürede küçük kâr almak; masraf, kayma ve funding çıkarıldıktan sonra kâr bırakıyor mu?

Bu çalışma yükseliş/düşüşü önceden TAHMİN etmez. Hareket olduktan sonra tepki verir.

Önceki çalışmalarla farkı: C16, C17, Analist V2 ve C18 yalnız 4 büyük coinde (BTC, ETH, SOL, BNB) yapıldı.
Hızlı hareket eden küçük/orta coinler hiç ölçülmedi; bu çalışma onları ölçer.

Kesin sınırlar:
- Canlı emir yok. Üretim kodu değişmez. `KAISTRADE_FARMER_LIVE` ve `KAISTRADE_FARMER_TPM_VERIFIED` KAPALI. KAIS FARMER LIVE = BLOCKED.
- C14 OOS (2026-06-01→2026-09-30) kullanılmaz; 2026-05-31 sonrası hiçbir veri indirilmez/okunmaz.
- Sonuçlara bakıp eşik, giriş, çıkış, evren veya özellik değiştirmek YASAK.
- Sembole özel ayar yok. LONG ve SHORT ayrı optimize edilmez (SHORT aynalanmış veridir).
- Order book yok.

## 1. Evren (sonuç görmeden, sızıntısız seçilir)

- Binance USDⓈ-M **perpetual**, USDT ile işlem gören semboller. Stabil çiftler (USDC, FDUSD vb.) dışlanır.
- Aday liste, `data.binance.vision` arşiv dizininden alınır. Dizin, delist edilmiş sembolleri de içerir (hayatta kalma yanlılığını azaltmak için).
- Seçim: 2025-03 ayı 15m mumlarından hesaplanan **ortalama 24 saatlik quote hacmi** en yüksek **100 sembol**.
  Bu hacim 2025-03'tür; TRAIN kararları 2025-04-13'te başlar, yani seçim geleceği görmez.
- Koşul: sembolün hem TRAIN hem VALID penceresinde veri bulunması **şart değildir**; veri olmayan dönem için olay üretilmez.
  (Böylece sonradan delist olanlar elenmez; ama sonradan listelenenler zaten listede olamaz.)
- Karar anında işlem filtresi: son 24 saatlik quote hacmi ≥ **10 milyon USDT** (bilinen bilgi).
- BTC, ETH, SOL, BNB listede hacme göre doğal olarak yer alır; **ayrı raporlanır** (büyük/küçük coin kırılımı bölüm 8).

## 2. Veri

Kaynak: `data.binance.vision`, USDⓈ-M futures, **yalnız** şu pencereler (ısınma dahil):
2025-03-01 → 2025-09-30 ve 2026-03-01 → 2026-05-31.

| Alan | Kaynak |
| --- | --- |
| 15m mum (hacim, quote hacim) | `klines/15m` aylık |
| 15m MARK fiyat mumu (etiket için) | `markPriceKlines/15m` aylık |
| Funding | `fundingRate` aylık |
| Open Interest | `metrics` günlük (5 dk) |

Veri kuralları (C18 ile aynı): her ZIP `.CHECKSUM` ile doğrulanır; eksik veri doldurulmaz/ikame edilmez; bir sembolün
bir günü %5'ten fazla eksik/geçersizse o sembol-gün çalışmadan çıkarılır; çıkarılan oranlar raporlanır; 2026-06 ve sonrası
dosya adı çıkarsa indirici durur.

## 3. Bölünmeler (C12–C19 ile aynı)

| Dönem | Aralık | Kullanım |
| --- | --- | --- |
| TRAIN | 2025-04-13 → 2025-09-30 (gerçek veri) | ölçüm, walk-forward, kararlılık |
| VALID | 2026-04-13 → 2026-05-31 (gerçek veri) | tek seferlik doğrulama |
| OOS | 2026-06-01 → 2026-09-30 | **kullanılmaz** (tüketildi) |
| FORWARD | 2026-11-10 ve sonrası | gerçek hakem (bölüm 11) |

## 4. "Hareket başladı" olayı (sabit tanım)

Karar anı = 15m mum C'nin **kapanışı**; giriş = C+1 mumunun **açılışı**. Yalnız C ve öncesi bilinir.

ATR = C mumunun ATR14 (Wilder), mutlak değer. Hacim ölçüsü = son 4 mumun (C-3..C) toplam quote hacmi.

**Olay E1 (LONG, yükseliş patlaması)** — hepsi birden:
1. Son 4 mumun net değişimi: `close(C) - open(C-3) ≥ 2.5 × ATR`.
2. Hacim patlaması: son 4 mum quote hacmi ≥ **3 ×** önceki 7 günün (672 mum, C-4 ve öncesi) 4-mumluk hacim **medyanı**.
3. Hâlâ itiyor: `close(C)` son 4 mumun [low, high] aralığının **üst %25**'inde.
4. Likidite: 24 saatlik quote hacmi ≥ 10 milyon USDT.

**SHORT:** aynı kurallar aynalanmış veriyle (düşüş patlaması).

**Olay E2 = E1 ve OI teyidi:** ek olarak `OI değeri(C-5dk) / OI değeri(C-5dk-1 saat) - 1 > 0` (yeni pozisyon açılıyor,
yalnız kapanma/sıkışma değil). OI yalnız `create_time ≤ T-300 s` satırlarından alınır. OI eksikse E2 oluşmaz.

**Tekrar kuralı:** aynı sembolde aynı yönde bir olaydan sonra **16 mum (4 saat)** yeni olay sayılmaz (üst üste binme yok).
Her olay tek işlem.

## 5. Giriş ve çıkış (sabit)

- Giriş: C+1 açılışı, piyasa emri varsayımı.
- Risk birimi 1R = **1 × ATR(C)**.
- **Birincil çıkış X1:** TP = +1.0 ATR, SL = −1.0 ATR, zaman çıkışı = 8 mum (2 saat) sonunda kapanış.
- Her mumda MARK fiyatın yüksek/düşüğü ile dokunuş; aynı mumda ikisi de değerse **STOP** sayılır; TP/SL seviyesinden dolum varsayılır.
- İkincil (yalnız betimleyici, karar vermez): **X2** TP +0.5 / SL −1.0 / 8 mum (çok hızlı kâr); **X3** TP +1.5 / SL −1.0 / 8 mum.

## 6. Maliyet (birincil sabit; hepsi net R'ye girer)

- Taker komisyon: 5 bp / taraf.
- Kayma: **5 bp / taraf** (hareket anında geniş spread varsayımı). Hassasiyet: 10 bp / taraf.
- Funding: pozisyon tutulduğu sürede gerçek funding (yöne göre işaretli).
- Toplam gidiş-dönüş ≈ 20 bp + funding. `net R = (brüt − maliyet) / 1R`, 1R fiyat cinsinden ATR/giriş.

## 7. Etiket ve sızıntı koruması

- Etiket C+1'den itibaren hesaplanır; olay tanımındaki hiçbir değer C+1 ve sonrasına bakmaz.
- Zehirleme testi: C+1 ve sonrası veri bozulunca olay tanımı bit-bit aynı çıkmalı; çıkmazsa çalışma durur.
- 7 günlük medyan için gerekli tüm mumlar mevcut olmalı; eksikse olay oluşmaz (doldurma yok).
- Walk-forward: genişleyen pencere, 2 gün embargo, 3 kat (C17 ile aynı kural).
- OOS reddi: 2026-06-01 ve sonrası zaman damgası/dosya yolu reddedilir.

## 8. Ölçülecekler (karar kuralı ve betimleyiciler)

**Sayım aşaması (sonuç görmeden):** TRAIN ve VALID'de olay sayıları (sembol × ay, LONG/SHORT, E1/E2). **TRAIN'de E1 < 300 veya E2 < 150 ise**
çalışma "yetersiz örnek" olarak durur; eşikler gevşetilmez.

**Birincil karar kümesi:** {E1, E2} × X1 (iki test; Bonferroni için güven aralıkları %97,5).

TRAIN'de şartlar (hepsi birden):
- N ≥ 300 (E2 için ≥ 150).
- Ortalama net R > +0,05 ve gün-küme bootstrap %97,5 aralığı alt sınırı > 0.
- Profit factor ≥ 1,10.
- Walk-forward 3 katın en az 2'sinde beklenti > 0.
- **Yoğunlaşma:** hiçbir sembol olayların %10'unu; en çok kazandıran 5 sembol çıkarılınca ortalama net R hâlâ > 0.

VALID (tek seferlik, TRAIN'e dönmek YASAK): N ≥ 100, ortalama net R > 0, PF ≥ 1,05.

Betimleyiciler (karar vermez): LONG/SHORT ayrı; büyük (BTC/ETH/SOL/BNB) vs diğer coinler; X2/X3; kayma 10 bp hassasiyeti;
aylık sonuçlar; ortalama tutma süresi; kayıp serisi; TP/SL/zaman çıkışı oranları.

**Kıyaslamalar:** (a) aynı sembol ve saat dilimlerinden, aynı sayıda **rastgele giriş** ile aynı çıkış; (b) **ters yön** tanısı (hareketin tersine gir);
(c) olay olmayan aynı koşullu anlar. Olay, rastgeleden ve tersinden anlamlı biçimde iyi olmalı.

## 9. Karar

- TRAIN şartları + VALID şartı geçerse: "geçmiş veride avantaj var" yazılır; **canlı onay verilmez**, bölüm 11'e gidilir.
- Biri geçmezse: "avantaj YOK" ve durulur. Çıkış/giriş/eşik değiştirmek YASAK.
- Yalnız E2 geçerse E2 "OI teyidi işe yarıyor" olarak yazılır; E1'in kararı değişmez.

## 10. Yasaklar

- VALID görüldükten sonra TRAIN'e dönüp kural değiştirmek.
- Sonuca bakıp olay eşiği (2.5 ATR, 3×, %25), çıkış, evren, hacim eşiği değiştirmek.
- Sembole özel ayar; LONG ve SHORT'u ayrı optimize etmek.
- OOS verisini hiçbir amaçla kullanmak.
- Eksik veriyi doldurmak.

## 11. Gerçek hakem: ileriye dönük (FORWARD)

Geçmiş veride geçen hiçbir sonuç "kanıt" sayılmaz. Bölüm 9 geçerse: kural dondurulur, 2026-11-10 sonrası veri **mühürlenir**,
en az **90 gün** sonra tek seferde değerlendirilir; bu sürede ayar yok. Canlıya geçiş yalnız bu sonuçtan sonra ve kullanıcının açık onayıyla.

## 12. Çıktılar

Veri ön kontrol raporu; evren listesi ve hash'i; olay tanım dosyası + hash; sayım raporu; TRAIN, walk-forward, VALID sonuçları;
sızıntı denetimi; kıyaslamalar; nihai rapor (OOS kullanılmadı, canlı emir yok, üretim kodu değişmedi, KAIS FARMER LIVE = BLOCKED).

## 13. Onay için açık noktalar

1. Evren **100 sembol** (hacme göre, 2025-03). Daha fazla/az istenirse kilitten önce söylenmeli.
2. Olay eşikleri (2.5 ATR / 3× hacim / üst %25) ve çıkış (1 ATR TP, 1 ATR SL, 2 saat) **kuramsal sabit seçimlerdir**, sonuca bakılarak seçilmedi; ayarlanmayacak.
3. Kayma 5 bp/taraf varsayımdır; gerçek hareket anlarında daha kötü olabilir (10 bp hassasiyeti raporlanır).
4. Veri indirme yaklaşık 31 bin dosya (çoğu OI günlük dosyası) ve birkaç GB; senin bilgisayarında yapılır, birkaç saat sürebilir.
5. Hayatta kalma yanlılığı tamamen sıfırlanamaz (sonradan delist olan zayıf coinler veride var ama sınırlı); raporda sınır olarak yazılır.

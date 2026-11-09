# C18 – Türev Verisi (Funding + Open Interest, sonra Long/Short + Likidasyon) · ÖN KAYIT

Sürüm: c18-onkayit-v1 · Hazırlanma: 2026-11-09 · Dal: `feat/kais-farmer`
Durum: ONAYLANDI ve KİLİTLENDİ (2026-11-09 07:32 UTC+2). Veri indirilmedi, sonuç görülmedi.

Bu belge, veri indirilmeden ve hiçbir sonuç görülmeden yazıldı. Onaydan sonra SHA-256 değeri
`c18-turev-veri-onkayit.sha256` dosyasına yazılır ve kilitlenir. Kilitten sonra her kural sabittir;
değişiklik gerekirse yeni sürüm açılır, gerekçesi ve o ana kadar görülen veri yazılır.

---

## 0. Amaç ve kesin sınırlar

**Soru:** Funding, Open Interest (OI), Long/Short oranı ve likidasyon verisi, yalnız fiyat verisinin
vermediği yön veya hareket bilgisini taşıyor mu? Taşıyorsa bu bilgi masraftan sonra kâra dönüşüyor mu?

Önceki bulgular (C16, C17, Analist V2): yalnız fiyat verisiyle yön bilgisi ya yok ya masrafı (≈ %0,16)
karşılamayacak kadar küçük. Bu çalışma yeni bilgi kaynaklarını ayrı ayrı ölçer.

Kesin sınırlar:
- Canlı emir yok. Üretim kodu değişmez (`analysis.py`, Original V2, Farmer, emir/risk akışı).
- `KAISTRADE_FARMER_LIVE` ve `KAISTRADE_FARMER_TPM_VERIFIED` KAPALI kalır. KAIS FARMER LIVE = BLOCKED.
- C14 OOS (2026-06-01→2026-09-30) C14 ve C17 tarafından kullanıldı. Bu çalışmada **ayar, seçim veya
  hakem olarak kullanılmaz** ve ayrıca 2026-05-01 sonrası hiçbir veri model seçiminde kullanılmaz.
- Order book verisi bu çalışmada YOK (ağır; geçmiş veri kalitesi ayrı bir problem).
- Sonuçlara bakıp eşik, özellik, etiket veya kural değiştirmek YASAK.

## 1. Veri

Kaynak: `data.binance.vision`, USDⓈ-M futures. Sembol evreni C16/C17 ile aynı (Farmer evreni).

| Grup | Alan | Kaynak |
| --- | --- | --- |
| Taban | 15m mum + MARK fiyat | C16/C17 ile aynı veri seti |
| G1 | Funding oranı | `fundingRate` (8 saatlik) |
| G1 | Open Interest (sayı ve değer) | `metrics` günlük dosyaları (5 dk) |
| G2 | Top trader long/short oranı, genel long/short hesap oranı, taker alış/satış oranı | `metrics` |
| G2 | Likidasyon | `liquidationSnapshot` — **varlığı ve eksiksizliği ön kontrolle doğrulanır** |

Veri ön kontrolü (sonuç görmeden): her grup için sembol × gün kapsamı, boşluklar, tekrarlar,
zaman damgası anlamı. Eksik/yarım veri kuralı: bir günün %5'inden fazlası eksikse o sembol-gün
çalışmadan çıkarılır; çıkarılan oranlar raporlanır. Likidasyon verisi eksik çıkarsa G2'nin likidasyon
kısmı çalışmadan **çıkarılır**, yerine ikame veri konmaz; rapor bunu açıkça yazar.

## 2. Bölünmeler (C12–C17 ile aynı)

| Dönem | Aralık | Kullanım |
| --- | --- | --- |
| TRAIN | 2025-04-01 → 2025-12-31 (gerçek veri 2025-04-13 → 09-30) | seçim, walk-forward, kararlılık |
| VALID | 2026-01-01 → 2026-05-31 (gerçek veri 2026-04-13 → 05-31) | tek seferlik doğrulama |
| OOS | 2026-06-01 → 2026-09-30 | **kullanılmaz** (tüketildi) |
| FORWARD | 2026-11-10 ve sonrası | gerçek hakem, bölüm 10 |

Not: VALID gerçek verisi kısa (~7 hafta). Bu yüzden güç düşüktür; VALID sonucu "kanıt" değil
"tutarlılık kontrolü" olarak raporlanır.

## 3. Etiketler (sabit)

- **Yön etiketi:** C16/C17 ile aynı. Giriş = C+1 açılışı; ATR14 (Wilder); +2 ATR önce mi −2 ATR önce mi;
  aynı mumda ikisi de olursa STOP. LONG/SHORT simetrik, MARK fiyat.
- **Hareket etiketi (ayrı):** karar anından sonraki 4 saatte (16 mum) fiyatın giriş fiyatından
  herhangi bir yöne en az 2 ATR uzaklaşması (high/low MARK). Yön gözetmez.
- Etiket özelliklerden bağımsız hesaplanır; etiket penceresi sonrası 2 günlük embargo.

## 4. Özellikler (sabit liste, sonradan eklenmez)

**Fiyat tabanı (karşılaştırma için):** C16'da kullanılan 64 yapı özelliğinden önceden seçilmiş sabit
küçük küme: ATR yüzdesi, 1/4/24 saatlik getiri, EMA20–EMA200 uzaklığı/ATR, hacim z-skoru, mum gövde/aralık
oranı. (Liste kodda sabitlenir, hash'e girer.)

**G1 (12 özellik):**
1. Funding seviyesi (son açıklanan)
2. Funding değişimi (önceki döneme göre)
3. Funding z-skoru (yalnız geçmiş 30 gün)
4. Funding işareti süreklilik sayısı (kaç dönemdir aynı işaret)
5–7. OI değişimi: 1 saat, 4 saat, 24 saat
8. OI değeri z-skoru (yalnız geçmiş 30 gün)
9. OI–fiyat ayrışması (OI 4s değişimi × fiyat 4s getirisi işareti)
10. OI/hacim oranı
11. Funding × OI değişimi etkileşimi (işaretli)
12. OI 24s değişimi / ATR yüzdesi

**G2 (en çok 8 özellik; likidasyon varsa):** top trader L/S oranı seviyesi ve 4s değişimi, hesap L/S
oranı seviyesi ve değişimi, taker alış/satış oranı, likidasyon hacmi (yönlü) 1s/4s, net likidasyon
dengesi. Likidasyon yoksa ilgili üçü çıkar.

SHORT, aynalanmış veriyle hesaplanır (LONG ve SHORT ayrı optimize edilmez). Sembole özel eşik yok.

## 5. Zaman kuralı (sızıntı koruması)

- Funding: yalnız **açıklandığı andan sonra** bilinir; tahmini/öngörülen funding kullanılmaz.
- `metrics` değerleri: 5 dk aralığın **kapanış zamanı** ≤ karar zamanı − 5 dk olmalı.
- Likidasyon: olay zamanı ≤ karar zamanı.
- Rolling z-skorları yalnız geçmiş pencerelerle. Eğitimde hesaplanan hiçbir istatistik VALID'e sızmaz.
- Sızıntı denetimi: gelecek veriyi zehirleme testi, zaman damgası ihlal sayımı. Geçmezse çalışma durur.

## 6. Aşama 1 – Bilgi var mı? (G1: Funding + OI)

**Ölçüt:** Fiyat tabanına G1 eklenince **ek** bilgi ne kadar? Ayrı ayrı iki hedef için:
(a) yön etiketi (LONG/SHORT), (b) hareket etiketi.

Yöntem (C16 ile aynı disiplin):
1. Tek özellik taraması: gün-küme Spearman ve AUC, Holm + BH-FDR düzeltmesi.
2. TRAIN-A / TRAIN-B kararlılığı: aynı işaret ve |etki| ≥ eşik her iki yarıda.
3. Model: fiyat tabanı vs fiyat tabanı + G1 (ridge-logistic, sabit hiperparametre).
   Genişleyen walk-forward, 2 gün embargo.
4. Metrik: log loss iyileşmesi ve AUC farkı; gün-küme bootstrap güven aralığı (B=2000).
5. Hareket sinyali için: en yüksek %10 sinyalde gerçekleşme oranı / taban oranı (lift).

**Dondurma:** TRAIN sonuçları bittikten sonra model ve özellik tanımı + kod SHA-256 ile dondurulur.

**Durma/Devam kuralı (VALID, tek seferlik, TRAIN'e dönmek YASAK):**
- G1 bilgi taşıyor sayılır **ancak** şunların hepsi sağlanırsa:
  - TRAIN walk-forward log loss iyileşmesi güven aralığı alt sınırı > 0;
  - 3 katın en az 2'sinde iyileşme pozitif;
  - VALID'de iyileşme işareti pozitif ve güven aralığı alt sınırı > −0,0005.
- Yön ve hareket ayrı karar alır. Sadece hareket geçerse "hareket bilgisi var, yön bilgisi yok" yazılır.
- Hiçbiri geçmezse G1 = YOK. Aşama 4'te kullanılmaz.

## 7. Aşama 2 – G2 (Long/Short + Likidasyon)

Aynı yöntem ve aynı kurallar. G2, G1'in **üstüne** eklenir (fiyat + G1 + G2 vs fiyat + G1).
VALID tek bir kez bakılır: G1 ve G2 sonuçları aynı bakışta, ayrı ayrı raporlanır (iki grup için Holm).
G2'nin eklenmesi için G1'in geçmesi şart değildir; fakat G2 fiyat tabanına karşı da ayrıca ölçülür.

## 8. Aşama 3 – "Hareket olacak mı?" ayrı raporu

Yön tahmininden bağımsız. Hedef: hareket etiketi (bölüm 3). Girdi: fiyat tabanı, + G1, + G2.
Raporlanacaklar: AUC, kalibrasyon (eğim, ECE), en yüksek %10/%20 dilimde lift, zaman
kararlılığı (aylık). Bu rapor, yön sonucundan bağımsız olarak üretilir ve yorumlanır.

## 9. Aşama 4 – İşlem simülasyonu (yalnız bilgi çıkarsa)

Koşul: en az bir hedefte aşama 1–2 kuralı geçmiş olmalı. Geçmediyse bu aşama **çalıştırılmaz**.

- Giriş: dondurulmuş sinyalin en yüksek dilimi (eşik TRAIN'den, sabit, sonradan değişmez).
- Çıkış: stop = 2 ATR; TP = 1R/2R/3R (3 ayrı sabit varyant, tek birincil: 2R); zaman çıkışı 24 saat.
- Maliyet: taker 5 bp/taraf, kayma 2 bp/taraf (hassasiyet: 4 bp/taraf), gerçek funding (tutulan
  süre boyunca ödenen/alınan), hepsi birlikte net R'ye girer.
- PASS (C17 ile aynı): N ≥ 100, net beklenti > +0,05R, PF ≥ 1,10, Wilson alt sınırı > %50 (TP yönlü
  setup için), walk-forward 3 katın ≥ 2'sinde beklenti > 0.
- Kıyaslama: aynı sayıda rastgele giriş (N eşleştirilmiş), ters yön tanısı.

## 10. Gerçek hakem: ileriye dönük (FORWARD)

Geçmiş veride geçen hiçbir sonuç "kanıtlanmış" sayılmaz. Aşama 4 PASS olursa:
- Kural dondurulur; 2026-11-10 sonrası veri **mühürlenir** ve tek seferde, en az 90 gün sonra değerlendirilir.
- Bu sürede ayar, eşik, özellik değişikliği YASAK.
- Canlıya geçiş kararı ancak bu sonuçtan sonra ve kullanıcının açık onayıyla verilir.

## 11. Yasaklar

- VALID görüldükten sonra TRAIN'e dönüp kural değiştirmek.
- Sonuca bakıp özellik eklemek/çıkarmak, eşik oynamak.
- Sembole özel ayar; LONG ve SHORT'u ayrı optimize etmek.
- OOS (2026-06→09) verisini hiçbir amaçla kullanmak.
- Order book verisi eklemek.
- Eksik veriyi ikame veriyle doldurmak.

## 12. Çıktılar

- Veri ön kontrol raporu (kapsam, boşluk, çıkarılan oran).
- Özellik tanım dosyası + hash; model dondurma dosyası + hash.
- TRAIN, walk-forward, VALID sonuçları (JSON + özet).
- Sızıntı denetimi raporu.
- "Hareket olacak mı" ayrı raporu.
- Aşama 4 raporu (yalnız koşul sağlanırsa).
- Nihai rapor; şunları açıkça yazar: OOS kullanılmadı, canlı emir gönderilmedi, üretim kodu
  değişmedi, KAIS FARMER LIVE = BLOCKED.

## 13. Onay için açık noktalar

1. Verinin indirilmesi kullanıcının bilgisayarında yapılır (sandbox'ta ağ yok). İndirme betiği hazırlanır;
   çalıştırılması kullanıcıya aittir.
2. Likidasyon verisinin arşivde yeterli olup olmadığı indirmeden önce doğrulanır; eksikse G2 likidasyonsuz yürür.
3. VALID dönemi kısa (~7 hafta). Bu yüzden VALID kararı "tutarlılık kontrolü"dür; kesin kanıt FORWARD'dan gelir.
4. Durma kuralları bu belgededir; sonuç görüldükten sonra gevşetilmez.

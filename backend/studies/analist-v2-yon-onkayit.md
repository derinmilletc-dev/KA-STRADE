# Analist Motoru V2 – Yön Tahmini · ÖN KAYIT

Sürüm: onkayit-v1 · Kayıt tarihi: 2026-11-04 · Dal: `research/analist-v2-yon` · Taban: `main@06c82fd`

Bu belge, veri indirilmeden, model kurulmadan ve hiçbir sonuç görülmeden yazıldı. Buradaki her kural,
sonuçlar görüldükten sonra değiştirilemez. Değişiklik gerekirse yeni bir sürüm (`onkayit-v2`) açılır,
gerekçesi yazılır ve o ana kadar görülen veriler belirtilir. Bu belge araştırma hazırlığıdır;
model geliştirme veya üretime geçiş onayı değildir.

---

## 0. Kapsam ve kesin sınırlar

- Yeni motor tamamen bağımsız bir araştırma katmanıdır. Canlı işlem kararlarına bağlanmaz.
- Dokunulmayacaklar: `backend/app/analysis.py` ve `analyze()`, mevcut Analyst API alanları
  (confidence, long_score, short_score, final_decision_score, smart_score), Original V2,
  Kais Original, Manual, Grid, Binance emir akışı, SL/TP, kaldıraç, pozisyon boyutu, risk rezervasyonları.
- PR birleştirme ve deployment yok.
- Bu sürümde kayıtlı korunan dosya hash'leri (`main@06c82fd`):

| Dosya / klasör | SHA-256 veya git ağaç kimliği |
| --- | --- |
| backend/app/analysis.py | 1000158d247fbce0ecdea39097e9ed18cccf2617c019734b309148ac53855ef2 |
| backend/app/v25_execution.py | f97030fd0d40831e03da0053d9af9c19c7eb83e180f89c80fc4e7ddb0b292c19 |
| backend/app/execution_core.py | e69f4d7cd84fe71f208e28aaffe478b2abe23327fb68292b9ccf2f32dd27dfcd |
| backend/app/strategies/ (ağaç) | b1cf818743d984c50ae7a088af99b018ae519138 |

Araştırmanın her çalıştırması başta ve sonda bu dört değeri doğrular. Ana dal başka çalışmalarla
ilerlerse güncel değerler çalıştırma kaydına yazılır. Araştırma kodunun bu dosyaları değiştirmediği,
araştırma dalının diff'inde bu yolların bulunmamasıyla gösterilir.

## 1. Onaylanan kararlar (2026-11-04)

| # | Karar |
| --- | --- |
| K1 | İleriye dönük test en az 90 gün. Güç analizi yetersiz derse süre, test başlamadan önce en fazla 180 gün olarak sabitlenir. |
| K2 | 2026-05-01…2026-10-31 model seçimi ve hiperparametre belirlemede kullanılmaz. |
| K3 | 2023-06…2023-09 yalnız gerçekten açılmadığı doğrulanırsa mühürlü ikincil test olur. |
| K4 | İlk araştırma yalnız OHLCV ile. Funding, açık pozisyon, long/short oranları dahil değil. |
| K5 | İlk aşamada scikit-learn. LightGBM kurulmaz. |
| K6 | BEKLE hedef oranı %40. |

## 2. K3 denetimi: 2023-06…2023-09 mühürlü test OLMAYACAK

Denetim (2026-11-04, salt okuma): repodaki tüm metin dosyaları 2023-06-01…2023-10-01 aralığına düşen
tarih ve epoch değerleri için tarandı. Bulgular:

- `backend/download_independent_block.py` bu aralığı açıkça `"[2023-06-01, 2026-10-01) UTC INCLUDING WARMUP"`
  olarak Stage 3–6b verisi sayar ve bu aralığa dokunan veriyi reddeder (`OverlapError`).
- `backend/studies/stage5-plan.json` ve `stage6-plan.json`: arşiv 2023-06'dan başlar; bu aylar
  ısınma girdisi olarak yüklenmiş ve işlenmiştir.
- `backend/studies/donchian-holdout-plan.json`: test dönemi 2023-06-01'de biter (bitişik).

Sonuç: projenin kendi kuralı bu ayları "kullanılmış veri" sayıyor. Bu ayların hiç incelenmediğine dair
kesin bir kanıt yok. K3 gereği bu blok **test olarak kullanılmaz**; geliştirme verisinin parçasıdır.
Bu belgedeki tek bağımsız test, bölüm 7'deki ileriye dönük testtir. 2020-10 öncesi dönem de test
olamaz: yalnız ~9 coin var ve sonraki çalışmaların ısınma pencerelerine girip girmediği doğrulanamıyor.

## 3. Veri

### 3.1 İzin verilen alanlar (K4)

- 15m USDⓈ-M perpetual mumlarının yalnız **open, high, low, close, volume** alanları:
  coinin kendisi ve piyasa bağlamı olarak BTCUSDT.
- 1h ve 4h mumlar 15m'den türetilir: yalnız 4 / 16 kesintisiz 15m mumdan oluşan tam gruplar.
- Kullanılmayacaklar (ilk aşama): taker alım hacmi, quote hacmi, işlem sayısı (aynı arşiv dosyasında olsalar da
  OHLCV değiller), funding, açık pozisyon, long/short oranları, mark/premium mumları, bookTicker, aggTrades.

### 3.2 Kaynaklar

| Kullanım | Kaynak |
| --- | --- |
| Geliştirme (tarihsel) | `https://data.binance.vision/data/futures/um/monthly/klines/…/15m/` (checksum'lı, kimliksiz) |
| İleriye dönük test: tahmin anında | Binance USDⓈ-M herkese açık REST mum uç noktası (`GET /fapi/v1/klines`, anahtarsız). Canlı işlem sistemi kullanılmaz. |
| İleriye dönük test: sonuç ve etiket | `https://data.binance.vision/data/futures/um/daily/klines/…/15m/` günlük arşivleri (yalnız sonradan) |

### 3.3 Coin evreni

Analist A/B çalışmasındaki 30 coin: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT,
AVAXUSDT, LINKUSDT, LTCUSDT, DOTUSDT, TRXUSDT, BCHUSDT, NEARUSDT, SUIUSDT, APTUSDT, ARBUSDT, OPUSDT,
FILUSDT, ATOMUSDT, ETCUSDT, UNIUSDT, AAVEUSDT, INJUSDT, TIAUSDT, SEIUSDT, WLDUSDT, 1000PEPEUSDT, ENAUSDT,
HBARUSDT. Bir coin, karar anında en az
400 kapanmış 4h mumu varsa o karar için evrene girer. Bilinen sınırlama: liste bugünkü likit coinlerden
seçildi (hayatta kalma yanlılığı); sonuçlarla birlikte raporlanır.

## 4. Dönemler

| Dönem | Rol |
| --- | --- |
| 2020-01-01…2020-09-30 | Kullanılmaz |
| 2020-10-01…2021-12-31 | Geliştirme, yalnız eğitim: k penceresi (bölüm 5.3); hiçbir katın testi değildir |
| 2022-01-01…2026-04-30 | Geliştirme: walk-forward test katları; her kat kendinden önceki tüm geliştirme verisiyle eğitilir |
| 2026-05-01…2026-10-31 | Hiçbir amaçla kullanılmaz (K2; ayrıca son eğitime de katılmaz) |
| 2026-11-01…S−1 | Kullanılmaz (kayıt ile ileriye dönük test başlangıcı arası) |
| S…S+D | İleriye dönük test. S ve D bölüm 7.4'te tanımlanır. |

Son model (dondurulan) yalnız 2020-10-01…2026-04-30 verisiyle eğitilir.

## 5. Etiketler

### 5.1 Tanımlar

- Karar anı t: her saat başı (UTC, dakika 0). Girdi: açılış zamanı ≤ t − 15 dk olan, yani t anında
  kapanmış 15m mumlar; 1h/4h mumlar için açılış + süre ≤ t.
- Ufuklar: H ∈ {1 saat, 4 saat, 24 saat}; n = 4, 16, 96 adet 15m mum.
- p0 = açılış zamanı t olan 15m mumun açılış fiyatı. pH = açılış zamanı t + H − 15 dk olan mumun kapanışı.
  t…t+H arasında eksik 15m mum varsa örnek dışlanır.
- r = ln(pH / p0).
- σ = (ATR14(15m, t'de kapanmış mumlar) / p0) × √n. ATR, `analysis.atr` ile aynı Wilder tanımıdır
  (fonksiyon yalnız okunur).
- c = 0,0016 (16 bp gidiş-dönüş: taraf başı 5 bp komisyon + 3 bp slippage).

### 5.2 Sınıflar

- LONG: r − c ≥ k_H · σ
- SHORT: −r − c ≥ k_H · σ
- BEKLE: diğer tüm durumlar. Eşdeğer: |r| < c + k_H · σ. BEKLE gerçek bir sınıftır.

k_H ≥ 0 olduğu için LONG ve SHORT aynı anda doğru olamaz.

### 5.3 k_H'nin tek ve sabit değere dönüştürülmesi

- Her ufuk için k_H **bir kez**, yalnız k penceresinde (2020-10-01…2021-12-31) hesaplanır.
- u = (|r| − c) / σ değerleri, penceredeki tüm geçerli (coin, t) örneklerinden havuzlanır (eşit ağırlık).
- k_H = u'nun 40. yüzdeliği (doğrusal ara değer), 4 ondalığa yuvarlanır. Bu, BEKLE payını
  pencerede tam %40 yapan değerdir (K6). Değer negatif çıkarsa k_H = 0 alınır ve raporlanır.
- k_H performansa bakılmadan, tek bir sayı olarak bulunur ve hesaplandığı gün commit'lenir.
- Aynı k_H; tüm walk-forward katlarında, son eğitimde ve ileriye dönük testte değiştirilmeden kullanılır.
  Böylece katlar arasında etiket tanımı değişmez ve birleşik ölçüm tek bir etiket tanımına dayanır.
- Sonraki dönemlerde BEKLE payı %40'tan sapabilir. Bu sapma raporlanır, k düzeltilmez.
- Duyarlılık (yalnız raporlanır, seçimi etkilemez): k_H × 0,5 ve × 1,5 ile etiketlenmiş geliştirme katları.

### 5.4 İkincil çıktılar

- Hareket gücü: z = |r| / σ → Zayıf < 0,5 · Orta 0,5–1,5 · Güçlü ≥ 1,5 (sabit sınırlar). Ayrı bir
  sıralı model araştırılır; bu ön kayıtta başarı kriteri yoktur, betimleyicidir ve üretime aday değildir.
- İşlem uygunluğu: ilk aşamada yalnız betimleyici ve kural tabanlı (Corwin–Schultz OHLC spread tahmini,
  close×volume ile 24 saatlik hacim yüzdeliği, ATR yüzdeliği, en yakın 80 mumluk tepe/dibe uzaklık).
  Başarı kriterine girmez, üretime aday değildir.

## 6. Özellikler ve modeller

### 6.1 Özellik grupları (OHLCV'den)

Tüm özellikler t'de kapanmış mumlardan hesaplanır ve fiyattan bağımsız normalize edilir (ATR, σ, yüzde,
yüzdelik). Ham fiyat kullanılmaz. Pencere uzunlukları 15m mum sayısıdır.

| Grup | Özellikler |
| --- | --- |
| Trend | (kapanış−EMA20)/ATR; (EMA20−EMA50)/ATR; (EMA50−EMA200)/ATR (yalnız ≥ 400 mum varsa, yoksa 0); EMA20 ve EMA50'nin 4 mumluk eğimi/ATR; son EMA20/EMA50 kesişiminden beri geçen mum (en fazla 96); onaylı salınımlarla HH/HL (+1) – LH/LL (−1) yapı puanı; ADX14 |
| Momentum | RSI14 − 50; RSI14'ün 4 mumluk değişimi; MACD(12,26,9) histogramı/ATR ve 4 mumluk değişimi; 1, 4, 16 mumluk log getiri/σ₁ (σ₁ = ATR/kapanış); ivme = son 4 mum getirisi − önceki 4 mum getirisi; uyumsuzluk: fiyat 50 mumluk yeni tepe (dip) yaparken RSI yapmıyorsa −1 (+1), aksi halde 0 |
| Piyasa yapısı | Kırılım: kapanış önceki 80 mumun (son 3 hariç) tepesinin üstünde +1, dibinin altında −1; sahte kırılım: son 4 mumda fitil bu seviyelerin ötesine geçip kapanış içeride kalmışsa yönüne göre ±1; retest: son 16 mumda kırılım olmuş ve fiyat kırılan seviyeye 0,25 ATR yaklaşıp kapanışla tutunmuşsa ±1; (80 mumluk tepe − kapanış)/ATR ve (kapanış − 80 mumluk dip)/ATR; fitil reddi: son 3 mumda (üst fitil − alt fitil)/aralık ortalaması |
| Hacim | RVOL = son mum hacmi / son 96 mumun medyan hacmi; log(20 mum ort. hacim / 100 mum ort. hacim); mum yönü (kapanış−açılış işareti) ile hacmin 20 mumluk Spearman korelasyonu; hacim destekli kırılım = kırılım × (RVOL ≥ 1,5) |
| Volatilite | ATR/kapanış; ATR'nin 300 mumluk yüzdeliği; Bollinger(20,2) genişliğinin 300 mumluk yüzdeliği; genişleme = (son 8 mum ort. aralığı)/(önceki 22 mum ort. aralığı); ani değişim = mutlak(son mum log getirisi) / σ₁ |
| Çoklu zaman dilimi | 1h ve 4h için ayrı ayrı (kendi ATR'leriyle): (kapanış−EMA20)/ATR, (EMA20−EMA50)/ATR, (EMA50−EMA200)/ATR (≥ 400 mum varsa), RSI14 − 50, MACD histogramı/ATR, yapı puanı, ATR yüzdeliği. Bir zaman dilimi < 250 mum ise o zaman diliminin özellikleri 0 ve ayrı bir "eksik" göstergesi 1 olur |
| Piyasa bağlamı | BTCUSDT'nin 15m (kapanış−EMA20)/ATR, (EMA20−EMA50)/ATR, RSI14 − 50, 16 mumluk log getiri/σ₁, ATR yüzdeliği. BTCUSDT'nin kendisi için bu grup 0'dır (U6) |

Onaylı salınım: bir mumun tepesi solundaki 3 mumdan büyük ve sağındaki 3 kapanmış mumdan büyük veya eşitse
(dip için tersi) salınım sayılır; sağdaki 3 mum t'de kapanmış olmalıdır.

Tekrar sayım kuralı: her eğitim katında Spearman |ρ| ≥ 0,85 olan özellikler bir kümedir; her kümeden
tanımı en basit olan, eşitlikte bölüm 6.1 tablosunda önce yazılan tek özellik kalır. Kural performansa bakmaz.

### 6.2 Modeller (K5: yalnız scikit-learn)

| Kod | Model |
| --- | --- |
| B0 | Sabit: eğitimdeki sınıf oranları |
| B1 | Süreklilik: son H süresinin getirisi aynı k_H ve σ kuralıyla sınıfa çevrilir; olasılıklar eğitimde bu sınıfa koşullu sınıf frekanslarıdır |
| B2 | Kural tabanlı: EMA sıralaması + MACD + RSI'nin üç sınıflı hali; olasılıklar eğitimdeki koşullu frekanslardır |
| M1 | `LogisticRegression` çok sınıflı, L2, standartlaştırılmış girdiler; C ∈ {0,01; 0,1; 1} |
| M2 | `HistGradientBoostingClassifier`: max_depth = 3, learning_rate = 0,05, max_leaf_nodes = 15, l2_regularization = 1,0, early_stopping = True |

### 6.3 Walk-forward

- Genişleyen eğitim penceresi 2020-10-01'den başlar. Test katları: 2022-01-01'den başlayan 16 adet 3 aylık
  kat (2022-01…2025-12) ve son kat 2026-01-01…2026-04-30 (4 ay). Toplam 17 kat.
- Her eğitim penceresi zaman sırasıyla üçe bölünür: ilk %70 model eğitimi, sonraki %15 hiperparametre
  seçimi (M1'de C, M2'de erken durdurma), son %15 kalibrasyon. Dilimler arasında H + 24 saat purge bırakılır.
  Hiperparametre ve kalibrasyon dilimleri birbirinden ayrıdır.
- Purge: etiket penceresi [t, t+H] test katının başlangıcını geçen eğitim örnekleri atılır; ayrıca
  eğitim sonu ile test başı arasında en az H + 24 saat boşluk bırakılır.
- Örtüşme: eğitimde her örneğe 1 / (aynı coinde etiket penceresi örtüşen örnek sayısı) ağırlığı verilir.
- Kalibrasyon: çok sınıflı vektör ölçekleme (log-olasılıklar üzerinde çok sınıflı lojistik regresyon)
  yalnız son %15'lik kalibrasyon diliminde kurulur. Son model de aynı bölmeyle, 2020-10-01…2026-04-30
  penceresinde eğitilir.

### 6.4 Seçim (her ufuk için ayrı, yalnız geliştirme katlarında)

1. Ölçüt: 17 katın birleşik dış-örneklem log loss değeri.
2. Aday olmak için B0 ve B1'i geçmek gerekir: log loss iyileşmesinin %95 aralık alt sınırı > 0 (bölüm 8.3).
3. M2 yalnız M1'e göre log loss farkının %95 aralığı sıfırı dışlarsa seçilir; aksi halde M1.
4. P6 (bölüm 8.1) geliştirme katlarında sağlanmalıdır; sağlanmazsa o ufuk için model yoktur.

## 7. İleriye dönük test

### 7.1 Tahmin anında üretim

- Tahminler her saat t için t anında, yalnız herkese açık Binance REST mum verisiyle üretilir.
- Kullanılan her mum için açılış zamanı ≤ t − 15 dk (15m) şartı kodla doğrulanır; şartı sağlamayan mum
  hesaptan atılır. Günlük arşivler tahmin üretiminde kullanılmaz.
- Tahmin kaydı: t, coin, ufuk, üç sınıf olasılığı, hareket gücü, k_H, model sürümü ve kod hash'i,
  kullanılan son mumun açılış zamanı ve OHLCV değerleri, üretim zamanı.
- Kanıt: her saatin kayıtları tek bir dosyada toplanır, SHA-256'sı önceki dosyanın hash'iyle zincirlenir
  ve ayrı bir dala (`research/analist-v2-ileri-kayit`) push edilir. Zaman kanıtı GitHub'ın push olay
  zamanıdır (commit tarihi kanıt sayılmaz). Push olay kimlikleri her gün bir dosyaya yedeklenir.
- Geçerlilik: bir saatin tahminleri ancak push olay zamanı ≤ t + 10 dk ise test örneği sayılır.
  Geç veya eksik saatler dışlanır.
- İş, canlı işlem sisteminden ayrı bir ortamda çalışır; canlı sistemin kodunu, hesabını veya anahtarlarını kullanmaz.

### 7.2 Sonradan doğrulama

- Etiketler ufuk dolduktan sonra yalnız günlük arşivlerden (bölüm 5) hesaplanır.
- Tutarlılık kontrolü: tahminde kullanılan REST mumları arşivdeki aynı mumlarla karşılaştırılır.
  Fark oranı raporlanır. Arşivden yeniden hesaplanan özelliklerle canlı özellikler aynı olmalıdır
  (tolerans 1e-9); uyuşmazlık oranı raporlanır.

### 7.3 Ara bakış yasağı

- Test süresince hiçbir başarı ölçüsü (etiket, doğruluk, log loss vb.) hesaplanmaz ve bakılmaz.
- İzlenen tek şey operasyoneldir: tahmin kapsama oranı, geç kayıtlar, veri hataları.
- Başarı için erken durdurma yoktur.

### 7.4 Başlangıç (S) ve süre (D)

- S = şu üçü tamamlandıktan sonraki ilk UTC 00:00: (a) model ve k_H'nin dondurulup push edilmesi,
  (b) güç analizi ve D'nin push edilmesi, (c) 7 günlük kuru çalıştırma (kuru çalıştırma tahminleri
  test sayılmaz).
- D (K1): geliştirme katlarının dış-örneklem tahminlerinden, gün bazlı blok yeniden örneklemeyle
  simülasyon. Gerçek AUC_LS = 0,53 varsayımıyla 3 ufuk Holm düzeltmesi altında P3'ün (bölüm 8.1)
  geçme olasılığı hesaplanır. D = {90, 120, 150, 180} içinde bu olasılığın ≥ 0,80 olduğu en küçük değer.
  180 günde bile < 0,80 ise D = 180 olur ve test öncesinde "güç yetersiz" olarak kaydedilir.
- Kapsama: geçerli saat oranı %90'ın altına düşerse eksik gün sayısı kadar süre uzatılır. Bu kural
  yalnız kapsama oranına bakar, sonuçlara değil. Altyapı 14 günden fazla aralıksız çalışmazsa test
  geçersiz sayılır ve yeni S ile yeniden başlar.

## 8. Ölçüler ve kriterler

### 8.1 İstatistiksel kriterler (her ufuk ayrı; ileriye dönük testte)

Tahmin edilen sınıf = en yüksek olasılıklı sınıf (argmax).

| # | Kriter | Geçme koşulu |
| --- | --- | --- |
| P1 | Log loss (doğal log; olasılıklar [1e-15, 1−1e-15] aralığına kırpılır), B0'a göre | İyileşme > 0 ve Holm düzeltilmiş tek yönlü p < 0,05 |
| P2 | Log loss, B1'e göre | Aynı |
| P3 | AUC_LS: gerçek etiketi LONG veya SHORT olan örneklerde, skor = P(LONG) − P(SHORT), LONG pozitif sınıf | AUC_LS ≥ 0,53 ve Holm düzeltilmiş tek yönlü p (H0: AUC_LS ≤ 0,50) < 0,05 |
| P4 | LONG precision > LONG taban oranı ve SHORT precision > SHORT taban oranı (taban oranı = test kümesindeki gerçek sınıf payı) | İkisi de > taban oranı ve her biri için Holm düzeltilmiş tek yönlü p < 0,05 |
| P5 | Kalibrasyon (bölüm 8.2) | Her sınıf için ECE ≤ 0,03 ve 0,8 ≤ eğim ≤ 1,2 |
| P6 | Kararlılık (geliştirmede) | 17 katın en az 12'sinde (%70) B0'a göre log loss iyileşmesi |

Bir ufuk ancak P1–P5 ileriye dönük testte ve P6 geliştirmede sağlanırsa "yön tahmini doğrulandı" sayılır.

Raporlanan ama karar vermeyen ölçüler: LONG/SHORT/BEKLE recall, üç sınıflı macro F1, balanced accuracy,
her sınıf için birine karşı diğerleri ROC-AUC, çok sınıflı Brier (Σ_k (p_k − y_k)²), karışıklık matrisi,
güvenilirlik diyagramı, tahmin edilen sınıfa göre ortalama ileri getiri (bp ve σ cinsinden).

### 8.2 Kalibrasyon hesabı (sabit)

- Her sınıf k için birine karşı diğerleri: y_k ∈ {0,1}, p_k = modelin o sınıf olasılığı.
- Kutular: değerlendirme kümesinde p_k'ye göre 10 eşit sayılı kutu. Sıralama kararlıdır
  (eşit p_k'de veri sırası korunur); kutu b = ⌊10 · sıra / N⌋.
- ECE_k = Σ_b (n_b / N) · |ortalama(y_k | b) − ortalama(p_k | b)|.
- Kalibrasyon eğimi_k: y_k ~ a + b · logit(p_k) cezasız lojistik regresyonu; p_k [1e-6, 1−1e-6]
  aralığına kırpılır; eğim = b.
- P5 nokta tahminlerine göre değerlendirilir; %95 aralıklar raporlanır.
- Güvenilirlik diyagramı aynı 10 kutuyu kullanır.
- Olasılık gösterme kuralı: P5 geçilmiş olmalı ve olasılığın düştüğü kutu en az 200 tahmin ve en az
  30 farklı UTC gün içermeli.

### 8.3 Güven aralıkları ve p-değerleri (sabit)

- İki yönlü küme bootstrap: 7 günlük hareketli takvim blokları (dairesel) ve coinler birbirinden bağımsız,
  iadeli olarak yeniden örneklenir. Bir örneğin ağırlığı = (gününün bloklarda seçilme sayısı) ×
  (coininin seçilme sayısı). B = 2000, tohum 20261105.
- %95 aralık: yüzdelik yöntemi (2,5 ve 97,5).
- Tek yönlü p-değeri: istatistiğin sıfır hipotezi sınırında veya ötesinde kaldığı bootstrap
  tekrarlarının oranı; en küçük değer 1/2000.
- Holm düzeltmesi P1–P4 için ayrı ayrı, 3 ufuk üzerinde, α = 0,05.

### 8.4 Ekonomik kriter (ayrı rapor; yön kriterinden bağımsız)

- Kural: tahmin LONG veya SHORT ve o sınıfın olasılığı ≥ τ_H. τ_H, geliştirme katlarının birleşik
  dış-örneklem tahminlerinde LONG/SHORT tahminlerinin en yüksek olasılıklı %20'sini ayıran eşiktir
  ve donma ile birlikte sabitlenir.
- Sonuç: yön × r − c (bp ve σ cinsinden). 24 saatlik ufukta funding K4 gereği modele girmez; sonuçta
  da kullanılmaz ve bu eksiklik raporlanır.
- "Ekonomik avantaj var" yalnız ortalama net sonuç > 0 ve %95 aralık alt sınırı > 0 ise denir.
  Aksi halde sonuç yalnız istatistiksel sunulur; kârlılık iddia edilmez.

## 9. Durma kuralları

| Kod | Ne zaman | Sonuç |
| --- | --- | --- |
| S1 | Sinyal taraması (geliştirme): her özellik grubu tek başına M1 ile (C = 0,1 sabit) walk-forward katlarda ölçülür. Bir ufukta hiçbir grup B0'a göre log loss iyileşmesinin %95 aralık alt sınırı > 0 vermezse | O ufuk "OHLCV ile tahmin edilebilir yön bulunamadı" olarak raporlanır, modellenmez |
| S2 | Bölüm 6.4 sonunda hiçbir ufukta model kalmazsa | İleriye dönük test başlamaz; rapor verilir |
| S3 | İleriye dönük test sırasında | Başarı için erken durma yok; yalnız bölüm 7.4'teki operasyonel kurallar |
| S4 | Test sonrası kriter sağlanmazsa | O ufuk üretime alınmaz. Aynı ileriye dönük veriyle yeniden ayar ve tekrar test yapılmaz; yeni sürüm yeni bir ileriye dönük dönem gerektirir |

## 10. Üretime geçiş

Bu ön kayıt üretime geçişi onaylamaz. Geçiş için şu koşulların tümü ve ayrı bir yazılı onay gerekir:
P1–P5 ileriye dönük testte ve P6 geliştirmede sağlanmış; en az 30 gün gölge mod (hesaplanır, gösterilmez);
canlı ve çevrim dışı özelliklerin ≥ 1.000 kararda aynı olduğu eşitlik testi; eksik veride otomatik BEKLE;
kalibrasyon ve özellik kayması izlemesi ile kill switch; eski Analyst alanlarından, emir akışından,
SL/TP'den, pozisyon boyutundan ve risk rezervasyonundan tam ayrım; otomatik merge olmaması.

## 11. Çözülmemiş metodolojik sorunlar

| # | Sorun | Etki / ne zaman çözülmeli |
| --- | --- | --- |
| U1 | İleriye dönük tahmin işi nerede çalışacak? Binance USDⓈ-M REST, bazı ülkelerden (örneğin ABD IP'leri) erişimi engeller; GitHub Actions çalıştırıcıları ABD'dedir. Canlı sistemden ayrı, erişimi doğrulanmış bir ortam gerekir. | S'den önce; kuru çalıştırmada doğrulanır |
| U2 | Zaman kanıtı GitHub push olaylarına dayanır; Events API kayıtları sınırlı süre tutar. Günlük yedek planlandı ama bağımsız bir zaman damgası hizmeti yok. | S'den önce |
| U3 | Bağımsız tarihsel test yok (bölüm 2). Tüm kanıt tek bir 90–180 günlük ileriye dönük döneme dayanır; tek bir piyasa rejimini temsil eder. | Kalıcı sınırlama; raporlanır |
| U4 | Güç yetersiz olabilir (bölüm 7.4). | D hesaplanınca kesinleşir |
| U5 | Hayatta kalma yanlılığı: coin listesi bugünün likit coinleri. | Kalıcı sınırlama; raporlanır |
| U6 | "Yalnız OHLCV" yorumu BTCUSDT OHLCV'sini piyasa bağlamı olarak içeriyor. Onay gerekir; onaylanmazsa grup çıkarılır. | Geliştirme başlamadan önce |
| U7 | Son model 2026-04-30'a kadarki veriyle eğitilir; S tarihine kadar en az 7 aylık bir boşluk oluşur. Model eskiyebilir. | Bilinçli tercih (K2'nin muhafazakâr uygulaması) |
| U8 | 24 saatlik sonuçlarda funding yok (K4). Ekonomik sonuç bu ufukta iyimser olabilir. | Raporlanır |
| U9 | Corwin–Schultz spread tahmininin bu coinlerdeki doğruluğu bilinmiyor; işlem uygunluğu bu yüzden yalnız betimleyicidir. | İkinci aşama |

## 12. Tutarlılık denetimi (2026-11-04)

Belgenin önceki araştırma planına göre değişen ve kendi içinde kontrol edilen noktaları:

- Mühürlü ikincil test kaldırıldı (bölüm 2). Kriterler yalnız ileriye dönük teste bağlandı (8.1);
  geliştirme verisi 2023-06…09'u içerir (4); "mühürlü blok için purge" kuralı kaldırıldı.
- İleriye dönük test başlangıcı sabit tarih (2026-11-05) değil, dondurma + güç analizi + kuru çalıştırma
  sonrası S'dir (7.4). Model dondurulmadan test başlayamaz.
- k_H tek pencerede, tek sayı (5.3); katlar arası etiket tanımı sabit.
- Hiperparametre seçimi ve kalibrasyon ayrı, purge'lü zaman dilimlerinde (6.3); aynı veri iki iş için kullanılmaz.
- Kat sayısı tarih aralığıyla uyumlu: 16 × 3 ay + son 4 aylık kat = 17 kat; P6 eşiği 12/17 (6.3, 8.1).
- LightGBM (M3) ve funding/açık pozisyon/long-short grubu kaldırıldı (K4, K5). Taker alım oranı özelliği çıkarıldı.
- 2026-05…10 son eğitime de katılmaz (4); K2 ile çelişmez, daha muhafazakârdır.
- ECE, eğim, kutu sayısı, bootstrap ve Holm uygulaması sabitlendi (8.2, 8.3). P1–P4 p-değerine,
  P5 nokta tahminine dayanır; P6 geliştirmede ölçülür.
- Tahmin anında yalnız REST mumları; günlük arşivler yalnız sonuç için (7.1, 7.2).
- Dönem tablosundaki aralıklar örtüşmez; her tarih tek bir role sahiptir. k penceresi yalnız eğitimdir ve
  hiçbir test katına girmez (4).
- Kalan bilinçli gerilim: B1 ve B2 de eğitimdeki koşullu frekanslarla olasılık üretir; bu yüzden P2,
  "trend sürer" varsayımının kalibre edilmiş halini geçmeyi ister (6.2).

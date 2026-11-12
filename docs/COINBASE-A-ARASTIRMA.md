# Coinbase (ABD vadeli) — A Aşaması Araştırma Raporu

Tarih: 2026-11-12
Kapsam: Coinbase Advanced Trade API ile ABD vadeli ürünlerinde SADECE MANUEL işlem.
Bu belge kod değildir. Mevcut sisteme dokunmaz.

## Nasıl okunmalı

Bulguların hepsi Coinbase'in resmi geliştirici sayfalarından alındı (en altta liste).
Sayfalar otomatik özetleme ile okundu. Bu yüzden aşağıdaki işaretlere dikkat:

- **[BELGEDE VAR]** Sayfada açıkça yazıyor.
- **[ÇIKARIM]** Sayfadaki hata kodlarından veya örneklerden çıkardım, açık cümle yok.
- **[BELİRSİZ]** Belgede yok. Canlı testte veya Coinbase'e sorarak netleşmeli.

Önemli iddialar (stop mesafesi, tek bacaklı stop) B/C aşamasından önce ham API şemasından bir kez daha doğrulanmalı.

---

## 0. En önemli uyarılar (özet)

1. **Yanlış belgeye dikkat.** "guides/derivatives/technical" sayfası ABD vadeli (CFM) değil, başka bir ürünü (Deribit tabanlı küresel türevler) anlatıyor. Orada geçen `stop_market`, `otoco`, `reduce` gibi şeyler ABD vadeli için GEÇERLİ DEĞİL. Planı bunlara dayandırmıyoruz.
2. **Sandbox imzayı test etmez.** Sandbox kimlik doğrulaması istemiyor ve cevapları sabit. Vadeli (cfm) uçları da listede yok. İmza doğruluğu ancak gerçek, salt okunur bir anahtarla görülür.
3. **Hata HTTP 200 ile gelebilir.** Emir reddedilince Coinbase `200` döndürüp içinde `success: false` yazıyor. Kod HTTP durumuna bakıp "başarılı" sayarsa sessiz hata olur. Her cevapta `success` alanı zorunlu kontrol edilecek.
4. **`reduce_only` alanı yok.** Emir şemasında bu alan görünmüyor, sadece hata kodlarında geçiyor. Yani stop emri, pozisyon kapandıktan sonra kalırsa ters yönde YENİ pozisyon açabilir. Pozisyon kapanınca açık stop'ların iptali zorunlu olacak (bölüm 4).
5. **Stop, stop-limit.** Düz stop-market yok. Stop tetiklenir ama limit fiyatına ulaşamazsa hiç dolmayabilir. En kötü kayıp hesabı buna göre yapılmalı (bölüm 4).

---

## 1. Kimlik doğrulama

Kaynak: api-key-authentication sayfası. Hepsi **[BELGEDE VAR]**.

- Algoritma ES256. Ed25519 desteklenmiyor.
- Anahtar: `-----BEGIN EC PRIVATE KEY-----` ile başlayan PEM. Satır sonları korunmalı.
- Başlık: `alg=ES256`, `kid=organizations/{org_id}/apiKeys/{key_id}`, `nonce` (rastgele), `typ=JWT`.
- İçerik: `sub` = anahtar adı, `iss` = `"cdp"`, `nbf` = şimdi (saniye), `exp` = `nbf + 120`, `uri` = `METHOD host/path`.
- `uri` örneği: `GET api.coinbase.com/api/v3/brokerage/accounts`. Şema (`https://`) yazılmaz.
- Her istek için YENİ JWT üretilmeli.
- Gönderim: `Authorization: Bearer <JWT>`.
- C# örneği farklı (`coinbase-cloud`, 1 dk). Bunu izlemiyoruz, `cdp` ve 120 sn kullanıyoruz.

Belirsizler:
- **[BELİRSİZ]** Saat kayması toleransı yazmıyor. 401 hata metinleri de yazmıyor. Kayma varsa Coinbase sadece "yetkisiz" diyebilir.
- **[BELİRSİZ]** `uri` içinde sorgu parametreleri (`?product_type=...`) yer alıyor mu? Belge sadece yolu gösteriyor. B aşamasında sorgulu bir uçla (pozisyonlar, emir listesi) gerçek anahtarla denenecek.
- **[BELİRSİZ]** Coinbase'in sunucu saatini veren bir uç var mı? Doğrulamadım. Yoksa kendi sunucu saatimizi dış bir kaynakla karşılaştırırız.

B için sonuç: ES256 imzası yeni paket gerektirmez. Depoda zaten `cryptography` var.

---

## 2. Vadeli hesap, marjin, pozisyon

### Para nasıl hareket ediyor **[BELGEDE VAR]**
- Nakit hep spot hesaba (CBI) yatar.
- Spot'tan vadeli hesaba (CFM) aktarım OTOMATİK. Marjin gerektiğinde taşınır.
- Geri çekmek için "sweep" gerekir. Bu uç `transfer` izni ister.
- **Karar:** Bizim anahtarda `transfer` izni OLMAMALI. B aşamasında anahtarda bu izin varsa kaydı reddedip açıkça yazacağız. (Anahtar izinlerini okuyan bir uç olup olmadığını doğrulamadım **[BELİRSİZ]**.)

### Bakiye özeti: `GET /api/v3/brokerage/cfm/balance_summary` (izin: `view`)
Ön kontrol için kullanacağımız alanlar **[BELGEDE VAR]**:

| Alan | Anlamı |
|---|---|
| `futures_buying_power` | Vadelide kullanılabilir nakit |
| `available_margin` | Teminat ihtiyacına karşı kalan fon |
| `initial_margin` | Pozisyon açmak için gereken teminat |
| `liquidation_threshold` | Bu seviyeye inilirse pozisyonlar zorla kapanır |
| `liquidation_buffer_amount` / `_percentage` | Zorla kapanmaya ne kadar kaldığı. 0 olursa kapanır |
| `unrealized_pnl`, `daily_realized_pnl` | Açık ve bugün gerçekleşen kâr/zarar |
| `intraday_margin_window_measure` | Gün içi pencere ölçüleri |
| `overnight_margin_window_measure` | Gece penceresi ölçüleri |

Her pencere ölçüsünün içinde: `margin_window_type`, `margin_level` (BASE/WARNING/DANGER/LIQUIDATION), `initial_margin`, `maintenance_margin`, `liquidation_buffer`, `total_hold`, `futures_buying_power`.

**Gece teminatı kuralı:** Ön kontrol, hem gün içi hem gece ölçüsüne bakacak ve DAHA KÖTÜ olanı baz alacak. Böylece gündüz açılan pozisyon geceye girince zorla kapanmaz.

### Marjin penceresi: `GET /api/v3/brokerage/cfm/intraday/current_margin_window` (izin: `view`)
- Dönen: `margin_window_type` (OVERNIGHT, WEEKEND, INTRADAY, TRANSITION), `end_time`, iki "kill switch" bayrağı. **[BELGEDE VAR]**
- Gün içi pencere rehberde 8:00–16:00 ET olarak geçiyor. **[BELGEDE VAR]**
- Her pencere türünün tam anlamı yazmıyor. **[BELİRSİZ]**

### Pozisyonlar: `GET /api/v3/brokerage/cfm/positions` (izin: `view`)
Alanlar: `product_id`, `side` (LONG/SHORT), `number_of_contracts`, `avg_entry_price`, `current_price`, `unrealized_pnl`, `daily_realized_pnl`, `expiration_time`. **[BELGEDE VAR]**
Kaldıraç ve marjin türü bu cevapta YOK.

### "Vadeli hesap açık mı?"
- **[BELİRSİZ]** Bunu soran özel bir uç yok. Hesap yoksa `balance_summary` ne döner, belgede yazmıyor. B aşamasında gerçek anahtarla bu hata kaydedilecek. Hesap yoksa kullanıcıya "Coinbase'de vadeli hesabın açık değil" açık mesajı gösterilecek.

### Ürün bilgisi: `GET .../products/{product_id}`
Sözleşme boyutu ve marjin oranları buradan okunur **[BELGEDE VAR]**:
- `future_product_details`: `contract_size`, `contract_expiry`, `contract_expiry_type` (EXPIRING/PERPETUAL), `risk_managed_by`, `intraday_margin_rate` ve `overnight_margin_rate` (uzun/kısa ayrı).
- Genel: `base_increment`, `base_min_size`, `base_max_size`, `trading_disabled`, `status`, `product_venue` (FCM bizim ürünlerimiz).
- **[BELİRSİZ]** `contract_size` birimi yazmıyor (örn. 0.01 BTC mi). Birimi gerçek cevapla doğrulayıp sabitleyeceğiz.

---

## 3. Emir türleri

Kaynak: orders rehberi ve create-order şeması.

| Tür | Alan adı | Not |
|---|---|---|
| Piyasa | `market_market_ioc` | `base_size` verilir |
| Limit | `limit_limit_gtc` | `base_size`, `limit_price` |
| Stop-limit | `stop_limit_stop_limit_gtc` | `base_size`, `limit_price`, `stop_price`, `stop_direction` (UP/DOWN). Tetik: son işlem fiyatı |
| Bracket | `trigger_bracket_gtc` | Bir tarafta limit (kâr al), diğerinde tetik (zarar durdur) |

Ek alanlar: `leverage` (varsayılan 1.0), `margin_type` (CROSS/ISOLATED), `client_order_id`. **[BELGEDE VAR]**

- `client_order_id` benzersiz değilse yeni emir açılmaz, ESKİ emir döner. **[BELGEDE VAR]** Bu bizim için iyi: aynı emir iki kez gitmesin diye her emre benzersiz kimlik vereceğiz.
- `margin_type` açıklaması "Intx Perps" için diyor. ABD vadelide işe yarayıp yaramadığı **[BELİRSİZ]**. `leverage` da öyle: ABD vadelide kaldıraç ürünün marjin oranından mı çıkıyor, emirde mi seçiliyor, belgede net değil.

### Hata cevabı
- Başarılı: `success: true` + `success_response.order_id`.
- Reddedilen: HTTP 200 + `success: false` + `error_response.new_order_failure_reason`. **[BELGEDE VAR]**
- Beklenmeyen hata: HTTP 200 dışı, `error`, `code`, `message`, `details`.

Bizim için önemli red nedenleri (hepsi belgede, Türkçe açık mesaja çevrilecek):
`INSUFFICIENT_FUND`, `BREACHED_RISK_LIMIT`, `IN_LIQUIDATION`, `ORDER_ENTRY_DISABLED`, `TRADING_DISABLED`, `PRODUCT_TRADING_HALTED`, `INVALID_LEVERAGE`, `INVALID_MARGIN_TYPE`, `INVALID_FCM_TRADING_SESSION`, `FUTURES_AFTER_HOUR_INVALID_ORDER_TYPE`, `FUTURES_AFTER_HOUR_INVALID_TIME_IN_FORCE`, `GEOFENCING_RESTRICTION`, `LIMIT_ORDER_PRICE_EXCEEDS_PRICE_BAND_*`, `INVALID_SIZE_PRECISION`, `STOP_LOSS_PRICE_TOO_LOW/HIGH`, `REDUCE_ONLY_*`.

---

## 4. Stop (en kritik bölüm)

### 4.1 Bağlı stop (attached TP/SL)
- Ana emre `attached_order_configuration` ile eklenir. Yalnız `trigger_bracket_gtc` kabul. **[BELGEDE VAR]**
- Boyut YAZILMAZ, ana emirle aynı büyüklükte olur. **[BELGEDE VAR]**
- Bir bacak dolunca diğeri kapanır. **[BELGEDE VAR]**
- Bracket'taki stop, tetikte stop-limit olur. Limit fiyatı tetikten %5 uzakta (alımda +%5, satımda −%5). **[BELGEDE VAR, özetten okundu, ham şemadan tekrar doğrulanacak]**
- Hata kodu `ATTACHED_ORDERS_ONLY_ALLOWED_ON_MARKET_LIMIT`: ana emir piyasa VEYA limit olabilir. **[ÇIKARIM]** Yani piyasa girişle bağlı stop çalışabilir. Ama belgedeki tek örnek limit girişli.
- Hata kodu `SINGLE_LEGGED_TPSL_NOT_ALLOWED`: tek bacaklı (sadece stop) bağlı emir REDDEDİLEBİLİR. **[ÇIKARIM]** Bu doğruysa bağlı yolda hem kâr al hem zarar durdur bacağı verilmesi gerekir.
- Hata kodları `FUTURES_AFTER_HOUR_INVALID_ORDER_TYPE` ve `..._TIME_IN_FORCE`: gece/pencere dışı bazı emir türleri reddedilebilir. **[ÇIKARIM]** Stop-limit'in gece kabul edilip edilmediği **[BELİRSİZ]**.

### 4.2 Ayrı stop
Giriş dolduktan sonra ayrı `stop_limit_stop_limit_gtc` emri kurulabilir. Limit fiyatını biz seçeriz.

### 4.3 `reduce_only` yok → ters pozisyon riski
- Şemada `reduce_only` alanı yok. **[BELGEDE YOK]**
- Pozisyon başka yoldan kapanırsa (elle kapatma, kâr al bacağı dolması) ve bir stop emri açık kalırsa, tetiklendiğinde ters yönde yeni pozisyon açabilir. **[ÇIKARIM]**
- Önlem: Her kapatma sonrası pozisyonun sıfır olduğunu doğrula, sonra o ürünün açık stop emirlerini iptal et ve iptali de doğrula. Ek olarak "yetim stop" kontrolü: pozisyon yok ama stop açıksa uyar ve iptal et.
- Bağlı bracket'ın ana pozisyon kapanınca otomatik iptal olup olmadığı **[BELİRSİZ]**.

### 4.4 Stop-limit dolmama riski ve 5 USDT kuralı
Stop-limit tetiklenir ama fiyat limitten hızlı kayarsa DOLMAZ. Pozisyon korumasız kalır.
Bu yüzden "en kötü kayıp 5 USDT" kontrolü iki şey hesaplamalı:
1. Normal senaryo: `(giriş − stop) × sözleşme boyutu × adet`.
2. Kötü senaryo: `(giriş − stop limit fiyatı) × ...`. Limit stop'tan uzaksa kayıp artar. Limit yakınsa dolmama ihtimali artar.

**Karar bekliyor (C aşaması):** Limit mesafesi ne olsun? Öneri: stop'tan çok yakın bir limit seç, dolmazsa "stop dolmadı" alarmı ver ve pozisyonu piyasa emriyle kapatmayı dene.

### 4.5 Stop doğrulama (senin kuralın)
Giriş sonrası: açık emirleri çek (`orders/historical/batch`, `order_status=OPEN`, `product_type=FUTURE`), stop'u `attached_order_id` / `originating_order_id` veya ürün+yön+boyutla bul.
Bulunamazsa → pozisyonu HEMEN kapat (`close_position`) ve kullanıcıya açık mesaj.
Stop kurulamıyorsa giriş hiç açılmaz. Önce bağlı stop denenir. Ayrı stop yolunda giriş stop'tan önce dolar, arada kısa bir korumasız an olur. Bunu kabul edip etmeyeceğin C aşaması kararıdır.

---

## 5. Kapat, azalt, stop taşı

### Kapat: `POST /orders/close_position`
- Alanlar: `client_order_id`, `product_id`, `size` (isteğe bağlı). **[BELGEDE VAR]**
- `size` verilirse kısmi kapatma (azaltma) olur. Verilmezse davranış **[BELİRSİZ]** (muhtemelen hepsi). Biz HER ZAMAN `size` yazacağız.
- Piyasa emri mi, belgede yazmıyor **[BELİRSİZ]**.
- Pozisyon yokken `CANNOT_CLOSE_ZERO_POSITION`.

### Stop taşı: `POST /orders/edit`
- Alanlar: `order_id`, `price`, `size` (üçü zorunlu), `stop_price` (ops., "TP/SL veya SL emirleri için"). **[BELGEDE VAR]**
- Hata kodları `ONLY_LIMIT_ORDER_EDITS_SUPPORTED`, `CANNOT_EDIT_TRIGGERED_ORDER` var. Düz stop-limit emrinin düzenlenebildiği **[BELİRSİZ]**.
- Tetiklenmiş emir düzenlenemez. **[BELGEDE VAR]**
- Seçenekler (canlı testten sonra seçilecek):
  - A) `edit` çalışırsa onu kullan (en güvenlisi, korumasız an yok).
  - B) Yeni stop kur → doğrula → eskisini iptal et. Arada iki stop olur. İkisi de tetiklenirse ters pozisyon riski. Bu riski kapatmak için B yolu ancak pozisyon boyutuna göre doğrulama ile.
  - C) Önce eskiyi iptal et, sonra yenisini kur. Arada korumasız an. KURALINA AYKIRI, kullanma.

### İptal: `POST /orders/batch_cancel`
- `order_ids` listesi, her biri için `success` ve `failure_reason`. **[BELGEDE VAR]**
- Bracket bacaklarını nasıl ele aldığı **[BELİRSİZ]**.

---

## 6. Sandbox

- Adres: `https://api-sandbox.coinbase.com/api/v3/brokerage/...` **[BELGEDE VAR]**
- Kimlik doğrulama gerekmiyor. Cevaplar "static and pre-defined". **[BELGEDE VAR]**
- Listedeki uçlar: hesaplar, emirler (oluştur, toplu iptal, düzenle, önizle, geçmiş, close_position), portföyler, intx (perpetual) uçları. `cfm/...` uçları listede YOK. **[BELGEDE VAR]**
- `X-Sandbox` başlığı bazı uçlarda hazır hata örneği verir (yetersiz bakiye, iptal hatası vb.).

Sonuç: Sandbox sadece istek biçimini ve hata cevabını ayrıştırma kodunu denemeye yarar. Stop davranışı, marjin, imza doğrulaması burada test EDİLEMEZ.

---

## 7. Hız sınırı

- Advanced Trade için saniye bazlı sınır **[BELİRSİZ]**. Sayfayı açamadım.
- Coinbase Business sayfası anahtar başına saatte 10.000 istek ve 429 hata diyor ama bunun Advanced Trade için geçerli olduğu yazmıyor. **[BELİRSİZ]**
- Plan: İstemci kendi içinde ihtiyatlı bir sınır uygular (ayarlanabilir), 429 gelirse açık mesaj gösterir ve bekler, sessizce tekrar denemez.

---

## 8. Okunamayan kaynaklar

Bunlar açılmadı, bilgi eksik kalabilir:
- Coinbase yardım merkezi: "TP/SL emirleri (Derivatives)" (4 dil sürümü denendi).
- Yardım merkezi: "Order management (US derivatives)".
- Advanced Trade hız sınırı sayfası.

Bunlar tam olarak stop davranışını (tek bacak, gece, tutar) anlatan sayfalar. En çok bu yüzden stop tarafı canlı testle kesinleşecek.

---

## 9. B aşamasına etkisi

B (salt okunur omurga) bu bulgularla başlayabilir:
- İmza (ES256, `cdp`, 120 sn, her istekte yeni).
- Saat kontrolü (kendi saatimiz) ve 401'de açık "anahtar, saat veya adres" mesajı.
- Ayrı kasa, ayrı şifre anahtarı. `transfer` izni varsa reddet (doğrulama imkanı varsa).
- Okuma: bakiye özeti, marjin penceresi, pozisyonlar, ürün bilgisi.
- Her cevapta `success` / hata alanı kontrolü.
- Hız sınırı koruması.

## 10. C ve E için açık sorular (canlı testte netleşecek)

1. Piyasa girişli emirde `attached_order_configuration` çalışıyor mu?
2. Yalnız stop (tek bacak) bağlı emir kabul ediliyor mu? Yoksa TP+SL ikisi mi şart?
3. Bağlı stop'un limiti gerçekten tetikten %5 uzakta mı? Biz değiştirebiliyor muyuz?
4. Ayrı stop-limit: gece/hafta sonu penceresinde kabul ediliyor mu?
5. `edit` ile stop fiyatı taşınabiliyor mu?
6. Pozisyon kapanınca bağlı stop otomatik iptal oluyor mu?
7. `close_position` piyasa emri mi? `size` verince tam ne yapıyor?
8. `contract_size` birimi ne? En küçük sözleşmenin dolar değeri 5 USD kuralına sığıyor mu?
9. `leverage` ve `margin_type` ABD vadelide emirde ne işe yarıyor? 3-5x kuralını nasıl ölçeceğiz (öneri: pozisyon değeri / ayrılan teminat)?
10. Vadeli hesap yokken `balance_summary` ne döndürüyor?
11. Sorgulu uçlarda (`?product_type=FUTURE`) JWT `uri` alanına sorgu giriyor mu?
12. Hız sınırı ne?
13. 5 USDT kuralı Coinbase'de USD ile ölçülür. 1 USDT = 1 USD kabul ediyoruz, onay bekler.

---

## Kaynaklar

- [API key authentication](https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication)
- [Futures rehberi](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/futures)
- [Orders rehberi](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders)
- [Sandbox](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/sandbox)
- [Get Futures Balance Summary](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/futures/get-futures-balance-summary)
- [List Futures Positions](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/futures/list-futures-positions)
- [Get Current Margin Window](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/futures/get-current-margin-window)
- [Schedule Futures Sweep](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/futures/schedule-futures-sweep)
- [Create Order](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order)
- [Edit Order](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/edit-order)
- [Cancel Order](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/cancel-order)
- [List Orders](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/list-orders)
- [Close Position](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/close-position)
- [Get Product](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/products/get-product)
- [Derivatives technical guide (başka ürün, kullanılmadı)](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/derivatives/technical)
- [Coinbase Business rate limiting (Advanced Trade için doğrulanmadı)](https://docs.cdp.coinbase.com/coinbase-business/api-architecture/rate-limiting.md)

# V25 Live Guard — güvenli kullanım ve yayın rehberi

## Ne yapar?

V25, yalnızca Binance USDⓈ-M Futures hesabında çalışan ayrı bir canlı yürütme katmanıdır. Analiz, risk boyutlandırma, emir, Stop/TP, emir/pozisyon akışı, kapanış PnL doğrulaması ve acil durdurmayı tek denetim zincirinde birleştirir.

Canlı giriş akışı şöyledir:

1. Kapanmış mumlardan LONG/SHORT/BEKLE kararı üretilir.
2. Güven, tuzak, spread, günlük işlem, gerçekleşmiş/açık zarar, açık pozisyon, yinelenen parite ve PnL doğrulama kapıları ölçülür.
3. Risk politikası giriş–Stop mesafesine göre miktarı hesaplar.
4. Binance sembol filtresi ile fiyat ve miktar aşağı/yakın kurala yuvarlanır; minimum miktar ve notional doğrulanır.
5. One-way mod, isolated marjin ve borsanın gerçekten uyguladığı kaldıraç kontrol edilir.
6. Benzersiz `PTBLV_` client ID ile giriş gönderilir. Belirsiz HTTP 503 sonucunda aynı kimlik sorgulanır; kör tekrar yapılmaz.
7. Market girişinde Stop önce, sonra TP1–TP3 kurulur. Stop kurulamazsa takip edilen pozisyon reduce-only kapatılır.
8. Binance özel kullanıcı akışı emir/dolum/koruma olaylarını iletir. Akış kesilirse REST uzlaştırması pozisyon ve korumaları kontrol etmeye devam eder.
9. Pozisyon kapandığında sonuç Binance `userTrades`, `allOrders` ve `allAlgoOrders` kayıtlarında yalnızca planın kesin `orderId/clientOrderId` kimlikleriyle eşleştirilir. Kesin sonuç yoksa yeni girişler kilitlenir.
10. Borsa emri kabul ettikten hemen sonra bilgisayar kapanırsa önceden kalıcılaştırılan niyet kaydıyla plan kurtarılır; eksik Stop/TP yeniden denetlenir.

## Canlı yayın kapıları

Canlı emir kilidi ancak aşağıdaki kapıların tamamı geçtiğinde açılır:

- Ayrı canlı API anahtarı Windows DPAPI kasasında.
- Anahtara bağlı 24 saatlik yerel risk izni.
- Salt-okunur Binance bağlantısı başarılı.
- Binance hesabı One-way pozisyon modunda.
- Mevcut risk politikasının sahibi tarafından onaylanması.
- En az 30 aktif gün, 100 kapanmış Demo işlem ve diğer tatbikatlardan oluşan Demo Sertifikası.
- Panelde 5 dakikalık canlı emir kilidinin ayrıca açılması. Bu pencere otomasyonu başlatır; başlayan gözetimli otomasyon oturumu en fazla 1 saat çalışır.

Bilgisayar veya API yeniden başladığında 5 dakikalık emir izni ve bir saatlik otomasyon oturumu **daima kapalı** başlar. Mevcut Stop/TP korumalarını izlemek ve pozisyonu azaltmak için gereken risk düşürücü işlemler çalışmaya devam eder.

## Binance anahtarı

Canlı kullanım için yeni ve yalnızca bu botta kullanılan ayrı bir API anahtarı oluşturun:

- Okuma izni: açık.
- USDⓈ-M Futures işlem izni: açık.
- Para çekme/transfer izni: kapalı.
- Mümkünse yalnızca sabit VPS/ev IP adresiniz: izinli.
- Anahtar ekran görüntüsünde, sohbette, e-postada veya tarayıcı formunda: bulunmamalı.

Mümkünse yalnızca ProTreBot için ayrılmış, içinde manuel veya başka bot pozisyonu bulunmayan ayrı bir Binance alt hesabı kullanın. Bu, aynı paritedeki harici pozisyonların V25 uzlaştırmasıyla karışmasını önler. V25 başka botların veya manuel işlemlerin sahibi olduğunu varsaymaz.

Daha önce görüntüsü paylaşılan anahtar artık güvenli sayılmaz. Binance API Yönetimi'nden silip yenisini üretin. Yereldeki eski anahtarı da `BINANCE-CANLI-ANAHTARI-SIL.bat` ile kaldırın.

## İlk canlı deneme sırası

1. `PROFESYONEL-DOGRULAMA.bat` sonucu başarılı olmalı.
2. `SAGLIK-KONTROL.bat` ile API, panel, Docker, TimescaleDB ve Redis kontrol edilmeli.
3. Demo Sertifikası tüm kapıları geçmeli.
4. `BINANCE-CANLI-AYARLA.bat` ile yeni anahtar DPAPI kasasına kaydedilmeli.
5. Panel → **V25 Canlı Kasa** → **SALT OKUNUR TEST** başarılı olmalı.
6. Hesap modu `ONE-WAY`, özel kullanıcı akışı `CANLI` görünmeli.
7. Risk limitleri 1x ve minimum marjinle kaydedilip onaylanmalı.
8. `/order/test` başarılı olmalı; bu test gerçek emir oluşturmaz.
9. `CANLI-ISLEM-IZNI.bat` çalıştırılmalı.
10. Panelde 5 dakikalık kilit açılmalı. **MARKET / LIMIT Emir Bileti** ile önce testi, ardından gerekiyorsa `CANLI EMİR GÖNDER` ikinci onayını kullanın.
11. **Canlı Seviye Grafiği** üzerinde giriş, Stop ve TP1–TP3 çizgilerini görün; bunları Binance arayüzündeki gerçek emirlerle çapraz kontrol edin. Sarı kademeler yalnızca görsel rehberdir ve borsaya gönderilmiş grid emri değildir.
12. Küçük denemenin açılış, kapanış, PnL doğrulama ve acil durdurma kanıtı görülmeden otomatik mod açılmamalı.

## Acil durum

- **Otomasyonu Durdur:** Yeni otomatik girişleri durdurur; mevcut korumalar kalır.
- **Canlı İzni Kapat:** `CANLI-IZNI-KAPAT.bat`, yerel 24 saatlik izni anında siler; anahtar kasada kalır.
- **Acil Durdur:** Yalnızca V25 etiketli emirleri ve korumaları iptal eder, V25'in takip ettiği pozisyonlara reduce-only kapanış yollar. Manuel/başka bot pozisyonlarına dokunmaz.
- **Anahtarı Sil:** `BINANCE-CANLI-ANAHTARI-SIL.bat`, DPAPI kasasındaki canlı anahtarı ve canlı izni siler. Binance tarafındaki anahtar ayrıca Binance API Yönetimi'nden silinmelidir.
- İnternet, borsa veya bilgisayar arızasında Binance uygulamasından pozisyon ve Stop/TP durumunu doğrudan kontrol edin. Yerel bir program borsa kesintisini veya piyasa boşluğunu engelleyemez.

## Bilinen ve bilinçli sınırlar

- Yalnızca Binance USDⓈ-M Futures, One-way ve isolated kullanım hedeflenir.
- Para yatırma, çekme, transfer ve müşteri fonu saklama yoktur.
- V25, kendisinin takip etmediği manuel pozisyonları kapatmaz.
- Doğrulanan net işlem PnL'i gerçekleşen işlem PnL'inden USDT komisyonunu düşer; funding ayrı Binance gelir kaydıdır ve bu değere dahil değildir.
- Aynı hesapta manuel/başka bot işlemi bulunursa V25 yalnızca kendi kesin emir kimliklerini PnL hesabına alır; kimlik kanıtı bulunamazsa sonucu tahmin etmez ve yeni girişleri kilitler.
- Canlı V25, tek yönlü giriş + zorunlu Stop + TP1–TP3 yürütür. Paneldeki sarı kademe çizgileri görseldir; canlı çok emirli grid motoru değildir. Analiz Stüdyosu'ndaki canlı grid ayrı bir motordur (`backend/app/grid_live.py`); varsayılan AÇIKTIR ve sunucuda `KAISTRADE_GRID_LIVE=0` ile kapatılır. Ayrıntı: `docs/LIMITS.md`. Eski grid laboratuvarları Paper/Demo kapsamındadır.
- Stop emri fiyat garantisi değildir; hızlı piyasada kayma ve likidasyon olabilir.
- Yerel panel yalnızca `127.0.0.1` üzerinde açılır. İnternete/VPS dış ağına yayınlamak için TLS, ters proxy, güvenlik duvarı, merkezi KMS ve bağımsız sızma testi gerekir.
- “Yapay zekâ güveni” olasılık veya kazanç garantisi değildir.

## Satışa çıkmadan önce

Bu ZIP teknik ürün adayıdır. Müşteriye veya gerçek paraya açılmadan önce en az şunlar tamamlanmalıdır:

- Bağımsız kod ve sızma testi; tehdit modellemesi.
- İmzalı/sürüm kontrollü güncelleme ve geri alma planı.
- KVKK/GDPR, kullanım koşulları, risk beyanı ve uygun ülke/mevzuat incelemesi.
- Müşteri fonu veya borsa kimlik bilgisi toplamayan açık saklama mimarisi.
- İzleme, alarm, olay müdahalesi, yedek ve destek süreçleri.
- Kontrollü kapalı beta, gerçekçi ücret/funding/kayma ölçümü ve belgelenmiş kayıp senaryoları.

Hiçbir sonuç geçmiş performansa veya yazılım testine dayanarak gelecekteki kârı garanti etmez.

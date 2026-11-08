# DCA — Demo (Binance Futures Demo / demo-fapi) smoke senaryosu

**Durum: HİÇBİR madde henüz çalıştırılmadı. Bu belge sonuç değil, çalıştırma planıdır. Sonuç tablosu doldurulmadan sistem "production verified" sayılmaz.**

## Önce bilinmesi gerekenler (koda göre kontrol edildi)
- Uygulamadaki DCA koşucusu (`DcaRunner`) **Binance Demo hesabına** (`demo-fapi.binance.com`) bağlanır: `POST /api/binance-demo/dca/start` (onay metni `DEMO DCA BAŞLAT`), `/dca/status`, `/dca/stop`, `/dca/emergency`, `/dca/resume`. Gerçek para yok, ARM gerekmez, plan/risk kuralları canlıyla aynı, defter `dca_demo.sqlite3`. Demo DCA başlatmak için Demo API anahtarının kayıtlı olması gerekir.
- Demo, canlıyla **aynı adaptörü** (`BinanceLiveGridAdapter`, stop için "algo" ucu) kullanır. Bu yüzden smoke testi burada yapılır.
- `app/grid_engine/testnet_adapter.py` + `scripts/grid_testnet_day1.py` DCA'yı çalıştırmaz ve klasik `STOP_MARKET` ucunu kullanır (canlı adaptör algo ucunu kullanır). Bu betik DCA için kanıt SAYILMAZ; yalnızca Grid gün-1 içindir.
- **Hata enjeksiyonu yok:** HTTP istemcisi `trust_env=False` ile kurulur, koda hata kancası konmadı (güvenlik mimarisi değiştirilmedi). Bu yüzden "stop isteğini düşür / iptal yanıtını düşür / kapanış yanıtını düşür" türü senaryolar Demo'da **tetiklenemez**; bunlar yalnız sahte borsa testlerinde (`tests/test_dca_safety.py`) kanıtlıdır. Gerçek borsada KANITLANMADIĞINI canlı raporunda açıkça belirt.

## A) Demo'da gerçekten yapılabilir senaryolar
Küçük marj kullan (ör. taban 6–10 USDT, 1–2 ek emir, kaldıraç 2–3x, recycle KAPALI). Her adımda Binance Demo arayüzündeki açık emirler/pozisyon ile `/dca/status` çıktısını (durum, `reason`, `alerts`) yan yana kaydet.

| # | Senaryo | Adımlar | Geçme ölçütü |
|---|---|---|---|
| A1 | Normal döngü | Başlat; fiyat ek emir seviyesine gelince bekle (dar `step_pct` ile) | Her dolumdan sonra Binance'te stop miktarı = pozisyon, TP = borsa `entryPrice` + hedef %; bekleyen ek emir sayısı plana uygun |
| A2 | Kısmi TP (fırsatçı) | TP kısmen dolarsa gözle (zorla tetiklenemez) | TP kalan miktara göre; stop kalan miktarı örter; ortalama borsa entryPrice (azalışta değişmez) |
| A3 | Süreç yeniden başlatma | Pozisyon açıkken ve ek emir beklerken sunucu sürecini `kill -9` ile öldür, yeniden başlat | Defter ↔ borsa uzlaşır; aynı ek emir ikinci kez kurulmaz (`openOrders` sayısı değişmez); stop doğrulanana kadar yeni ek emir yok |
| A4 | Stop elle silindi | Binance Demo arayüzünden bot stopunu iptal et | Bot stopu yeniden kurar (en geç birkaç tur); o sürede ek emir kurulmaz/iptal edilir; pozisyon büyümez |
| A5 | Bekleyen ek emir elle iptal | Ek emri arayüzden iptal et | Bot durumu tutarlı kalır, kontrolsüz çift emir çıkmaz; sonucu kaydet |
| A6 | Ağ kopması | Sunucudan demo-fapi.binance.com çıkışını ~60 sn engelle (güvenlik duvarı/hosts), sonra aç | Kopma boyunca yeni emir yok; açılınca uzlaşma; çift emir yok; durum `BLOCKED`/`INTERVENTION_REQUIRED` olursa nedeni kaydet |
| A7 | `/dca/stop` | Pozisyon açıkken çağır | Bekleyen girişler iptal, pozisyon stop+TP ile kalır |
| A8 | `/dca/emergency` | Pozisyon açıkken çağır (onay `DCA ACİL DURDUR`) | Girişler iptal, piyasadan kapanış TEK emirle, `positionRisk=0` borsadan doğrulanır, durum `EMERGENCY_CLOSED` |
| A9 | Elle pozisyon kapatma | Pozisyonu arayüzden market kapat | Bot kalan emirleri temizler/tur biter; geride stopsuz ya da yetim emir kalmaz. **Sonucu kaydet** (bu davranış testle bu oturumda ayrıca doğrulanmadı) |
| A10 | Tek bot limiti / aynı coin | Aynı coinde 2. bot dene | Reddedilir (aynı coin, en fazla 3 bot) |

## B) Yalnız sahte borsada kanıtlı (Demo'da tetiklenemez)
Stop güncelleme düşmesi → bekleyen DCA iptali; iptal yanıtı kaybı → durum sorgusu; stop yok + DCA fiyatı tetiklenir → pozisyon büyümez; stop yok + iptal başarısız → kapat/INTERVENTION; stop yok + kapanış yanıtı kaybı → çift kapanış yok; hedef doğrulanmadan cycle ilerlemez. Test: `tests/test_dca_safety.py` (`TestNoGrowthWithoutVerifiedStop`, `TestRestartWithOpenDcaAndStop`, `TestProtectiveOrdersGateCycle`).
Gerçek borsada ilk karşılaşılırsa (canlıda) bu durumlar için `DCA_LIVE_CHECKLIST.md` "olağan dışı durum" adımlarını uygula.

## Özellikle ölç / gözle
- Dolum ile botun stop'u genişletmesi arasındaki süre (A1): hedef tek kontrol turu. Uzunsa kaydet.
- Binance Demo'da algo stop emrinin `triggerPrice`, `reduceOnly`, `clientAlgoId` alanları ve iptali (adaptör beklentisi).
- `positionRisk` alanları: `entryPrice`, `liquidationPrice` (0 dönerse bot bunu "bilinmiyor" sayar, tahmini likidasyonu kullanır).
- Hız sınırı (HTTP 429/418) görülürse zamanı ve botun tepkisini kaydet.

## Sonuç tablosu (elle doldurulacak)
| # | Geçti/Kaldı/Çalıştırılmadı | Tarih | Süre (sn) | Borsa emir kimlikleri | Not |
|---|---|---|---|---|---|
| A1–A10 | Çalıştırılmadı | | | | |

Bir madde KALIRSA canlıya geçilmez. Tüm A maddeleri geçmeden "production verified" yazılmaz.

# DCA — İlk canlı çalıştırma kontrol listesi

Bu liste kodun mevcut güvenlik davranışlarını değiştirmez; yalnız kontrollü başlangıç içindir. Durum: **Demo smoke (docs/DCA_TESTNET_SMOKE.md) A1–A10 geçmeden bu listeye geçme.**

## 1. Başlamadan önce
- [ ] `docs/DCA_TESTNET_SMOKE.md` sonuç tablosu dolu; A1, A3, A4, A8 mutlaka GEÇTİ.
- [ ] Sunucuda `python -m unittest` ile `test_dca_*`, `test_grid_live`, `test_grid_recovery` yeşil (httpx/fastapi kurulu ortamda; 4 test bu oturumda httpx eksikliğinden çalışmamıştı).
- [ ] Binance hesabı **one-way** modda; izole marjin; API anahtarında para çekme izni YOK.
- [ ] ARM + yazılı onay (`CANLI DCA BAŞLAT`) süreci biliniyor; acil durdur (`DCA ACİL DURDUR`) ekranda ulaşılabilir.
- [ ] Bakım penceresi/sunucu yeniden başlatması planlı değil; log izleme açık.

## 2. Başlangıç sınırları (config'teki mevcut limitler referans)
Koddaki tavanlar: toplam marjin sert tavanı `HARD_MAX_MARGIN_USDT = 300` (`execution_core.py`), DCA kaldıraç tavanı 5x, en çok 5 ek emir, hacim çarpanı ≤ 2, tek işlem zarar tavanı (politika varsayılanı 3 USDT, sert tavan 25), günlük zarar limiti (politika varsayılanı 10 USDT), toplam maruziyet (varsayılan 350 USDT), kullanıcı başına en çok 3 bot.
Öneri (bunlar tavan değil, ilk gün için SINIRLAMA):
- [ ] `KAISTRADE_DCA_LIVE_MAX_MARGIN` = **20** (sert tavanın çok altında; ilk günler için). Sonra kademeli artır.
- [ ] Kaldıraç **2x–3x** (tavan 5x); ek emir **2–3**; hacim çarpanı ≤ 1.5.
- [ ] Tek işlem en kötü zarar ≤ **3 USDT**, günlük zarar limiti ≤ **10 USDT** (varsayılan politika; yükseltme).
- [ ] Plan önizlemede "en kötü zarar" (ücret + kayma + funding payı dahil) ve stop–likidasyon mesafesi okunup onaylandı. Frontend rakamı yaklaşıktır; sunucu planı esastır.
- [ ] Yüksek likiditeli tek coin (BTC/ETH sınıfı).

## 3. İlk çalıştırma
- [ ] **Recycle KAPALI** (`recycle=false`). Tur tamamlanınca bot durur (`COMPLETED`/`CYCLE_DONE`); yeniden başlatmayı elle yap.
- [ ] **Tek bot, tek coin.** İlk gün ikinci bot YOK.
- [ ] Başlat → hemen Binance arayüzünde doğrula: taban pozisyon var, **stop** (algo) emri var ve miktarı = pozisyon, **TP** limit emri var ve fiyatı = borsa `entryPrice` × (1 ± hedef %) civarı, bekleyen ek emir sayısı plana uygun.
- [ ] Her ek emir dolumundan sonra aynı kontrol: stop miktarı yeni pozisyona yükseldi mi, TP yeni ortalamaya kaydı mı. Stop miktarı pozisyondan küçük kalıyorsa → bölüm 5.

## 4. Loglarda / durum çıktısında izlenecekler (`/dca/live/status`: `status`, `reason`, `alerts`)
- Normal: `RUNNING`; tur bitişi `CYCLE_DONE` / `COMPLETED`; elle durdurma `STOPPED`; acil kapanış `EMERGENCY_CLOSED`.
- **Dikkat:** `BLOCKED` (nedeni `reason` alanında), **`INTERVENTION_REQUIRED`** (son güvenlik durumu: hemen müdahale).
- `alerts` içinde şunlar: "Doğrulanmış stop yokken bekleyen DCA emri…", "stop … kurulamadı", "ortalama … uyuşmuyor", "Kâr hedefi … yeterli net kâr bırakmıyor", "kapatma doğrulanamadı".
- `halt_reason` içinde `→ EMERGENCY_CLOSED` görülürse bot stop kuramayıp pozisyonu kapatmıştır: sebebi araştır, hemen tekrar başlatma.
- Uygulama günlüğünde: `DCA_STATS_FAILED`, `DCA_REPLAN_FAILED`.
- Günlük zarar/rezervasyon sayacı (`day` alanı) limite yaklaşıyor mu.

## 5. Olağan dışı durumda botu durdurma (sırayla)
1. **Yeni giriş durdur:** `POST /api/v25/dca/live/stop` (bekleyen girişleri iptal eder, pozisyon stop+TP ile kalır).
2. Stop/TP Binance'te **görünmüyorsa** ya da miktar pozisyonu örtmüyorsa → hemen **`POST /api/v25/dca/live/emergency`** (`DCA ACİL DURDUR`): girişleri iptal, piyasadan kapat, borsadan doğrula.
3. Acil durdur da `INTERVENTION_REQUIRED` verirse: Binance arayüzünden **elle** (a) bekleyen ek/giriş emirlerini iptal et, (b) pozisyonu market kapat, (c) kalan stop/TP'yi iptal et, (d) `positionRisk` = 0 doğrula. Botu tekrar başlatma.
4. API/ağ sorunu şüphesinde: önce Binance arayüzünden durumu kontrol et; botun emri gönderip göndermediğini **tahmin etme**, borsada emir listesine bak (`kd-` önekli kimlikler).
5. Her müdahaleden sonra: `status`/`alerts` çıktısını ve Binance emir/işlem geçmişini kaydet; nedeni anlamadan yeniden başlatma.
6. Genel kapatma anahtarı: `KAISTRADE_DCA_LIVE=0` yeni canlı DCA başlatmayı kapatır (stop ve acil durdur çalışmaya devam eder).

## 6. Genişletme koşulu
Ancak şunlardan sonra marjin/kaldıraç/bot sayısı artır: en az birkaç tamamlanmış tur sorunsuz, hiçbir `INTERVENTION_REQUIRED` yok, stop/TP her dolumda doğrulandı, günlük zarar limitine yaklaşılmadı. Recycle'ı en son, ayrıca ve küçük marjla aç.

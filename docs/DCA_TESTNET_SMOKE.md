# DCA — Binance Futures Testnet smoke senaryosu (canlıya almadan ÖNCE elle yapılır)

Amaç: fake borsada kanıtlanan davranışların gerçek borsa semantiğinde de geçerli olduğunu görmek. Yalnız TESTNET anahtarı kullanın; `KAISTRADE_DCA_LIVE_MAX_MARGIN` küçük tutulur (ör. 20). Her senaryoda `positionRisk`, `openOrders` ve botun alert/halt_reason alanını kaydedin.

| # | Senaryo | Nasıl tetiklenir | Beklenen |
|---|---|---|---|
| 1 | Normal döngü | Plan başlat; fiyatı ek emir seviyesine getir (ör. küçük coin, dar step) | Her dolumdan sonra stop miktarı = net pozisyon; TP = borsa `entryPrice`'a göre |
| 2 | Stop güncelleme düşer | Ek emir dolmadan hemen önce testnet API anahtarının IP kısıtını geçici kaldır/engelle ya da proxy ile `POST /fapi/v1/order` (STOP_MARKET) isteğini düşür | Bekleyen ek emirler iptal, `openOrders`'ta ek emir kalmaz; fiyat seviyeye gelse de pozisyon büyümez |
| 3 | İptal yanıtı kaybı | Proxy ile `DELETE /fapi/v1/order` yanıtını düşür (istek geçsin) | Bot emir durumunu `GET /fapi/v1/order` ile sorar, tekrar iptal göndermeden CANCELED görür |
| 4 | Stop kurulamıyor (≥3 tur) | Stop isteklerini kalıcı engelle | Pozisyon piyasadan kapanır (`C` emri), `positionRisk=0`, durum EMERGENCY_CLOSED; tek kapanış emri |
| 5 | Kapanış yanıtı kaybı | #4 + MARKET kapanış yanıtını düşür | Bot yeniden kapanış emri göndermez; `GET order` / `positionRisk` ile doğrular |
| 6 | İptal edilemeyen ek emir | #2 + iptal isteklerini kalıcı engelle | Pozisyon kapatılır, INTERVENTION_REQUIRED (son durum) ve alert; açık ek emir listelenir |
| 7 | Restart | #2 sırasında süreci öldür (kill -9), yeniden başlat | Defter ↔ borsa uzlaşır; stop doğrulanana kadar ek emir yok; sonra yeniden kurulur |
| 8 | Kısmi TP | TP limit emrini testnet'te kısmen doldur | TP kalan miktara göre; ortalama borsa `entryPrice` (azalışta değişmez) |

## ÖZELLİKLE doğrulanacak (fake'in garanti edemediği)
- Dolum ile bir sonraki gözlem arasındaki pencere: stop güncellenirken ek emir dolarsa, o dolum miktarı için eski stop kısmen örter. Bot bunu bir sonraki kontrolde yakalar; kalan boşluk süresini ölçün (hedef: tek tick). Gerekirse STOP_MARKET `closePosition=true` değerlendirilir.
- `reduceOnly` / `positionRisk.entryPrice` / `liquidationPrice` alanlarının gerçek formatı.
- Rate limit (HTTP 429/418) altında iptal-doğrulama döngüsünün davranışı.

## Sonuç tablosu
Her satır için: geçti/kaldı, süre (sn), borsa order id'leri, ekran/log notu. Bir satır kalırsa canlıya ALMAYIN.

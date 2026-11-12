# Coinbase — C aşaması: emir akışı (varsayılan KAPALI)

Bu aşama emir verebilir. Bu yüzden **iki ayrı anahtar** ister:

| Ayar | Anlamı |
|---|---|
| `COINBASE_ENABLED=true` | B aşaması (salt okunur) açılır |
| `COINBASE_ORDERS_ENABLED=true` | Emir rotaları yüklenir. Kapalıyken tek emir bile gitmez (kod içinde de ikinci kez kontrol edilir). |

Mevcut Binance sistemi, botlar ve testleri değişmedi.

## Değişmez kurallar (kodda sabit; env sadece SIKILAŞTIRABİLİR)
- Stop zorunlu. Stop kurulamaz veya doğrulanamazsa pozisyon hemen kapatılır; kapanmazsa **ACİL** hata.
- En kötü kayıp ≤ 5 USD (1 USDT = 1 USD). Hesap: giriş kayması + stop-limit'in *limit* fiyatı + komisyon.
- Kaldıraç 3x–5x (= bildirilen teminata göre).
- En fazla 5 açık emir, en fazla 3 açık pozisyon.
- Günlük zarar sınırı: varsayılan 10 USD (`COINBASE_DAILY_LOSS_LIMIT_USD`, 1–50). Değer Coinbase'in "bugünkü gerçekleşen kâr/zarar" bilgisinden alınır.
- Sabit sözleşme boyutu 5 USD kuralına sığmıyorsa emir **reddedilir** ve nedeni yazılır.

## Rotalar (`/api/coinbase/orders/...`)
| Rota | İş |
|---|---|
| `POST /preflight` | Ön kontrol. Hiçbir şey göndermez; planı ve tüm red sebeplerini döner |
| `POST /open` | Piyasa girişi → pozisyon doğrulama → stop-limit koy → stop'u doğrula |
| `POST /close` | Pozisyonu kapat, sonra bizim stop emirlerini temizle (doğrulayarak) |
| `POST /reduce` | Eski stop kalkar → azalt → küçük stop konur → doğrulanır |
| `POST /stop` | Stop koy/taşı. Sadece pozisyona yaklaştırılır; gevşetme reddedilir |
| `GET /protection` | Her pozisyonun stop durumu; yetim stop listesi |
| `POST /cleanup-orphans` | Sadece `kais-s-` ile başlayan kendi yetim stop'larımızı iptal eder |
| `GET /open` | Açık emirler |

Onay metinleri: `COINBASE EMİR AÇ`, `COINBASE POZİSYON KAPAT`, `COINBASE DEĞİŞTİR`. Her istekte tek seferlik `request_id` ister (çift tıklama korunması). Aynı anda tek akış çalışır (kilit).

## Hatalar
Hepsi 4xx + Türkçe mesaj. `critical: true` olanlar acildir (stop'suz pozisyon, sonucu belirsiz emir, kapanmayan pozisyon). Beklenmeyen hata bile "Coinbase'de kontrol et" mesajıyla döner.

## Canlı testte (E) doğrulanacak, şimdilik güvenli taraf
- Piyasa girişinde `leverage` alanının kabul edilip edilmediği (kabul edilmezse emir reddedilir, bir şey açılmaz).
- `base_size`'ın sözleşme sayısı olduğu.
- Stop-limit emrinin Coinbase'de beklenen alanlarla görünmesi (görünmezse doğrulama başarısız → pozisyon kapanır).
- Bağlı (bracket) stop denenmedi; ilk sürüm ayrı stop emri kullanır.
- Stop-limit boşlukta dolmayabilir; bu risk kalır. Yetim stop temizliği için `/protection` düzenli bakılmalı.

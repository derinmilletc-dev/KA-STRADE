# Coinbase B aşaması (salt okunur) — kurulum

Bu aşamada Coinbase'te **emir yok, para hareketi yok**. Sadece anahtar kaydı ve okuma var.
Varsayılan KAPALI: aşağıdaki ayarlar yapılmadan siteye hiçbir etkisi yoktur.

## Sunucu ayarları (hepsi gerekli)

| Ayar | Anlamı |
|---|---|
| `COINBASE_ENABLED=true` | Özelliği açar. Yoksa veya başka değerse Coinbase rotaları hiç yüklenmez. |
| `COINBASE_ALLOWED_EMAILS=senin@mailin.com` | Sadece bu hesap(lar) kullanabilir. Boşsa kimse kullanamaz. Hesap ayrıca yönetici (OWNER) olmalı. |
| `COINBASE_VAULT_MASTER_KEY=…` | Coinbase anahtar kasasının şifresi, en az 24 karakter. Binance kasasından ayrıdır, onunla paylaşılmaz. |
| `COINBASE_MIN_REQUEST_INTERVAL_SECONDS` | İsteğe bağlı. İki Coinbase isteği arası en kısa süre (varsayılan 0.25). |

`COINBASE_VAULT_MASTER_KEY` kaybolursa ya da değişirse kayıtlı Coinbase anahtarı açılmaz; anahtarı yeniden kaydetmek gerekir.

## Coinbase anahtarı nasıl olmalı

- Coinbase Developer Platform'da **ECDSA (ES256)** anahtarı. Ed25519 ve eski tip anahtarlar çalışmaz.
- İzinler: **görüntüleme** (ve ileride işlem). **Transfer izni KAPALI olmalı.** Açıksa ve Coinbase izinleri okunabiliyorsa anahtar kaydedilmez.
- Anahtar adı `organizations/…/apiKeys/…` biçiminde olmalı.

## Rotalar (hepsi giriş ister, sahip + izin listesi şartı)

| Rota | Ne yapar |
|---|---|
| `GET /api/coinbase/status` | Kasa hazır mı, anahtar kayıtlı mı, saat farkı |
| `PUT /api/coinbase/key` | Anahtarı test edip kasaya kaydeder (onay metni + 2FA kodu ister) |
| `DELETE /api/coinbase/key` | Anahtarı siler (onay metni + 2FA kodu ister) |
| `GET /api/coinbase/account` | Vadeli hesap bakiyesi, gün içi ve gece marjin, marjin penceresi |
| `GET /api/coinbase/positions` | Açık vadeli pozisyonlar |

Coinbase kaynaklı her sorun 424 (veya 429) ve Türkçe mesajla döner.

## Kapalıyken

`COINBASE_ENABLED` açık değilse: Coinbase rotası yok, tablo oluşmaz, ağ isteği yok. Binance ve botlar aynen çalışır.

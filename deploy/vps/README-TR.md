# KaisTrade VPS kurulumu (Ubuntu 24.04)

Bu dosyalar backend'i Render yerine kendi sunucunuzda çalıştırır. **Hiçbir gizli değer repoda tutulmaz**; ortam değişkenleri yalnız sunucudaki `/etc/kaistrade.env` dosyasındadır.

## Sırayla

1. DNS: `api.kaistrade.com` için sunucu IP'sine **A kaydı**.
2. Sunucuya root ile bağlanın, kurulumu çalıştırın:
   ```
   curl -fsSL https://raw.githubusercontent.com/derinmilletc-dev/KA-STRADE/main/deploy/vps/install.sh -o install.sh
   bash install.sh
   ```
3. `nano /etc/kaistrade.env` ile Render → KAISTRADE → Environment içindeki değişkenleri `ANAHTAR=değer` satırları olarak yapıştırın.
   - `QUOTAGUARD_URL` satırını **eklemeyin** (artık gerekmiyor).
   - `PROTREBOT_VAULT_MASTER_KEY` ve `DATABASE_URL` Render'dakiyle **birebir** aynı olmalı.
   - `PROTREBOT_DATA_DIR=/var/lib/kaistrade`
   - `PORT` gerekmez.
4. Binance API anahtarının IP listesine sunucunun IP'sini ekleyin.
5. **Geçiş günü** (açık pozisyon yokken): Render servisini Suspend edin, sonra sunucuda `systemctl enable --now kaistrade`.
6. Kontrol: `curl https://api.kaistrade.com/healthz`, `journalctl -u kaistrade -f`.

## Geri dönüş
Sunucuda `systemctl stop kaistrade`, Render'da servisi Resume edin, Vercel yönlendirmesini eski adrese çevirin.

## Güncelleme
`bash /opt/kaistrade/deploy/vps/update.sh`

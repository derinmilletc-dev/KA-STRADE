#!/usr/bin/env bash
# Her gece veritabanı + veri klasörü + ayar dosyası yedeği (systemd zamanlayıcısı çalıştırır, root).
# El ile:  sudo systemctl start kaistrade-backup     Kayıtlar: journalctl -u kaistrade-backup -n 30
# Yedekler /var/backups/kaistrade içinde 14 gün tutulur. Alınamazsa e-posta gelir.
# İsteğe bağlı sunucu dışı kopya: /etc/kaistrade.env içine BACKUP_RCLONE_REMOTE=uzak:klasor yaz (rclone kurulu olmalı).
set -euo pipefail
DIR=/var/backups/kaistrade
DB=kaistrade
KEEP_DAYS=14
APP=/opt/kaistrade
umask 077
install -d -m 755 "$DIR"
TS=$(date -u +%Y%m%d-%H%M%S)

fail() {
  echo "YEDEK ALINAMADI (satır $1)" >&2
  rm -f "$DIR"/*.tmp
  ( cd "$APP/backend" && "$APP/.venv/bin/python" -m app.watchdog --notify "KaisTrade YEDEK ALINAMADI" \
      "Gece yedeği alınamadı. Sunucuda 'journalctl -u kaistrade-backup -n 40' ile bak." ) || true
}
trap 'fail $LINENO' ERR

sudo -u postgres pg_dump -Fc "$DB" > "$DIR/db-$TS.dump.tmp"
sudo -u postgres pg_restore --list < "$DIR/db-$TS.dump.tmp" > /dev/null   # bozuk yedeği yakalar
[ "$(stat -c %s "$DIR/db-$TS.dump.tmp")" -gt 10000 ]                      # boş/çok küçük yedeği yakalar
mv "$DIR/db-$TS.dump.tmp" "$DIR/db-$TS.dump"

PATHS=()
[ -d /var/lib/kaistrade ] && PATHS+=(var/lib/kaistrade)
[ -f /etc/kaistrade.env ] && PATHS+=(etc/kaistrade.env)
if [ "${#PATHS[@]}" -gt 0 ]; then tar czf "$DIR/files-$TS.tgz" -C / "${PATHS[@]}"; fi
( cd "$DIR" && sha256sum "db-$TS.dump" $( [ -f "files-$TS.tgz" ] && echo "files-$TS.tgz" ) > "SHA256-$TS.txt" )

find "$DIR" -type f \( -name 'db-*.dump' -o -name 'files-*.tgz' -o -name 'SHA256-*.txt' \) -mtime +"$KEEP_DAYS" -delete

if [ -n "${BACKUP_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null; then
  rclone copy "$DIR" "$BACKUP_RCLONE_REMOTE" --include "*-$TS.*"
fi

date +%s > "$DIR/LAST_OK"; chmod 644 "$DIR/LAST_OK"   # bekçi bu dosyanın yaşına bakar
echo "Yedek tamam: $(du -h "$DIR/db-$TS.dump" | cut -f1) veritabanı"

#!/usr/bin/env bash
# Yeni sürümü çekip servisi yeniden başlatır (root): bash /opt/kaistrade/deploy/vps/update.sh
set -euo pipefail
APP_DIR=/opt/kaistrade
# Klasör kais kullanıcısına ait; root olarak ikinci çalıştırmada git "dubious ownership" hatası vermesin.
git config --global --get-all safe.directory | grep -qx "$APP_DIR" || git config --global --add safe.directory "$APP_DIR"
git -C "$APP_DIR" fetch origin main
git -C "$APP_DIR" reset --hard origin/main
chown -R kais:kais "$APP_DIR"
sudo -u kais "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
systemctl restart kaistrade
sleep 5
systemctl --no-pager --lines=15 status kaistrade
# Bekçi: site/sunucu düşerse e-posta (her dakika kontrol). Her güncellemede yeniden kurulur, zararsızdır.
install -m 644 "$APP_DIR/deploy/vps/kaistrade-watchdog.service" /etc/systemd/system/kaistrade-watchdog.service
install -m 644 "$APP_DIR/deploy/vps/kaistrade-watchdog.timer" /etc/systemd/system/kaistrade-watchdog.timer
systemctl daemon-reload
systemctl enable --now kaistrade-watchdog.timer
# Gece yedeği: her gün 01:15 UTC; ilk kurulumda hemen bir yedek de alınır.
install -m 644 "$APP_DIR/deploy/vps/kaistrade-backup.service" /etc/systemd/system/kaistrade-backup.service
install -m 644 "$APP_DIR/deploy/vps/kaistrade-backup.timer" /etc/systemd/system/kaistrade-backup.timer
systemctl daemon-reload
systemctl enable --now kaistrade-backup.timer
[ -f /var/backups/kaistrade/LAST_OK ] || systemctl start --no-block kaistrade-backup.service

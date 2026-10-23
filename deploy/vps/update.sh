#!/usr/bin/env bash
# Yeni sürümü çekip servisi yeniden başlatır (root): bash /opt/kaistrade/deploy/vps/update.sh
set -euo pipefail
APP_DIR=/opt/kaistrade
git -C "$APP_DIR" fetch origin main
git -C "$APP_DIR" reset --hard origin/main
chown -R kais:kais "$APP_DIR"
sudo -u kais "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
systemctl restart kaistrade
sleep 5
systemctl --no-pager --lines=15 status kaistrade

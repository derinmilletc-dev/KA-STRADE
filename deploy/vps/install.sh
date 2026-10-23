#!/usr/bin/env bash
# KaisTrade backend — Ubuntu 24.04 VPS kurulumu. root olarak çalıştırın:
#   bash install.sh
# Servisi BAŞLATMAZ (canlı hesapta iki sistem aynı anda çalışmasın diye): geçiş gününde elle başlatılır.
set -euo pipefail

DOMAIN="${DOMAIN:-api.kaistrade.com}"
REPO="${REPO:-https://github.com/derinmilletc-dev/KA-STRADE.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/kaistrade
DATA_DIR=/var/lib/kaistrade
ENV_FILE=/etc/kaistrade.env

if [ "$(id -u)" -ne 0 ]; then echo "root olarak çalıştırın."; exit 1; fi

echo "==> Paketler"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y software-properties-common curl git ufw debian-keyring debian-archive-keyring apt-transport-https gnupg
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y python3.13 python3.13-venv python3.13-dev build-essential libpq-dev

echo "==> Caddy (otomatik HTTPS)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> Kullanıcı ve klasörler"
id kais >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/kais --shell /usr/sbin/nologin kais
mkdir -p "$DATA_DIR"
chown kais:kais "$DATA_DIR"

echo "==> Kod"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
chown -R kais:kais "$APP_DIR"

echo "==> Python ortamı"
sudo -u kais python3.13 -m venv "$APP_DIR/.venv"
sudo -u kais "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u kais "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

echo "==> Servis ve Caddy ayarı"
install -m 644 "$APP_DIR/deploy/vps/kaistrade.service" /etc/systemd/system/kaistrade.service
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/vps/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable caddy
systemctl restart caddy

echo "==> Güvenlik duvarı (SSH, HTTP, HTTPS)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

if [ ! -f "$ENV_FILE" ]; then
  install -m 600 -o root -g root /dev/null "$ENV_FILE"
  echo
  echo "UYARI: $ENV_FILE boş oluşturuldu. Render'daki değişkenleri içine yapıştırın (nano $ENV_FILE)."
fi

echo
echo "Kurulum bitti. Servis BAŞLATILMADI."
echo "Geçiş gününde: systemctl enable --now kaistrade"

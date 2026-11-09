#!/usr/bin/env bash
# Yardımcı kişi için "yalnız güncelleme" erişimi kurar (root olarak çalıştır).
#   Kurulum : sudo bash /opt/kaistrade/deploy/vps/helper-access.sh add arkadas "ssh-ed25519 AAAA... not"
#   Kaldırma: sudo bash /opt/kaistrade/deploy/vps/helper-access.sh remove arkadas
# Yardımcı kişi sunucuya anahtarıyla girer ve yalnız şunu çalıştırabilir:  sudo /usr/local/sbin/kais-update
# Başka hiçbir komuta (sudo bash dahil) izni yoktur; ayar dosyalarını ve Binance anahtarlarını okuyamaz.
set -euo pipefail

APP_DIR=/opt/kaistrade
WRAPPER=/usr/local/sbin/kais-update

[ "$(id -u)" -eq 0 ] || { echo "Root olarak çalıştır: sudo bash $0 ..." >&2; exit 1; }
action="${1:-}"; user="${2:-}"
[[ "$user" =~ ^[a-z][a-z0-9_-]{1,30}$ ]] || { echo "Kullanıcı adı: küçük harf/rakam, 2-31 karakter (ör. arkadas)." >&2; exit 1; }
[ "$user" != "root" ] && [ "$user" != "kais" ] || { echo "Bu ad kullanılamaz." >&2; exit 1; }

case "$action" in
add)
  key="${3:-}"
  [[ "$key" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp[0-9]+)\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "Üçüncü parametre: arkadaşın AÇIK ssh anahtarı (ssh-ed25519 ... ile başlayan satır, tırnak içinde)." >&2; exit 1; }
  [ -d "$APP_DIR/.git" ] || { echo "$APP_DIR bulunamadı." >&2; exit 1; }

  # Root'a ait, uygulama kullanıcısının değiştiremeyeceği güncelleme komutu (update.sh ile aynı adımlar).
  tmp="$(mktemp)"
  cat > "$tmp" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/kaistrade
git -C "$APP_DIR" fetch origin main
git -C "$APP_DIR" reset --hard origin/main
chown -R kais:kais "$APP_DIR"
sudo -u kais "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"
systemctl restart kaistrade
sleep 5
systemctl --no-pager --lines=15 status kaistrade
WRAP
  install -m 750 -o root -g root "$tmp" "$WRAPPER"; rm -f "$tmp"

  id "$user" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$user"
  home="$(getent passwd "$user" | cut -d: -f6)"
  install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
  printf '%s\n' "$key" > "$home/.ssh/authorized_keys"
  chown "$user:$user" "$home/.ssh/authorized_keys"; chmod 600 "$home/.ssh/authorized_keys"
  passwd -l "$user" >/dev/null 2>&1 || true

  sf="$(mktemp)"
  printf '%s ALL=(root) NOPASSWD: %s\n' "$user" "$WRAPPER" > "$sf"
  visudo -cf "$sf" >/dev/null || { rm -f "$sf"; echo "sudoers dosyası geçersiz; iptal." >&2; exit 1; }
  install -m 440 -o root -g root "$sf" "/etc/sudoers.d/helper-$user"; rm -f "$sf"

  echo "Tamam. $user yalnız şunu çalıştırabilir:  sudo $WRAPPER"
  echo "Kaldırmak için: sudo bash $0 remove $user"
  ;;
remove)
  rm -f "/etc/sudoers.d/helper-$user"
  if id "$user" >/dev/null 2>&1; then pkill -u "$user" 2>/dev/null || true; deluser --remove-home "$user"; fi
  echo "$user kaldırıldı (sudo izni ve hesabı silindi)."
  ;;
*)
  echo "Kullanım: $0 add <kullanici> \"<acik ssh anahtari>\"   |   $0 remove <kullanici>" >&2; exit 1 ;;
esac

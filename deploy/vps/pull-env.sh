#!/usr/bin/env bash
# Render'daki ortam değişkenlerini /etc/kaistrade.env dosyasına aktarır (root):
#   sudo bash pull-env.sh
# Değerler yalnız sunucuya yazılır; ekrana basılmaz. İşten sonra Render API anahtarını silin.
set -euo pipefail
SERVICE_ID="${SERVICE_ID:-srv-dare70hsrm7s73e7moag}"
OUT=/etc/kaistrade.env
if [ "$(id -u)" -ne 0 ]; then echo "sudo ile çalıştırın."; exit 1; fi
command -v jq >/dev/null || { apt-get update -y >/dev/null; apt-get install -y jq >/dev/null; }

read -rsp "Render API anahtarı (görünmez, yapıştırıp Enter): " KEY; echo
read -rsp "Veritabanı EXTERNAL URL (görünmez, yapıştırıp Enter): " DBURL; echo
[ -n "$KEY" ] && [ -n "$DBURL" ] || { echo "Boş değer."; exit 1; }

TMP=$(mktemp); chmod 600 "$TMP"; trap 'rm -f "$TMP" "$TMP.json"' EXIT
CURSOR=""
: > "$TMP.json"
while :; do
  URL="https://api.render.com/v1/services/$SERVICE_ID/env-vars?limit=100"
  [ -n "$CURSOR" ] && URL="$URL&cursor=$CURSOR"
  RESP=$(curl -fsS -H "Authorization: Bearer $KEY" -H "Accept: application/json" "$URL")
  echo "$RESP" >> "$TMP.json"
  N=$(echo "$RESP" | jq 'length')
  [ "$N" -lt 100 ] && break
  CURSOR=$(echo "$RESP" | jq -r '.[-1].cursor')
done

jq -rs 'add | .[] | .envVar | select(.key != null)
  | select(.key|test("^(QUOTAGUARD_|RENDER|PORT$|DATABASE_URL$|PROTREBOT_DATA_DIR$)")|not)
  | "\(.key)=\(.value|tojson)"' "$TMP.json" > "$TMP"
printf 'DATABASE_URL=%s\n' "$(printf '%s' "$DBURL" | jq -R .)" >> "$TMP"
echo 'PROTREBOT_DATA_DIR="/var/lib/kaistrade"' >> "$TMP"
install -m 600 -o root -g root "$TMP" "$OUT"
echo "Yazıldı: $OUT  ($(wc -l < "$OUT") satır)"
echo "Anahtar isimleri:"; sed 's/=.*//' "$OUT"

#!/usr/bin/env bash
# YENİ sunucuda (install.sh çalıştıktan sonra) çalışır: eski sunucudan gelen paketi kurar, servisi BAŞLATMAZ.
# Kullanım:  bash move-import.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root olarak çalıştır"; exit 1; }
IN=/root/kais-move
DB=kaistrade
[ -f "$IN/kais.dump" ] || { echo "$IN/kais.dump yok: önce eski sunucuda move-export.sh çalıştır."; exit 1; }
systemctl is-active --quiet kaistrade && { echo "kaistrade çalışıyor; durdur: systemctl stop kaistrade"; exit 1; }

echo "[1/6] Dosyalar doğrulanıyor..."
( cd "$IN" && sha256sum -c SHA256SUMS )
MAJOR="$(cat "$IN/pgmajor")"

echo "[2/6] Ayar dosyası ve veri klasörü yerleştiriliyor..."
[ -f /etc/kaistrade.env ] && cp -p /etc/kaistrade.env "/etc/kaistrade.env.bos-yedek"
tar xzf "$IN/kais-data.tgz" -C /
chown -R kais:kais /var/lib/kaistrade
chmod 600 /etc/kaistrade.env

echo "[3/6] PostgreSQL $MAJOR kuruluyor..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql-common ca-certificates curl >/dev/null
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
apt-get install -y -qq "postgresql-$MAJOR" "postgresql-client-$MAJOR" >/dev/null
systemctl enable --now postgresql >/dev/null 2>&1 || true
for i in $(seq 1 20); do sudo -u postgres psql -qtAc "select 1" >/dev/null 2>&1 && break; sleep 2; done

echo "[4/6] Veritabanı hazırlanıyor (ayar dosyasındaki şifreyle)..."
PASS="$(/opt/kaistrade/.venv/bin/python - <<'PYEOF'
import json, urllib.parse
for line in open("/etc/kaistrade.env", encoding="utf-8"):
    line = line.rstrip("\n")
    if line.startswith("DATABASE_URL="):
        v = line.split("=", 1)[1]
        try: v = json.loads(v)
        except Exception: v = v.strip("'\"")
        print(urllib.parse.unquote(urllib.parse.urlparse(v).password or ""))
        break
PYEOF
)"
[ -n "$PASS" ] || { echo "DATABASE_URL içinde şifre bulunamadı"; exit 1; }
if sudo -u postgres psql -qtAc "select 1 from pg_roles where rolname='kaistrade'" | grep -q 1; then
  sudo -u postgres psql -qc "alter role kaistrade with login password '$PASS'" >/dev/null
else
  sudo -u postgres psql -qc "create role kaistrade with login password '$PASS'" >/dev/null
fi
sudo -u postgres psql -qc "drop database if exists $DB" >/dev/null
sudo -u postgres createdb -O kaistrade "$DB"

echo "[5/6] Yedek yükleniyor..."
BIN="/usr/lib/postgresql/$MAJOR/bin"
PGPASSWORD="$PASS" "$BIN/pg_restore" --no-owner --no-privileges -h 127.0.0.1 -U kaistrade -d "$DB" "$IN/kais.dump" || echo "   (pg_restore uyarıları olabilir; sayım kontrolü aşağıda)"

echo "[6/6] Kayıt sayıları karşılaştırılıyor..."
for t in $(sudo -u postgres psql -d "$DB" -tAc "select tablename from pg_tables where schemaname='public' order by 1"); do
  echo "$t $(sudo -u postgres psql -d "$DB" -tAc "select count(*) from \"$t\"")"
done > /root/counts-yeni.txt
if diff "$IN/counts.txt" /root/counts-yeni.txt >/dev/null; then
  echo "OK: tüm tabloların kayıt sayıları eski sunucuyla BİREBİR aynı ($(wc -l < /root/counts-yeni.txt) tablo)."
else
  echo "UYARI: sayılar farklı:"; diff "$IN/counts.txt" /root/counts-yeni.txt || true; exit 1
fi
echo
echo "Veri hazır. Servis BAŞLATILMADI. Sıradaki adımlar: Binance IP listesi, DNS, sonra: systemctl enable --now kaistrade"

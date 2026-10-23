#!/usr/bin/env bash
# Render PostgreSQL -> bu sunucudaki PostgreSQL. Şifre/URL ASLA ekrana yazılmaz.
# Kullanım (root):  sudo bash /opt/kaistrade/deploy/vps/migrate-db.sh
# Geri dönüş: cp /etc/kaistrade.env.render-backup /etc/kaistrade.env && systemctl restart kaistrade
set -euo pipefail
ENV_FILE=/etc/kaistrade.env
PY=/opt/kaistrade/.venv/bin/python
BACKUP_DIR=/var/backups/kaistrade
[ "$(id -u)" = 0 ] || { echo "root olarak çalıştır: sudo bash $0"; exit 1; }

getenv() {  # ENV_FILE içinden değer oku (tırnaklar temizlenir)
  "$PY" - "$1" <<'PYEOF'
import json, sys
key = sys.argv[1]
for line in open("/etc/kaistrade.env", encoding="utf-8"):
    line = line.rstrip("\n")
    if "=" not in line or line.startswith("#"):
        continue
    k, v = line.split("=", 1)
    if k == key:
        try:
            v = json.loads(v)
        except Exception:
            v = v.strip("'\"")
        print(v)
        break
PYEOF
}

OLD_URL="$(getenv DATABASE_URL)"
[ -n "$OLD_URL" ] || { echo "DATABASE_URL bulunamadı"; exit 1; }
if echo "$OLD_URL" | grep -qE '@(127\.0\.0\.1|localhost)[:/]'; then
  echo "DATABASE_URL zaten bu sunucuya işaret ediyor; yapılacak bir şey yok."; exit 0
fi

echo "[1/8] Render sunucu sürümü okunuyor..."
MAJOR=""
for i in 1 2 3 4 5 6; do
  MAJOR="$(OLD_URL="$OLD_URL" "$PY" - <<'PYEOF' 2>/dev/null || true
import asyncio, os, asyncpg
async def go():
    c = await asyncpg.connect(os.environ["OLD_URL"], ssl="prefer", timeout=20)
    n = int(await c.fetchval("SHOW server_version_num"))
    await c.close()
    print(n // 10000)
asyncio.run(go())
PYEOF
)"
  [ -n "$MAJOR" ] && break
  echo "   bağlantı reddedildi, tekrar deneniyor ($i/6)..."; sleep 10
done
[ -n "$MAJOR" ] || { echo "Render veritabanına bağlanılamadı. Birkaç dakika sonra tekrar çalıştır."; exit 1; }
echo "   Render PostgreSQL sürüm: $MAJOR"

echo "[2/8] PostgreSQL $MAJOR kuruluyor..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql-common ca-certificates curl >/dev/null
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null
apt-get install -y -qq "postgresql-$MAJOR" "postgresql-client-$MAJOR" >/dev/null
systemctl enable --now postgresql >/dev/null 2>&1 || true
for i in $(seq 1 20); do sudo -u postgres psql -qtAc "select 1" >/dev/null 2>&1 && break; sleep 2; done

echo "[3/8] Veritabanı ve kullanıcı hazırlanıyor..."
NEW_PASS="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
if sudo -u postgres psql -qtAc "select 1 from pg_roles where rolname='kaistrade'" | grep -q 1; then
  sudo -u postgres psql -qc "alter role kaistrade with login password '$NEW_PASS'" >/dev/null
else
  sudo -u postgres psql -qc "create role kaistrade with login password '$NEW_PASS'" >/dev/null
fi
if ! sudo -u postgres psql -qtAc "select 1 from pg_database where datname='kaistrade'" | grep -q 1; then
  sudo -u postgres createdb -O kaistrade kaistrade
fi
sudo -u postgres psql -qc "alter database kaistrade owner to kaistrade" >/dev/null
NEW_URL="postgresql://kaistrade:${NEW_PASS}@127.0.0.1:5432/kaistrade"
BIN="/usr/lib/postgresql/$MAJOR/bin"

echo "[4/8] Servis durduruluyor (veri kaybı olmasın)..."
systemctl stop kaistrade

echo "[5/8] Render'dan yedek alınıyor..."
mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
DUMP="$BACKUP_DIR/render-$(date +%Y%m%d-%H%M%S).dump"
ok=0
for i in 1 2 3 4 5 6; do
  if PGSSLMODE=prefer "$BIN/pg_dump" -Fc --no-owner --no-privileges -d "$OLD_URL" -f "$DUMP"; then ok=1; break; fi
  echo "   yedek alınamadı, tekrar ($i/6)..."; sleep 10
done
if [ "$ok" != 1 ]; then
  echo "Yedek alınamadı; servis eski ayarla yeniden başlatılıyor."; systemctl start kaistrade; exit 1
fi
chmod 600 "$DUMP"
echo "   yedek: $DUMP ($(du -h "$DUMP" | cut -f1))"

echo "[6/8] Yeni veritabanına yükleniyor..."
sudo -u postgres psql -qc "drop database if exists kaistrade" >/dev/null
sudo -u postgres createdb -O kaistrade kaistrade
PGPASSWORD="$NEW_PASS" "$BIN/pg_restore" --no-owner --no-privileges -h 127.0.0.1 -U kaistrade -d kaistrade "$DUMP" || echo "   (pg_restore uyarıları olabilir; sayım kontrolü aşağıda)"

echo "[7/8] Kayıt sayıları karşılaştırılıyor..."
if ! OLD_URL="$OLD_URL" NEW_URL="$NEW_URL" "$PY" - <<'PYEOF'
import asyncio, os, sys, asyncpg
async def counts(url):
    c = await asyncpg.connect(url, ssl="prefer", timeout=30)
    tables = [r["tablename"] for r in await c.fetch("select tablename from pg_tables where schemaname='public' order by 1")]
    out = {}
    for t in tables:
        out[t] = await c.fetchval(f'select count(*) from "{t}"')
    await c.close()
    return out
async def go():
    old = await counts(os.environ["OLD_URL"])
    new = await counts(os.environ["NEW_URL"])
    bad = [t for t in old if new.get(t) != old[t]]
    print(f"   tablo: eski {len(old)} / yeni {len(new)}; kayit toplami: eski {sum(old.values())} / yeni {sum(new.values())}")
    for t in bad:
        print(f"   FARK: {t} eski={old[t]} yeni={new.get(t)}")
    sys.exit(1 if bad else 0)
asyncio.run(go())
PYEOF
then
  echo "Sayımlar uyuşmadı; env değişmedi, servis eski veritabanıyla başlatılıyor."; systemctl start kaistrade; exit 1
fi

echo "[8/8] Sunucu yeni veritabanına yönlendiriliyor..."
cp -p "$ENV_FILE" "$ENV_FILE.render-backup"
NEW_URL="$NEW_URL" "$PY" - <<'PYEOF'
import os
path = "/etc/kaistrade.env"
lines = open(path, encoding="utf-8").read().split("\n")
out = []
for line in lines:
    if line.startswith("DATABASE_URL="):
        line = "DATABASE_URL=" + os.environ["NEW_URL"]
    out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out))
PYEOF
chmod 600 "$ENV_FILE"
systemctl start kaistrade
for i in $(seq 1 30); do
  if curl -fsS -m 3 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then echo "SAĞLIKLI: servis yeni veritabanıyla çalışıyor."; exit 0; fi
  sleep 2
done
echo "UYARI: healthz yanıt vermedi. Geri dönüş: cp $ENV_FILE.render-backup $ENV_FILE && systemctl restart kaistrade"
exit 1

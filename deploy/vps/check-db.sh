#!/usr/bin/env bash
# Veritabanı bağlantısını sunucudan dener; şifreyi ASLA yazdırmaz:  sudo bash check-db.sh
set -euo pipefail
exec /opt/kaistrade/.venv/bin/python - <<'PY'
import asyncio, json, re
from urllib.parse import urlsplit
import asyncpg

env = {}
for line in open("/etc/kaistrade.env", encoding="utf-8"):
    line = line.rstrip("\n")
    if "=" not in line or line.startswith("#"):
        continue
    k, v = line.split("=", 1)
    try:
        v = json.loads(v)
    except Exception:
        pass
    env[k] = v

url = env.get("DATABASE_URL", "")
u = urlsplit(url)
print("sema     :", u.scheme)
print("kullanici:", u.username)
print("sunucu   :", u.hostname, "port:", u.port)
print("veritaban:", u.path)
print("sifre uzunlugu:", len(u.password or ""))
print("url sonunda bosluk/tirnak var mi:", url != url.strip() or '"' in url or "'" in url)

async def go(ssl):
    try:
        c = await asyncpg.connect(url, timeout=10, ssl=ssl)
        print(f"ssl={ssl}: BAGLANDI, surum:", (await c.fetchval("select version()"))[:30])
        await c.close()
    except Exception as e:
        msg = re.sub(r"://[^@\s]+@", "://***@", str(e))
        print(f"ssl={ssl}: HATA {type(e).__name__}: {msg}")

for mode in ("prefer", "require"):
    asyncio.run(go(mode))
PY

#!/usr/bin/env bash
# Canlı yürütme durumunu ve son olayları gösterir (şifre/anahtar yazmaz):  sudo bash show-state.sh
set -euo pipefail
exec /opt/kaistrade/.venv/bin/python - <<'PY'
import asyncio, json
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

async def go():
    c = await asyncpg.connect(env["DATABASE_URL"], ssl="prefer")
    rows = await c.fetch("SELECT state_key, payload FROM application_state_snapshots WHERE state_key LIKE 'v25_live:state:%'")
    for r in rows:
        d = r["payload"]
        d = json.loads(d) if isinstance(d, str) else d
        if not d.get("events"):
            continue
        print("ANAHTAR", r["state_key"][-8:])
        print("emergency", d.get("emergency"))
        print("yurutme", d.get("execution_state"), "kilitli", d.get("real_trading_locked"), "recon", d.get("reconciliation_required"))
        print("kilit_nedeni", d.get("live_lock_reason"))
        for e in d.get("events", [])[:14]:
            print(str(e.get("created_at"))[11:19], e.get("kind"), str(e.get("message"))[:140])
    await c.close()

asyncio.run(go())
PY

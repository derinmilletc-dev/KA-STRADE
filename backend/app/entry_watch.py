"""Giriş fırsatı alarmı (SALT OKUNUR: emir göndermez, yalnız bildirir).

Kullanıcı izleme listesine coin ekler; döngü ~90 sn'de bir o coinin yorumunu hesaplar. Aşağıdakilerin HEPSİ birlikte olunca
BİR kez bildirir: 15 dk veya 1 saatte hacimli kırılım (ya da ters tuzak) + yorumun aynı yöne eğilmesi + zaman dilimleri arasında
çelişki olmaması + en az 3 zaman diliminin aynı yönde olması. Bildirim bir emir önerisi değil, "bakmaya değer" haberidir."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from . import coin_extras, coin_read, coin_read_service

logger = logging.getLogger(__name__)

TABLE = "kaistrade_entry_watch"
INTERVAL_SECONDS = 90
COOLDOWN_SECONDS = 1800
MAX_PER_USER = 12
DIRECTIONS = ("both", "long", "short")
_SCHEMA_DONE: set[int] = set()
UP_KINDS = ("breakout_up", "bear_trap")
DOWN_KINDS = ("breakout_down", "bull_trap")


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          id BIGSERIAL PRIMARY KEY,
          user_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          direction TEXT NOT NULL DEFAULT 'both',
          active_keys TEXT NOT NULL DEFAULT '',
          last_push_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          UNIQUE (user_id, symbol)
        )
        """)
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def normalize(symbol: Any, direction: Any) -> dict[str, str] | None:
    sym = coin_read_service.valid_symbol(symbol)
    if sym is None:
        return None
    return {"symbol": sym, "direction": direction if direction in DIRECTIONS else "both"}


def detect(data: dict[str, Any], direction: str = "both") -> dict[str, dict[str, Any]]:
    """Şu an sağlanan fırsatlar: {anahtar: {side, tf, text, plan}}. Saf hesap."""
    summary = data.get("summary") or {}
    if summary.get("conflict") or summary.get("lean") not in ("LONG", "SHORT"):
        return {}
    lean = summary["lean"]
    if direction in ("long", "short") and lean != direction.upper():
        return {}
    alignment = summary["alignment"]
    agree = alignment["up"] if lean == "LONG" else alignment["down"]
    if agree < 3:
        return {}
    kinds = UP_KINDS if lean == "LONG" else DOWN_KINDS
    out: dict[str, dict[str, Any]] = {}
    reads = {r["tf"]: r for r in data.get("timeframes", [])}
    for tf in ("15m", "1h"):
        r = reads.get(tf)
        if not r or r["event"] not in kinds or not r["volume_spike"]:
            continue
        plan = coin_extras.trade_plan(lean, data["price"], data["levels"], data.get("atr"), summary["confidence"])
        label = coin_read.EVENT_NAME[r["event"]]
        text = f"{r['label']} grafikte hacimli {label}, {agree}/{alignment['total']} zaman dilimi aynı yönde."
        if plan:
            text += f" Düşünülebilecek stop {coin_read.price_text(plan['stop'])}, ilk hedef {coin_read.price_text(plan['targets'][0]['price'])} (1:{coin_read.tr_num(plan['rr'])})."
        out[f"{tf}:{r['event']}:{lean}"] = {"side": lean, "tf": tf, "text": text, "plan": plan}
    return out


def plan_push(active: list[str], found: dict[str, Any], last_push_at: datetime | None, now: datetime) -> tuple[list[str], list[str]]:
    new = [k for k in found if k not in active]
    cooled = last_push_at is None or (now - last_push_at).total_seconds() >= COOLDOWN_SECONDS
    send = new if new and cooled else []
    return send, sorted((set(active) & set(found)) | set(send))


def message(symbol: str, found: dict[str, Any], keys: list[str]) -> tuple[str, str]:
    first = found[sorted(keys)[0]]
    side = "LONG" if first["side"] == "LONG" else "SHORT"
    return f"🎯 {symbol} için {side} fırsatı oluştu", first["text"] + " Karar senin; önce grafiğe bak."


def _row(row: Any) -> dict[str, Any]:
    return {"id": int(row["id"]), "symbol": str(row["symbol"]), "direction": str(row["direction"]),
            "active_keys": [k for k in str(row["active_keys"] or "").split(",") if k], "last_push_at": row["last_push_at"]}


async def list_watch(pool: Any, user_id: str) -> list[dict[str, Any]]:
    await ensure_schema(pool)
    rows = await pool.fetch(f"SELECT id, symbol, direction, active_keys, last_push_at FROM {TABLE} WHERE user_id = $1 ORDER BY id", user_id)
    return [_row(r) for r in rows]


async def upsert(pool: Any, user_id: str, data: dict[str, str]) -> dict[str, Any]:
    await ensure_schema(pool)
    count = await pool.fetchval(f"SELECT COUNT(*) FROM {TABLE} WHERE user_id = $1 AND symbol <> $2", user_id, data["symbol"])
    if int(count or 0) >= MAX_PER_USER:
        raise ValueError(f"En fazla {MAX_PER_USER} coin izlenebilir.")
    row = await pool.fetchrow(
        f"""
        INSERT INTO {TABLE} (user_id, symbol, direction) VALUES ($1, $2, $3)
        ON CONFLICT (user_id, symbol) DO UPDATE SET direction = EXCLUDED.direction, active_keys = ''
        RETURNING id, symbol, direction, active_keys, last_push_at
        """, user_id, data["symbol"], data["direction"])
    return _row(row)


async def delete(pool: Any, user_id: str, symbol: str) -> bool:
    await ensure_schema(pool)
    return str(await pool.execute(f"DELETE FROM {TABLE} WHERE user_id = $1 AND symbol = $2", user_id, symbol)).endswith(" 1")


async def _push(pool: Any, user_id: str, title: str, body: str, url: str) -> None:
    from .web_push import configured, send_to_user
    if not configured():
        logger.warning("Giriş fırsatı çaldı ama telefon bildirimi kapalı (VAPID ayarı yok)")
        return
    if not await send_to_user(pool, user_id, "read", title, body, force=True, url=url):
        logger.warning("Giriş fırsatı bildirimi hiçbir cihaza gitmedi")


async def run_once(app: Any, now: datetime | None = None) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    rows = await pool.fetch(f"SELECT id, user_id, symbol, direction, active_keys, last_push_at FROM {TABLE}")
    watches = [{**_row(r), "user_id": str(r["user_id"])} for r in rows]
    cache: dict[str, dict[str, Any] | None] = {}
    sent = 0
    for w in watches:
        sym = w["symbol"]
        if sym not in cache:
            try:
                cache[sym] = await coin_read_service.read(app, sym, with_futures=False, tfs=coin_read_service.ALARM_TFS)
            except Exception as exc:  # noqa: BLE001
                logger.info("Giriş fırsatı %s için veri alamadı (%s)", sym, type(exc).__name__)
                cache[sym] = None
        data = cache[sym]
        if data is None:
            continue
        found = detect(data, w["direction"])
        send, next_keys = plan_push(w["active_keys"], found, w["last_push_at"], now)
        if send:
            title, body = message(sym, found, send)
            try:
                await _push(pool, w["user_id"], title, body, "/master-trade")
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Giriş fırsatı bildirimi gönderilemedi (%s)", type(exc).__name__)
                continue
            await pool.execute(f"UPDATE {TABLE} SET active_keys = $2, last_push_at = $3 WHERE id = $1", w["id"], ",".join(next_keys), now)
        elif sorted(w["active_keys"]) != next_keys:
            await pool.execute(f"UPDATE {TABLE} SET active_keys = $2 WHERE id = $1", w["id"], ",".join(next_keys))
    return sent


async def watch_loop(app: Any) -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await run_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Giriş fırsatı alarm turu başarısız")
        await asyncio.sleep(INTERVAL_SECONDS)

"""Coinbase bildirim izleyicisi (SALT OKUNUR, emir göndermez).

Her ~45 sn'de anahtarı kayıtlı her hesap için yeni dolumlara bakar. Stop ya da hedef çalıştıysa, bekleyen emir
dolduysa veya Coinbase'de dışarıdan bir emir dolduysa telefona bildirim gönderir ve olayı günlüğe yazar.

- İlk turda yalnızca "son görülen dolum" kaydedilir; geçmiş için bildirim yağmuru olmaz.
- Bir hesapta hata olursa o hesap atlanır, diğerleri ve uygulama etkilenmez.
- Bildirim gönderilemese bile (abonelik yok, VAPID yok) olay günlüğe yazılır; ekrandaki akış onu gösterir.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from . import activity, journal, market, service, vault
from .client import CoinbaseClient
from .jwt_auth import load_private_key

logger = logging.getLogger(__name__)

WATCH_TABLE = "kaistrade_coinbase_watch"
INTERVAL_SECONDS = 45
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {WATCH_TABLE} (
          user_id TEXT PRIMARY KEY,
          last_fill_time TEXT,
          positions JSONB,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def _positions_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        side = activity.position_side(row.get("side"))
        if side:
            result[str(row["product_id"])] = {"side": side, "entry": row.get("avg_entry_price")}
    return result


def notification_for(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """(kategori, başlık, metin). Uygulamadan senin yaptığın giriş/kapanış için bildirim yok (zaten ekrandasın)."""
    kind = event["kind"]
    detail = f"{event['product_id']} · {event['size']} sözleşme @ {event['price']}"
    pnl = f" · yaklaşık {'+' if not str(event['pnl']).startswith('-') else ''}{event['pnl']} USD (komisyon hariç)" if event.get("pnl") is not None else ""
    if kind == "target":
        return "close", f"🎯 {event['title']}", detail + pnl
    if kind == "stop":
        return "close", f"🛑 {event['title']}", detail + pnl
    if kind == "protect":
        return "close", f"⚠️ {event['title']}", detail
    if kind == "entry" and "bekleyen" in event["title"]:
        return "entry", f"✅ {event['title']}", detail
    if kind == "other":
        return "entry", f"ℹ️ {event['title']}", detail
    return None


async def check_user(pool: Any, user_id: str, client: Any, *, send: Any) -> list[dict[str, Any]]:
    """Bir hesabı kontrol eder; gönderilen olayları döner. send(pool, user_id, kategori, başlık, metin)."""
    await ensure_schema(pool)
    fills = await activity.fetch_fills(client)
    positions = await service.fetch_positions(client)
    now_map = _positions_map(positions)
    row = await pool.fetchrow(f"SELECT last_fill_time, positions FROM {WATCH_TABLE} WHERE user_id = $1", user_id)
    newest = max((fill["time"] for fill in fills), default=None)
    if row is None:
        await pool.execute(
            f"INSERT INTO {WATCH_TABLE} (user_id, last_fill_time, positions) VALUES ($1, $2, $3::jsonb) ON CONFLICT (user_id) DO NOTHING",
            user_id, newest, json.dumps(now_map, default=str))
        return []
    last = row["last_fill_time"]
    before = row["positions"]
    if isinstance(before, str):
        try:
            before = json.loads(before)
        except ValueError:
            before = {}
    before = before if isinstance(before, dict) else {}
    fresh = [fill for fill in fills if last is None or fill["time"] > last]
    sent: list[dict[str, Any]] = []
    if fresh:
        roles: dict[str, str] = {}
        for role in ("entry", "entry_rest", "stop", "close"):
            for order_id in await journal.our_order_ids(pool, user_id, (role,)):
                roles[order_id] = role
        try:
            sizes = activity.contract_sizes(await market.fetch_products(client))
        except Exception:
            sizes = {}
        state = {product: {"side": value.get("side"), "entry": activity._dec(value.get("entry"))} for product, value in before.items()}
        for order in sorted(activity.group_fills(fresh), key=lambda item: item["time"]):
            event = activity.classify(order, roles.get(order["order_id"]), state.get(order["product_id"]), sizes.get(order["product_id"]))
            if event["kind"] in ("target", "stop", "protect", "manual_close"):
                state.pop(order["product_id"], None)
            elif event["kind"] in ("entry", "other"):
                state[order["product_id"]] = {"side": "LONG" if order["side"] == "BUY" else "SHORT", "entry": order["price"]}
            await journal.record(pool, user_id, "watch", event["kind"], product_id=order["product_id"], order_id=order["order_id"],
                                 detail={"title": event["title"], "price": event["price"], "size": event["size"], "pnl": event["pnl"], "time": event["time"]})
            note = notification_for(event)
            if note is not None:
                try:
                    await send(pool, user_id, *note)
                except Exception as exc:
                    logger.warning("Coinbase bildirimi gönderilemedi (%s)", type(exc).__name__)
            sent.append(event)
    await pool.execute(
        f"UPDATE {WATCH_TABLE} SET last_fill_time = $2, positions = $3::jsonb, updated_at = NOW() WHERE user_id = $1",
        user_id, newest or last, json.dumps(now_map, default=str))
    return sent


async def _push(pool: Any, user_id: str, category: str, title: str, body: str) -> None:
    from ..web_push import configured, send_to_user
    if configured():
        await send_to_user(pool, user_id, category, title, body, url="/master-trade")


async def _users(pool: Any) -> list[str]:
    try:
        rows = await pool.fetch(f"SELECT user_id FROM {vault.TABLE}")
    except Exception:  # tablo henüz yoksa (hiç anahtar kaydedilmemiş) izlenecek hesap yok
        return []
    return [str(row["user_id"]) for row in rows]


async def run_once(app: Any) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    count = 0
    for user_id in await _users(pool):
        try:
            stored = await vault.load_key(pool, user_id)
            if stored is None:
                continue
            key_name, pem = stored
            client = CoinbaseClient(key_name, load_private_key(pem))
            count += len(await check_user(pool, user_id, client, send=_push))
        except Exception as exc:
            logger.warning("Coinbase izleyici bu hesabı atladı (%s)", type(exc).__name__)
    return count


async def watcher_loop(app: Any) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Coinbase izleyici turu başarısız")
        await asyncio.sleep(INTERVAL_SECONDS)

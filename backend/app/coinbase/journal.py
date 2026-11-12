"""Coinbase emir günlüğü ve akış kilidi (ayrı tablolar; Binance tablolarına dokunmaz).

- Akış kilidi: aynı hesap için aynı anda yalnızca BİR emir akışı çalışır (çift tıklama / iki sekme).
  Kilit atomik tek sorguyla alınır; 120 sn'den eski kilit çökmüş sayılır ve devralınır.
- Günlük: her adım (açıldı, stop doğrulandı, kapatıldı, reddedildi, ACİL...) eklenir, asla silinmez/güncellenmez.
  Hangi stop emirlerinin bizim olduğu da buradan bilinir (yetim stop temizliği yalnız bizim emirlerimize dokunur).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

LOCK_TABLE = "kaistrade_coinbase_flow_lock"
EVENT_TABLE = "kaistrade_coinbase_events"
LOCK_STALE_SECONDS = 120
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LOCK_TABLE} (
          user_id TEXT PRIMARY KEY,
          token TEXT NOT NULL,
          started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
          id BIGSERIAL PRIMARY KEY,
          ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          user_id TEXT NOT NULL,
          action TEXT NOT NULL,
          product_id TEXT,
          order_id TEXT,
          role TEXT,
          outcome TEXT NOT NULL,
          detail JSONB
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


async def acquire_lock(pool: Any, user_id: str, token: str) -> bool:
    await ensure_schema(pool)
    row = await pool.fetchrow(
        f"""
        INSERT INTO {LOCK_TABLE} (user_id, token, started_at) VALUES ($1, $2, NOW())
        ON CONFLICT (user_id) DO UPDATE SET token = EXCLUDED.token, started_at = NOW()
        WHERE {LOCK_TABLE}.started_at < NOW() - INTERVAL '{LOCK_STALE_SECONDS} seconds'
        RETURNING token
        """,
        user_id, token,
    )
    return row is not None


async def release_lock(pool: Any, user_id: str, token: str) -> None:
    try:
        await pool.execute(f"DELETE FROM {LOCK_TABLE} WHERE user_id = $1 AND token = $2", user_id, token)
    except Exception:  # kilit bırakılamazsa 120 sn sonra kendiliğinden düşer; ama gizleme
        logger.exception("Coinbase akış kilidi bırakılamadı user_id=%s", user_id)


async def record(
    pool: Any, user_id: str, action: str, outcome: str, *,
    product_id: str | None = None, order_id: str | None = None, role: str | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Günlüğe yaz. Yazılamazsa False döner ve HATA günlüğe düşer; koruyucu işlem bu yüzden durdurulmaz."""
    try:
        await ensure_schema(pool)
        await pool.execute(
            f"INSERT INTO {EVENT_TABLE} (user_id, action, product_id, order_id, role, outcome, detail) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            user_id, action, product_id, order_id, role, outcome,
            json.dumps(detail, default=str) if detail is not None else None,
        )
        return True
    except Exception:
        logger.exception("Coinbase günlüğü yazılamadı action=%s outcome=%s", action, outcome)
        return False


async def our_order_ids(pool: Any, user_id: str, roles: tuple[str, ...]) -> set[str]:
    await ensure_schema(pool)
    rows = await pool.fetch(
        f"SELECT order_id FROM {EVENT_TABLE} WHERE user_id = $1 AND role = ANY($2::text[]) "
        "AND order_id IS NOT NULL AND ts > NOW() - INTERVAL '45 days'",
        user_id, list(roles),
    )
    return {str(row["order_id"]) for row in rows}


async def rest_plan(pool: Any, user_id: str, product_id: str) -> dict[str, Any] | None:
    """Bu ürün için en son bekleyen girişin kayıtlı planı (stop/hedef fiyatları). Yoksa None."""
    await ensure_schema(pool)
    row = await pool.fetchrow(
        f"SELECT detail FROM {EVENT_TABLE} WHERE user_id = $1 AND product_id = $2 AND outcome = 'rest_sent' "
        "AND ts > NOW() - INTERVAL '45 days' ORDER BY id DESC LIMIT 1",
        user_id, product_id,
    )
    if row is None:
        return None
    detail = row["detail"]
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) and detail.get("stop_price") and detail.get("stop_limit_price") else None

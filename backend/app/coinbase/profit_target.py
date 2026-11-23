"""Pozisyon başına "X USD kâr olunca otomatik kapat" hedefi.

Hedef veritabanında tutulur; izleyici (watcher) her turda net K/Z'yi (ödenen komisyon dahil) hedefle karşılaştırır,
ulaşıldıysa pozisyonu normal kapatma akışıyla kapatır ve bildirim gönderir. Hedef GİRİŞ FİYATINA bağlıdır:
pozisyon kapanıp yenisi açılırsa eski hedef sessizce silinir (yeni pozisyona yanlış hedef uygulanmaz).
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from . import activity, journal

logger = logging.getLogger(__name__)

TABLE = "kaistrade_coinbase_profit_target"
MAX_USD = Decimal("1000000")
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          user_id TEXT NOT NULL,
          product_id TEXT NOT NULL,
          target_usd TEXT NOT NULL,
          entry_price TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (user_id, product_id)
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def parse_usd(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0 or number > MAX_USD:
        return None
    return number.quantize(Decimal("0.01"))


def net_pnl(position: dict[str, Any], fee: Any) -> Decimal | None:
    """Ekrandaki net K/Z ile aynı: unrealized − ödenen komisyon (komisyon bilinmiyorsa brüt)."""
    pnl = activity._dec(position.get("unrealized_pnl"))
    if pnl is None:
        return None
    paid = activity._dec(fee) if fee is not None else None
    return pnl - paid if paid is not None else pnl


def reached(position: dict[str, Any], fee: Any, target: Decimal) -> bool:
    net = net_pnl(position, fee)
    return net is not None and net >= target


async def list_targets(pool: Any, user_id: str) -> dict[str, dict[str, str]]:
    await ensure_schema(pool)
    rows = await pool.fetch(f"SELECT product_id, target_usd, entry_price FROM {TABLE} WHERE user_id = $1", user_id)
    return {str(r["product_id"]): {"target_usd": str(r["target_usd"]), "entry_price": str(r["entry_price"])} for r in rows}


async def set_target(pool: Any, user_id: str, product_id: str, target: Decimal, entry_price: str) -> None:
    await ensure_schema(pool)
    await pool.execute(
        f"""
        INSERT INTO {TABLE} (user_id, product_id, target_usd, entry_price) VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id, product_id) DO UPDATE SET target_usd = EXCLUDED.target_usd, entry_price = EXCLUDED.entry_price, created_at = NOW()
        """,
        user_id, product_id, f"{target:f}", entry_price,
    )


async def clear_target(pool: Any, user_id: str, product_id: str) -> None:
    await ensure_schema(pool)
    await pool.execute(f"DELETE FROM {TABLE} WHERE user_id = $1 AND product_id = $2", user_id, product_id)


async def check_targets(pool: Any, user_id: str, client: Any, positions: list[dict[str, Any]], *, send: Any) -> list[str]:
    """Hedefe ulaşan pozisyonları kapatır. Kapatma başarısızsa hedef KALIR (sonraki turda tekrar denenir) ve bildirim gelir."""
    from . import flow
    done: list[str] = []
    targets = await list_targets(pool, user_id)
    if not targets:
        return done
    rows = {str(p["product_id"]): p for p in positions}
    fees: dict[str, Any] | None = None
    for product, item in targets.items():
        position = rows.get(product)
        entry = activity._dec(position.get("avg_entry_price")) if position else None
        saved = activity._dec(item["entry_price"])
        if position is None or entry is None or saved is None or entry != saved:
            await clear_target(pool, user_id, product)   # pozisyon kapanmış/değişmiş
            continue
        target = parse_usd(item["target_usd"])
        if target is None:
            await clear_target(pool, user_id, product)
            continue
        if fees is None:
            fills = await activity.fetch_fills(client, limit=250)
            fees = activity.position_fees(fills, positions)
        if not reached(position, fees.get(product), target):
            continue
        name = product.split("-")[0]
        try:
            await flow.close_flow(client, pool, user_id, product)
        except Exception as exc:  # noqa: BLE001 - kapatma hatası diğer hedefleri durdurmaz
            logger.warning("Kâr hedefi kapatması başarısız (%s)", type(exc).__name__)
            key = f"{product}:{item['entry_price']}:{item['target_usd']}"
            if not await journal.has_event(pool, user_id, "profit_target_failed", product_id=product, key=key, days=1):
                await journal.record(pool, user_id, "watch", "profit_target_failed", product_id=product, detail={"key": key})
                try:
                    await send(pool, user_id, "close", f"⚠️ {name} kâr hedefi otomatik kapanamadı", "Hedefe ulaştı ama kapatma başarısız oldu. Uygulamadan elle kapat.")
                except Exception:  # noqa: BLE001
                    pass
            continue
        await clear_target(pool, user_id, product)
        await journal.record(pool, user_id, "watch", "profit_target_closed", product_id=product, detail={"target_usd": item["target_usd"]})
        try:
            await send(pool, user_id, "close", f"✅ {name} kâr hedefi doldu", f"+{target:f} USD kâra ulaştı, pozisyon otomatik kapatıldı.")
        except Exception:  # noqa: BLE001
            pass
        done.append(product)
    return done

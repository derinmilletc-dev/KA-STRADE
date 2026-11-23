"""Coinbase fiyat alarmları (SALT OKUNUR: emir göndermez, sadece bildirim).

Kullanıcı bir ürün için seviye koyar; yön (yukarı/aşağı) kurulum anındaki fiyata göre sunucuda belirlenir.
Ayrı bir hafif döngü her ~10 sn'de yalnız alarmı olan hesapların ürün fiyatlarına bakar; seviye geçilince BİR kez
bildirim gider ve alarm kapanır (atomik: aynı alarm iki kez tetiklenmez). Bildirim, kullanıcı alarmı bilerek kurduğu
için bildirim tercihlerinden bağımsız gönderilir.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from . import market, vault
from .client import CoinbaseClient
from .jwt_auth import load_private_key

logger = logging.getLogger(__name__)

TABLE = "kaistrade_coinbase_price_alert"
INTERVAL_SECONDS = 10
MAX_PER_USER = 20
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          id BIGSERIAL PRIMARY KEY,
          user_id TEXT NOT NULL,
          product_id TEXT NOT NULL,
          level TEXT NOT NULL,
          direction TEXT NOT NULL,
          ref_price TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          triggered_at TIMESTAMPTZ
        )
        """
    )
    await pool.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT 'coinbase'")
    await pool.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_active ON {TABLE} (user_id) WHERE triggered_at IS NULL")
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def parse_level(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def direction_for(level: Decimal, price: Decimal) -> str | None:
    """Seviye fiyatın üstündeyse 'above' (yukarı çıkınca), altındaysa 'below'. Eşitse (zaten orada) None."""
    if level > price:
        return "above"
    if level < price:
        return "below"
    return None


def crossed(direction: str, level: Decimal, price: Decimal) -> bool:
    return price >= level if direction == "above" else price <= level


def message(product_id: str, direction: str, level: Decimal, price: Decimal) -> tuple[str, str]:
    name = product_id.split("-")[0]
    arrow = "yukarı çıktı ⬆️" if direction == "above" else "aşağı indi ⬇️"
    return f"🔔 {name} alarmı", f"{name} {level.normalize():f} seviyesinin {'üstüne' if direction == 'above' else 'altına'} geldi ({arrow}). Güncel: {price.normalize():f}"


def _row(row: Any) -> dict[str, Any]:
    return {"id": int(row["id"]), "product_id": str(row["product_id"]), "level": str(row["level"]), "direction": str(row["direction"]), "ref_price": str(row["ref_price"])}


async def list_alerts(pool: Any, user_id: str, exchange: str = "coinbase") -> list[dict[str, Any]]:
    await ensure_schema(pool)
    rows = await pool.fetch(f"SELECT id, product_id, level, direction, ref_price FROM {TABLE} WHERE user_id = $1 AND exchange = $2 AND triggered_at IS NULL ORDER BY id DESC", user_id, exchange)
    return [_row(r) for r in rows]


async def count_active(pool: Any, user_id: str, exchange: str = "coinbase") -> int:
    await ensure_schema(pool)
    row = await pool.fetchrow(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE user_id = $1 AND exchange = $2 AND triggered_at IS NULL", user_id, exchange)
    return int(row["n"]) if row else 0


async def add_alert(pool: Any, user_id: str, product_id: str, level: Decimal, direction: str, ref_price: Decimal, exchange: str = "coinbase") -> dict[str, Any]:
    await ensure_schema(pool)
    row = await pool.fetchrow(
        f"INSERT INTO {TABLE} (user_id, product_id, level, direction, ref_price, exchange) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, product_id, level, direction, ref_price",
        user_id, product_id, f"{level:f}", direction, f"{ref_price:f}", exchange,
    )
    return _row(row)


async def delete_alert(pool: Any, user_id: str, alert_id: int) -> None:
    await ensure_schema(pool)
    await pool.execute(f"DELETE FROM {TABLE} WHERE id = $1 AND user_id = $2", alert_id, user_id)


async def _claim(pool: Any, alert_id: int) -> bool:
    """Alarmı tetiklenmiş say; yalnız ilk çağıran True alır (iki döngü/çift bildirim olmaz)."""
    row = await pool.fetchrow(f"UPDATE {TABLE} SET triggered_at = NOW() WHERE id = $1 AND triggered_at IS NULL RETURNING id", alert_id)
    return row is not None


async def check_user(pool: Any, user_id: str, alerts: list[dict[str, Any]], quotes: dict[str, Any], *, send: Any) -> list[int]:
    fired: list[int] = []
    for alert in alerts:
        quote = quotes.get(alert["product_id"])
        price = parse_level(quote.get("mid")) if isinstance(quote, dict) else None
        level = parse_level(alert["level"])
        if price is None or level is None or not crossed(alert["direction"], level, price):
            continue
        if not await _claim(pool, alert["id"]):
            continue
        title, body = message(alert["product_id"], alert["direction"], level, price)
        try:
            await send(pool, user_id, title, body)
        except Exception as exc:  # noqa: BLE001 - bildirim hatası diğer alarmları durdurmaz
            logger.warning("Coinbase fiyat alarmı bildirimi gönderilemedi (%s)", type(exc).__name__)
        fired.append(alert["id"])
    return fired


async def _push(pool: Any, user_id: str, title: str, body: str) -> None:
    from ..web_push import configured, send_to_user
    if configured():
        await send_to_user(pool, user_id, "price", title, body, force=True, url="/master-trade")


async def binance_prices(app: Any) -> dict[str, Any]:
    """Binance USDT-M vadeli tüm sembol fiyatları (herkese açık, anahtar gerekmez, tek istek). {SEMBOL: {"mid": fiyat}}."""
    from ..main import market_data_request   # geç içe aktarma: main bu modülü başlangıçta yükler
    response = await market_data_request(app, "/fapi/v1/ticker/price", {})
    response.raise_for_status()
    return {str(row["symbol"]): {"mid": str(row["price"])} for row in response.json() if isinstance(row, dict) and "symbol" in row and "price" in row}


async def run_once(app: Any) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    await ensure_schema(pool)
    rows = await pool.fetch(f"SELECT id, user_id, product_id, level, direction, ref_price, exchange FROM {TABLE} WHERE triggered_at IS NULL")
    by_user: dict[str, list[dict[str, Any]]] = {}
    binance: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        (binance if str(row["exchange"]) == "binance" else by_user).setdefault(str(row["user_id"]), []).append({**_row(row)})
    total = 0
    if binance:
        try:
            quotes = await binance_prices(app)
            for user_id, alerts in binance.items():
                total += len(await check_user(pool, user_id, alerts, quotes, send=_push))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Binance fiyat alarmı turu atlandı (%s)", type(exc).__name__)
    for user_id, alerts in by_user.items():
        try:
            stored = await vault.load_key(pool, user_id)
            if stored is None:
                continue
            client = CoinbaseClient(stored[0], load_private_key(stored[1]))
            ids = list(dict.fromkeys(a["product_id"] for a in alerts))
            quotes: dict[str, Any] = {}
            for start in range(0, len(ids), market.MAX_BATCH_QUOTES):
                quotes.update(await market.fetch_quotes(client, ids[start:start + market.MAX_BATCH_QUOTES]))
            total += len(await check_user(pool, user_id, alerts, quotes, send=_push))
        except Exception as exc:  # noqa: BLE001 - bir hesap diğerlerini durdurmaz
            logger.warning("Coinbase fiyat alarmı bu hesabı atladı (%s)", type(exc).__name__)
    return total


async def alert_loop(app: Any) -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await run_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Coinbase fiyat alarmı turu başarısız")
        await asyncio.sleep(INTERVAL_SECONDS)

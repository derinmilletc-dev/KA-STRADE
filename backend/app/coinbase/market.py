"""Coinbase piyasa verisi (SALT OKUNUR): vadeli ürün listesi ve anlık fiyat. Emir yolu YOK.

Liste kısa süre önbelleğe alınır (Coinbase istek sınırını korumak için). Cevap biçimi bozuksa açık hata verilir;
eksik alan uydurulmaz (None kalır).
"""

from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Any

from .errors import CoinbaseError
from .money import fmt, to_decimal

PRODUCTS_PATH = "/api/v3/brokerage/products"
BEST_BID_ASK_PATH = "/api/v3/brokerage/best_bid_ask"
CACHE_SECONDS = 20.0
_ROOT = re.compile(r"^[A-Z0-9]{2,10}$")
_CACHE: dict[str, Any] = {"at": None, "rows": None}


def reset_cache() -> None:
    _CACHE.update(at=None, rows=None)


def _text(value: Any) -> str | None:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    return None


def _number(value: Any) -> str | None:
    """Sayı olarak okunabiliyorsa düz metin, değilse None (bozuk sayı sessizce 0 olmaz)."""
    text = _text(value)
    if text is None:
        return None
    try:
        number = Decimal(text)
    except Exception:  # noqa: BLE001
        return None
    return fmt(number) if number.is_finite() else None


def normalize_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("products")
    if not isinstance(rows, list):
        raise CoinbaseError("BAD_RESPONSE", "Coinbase ürün listesi beklenen biçimde değil: products yok.")
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CoinbaseError("BAD_RESPONSE", "Coinbase ürün listesinde bozuk satır var.")
        product_id = _text(row.get("product_id"))
        if not product_id:
            raise CoinbaseError("BAD_RESPONSE", "Coinbase ürün satırında product_id yok.")
        details = row.get("future_product_details")
        details = details if isinstance(details, dict) else {}
        root_raw = _text(details.get("contract_root_unit"))
        root = root_raw.upper() if root_raw and _ROOT.match(root_raw.upper()) else None
        status = _text(row.get("status"))
        tradable = row.get("trading_disabled") is not True and (status is None or status.lower() == "online")
        result.append({
            "product_id": product_id,
            "display": _text(row.get("display_name")) or product_id,
            "root": root,
            "price": _number(row.get("price")),
            "change_pct": _number(row.get("price_percentage_change_24h")),
            "volume_usd": _number(row.get("approximate_quote_24h_volume")),
            "expiry": _text(details.get("contract_expiry")),
            "contract_size": _number(details.get("contract_size")),
            "tradable": tradable,
        })
    result.sort(key=lambda item: Decimal(item["volume_usd"]) if item["volume_usd"] is not None else Decimal(-1), reverse=True)
    return result


async def fetch_products(client: Any, *, monotonic: Any = time.monotonic) -> list[dict[str, Any]]:
    now = monotonic()
    if _CACHE["rows"] is not None and _CACHE["at"] is not None and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["rows"]
    payload = await client.get(PRODUCTS_PATH, params={"product_type": "FUTURE"})
    rows = normalize_products(payload)
    _CACHE.update(at=now, rows=rows)
    return rows


async def fetch_quote(client: Any, product_id: str) -> dict[str, str]:
    payload = await client.get(BEST_BID_ASK_PATH, params={"product_ids": [product_id]})
    books = payload.get("pricebooks")
    book = next((b for b in books if isinstance(b, dict) and b.get("product_id") == product_id), None) if isinstance(books, list) else None
    if book is None:
        raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı beklenen biçimde değil: ürün fiyatı yok.")

    def top(side: str) -> Decimal:
        levels = book.get(side)
        if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
            raise CoinbaseError("BAD_RESPONSE", f"Coinbase fiyat cevabı beklenen biçimde değil: {side} boş.")
        price = to_decimal(levels[0].get("price"), "fiyat")
        if price <= 0:
            raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı geçersiz (sıfır/negatif).")
        return price

    bid, ask = top("bids"), top("asks")
    if bid > ask:
        raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı tutarsız (alış > satış).")
    mid = (bid + ask) / 2
    return {"product_id": product_id, "bid": fmt(bid), "ask": fmt(ask), "mid": fmt(mid.quantize(Decimal("0.0000001")).normalize()),
            "spread_pct": fmt(((ask - bid) / mid * 100).quantize(Decimal("0.001")))}

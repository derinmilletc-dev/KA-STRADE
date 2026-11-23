"""Kullanıcının kendi Coinbase risk limitleri (hesap başına, veritabanında).

Sunucu ortam değişkenleri (COINBASE_*) TAVAN olarak kalır: kullanıcı yalnız o tavanın içinde, daha sıkı bir değer seçebilir.
Boş bırakılan alan "özel limit yok" demektir ve sunucu varsayılanı geçerlidir. Kayıtlı limit yoksa davranış değişmez.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import CoinbaseError
from .limits import Limits, current_limits

TABLE = "kaistrade_coinbase_user_limits"
FIELDS = ("max_loss_usd", "daily_loss_limit_usd", "leverage_max", "stop_limit_gap_pct")
GAP_MIN, GAP_MAX = Decimal("0.0005"), Decimal("0.02")
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          user_id TEXT PRIMARY KEY,
          settings JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def _dec(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def bounds(ceiling: Limits) -> dict[str, tuple[Decimal, Decimal]]:
    return {
        "max_loss_usd": (Decimal("0.1"), ceiling.max_loss_usd),
        "daily_loss_limit_usd": (Decimal("1"), ceiling.daily_loss_limit_usd),
        "leverage_max": (ceiling.leverage_min, ceiling.leverage_max),
        "stop_limit_gap_pct": (GAP_MIN, GAP_MAX),
    }


def validate(values: dict[str, Any], ceiling: Limits | None = None) -> dict[str, str]:
    """Gelen alanları doğrular; boş/None alan 'özel limit yok' demektir. Geçersiz değer açık hata verir (sessizce düzeltilmez)."""
    ceiling = ceiling or current_limits()
    limits = bounds(ceiling)
    clean: dict[str, str] = {}
    for field in FIELDS:
        raw = values.get(field)
        if raw in (None, ""):
            continue
        number = _dec(raw)
        low, high = limits[field]
        if number is None or number < low or number > high:
            raise CoinbaseError("BAD_LIMIT", f"{field} değeri {low} ile {high} arasında bir sayı olmalı.", http_status=422)
        clean[field] = format(number.normalize(), "f")
    return clean


async def load(pool: Any, user_id: str) -> dict[str, str]:
    await ensure_schema(pool)
    row = await pool.fetchrow(f"SELECT settings FROM {TABLE} WHERE user_id = $1", user_id)
    if row is None:
        return {}
    settings = row["settings"]
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except ValueError:
            return {}
    if not isinstance(settings, dict):
        return {}
    ceiling = current_limits()
    try:
        return validate(settings, ceiling)  # tavan sonradan sıkılaştıysa eski kayıt geçersiz sayılır, tavan geçerli kalır
    except CoinbaseError:
        return {}


async def save(pool: Any, user_id: str, values: dict[str, Any]) -> dict[str, str]:
    await ensure_schema(pool)
    clean = validate(values)
    await pool.execute(
        f"""
        INSERT INTO {TABLE} (user_id, settings) VALUES ($1, $2::jsonb)
        ON CONFLICT (user_id) DO UPDATE SET settings = EXCLUDED.settings, updated_at = NOW()
        """,
        user_id, json.dumps(clean),
    )
    return clean


def apply(base: Limits, custom: dict[str, str]) -> Limits:
    changes: dict[str, Decimal] = {}
    for field in FIELDS:
        number = _dec(custom.get(field))
        if number is None:
            continue
        if field == "leverage_max":
            number = max(number, base.leverage_min)
        changes[field] = number
    return replace(base, **changes)


async def effective(pool: Any, user_id: str) -> Limits:
    """Emir akışlarının kullanacağı limit: sunucu tavanı + kullanıcının kendi (daha sıkı) değerleri."""
    return apply(current_limits(), await load(pool, user_id))


def describe(ceiling: Limits, custom: dict[str, str]) -> dict[str, Any]:
    eff = apply(ceiling, custom)
    return {
        "custom": custom,
        "effective": {field: format(getattr(eff, field).normalize(), "f") for field in FIELDS},
        "ceiling": {field: format(getattr(ceiling, field).normalize(), "f") for field in FIELDS},
        "bounds": {field: [format(low.normalize(), "f"), format(high.normalize(), "f")] for field, (low, high) in bounds(ceiling).items()},
        "max_open_orders": ceiling.max_open_orders,
    }

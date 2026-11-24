"""Binance gerçekleşen K/Z geçmişinden kişisel kalıplar (SALT OKUNUR). Kaynak: /fapi/v1/income (REALIZED_PNL), tek istek.

Gelir geçmişi yön ve açılış zamanı vermez; aynı coinde 2 dk içindeki kapanış satırları tek işlem sayılır (kısmi kapanışlar).
Komisyon ve fonlama dahil DEĞİLDİR: küçük kazançlar komisyondan sonra zarar olabilir. Sonuç 10 dk önbelleklenir."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from . import personal_stats

DAYS = 30
LIMIT = 1000
GROUP_SECONDS = 120
CACHE_SECONDS = 600.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def reset() -> None:
    _cache.clear()


def to_trades(rows: Any) -> list[dict[str, Any]]:
    """REALIZED_PNL satırlarını işlemlere çevirir. Geçersiz/USDT dışı satırlar atlanır."""
    items: list[tuple[str, int, float]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("incomeType") != "REALIZED_PNL" or row.get("asset") not in (None, "USDT"):
            continue
        try:
            stamp, amount = int(row["time"]), float(row["income"])
        except (KeyError, TypeError, ValueError):
            continue
        key = str(row.get("tranId") or f"{row.get('symbol')}:{stamp}:{amount}")
        if key in seen or amount != amount or abs(amount) == float("inf"):
            continue
        seen.add(key)
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            items.append((symbol, stamp, amount))
    items.sort(key=lambda x: (x[0], x[1]))
    trades: list[dict[str, Any]] = []
    for symbol, stamp, amount in items:
        last = trades[-1] if trades else None
        if last and last["product_id"] == symbol and stamp - last["_ms"] <= GROUP_SECONDS * 1000:
            last["net"] += amount
            last["_ms"] = stamp
            last["time"] = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).isoformat()
        else:
            trades.append({"product_id": symbol, "side": "", "net": amount, "_ms": stamp, "opened": None,
                           "time": datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).isoformat()})
    trades.sort(key=lambda t: t["_ms"])
    return [{k: v for k, v in t.items() if k != "_ms"} for t in trades]


async def load(client: Any, user_key: str, now: float | None = None) -> dict[str, Any]:
    stamp = time.time() if now is None else now
    hit = _cache.get(user_key)
    if hit and stamp - hit[0] < CACHE_SECONDS:
        return hit[1]
    rows = await client.signed("GET", "/fapi/v1/income", {"incomeType": "REALIZED_PNL", "startTime": int((stamp - DAYS * 86400) * 1000), "limit": LIMIT})
    trades = to_trades(rows)
    result = personal_stats.analyze(trades, basis="closed")
    result["days"] = DAYS
    result["truncated"] = isinstance(rows, list) and len(rows) >= LIMIT
    result["caveat"] = "Binance gelir geçmişine göre; komisyon ve fonlama dahil değil, işlemin yönü bilinmiyor."
    _cache[user_key] = (stamp, result)
    if len(_cache) > 200:
        for stale in sorted(_cache, key=lambda k: _cache[k][0])[:50]:
            _cache.pop(stale, None)
    return result

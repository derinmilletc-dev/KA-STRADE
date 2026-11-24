"""Yorum motorunun veri tarafı: mumları ve futures verisini toplar, coin_read motorunu çalıştırır, kısa süre önbellekler.

Yalnız herkese açık piyasa verisi (anahtar yok, emir yok). Ekran, Analist sayfası ve pozisyon alarmı aynı sonucu paylaşır;
aynı coin için aynı anda gelen istekler tek hesaba bağlanır."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from . import coin_read

logger = logging.getLogger(__name__)

SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")
CANDLE_LIMIT = 200
HISTORY_LIMIT = 1000
READ_TTL = 20.0
FUTURES_TTL = 60.0
HISTORY_TTL = 600.0
ALARM_TFS = ("5m", "15m", "1h", "4h", "1d")
Fetch = Callable[[str, str, int], Awaitable[list[dict[str, Any]]]]
Get = Callable[[str, dict[str, Any]], Awaitable[Any]]

_reads: dict[str, tuple[float, dict[str, Any]]] = {}
_futures: dict[str, tuple[float, dict[str, Any] | None]] = {}
_history: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}


def reset() -> None:
    _reads.clear(); _futures.clear(); _history.clear(); _locks.clear()


def valid_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    return symbol if SYMBOL.match(symbol) else None


def _default_fetch() -> Fetch:
    async def fetch(symbol: str, tf: str, limit: int) -> list[dict[str, Any]]:
        from .main import fetch_candles   # geç içe aktarma: main bu modülü başlangıçta yükler
        return await fetch_candles(symbol, tf, limit)
    return fetch


def _default_get(app: Any) -> Get:
    async def get(path: str, params: dict[str, Any]) -> Any:
        from .main import market_data_request
        response = await market_data_request(app, path, params, source="analysis")
        response.raise_for_status()
        return response.json()
    return get


async def gather_candles(symbol: str, tfs: tuple[str, ...], fetch: Fetch) -> dict[str, list[dict[str, Any]]]:
    results = await asyncio.gather(*(fetch(symbol, tf, CANDLE_LIMIT) for tf in tfs), return_exceptions=True)
    out = {tf: rows for tf, rows in zip(tfs, results) if isinstance(rows, list) and rows}
    if not out:
        raise RuntimeError("mum verisi alınamadı")
    return out


def _floats(rows: Any, key: str) -> list[float]:
    values = []
    for row in rows if isinstance(rows, list) else []:
        try:
            values.append(float(row[key]))
        except (KeyError, TypeError, ValueError):
            continue
    return values


async def fetch_futures_raw(symbol: str, price_change_pct: float | None, get: Get) -> dict[str, Any] | None:
    """Fonlama, açık pozisyon değişimi (4 saat) ve alıcı/satıcı oranı (son 1 saat). Biri alınamazsa o parça boş kalır."""
    funding = oi_change = taker = None
    try:
        funding = float((await get("/fapi/v1/premiumIndex", {"symbol": symbol}))["lastFundingRate"])
    except Exception:  # noqa: BLE001 - parça düşer, yorum devam eder
        funding = None
    try:
        values = _floats(await get("/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 5}), "sumOpenInterest")
        if len(values) >= 2 and values[0] > 0:
            oi_change = coin_read.pct(values[-1], values[0])
    except Exception:  # noqa: BLE001
        oi_change = None
    try:
        rows = await get("/futures/data/takerlongshortRatio", {"symbol": symbol, "period": "5m", "limit": 12})
        buy, sell = sum(_floats(rows, "buyVol")), sum(_floats(rows, "sellVol"))
        taker = buy / sell if sell > 0 else None
    except Exception:  # noqa: BLE001
        taker = None
    if funding is None and oi_change is None and taker is None:
        return None
    return {"funding": funding, "oi_change_pct": oi_change, "price_change_pct": price_change_pct, "taker_ratio": taker}


def _price_change_4h(candles: dict[str, list[dict[str, Any]]]) -> float | None:
    rows = coin_read.clean(candles.get("1h"))[:-1]
    return coin_read.pct(rows[-1]["close"], rows[-5]["close"]) if len(rows) >= 5 else None


async def read(app: Any, symbol: str, *, history_tf: str | None = None, with_futures: bool = True,
               tfs: tuple[str, ...] = coin_read.TFS, fetch: Fetch | None = None, get: Get | None = None,
               now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Tam okuma. Aynı (coin, süre, futures) için 20 sn önbellek; futures 60 sn, geçmiş doğruluk 10 dk."""
    fetch = fetch or _default_fetch()
    key = f"{symbol}|{history_tf or ''}|{int(with_futures)}|{','.join(tfs)}"
    hit = _reads.get(key)
    if hit and now() - hit[0] < READ_TTL:
        return hit[1]
    async with _locks.setdefault(symbol, asyncio.Lock()):
        hit = _reads.get(key)
        if hit and now() - hit[0] < READ_TTL:
            return hit[1]
        candles = await gather_candles(symbol, tfs, fetch)
        futures_raw = None
        if with_futures:
            cached = _futures.get(symbol)
            if cached and now() - cached[0] < FUTURES_TTL:
                futures_raw = cached[1]
            else:
                futures_raw = await fetch_futures_raw(symbol, _price_change_4h(candles), get or _default_get(app))
                _futures[symbol] = (now(), futures_raw)
        history = None
        if history_tf in coin_read.TFS:
            cached_h = _history.get((symbol, history_tf))
            if cached_h and now() - cached_h[0] < HISTORY_TTL:
                history = cached_h[1]
            else:
                try:
                    history = coin_read.backtest(await fetch(symbol, history_tf, HISTORY_LIMIT))
                    _history[(symbol, history_tf)] = (now(), history)
                except Exception as exc:  # noqa: BLE001 - geçmiş doğruluk olmadan da yorum çıkar
                    logger.info("Yorum geçmiş doğruluğu hesaplanamadı (%s)", type(exc).__name__)
        result = coin_read.build(candles, futures_raw, history=history, history_tf=history_tf)
        result["symbol"] = symbol
        _reads[key] = (now(), result)
        if len(_reads) > 400:
            for stale in sorted(_reads, key=lambda k: _reads[k][0])[:100]:
                _reads.pop(stale, None)
        return result

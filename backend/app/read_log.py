"""Yorum doğruluk takibi: ekranda gösterilen yönlü yorumları kaydeder, 1 saat sonra fiyatla karşılaştırır.

"Tuttu" = yorumun yönünde en az %0,1 hareket; "tutmadı" = ters yönde en az %0,1; arası "yatay". Bu bir ticaret başarısı değil,
yorumun kısa vadeli yön isabetidir. Yalnız insanların baktığı/alarm kurduğu coinler kaydedilir; başlangıçtan önceki geçmişi yoktur."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import coin_read, coin_read_service

logger = logging.getLogger(__name__)

TABLE = "kaistrade_read_log"
HORIZON = timedelta(hours=1)
FLAT_BAND_PCT = 0.1
LOG_EVERY_SECONDS = 900
EVAL_INTERVAL_SECONDS = 300
GIVE_UP = timedelta(hours=40)     # 15 dk mum geçmişi (200 mum) bu kadarını kapsar
STATS_DAYS = 7
MIN_SHOWN = 10                    # bundan az örnekte oran gösterilmez
_SCHEMA_DONE: set[int] = set()
_last_logged: dict[str, float] = {}


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          id BIGSERIAL PRIMARY KEY,
          symbol TEXT NOT NULL,
          logged_at TIMESTAMPTZ NOT NULL,
          lean TEXT NOT NULL,
          confidence TEXT NOT NULL,
          bias DOUBLE PRECISION NOT NULL,
          price DOUBLE PRECISION NOT NULL,
          later_price DOUBLE PRECISION,
          result TEXT
        )
        """)
    await pool.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_open_idx ON {TABLE} (logged_at) WHERE result IS NULL")
    await pool.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_symbol_idx ON {TABLE} (symbol, logged_at)")
    _SCHEMA_DONE.add(id(pool))


def reset_state() -> None:
    _SCHEMA_DONE.clear(); _last_logged.clear()


def grade(lean: str, price: float, later: float) -> str | None:
    """'hit' | 'miss' | 'flat'; yön ya da fiyat geçersizse None."""
    if lean not in ("LONG", "SHORT") or not (price > 0 and later > 0):
        return None
    move = (later / price - 1) * 100 * (1 if lean == "LONG" else -1)
    return "hit" if move >= FLAT_BAND_PCT else "miss" if move <= -FLAT_BAND_PCT else "flat"


def price_at(rows: Any, target: datetime) -> float | None:
    """Hedef andan önceki son kapanışlı mumun kapanışı; hedef mum verisinin dışındaysa None."""
    candles = coin_read.clean(rows)
    if not candles:
        return None
    ts = target.timestamp()
    scale = 1000.0 if candles[-1]["time"] > 1e11 else 1.0
    pick = None
    for c in candles:
        if c["time"] / scale <= ts:
            pick = c
    if pick is None or ts - pick["time"] / scale > 3600 or candles[-1]["time"] / scale < ts:
        return None
    return pick["close"]


def tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"n": 0, "hit": 0, "miss": 0, "flat": 0, "rate": None}
    for row in rows:
        r = row.get("result")
        if r in ("hit", "miss", "flat"):
            out[r] += 1; out["n"] += 1
    decided = out["hit"] + out["miss"]
    out["rate"] = round(out["hit"] / decided * 100) if out["n"] >= MIN_SHOWN and decided else None
    return out


async def maybe_log(app: Any, symbol: str, summary: dict[str, Any], price: float) -> None:
    pool = getattr(app.state, "db_pool", None) if app is not None else None
    if pool is None or summary.get("lean") not in ("LONG", "SHORT") or price <= 0:
        return
    now = time.monotonic()
    if now - _last_logged.get(symbol, -1e9) < LOG_EVERY_SECONDS:
        return
    _last_logged[symbol] = now
    try:
        await ensure_schema(pool)
        await pool.execute(f"INSERT INTO {TABLE} (symbol, logged_at, lean, confidence, bias, price) VALUES ($1, $2, $3, $4, $5, $6)",
                           symbol, datetime.now(timezone.utc), summary["lean"], summary["confidence"], float(summary["bias"]), float(price))
    except Exception as exc:  # noqa: BLE001 - kayıt düşerse yorum etkilenmez
        logger.info("Yorum kaydı yazılamadı (%s)", type(exc).__name__)


async def evaluate_once(app: Any, now: datetime | None = None, fetch: Any = None) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    rows = await pool.fetch(f"SELECT id, symbol, logged_at, lean, price FROM {TABLE} WHERE result IS NULL AND logged_at <= $1 ORDER BY logged_at LIMIT 300", now - HORIZON - timedelta(minutes=16))
    if not rows:
        return 0
    fetch = fetch or coin_read_service._default_fetch()
    candles: dict[str, Any] = {}
    done = 0
    for row in rows:
        symbol = str(row["symbol"])
        logged = row["logged_at"]
        if now - logged > GIVE_UP:
            await pool.execute(f"UPDATE {TABLE} SET result = 'skip' WHERE id = $1", int(row["id"]))
            continue
        if symbol not in candles:
            try:
                candles[symbol] = await fetch(symbol, "15m", 200)
            except Exception:  # noqa: BLE001
                candles[symbol] = None
        if candles[symbol] is None:
            continue
        later = price_at(candles[symbol], logged + HORIZON)
        verdict = grade(str(row["lean"]), float(row["price"]), later) if later else None
        if verdict:
            await pool.execute(f"UPDATE {TABLE} SET later_price = $2, result = $3 WHERE id = $1", int(row["id"]), later, verdict)
            done += 1
    return done


async def stats(pool: Any, symbol: str | None) -> dict[str, Any]:
    await ensure_schema(pool)
    since = datetime.now(timezone.utc) - timedelta(days=STATS_DAYS)
    all_rows = await pool.fetch(f"SELECT result, confidence FROM {TABLE} WHERE logged_at >= $1 AND result IN ('hit','miss','flat')", since)
    all_rows = [{"result": r["result"], "confidence": r["confidence"]} for r in all_rows]
    out: dict[str, Any] = {"days": STATS_DAYS, "overall": tally(all_rows), "by_confidence": {c: tally([r for r in all_rows if r["confidence"] == c]) for c in ("Yüksek", "Orta", "Düşük")}, "symbol": None}
    if symbol:
        sym_rows = await pool.fetch(f"SELECT result FROM {TABLE} WHERE logged_at >= $1 AND symbol = $2 AND result IN ('hit','miss','flat')", since, symbol)
        out["symbol"] = {"symbol": symbol, **tally([{"result": r["result"]} for r in sym_rows])}
    return out


async def eval_loop(app: Any) -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await evaluate_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Yorum doğruluk değerlendirmesi başarısız")
        await asyncio.sleep(EVAL_INTERVAL_SECONDS)

"""Emirsiz ileriye dönük test: puanlama motorunun (analysis.analyze) CANLI piyasada gerçekten işe yarayıp yaramadığını ölçer.

Her 15 dakikada en likit coinleri tarar, motorun verdiği LONG/SHORT sinyallerini kendi küçük veritabanına yazar, sonra gerçek
sonraki mumlarla sonucu çözer (stop mu, TP1 mi, 24 saat dolunca kapanış mı; komisyon + kayma sonrası R).

Güvenlik: hiçbir emir/anahtar/hesap bilgisi kullanmaz, yalnız herkese açık piyasa verisini okur, canlı sinyal günlüğüne ve hesaplara
dokunmaz. Hata durumunda uygulamayı etkilemez.

Varsayımlar (rapor da yazar): giriş, sinyal mumunu izleyen mumun açılışı; aynı mumda stop ve TP1 birlikte görülürse STOP sayılır
(kötümser); komisyon+kayma toplam 15 baz puan (gidiş-dönüş); tek hedef TP1; 24 saat içinde ikisi de olmazsa son kapanışla çıkılır.
Aynı coin+yönde 4 saat içinde tekrar eden sinyal sayılmaz (ardışık taramalar bağımsız örnek değildir)."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .analysis import analyze

logger = logging.getLogger(__name__)

BAR_SECONDS = 900
HORIZON_BARS = 96                 # 24 saat
COOLDOWN_SECONDS = 4 * 3600
UNIVERSE_SIZE = 30
KLINE_LIMIT = 260
COST_BPS = 15.0                   # komisyon (10) + kayma/spread (5), gidiş-dönüş, notional üzerinden
GATE_CONFIDENCE = 80              # canlı kapı varsayılanları (execution_core.DEFAULT_MIN_CONFIDENCE, max_trap_score)
GATE_TRAP = 35
MAX_RESOLVE_PER_TICK = 200

Get = Callable[[str, dict[str, Any]], Awaitable[Any]]


def enabled() -> bool:
    return str(os.getenv("KAIS_FORWARD_TEST", "1")).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def default_path() -> Path:
    from .local_storage import DATA_DIR
    return Path(os.environ.get("KAIS_FORWARD_TEST_PATH") or DATA_DIR / "forward-test.sqlite3")


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_klines(rows: Any) -> list[dict[str, float]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        try:
            out.append({"time": int(row[0] / 1000), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
        except (TypeError, ValueError, IndexError):
            continue
    return out


def closed_only(candles: list[dict[str, float]], now: float) -> list[dict[str, float]]:
    return [c for c in candles if c["time"] + BAR_SECONDS <= now]


def pick_universe(tickers: Any, size: int = UNIVERSE_SIZE) -> list[str]:
    rows = []
    for item in tickers if isinstance(tickers, list) else []:
        symbol = str(item.get("symbol") or "")
        volume = _num(item.get("quoteVolume"))
        if symbol.endswith("USDT") and "_" not in symbol and volume:
            rows.append((volume, symbol))
    return [symbol for _, symbol in sorted(rows, reverse=True)[:size]]


def signal_record(symbol: str, candles: list[dict[str, float]], now: float) -> dict[str, Any] | None:
    """Kapanmış mumlarla motoru çalıştırır; LONG/SHORT değilse ya da seviyeler geçersizse None."""
    closed = closed_only(candles, now)
    if len(closed) < 210:
        return None
    result = analyze(closed)
    direction = result.get("direction")
    if direction not in ("LONG", "SHORT"):
        return None
    entry, stop, tp1 = _num(result.get("entry")), _num(result.get("stop_loss")), _num(result.get("tp1"))
    if entry is None or stop is None or tp1 is None:
        return None
    ordered = stop < entry < tp1 if direction == "LONG" else tp1 < entry < stop
    if not ordered:
        return None
    ema = result.get("ema") or {}
    radar = result.get("radar") or {}
    return {
        "symbol": symbol, "direction": direction, "signal_time": closed[-1]["time"] + BAR_SECONDS,
        "confidence": result.get("confidence"), "trap": _num(radar.get("trap_score")), "entry": entry, "stop": stop,
        "tp1": tp1, "tp2": _num(result.get("tp2")), "tp3": _num(result.get("tp3")), "atr": _num(result.get("atr")),
        "ema20": _num(ema.get("ema20")), "ema50": _num(ema.get("ema50")), "ema200": _num(ema.get("ema200")),
        "macd_hist": _num(result.get("macd")), "rsi": _num(result.get("rsi")), "adx": _num(result.get("adx")),
        "vol_ratio": _num(result.get("volume_ratio")), "resistance": _num(result.get("resistance")), "support": _num(result.get("support")),
        "trend": result.get("trend"),
    }


def resolve(record: dict[str, Any], candles: list[dict[str, float]], now: float) -> dict[str, Any] | None:
    """Sinyal mumundan sonraki KAPANMIŞ mumlarla sonucu çözer. Henüz belli değilse None (bekler)."""
    bars = [c for c in closed_only(candles, now) if c["time"] >= record["signal_time"]][:HORIZON_BARS]
    if not bars:
        return None
    long = record["direction"] == "LONG"
    fill, stop, tp1 = bars[0]["open"], record["stop"], record["tp1"]
    risk = (fill - stop) if long else (stop - fill)
    reward = (tp1 - fill) if long else (fill - tp1)
    if risk <= 0 or reward <= 0:       # açılış zaten stop/hedefin ötesinde: dolum gerçekçi değil
        return {"hypothetical_result": "INVALID", "hypothetical_r_gross": None, "hypothetical_r_costs": None, "fill_price": fill}
    cost_r = (COST_BPS / 10000.0) * fill / risk
    mfe = mae = 0.0
    result, gross, held = None, 0.0, len(bars)
    for index, bar in enumerate(bars):
        favorable = ((bar["high"] - fill) if long else (fill - bar["low"])) / risk
        adverse = ((fill - bar["low"]) if long else (bar["high"] - fill)) / risk
        stop_hit = (bar["low"] <= stop) if long else (bar["high"] >= stop)
        tp_hit = (bar["high"] >= tp1) if long else (bar["low"] <= tp1)
        if stop_hit:                   # aynı mumda ikisi de ise kötümser: STOP
            result, gross, held = "STOP", -1.0, index + 1
            mae = max(mae, 1.0)
            break
        mfe, mae = max(mfe, favorable), max(mae, adverse)
        if tp_hit:
            result, gross, held = "TP1", reward / risk, index + 1
            break
    if result is None:
        if len(bars) < HORIZON_BARS:
            return None
        last = bars[-1]["close"]
        result, gross = "TIMEOUT", ((last - fill) if long else (fill - last)) / risk
    return {"hypothetical_result": result, "hypothetical_r_gross": round(gross, 4), "hypothetical_r_costs": round(gross - cost_r, 4),
            "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "hold_bars": held, "fill_price": fill, "resolved_at": int(now)}


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS forward_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL, signal_time INTEGER NOT NULL,
                record TEXT NOT NULL, outcome TEXT, UNIQUE(symbol, signal_time))""")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(self, record: dict[str, Any]) -> bool:
        with self._db() as db:
            cursor = db.execute("INSERT OR IGNORE INTO forward_signals(symbol, direction, signal_time, record) VALUES(?,?,?,?)",
                                (record["symbol"], record["direction"], record["signal_time"], json.dumps(record)))
            return cursor.rowcount > 0

    def recent(self, symbol: str, direction: str, since: int) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM forward_signals WHERE symbol=? AND direction=? AND signal_time>? LIMIT 1", (symbol, direction, since)).fetchone() is not None

    def pending(self, limit: int = MAX_RESOLVE_PER_TICK) -> list[tuple[int, dict[str, Any]]]:
        with self._db() as db:
            rows = db.execute("SELECT id, record FROM forward_signals WHERE outcome IS NULL ORDER BY signal_time LIMIT ?", (limit,)).fetchall()
        return [(row["id"], json.loads(row["record"])) for row in rows]

    def save_outcome(self, row_id: int, outcome: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("UPDATE forward_signals SET outcome=? WHERE id=?", (json.dumps(outcome), row_id))

    def records(self) -> list[dict[str, Any]]:
        """score_report'un okuduğu biçim: sinyal + sonuç + canlı kapı kararı."""
        with self._db() as db:
            rows = db.execute("SELECT record, outcome FROM forward_signals").fetchall()
        out = []
        for row in rows:
            record = json.loads(row["record"])
            outcome = json.loads(row["outcome"]) if row["outcome"] else {}
            passed = (_num(record.get("confidence")) or 0) >= GATE_CONFIDENCE and (_num(record.get("trap")) if record.get("trap") is not None else 100) <= GATE_TRAP
            out.append({**record, **outcome, "decision": "ACCEPTED" if passed else "REJECTED"})
        return out

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            total = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
            done = db.execute("SELECT COUNT(*) FROM forward_signals WHERE outcome IS NOT NULL").fetchone()[0]
            first = db.execute("SELECT MIN(signal_time) FROM forward_signals").fetchone()[0]
        return {"signals": total, "resolved": done, "pending": total - done, "first_signal_time": first}


async def scan_once(get: Get, store: Store, *, now: float | None = None, universe: int = UNIVERSE_SIZE) -> dict[str, int]:
    now = time.time() if now is None else now
    stats: Counter[str] = Counter()
    symbols = pick_universe(await get("/fapi/v1/ticker/24hr", {}), universe)
    gate = asyncio.Semaphore(4)

    async def scan(symbol: str) -> None:
        async with gate:
            try:
                candles = parse_klines(await get("/fapi/v1/klines", {"symbol": symbol, "interval": "15m", "limit": KLINE_LIMIT}))
                record = signal_record(symbol, candles, now)
                stats["scanned"] += 1
                if record is None:
                    stats["no_signal"] += 1
                elif store.recent(symbol, record["direction"], record["signal_time"] - COOLDOWN_SECONDS):
                    stats["cooldown"] += 1
                elif store.add(record):
                    stats["new"] += 1
            except Exception:  # noqa: BLE001 - tek coin hatası taramayı durdurmaz
                stats["errors"] += 1

    async def settle(row_id: int, record: dict[str, Any]) -> None:
        async with gate:
            try:
                rows = await get("/fapi/v1/klines", {"symbol": record["symbol"], "interval": "15m", "startTime": record["signal_time"] * 1000, "limit": HORIZON_BARS + 2})
                outcome = resolve(record, parse_klines(rows), now)
                if outcome is not None:
                    store.save_outcome(row_id, outcome)
                    stats["resolved"] += 1
            except Exception:  # noqa: BLE001
                stats["errors"] += 1

    await asyncio.gather(*(scan(s) for s in symbols))
    await asyncio.gather(*(settle(i, r) for i, r in store.pending()))
    return dict(stats)


async def forward_loop(app: Any, get: Get) -> None:
    """Uygulama içi arka plan döngüsü: her 15 dakikalık mum kapanışından ~40 sn sonra bir tur."""
    if not enabled():
        logger.info("FORWARD_TEST kapalı (KAIS_FORWARD_TEST=0)")
        return
    store = Store(default_path())
    await asyncio.sleep(120)
    while True:
        try:
            stats = await scan_once(get, store)
            logger.info("FORWARD_TEST tur: %s toplam=%s", stats, store.counts())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("FORWARD_TEST turu başarısız")
        now = time.time()
        await asyncio.sleep(BAR_SECONDS - (now % BAR_SECONDS) + 40)

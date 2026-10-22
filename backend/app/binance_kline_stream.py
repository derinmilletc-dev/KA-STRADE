from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from .binance_rate_limit import BINANCE_GLOBAL_BAN
from .public_market_data import protected_lane_available
from .public_market_data import ORIGINAL_INTERVALS

logger = logging.getLogger(__name__)

# Binance 2026-04-23'te eski /ws adresini emekliye ayırdı: kline gibi "market" kanalları yalnız /market altından akar.
# Eski adrese bağlanınca bağlantı açılır ama mum gelmez; 45 sn sessizlikten sonra koparak döngüye girer.
KLINE_STREAM_URL = "wss://fstream.binance.com/market/ws"
IDLE_TIMEOUT_SECONDS = 45
MAX_RECONNECT_DELAY_SECONDS = 60
MAX_CACHED_CANDLES = 1500
ALLOWED_INTERVALS = frozenset(ORIGINAL_INTERVALS)
INTERVAL_MILLISECONDS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


class KlineStreamUnavailable(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Binance geçici engeli sürerken mum önbelleği eksik.")
        self.retry_after = retry_after


class BinanceKlineStream:
    def __init__(
        self,
        *,
        connector: Callable[..., Any] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._connector = connector or websockets.connect
        self._wall_clock = wall_clock
        self._symbols: tuple[str, ...] = ()
        self._intervals: tuple[str, ...] = ()
        self._candles: dict[tuple[str, str], list[list[Any]]] = {}
        self._configure_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._generation = 0
        self.connected = False
        self.messages_received = 0
        self.invalid_messages = 0
        self.reconnects = 0
        self.rest_backfills = 0
        self.last_message_at: float | None = None
        self.last_kline_at: float | None = None
        self.last_disconnect_reason: str | None = None
        self._subscribed: frozenset[tuple[str, str]] = frozenset()

    def is_subscribed(self, symbol: str, interval: str) -> bool:
        return (symbol.upper(), interval) in self._subscribed

    def candles(self, symbol: str, interval: str) -> list[list[Any]]:
        return [row.copy() for row in self._candles.get((symbol.upper(), interval), [])]

    def set_candles(self, symbol: str, interval: str, rows: list[list[Any]]) -> None:
        self._merge_rows(symbol.upper(), interval, rows)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "symbols": len(self._symbols),
            "intervals": list(self._intervals),
            "stream_count": len(self._symbols) * len(self._intervals),
            "messages_received": self.messages_received,
            "invalid_messages": self.invalid_messages,
            "reconnects": self.reconnects,
            "rest_backfills": self.rest_backfills,
            "last_message_at": self.last_message_at,
            "last_kline_at": self.last_kline_at,
            "last_disconnect_reason": self.last_disconnect_reason,
            "source": "binance_futures_kline_websocket",
        }

    async def configure(self, symbols: list[str], intervals: tuple[str, ...] = ORIGINAL_INTERVALS) -> None:
        if (
            not isinstance(symbols, list)
            or len(symbols) > 100
            or any(
                not isinstance(symbol, str)
                or not symbol.isascii()
                or not symbol.isalnum()
                or not symbol.isupper()
                or not symbol.endswith("USDT")
                for symbol in symbols
            )
            or len(set(symbols)) != len(symbols)
            or not intervals
            or any(interval not in ALLOWED_INTERVALS for interval in intervals)
            or len(set(intervals)) != len(intervals)
        ):
            raise ValueError("Invalid Original V2 kline stream configuration")

        next_symbols = tuple(symbols)
        next_intervals = tuple(intervals)
        async with self._configure_lock:
            if next_symbols == self._symbols and next_intervals == self._intervals:
                if self._symbols and (self._task is None or self._task.done()):
                    self._generation += 1
                    self._task = asyncio.create_task(self._run(self._generation))
                return

            self._generation += 1
            task = self._task
            self._task = None
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            self.connected = False
            self._symbols = next_symbols
            self._intervals = next_intervals
            self._subscribed = frozenset(
                (symbol, interval) for symbol in next_symbols for interval in next_intervals
            )
            active_keys = {
                (symbol, interval)
                for symbol in self._symbols
                for interval in self._intervals
            }
            self._candles = {
                key: rows for key, rows in self._candles.items() if key in active_keys
            }
            if self._symbols:
                self._task = asyncio.create_task(self._run(self._generation))
                logger.info(
                    "ORIGINAL_KLINE_STREAM configured symbols=%s intervals=%s stream_count=%s",
                    len(self._symbols),
                    ",".join(self._intervals),
                    len(self._symbols) * len(self._intervals),
                )

    async def close(self) -> None:
        async with self._configure_lock:
            self._generation += 1
            task = self._task
            self._task = None
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.connected = False

    async def get_candles(
        self,
        symbol: str,
        interval: str,
        limit: int,
        rest_fetch: Callable[[dict[str, Any]], Awaitable[Any]],
    ) -> list[list[Any]]:
        symbol = symbol.upper()
        if not self.is_subscribed(symbol, interval):
            raise ValueError("Symbol and interval are not subscribed to the Original V2 stream")
        if limit < 1:
            raise ValueError("Kline limit must be positive")

        rows = self.candles(symbol, interval)
        interval_ms = INTERVAL_MILLISECONDS[interval]
        expected_open_time = int(self._wall_clock() * 1000 // interval_ms * interval_ms)
        needs_backfill = (
            len(rows) < limit
            or not rows
            or int(rows[-1][0]) < expected_open_time
        )
        if needs_backfill:
            retry_after = BINANCE_GLOBAL_BAN.remaining_seconds()
            # Paylaşımlı IP engelliyken eksik mum, korumalı yoldan (sabit IP, küçük bütçe) tamamlanabilir.
            if retry_after > 0 and not protected_lane_available():
                raise KlineStreamUnavailable(retry_after)
            reason = "bootstrap" if len(rows) < limit else "missing_current_candle"
            logger.info(
                "ORIGINAL_KLINE_REST_BACKFILL symbol=%s interval=%s reason=%s limit=%s source=original_live",
                symbol,
                interval,
                reason,
                limit,
            )
            self.rest_backfills += 1
            fetched = await rest_fetch({"symbol": symbol, "interval": interval, "limit": limit})
            if not isinstance(fetched, list):
                raise ValueError("Binance kline backfill returned an invalid payload")
            self._merge_rows(symbol, interval, fetched)
            rows = self.candles(symbol, interval)
        return rows[-limit:]

    def _merge_rows(self, symbol: str, interval: str, rows: list[list[Any]]) -> None:
        key = (symbol, interval)
        candles = {int(row[0]): row for row in self._candles.get(key, []) if isinstance(row, list) and row}
        for row in rows:
            if isinstance(row, list) and len(row) >= 6:
                try:
                    open_time = int(row[0])
                except (TypeError, ValueError, OverflowError):
                    continue
                candles[open_time] = row.copy()
        self._candles[key] = [
            candles[open_time] for open_time in sorted(candles)[-MAX_CACHED_CANDLES:]
        ]

    def _ingest(self, payload: Any) -> bool:
        # "stream" modu {"stream": ..., "data": {...}} sarmalı gönderir; iki biçim de kabul edilir.
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and "stream" in payload:
            payload = payload["data"]
        if not isinstance(payload, dict) or payload.get("e") != "kline":
            return False
        kline = payload.get("k")
        if not isinstance(kline, dict):
            self.invalid_messages += 1
            return False
        symbol = str(payload.get("s") or "").upper()
        interval = str(kline.get("i") or "")
        if not self.is_subscribed(symbol, interval):
            return False
        try:
            row = [
                int(kline["t"]),
                kline["o"],
                kline["h"],
                kline["l"],
                kline["c"],
                kline["v"],
                int(kline["T"]),
                kline.get("q", "0"),
                int(kline.get("n", 0)),
                kline.get("V", "0"),
                kline.get("Q", "0"),
                kline.get("B", "0"),
            ]
        except (KeyError, TypeError, ValueError, OverflowError):
            self.invalid_messages += 1
            return False
        self._append_live_row(symbol, interval, row)
        self.messages_received += 1
        self.last_kline_at = self._wall_clock()
        return True

    def _append_live_row(self, symbol: str, interval: str, row: list[Any]) -> None:
        # Akış saniyede yüzlerce güncelleme getirir; her mesajda tüm önbelleği sıralamak yerine
        # yalnız son mumu yenile ya da sona ekle. Sıra dışı (eski) mum gelirse tam birleştirmeye düş.
        rows = self._candles.get((symbol, interval))
        if not rows:
            self._candles[(symbol, interval)] = [row]
            return
        last_open = int(rows[-1][0])
        if row[0] == last_open:
            rows[-1] = row
        elif row[0] > last_open:
            rows.append(row)
            if len(rows) > MAX_CACHED_CANDLES:
                del rows[: len(rows) - MAX_CACHED_CANDLES]
        else:
            self._merge_rows(symbol, interval, [row])

    async def _run(self, generation: int) -> None:
        params = [
            f"{symbol.lower()}@kline_{interval}"
            for symbol in self._symbols
            for interval in self._intervals
        ]
        delay = 1
        while generation == self._generation:
            opened_at: float | None = None
            session_klines = 0
            reason = "unknown"
            try:
                async with self._connector(
                    KLINE_STREAM_URL,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=4096,
                ) as socket:
                    if generation != self._generation:
                        return
                    await socket.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": generation}))
                    opened_at = time.monotonic()
                    self.connected = True
                    logger.info(
                        "ORIGINAL_KLINE_STREAM connected url=%s symbols=%s intervals=%s stream_count=%s",
                        KLINE_STREAM_URL,
                        len(self._symbols),
                        ",".join(self._intervals),
                        len(params),
                    )
                    while generation == self._generation:
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=IDLE_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            # Bağlantı açık ama veri yok: yanlış adres ya da sessizce ölmüş soket.
                            reason = "no_data" if session_klines == 0 else "idle"
                            raise
                        self.last_message_at = self._wall_clock()
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                            self.invalid_messages += 1
                            logger.warning("ORIGINAL_KLINE_STREAM invalid_message")
                            continue
                        if isinstance(payload, dict) and payload.get("error"):
                            reason = "subscribe_rejected"
                            logger.error(
                                "ORIGINAL_KLINE_STREAM subscribe_rejected code=%s",
                                (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), dict) else None,
                            )
                            raise OSError("Binance kline subscription rejected")
                        if self._ingest(payload):
                            session_klines += 1
                            if session_klines == 1:
                                # Yalnız gerçek mum verisi geldiğinde bağlantı sağlıklı sayılır ve bekleme sıfırlanır.
                                delay = 1
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
                self.connected = False
                self.reconnects += 1
                if reason == "unknown":
                    reason = "connect_failed" if opened_at is None else "closed"
                self.last_disconnect_reason = reason
                lifetime = 0 if opened_at is None else round(time.monotonic() - opened_at)
                logger.warning(
                    "ORIGINAL_KLINE_STREAM disconnected reason=%s error_type=%s lifetime_seconds=%s klines=%s retry_seconds=%s",
                    reason,
                    type(exc).__name__,
                    lifetime,
                    session_klines,
                    delay,
                )
                await asyncio.sleep(delay)
                # Veri gelmeden kopan bağlantılarda bekleme katlanır; Binance sık kopan IP'leri engelleyebilir.
                delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)
            finally:
                self.connected = False

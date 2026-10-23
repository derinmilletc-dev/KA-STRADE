"""Tüm piyasa 24 saatlik ticker'ı WebSocket'ten (`!ticker@arr`) okur.

REST `/fapi/v1/ticker/24hr` (sembolsüz) 40 ağırlık alır; Render'ın ortak IP'si dolu olduğunda 429 üretip piyasa listesini
bayatlatıyordu. Akış istek ağırlığı harcamaz. Akış sağlıklı değilse (ısınmadı, koptu, eksik) çağıran REST'e düşer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from .binance_rate_limit import BINANCE_GLOBAL_BAN

logger = logging.getLogger(__name__)

# Kline akışıyla aynı "market" kanalı (eski /ws adresi 2026-04-23'te emekli oldu).
TICKER_STREAM_URL = "wss://fstream.binance.com/market/ws"
TICKER_STREAM_NAME = "!ticker@arr"
IDLE_TIMEOUT_SECONDS = 30
MAX_RECONNECT_DELAY_SECONDS = 60
MIN_SYMBOLS = 100
WARMUP_SECONDS = 60
MAX_AGE_SECONDS = 20
BAN_RECHECK_SECONDS = 30


class BinanceTickerStream:
    def __init__(
        self,
        *,
        connector: Callable[..., Any] | None = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connector = connector or websockets.connect
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._tickers: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self.enabled = False
        self.connected = False
        self.messages_received = 0
        self.invalid_messages = 0
        self.reconnects = 0
        self.first_message_at: float | None = None
        self.last_message_at: float | None = None
        self.last_disconnect_reason: str | None = None

    def enable(self) -> None:
        """Üretimde uygulama açılışında çağrılır; testler akışı hiç başlatmaz."""
        self.enabled = True

    def ensure_started(self) -> None:
        if not self.enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task, self._task = self._task, None
        self.enabled = False
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connected = False

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "symbols": len(self._tickers),
            "messages_received": self.messages_received,
            "invalid_messages": self.invalid_messages,
            "reconnects": self.reconnects,
            "last_message_at": self.last_message_at,
            "last_disconnect_reason": self.last_disconnect_reason,
            "healthy": self.snapshot() is not None,
            "source": "binance_futures_ticker_websocket",
        }

    def snapshot(self) -> list[dict[str, Any]] | None:
        """REST ticker/24hr biçiminde tüm semboller; akış sağlıklı değilse None (çağıran REST'e düşer)."""
        if not self.enabled or not self.connected:
            return None
        now = self._monotonic()
        if self.first_message_at is None or self.last_message_at is None:
            return None
        if now - self.last_message_at > MAX_AGE_SECONDS or now - self.first_message_at < WARMUP_SECONDS:
            return None
        if len(self._tickers) < MIN_SYMBOLS:
            return None
        return [dict(row) for row in self._tickers.values()]

    def _ingest(self, payload: Any) -> int:
        if isinstance(payload, dict) and isinstance(payload.get("data"), list) and "stream" in payload:
            payload = payload["data"]
        if not isinstance(payload, list):
            return 0
        accepted = 0
        for item in payload:
            if not isinstance(item, dict) or item.get("e") != "24hrTicker":
                continue
            symbol = item.get("s")
            if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum():
                self.invalid_messages += 1
                continue
            try:
                row = {
                    "symbol": symbol.upper(),
                    "priceChange": str(item["p"]),
                    "priceChangePercent": str(item["P"]),
                    "weightedAvgPrice": str(item["w"]),
                    "lastPrice": str(item["c"]),
                    "lastQty": str(item.get("Q", "0")),
                    "openPrice": str(item["o"]),
                    "highPrice": str(item["h"]),
                    "lowPrice": str(item["l"]),
                    "volume": str(item["v"]),
                    "quoteVolume": str(item["q"]),
                    "openTime": int(item["O"]),
                    "closeTime": int(item["C"]),
                    "firstId": int(item.get("F", 0)),
                    "lastId": int(item.get("L", 0)),
                    "count": int(item.get("n", 0)),
                }
                if any(not math.isfinite(float(row[key])) for key in ("lastPrice", "priceChangePercent", "quoteVolume")):
                    raise ValueError("non-finite ticker value")
            except (KeyError, TypeError, ValueError, OverflowError):
                self.invalid_messages += 1
                continue
            self._tickers[row["symbol"]] = row
            accepted += 1
        return accepted

    async def _run(self) -> None:
        delay = 1
        while self.enabled:
            reason = "unknown"
            opened_at: float | None = None
            received = 0
            # Binance IP engeli varken (418) el sıkışma da reddedilir; engel bitene dek bağlanmayı denemeyiz.
            if BINANCE_GLOBAL_BAN.applies_to("render_direct"):
                self.connected = False
                await asyncio.sleep(BAN_RECHECK_SECONDS)
                continue
            try:
                async with self._connector(
                    TICKER_STREAM_URL, open_timeout=10, ping_interval=20, ping_timeout=20, max_queue=64,
                ) as socket:
                    await socket.send(json.dumps({"method": "SUBSCRIBE", "params": [TICKER_STREAM_NAME], "id": 1}))
                    opened_at = self._monotonic()
                    self.connected = True
                    self.first_message_at = None
                    self._tickers.clear()
                    logger.info("TICKER_STREAM connected url=%s", TICKER_STREAM_URL)
                    while self.enabled:
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=IDLE_TIMEOUT_SECONDS)
                        except asyncio.TimeoutError:
                            reason = "no_data" if received == 0 else "idle"
                            raise
                        try:
                            payload = json.loads(raw)
                        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                            self.invalid_messages += 1
                            continue
                        if isinstance(payload, dict) and payload.get("error"):
                            reason = "subscribe_rejected"
                            raise OSError("Binance ticker subscription rejected")
                        if self._ingest(payload):
                            received += 1
                            self.messages_received += 1
                            self.last_message_at = self._monotonic()
                            if self.first_message_at is None:
                                self.first_message_at = self.last_message_at
                                delay = 1
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
                self.connected = False
                self.reconnects += 1
                if reason == "unknown":
                    reason = "connect_failed" if opened_at is None else "closed"
                self.last_disconnect_reason = reason
                logger.warning(
                    "TICKER_STREAM disconnected reason=%s error_type=%s messages=%s retry_seconds=%s",
                    reason, type(exc).__name__, received, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)
            finally:
                self.connected = False


TICKER_STREAM = BinanceTickerStream()

"""Binance futures zorunlu kapanış (likidasyon) akışı: `!forceOrder@arr`. İstek ağırlığı harcamaz.

Yalnız ilk yorum isteğinde başlar, son 15 dakikayı bellekte tutar. Akış sağlıklı değilse `recent` boş döner
ve yorum likidasyon bölümünü göstermez (yanlış "sakin" demez: `healthy` False)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

try:
    import websockets
    from websockets.exceptions import WebSocketException
except ImportError:   # yerel test ortamında kütüphane olmayabilir; üretimde vardır
    websockets = None  # type: ignore[assignment]

    class WebSocketException(Exception):  # type: ignore[no-redef]
        pass

from .binance_rate_limit import BINANCE_GLOBAL_BAN

logger = logging.getLogger(__name__)

URL = "wss://fstream.binance.com/market/ws"
STREAM = "!forceOrder@arr"
KEEP_SECONDS = 900
MAX_PER_SYMBOL = 400
IDLE_TIMEOUT = 120   # likidasyon olmayan sakin dakikalar olabilir; bu yüzden uzun
HEALTHY_AFTER = 60


class LiquidationStream:
    def __init__(self, *, connector: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._connector = connector or (websockets.connect if websockets else None)
        self._clock = clock
        self._monotonic = monotonic
        self._events: dict[str, deque[dict[str, float]]] = {}
        self._task: asyncio.Task | None = None
        self.enabled = False
        self.connected = False
        self.connected_at: float | None = None
        self.messages = 0

    def enable(self) -> None:
        self.enabled = True

    def ensure_started(self) -> None:
        if self.enabled and self._connector is not None and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task, self._task = self._task, None
        self.enabled = False
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connected = False

    @property
    def healthy(self) -> bool:
        return self.connected and self.connected_at is not None and self._monotonic() - self.connected_at >= HEALTHY_AFTER

    def ingest(self, payload: Any) -> int:
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict) or payload.get("e") != "forceOrder" or not isinstance(payload.get("o"), dict):
            return 0
        o = payload["o"]
        try:
            symbol = str(o["s"]).upper()
            side = str(o["S"]).upper()
            price = float(o.get("ap") or o["p"])
            qty = float(o.get("z") or o["q"])
        except (KeyError, TypeError, ValueError):
            return 0
        if not symbol.isalnum() or side not in ("BUY", "SELL") or not (price > 0 and qty > 0):
            return 0
        queue = self._events.setdefault(symbol, deque(maxlen=MAX_PER_SYMBOL))
        queue.append({"t": self._clock(), "price": price, "usd": price * qty, "side": side})   # type: ignore[dict-item]
        self.messages += 1
        return 1

    def recent(self, symbol: str, seconds: int = KEEP_SECONDS) -> list[dict[str, Any]]:
        queue = self._events.get(symbol.upper())
        if not queue:
            return []
        now = self._clock()
        while queue and queue[0]["t"] < now - KEEP_SECONDS:
            queue.popleft()
        return [dict(e) for e in queue if e["t"] >= now - seconds]

    async def _run(self) -> None:
        delay = 1
        while self.enabled:
            if BINANCE_GLOBAL_BAN.applies_to("render_direct"):
                await asyncio.sleep(30)
                continue
            try:
                async with self._connector(URL, open_timeout=10, ping_interval=20, ping_timeout=20, max_queue=256) as socket:
                    await socket.send(json.dumps({"method": "SUBSCRIBE", "params": [STREAM], "id": 1}))
                    self.connected, self.connected_at = True, self._monotonic()
                    delay = 1
                    while self.enabled:
                        raw = await asyncio.wait_for(socket.recv(), timeout=IDLE_TIMEOUT)
                        try:
                            self.ingest(json.loads(raw))
                        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                            continue
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
                logger.warning("LIQ_STREAM disconnected error_type=%s retry_seconds=%s", type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            finally:
                self.connected = False


LIQ_STREAM = LiquidationStream()

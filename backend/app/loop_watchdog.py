"""Ana olay döngüsü takılırsa (bir kod senkron işte 1 sn'den uzun kalırsa) hangi satırda olduğunu günlüğe yazar.

Neden: emirden sonra durum isteği handler'a girmeden ~20 sn bekliyor ve sunucu günlüğünde sebep görünmüyordu.
Ayrı bir iş parçacığı döngünün nabzını izler; nabız 1 sn'den fazla gecikirse döngü iş parçacığının
çağrı yığınını bir kez (ve her 5 sn'de bir) yazar. Yalnız okuma yapar, iş mantığına dokunmaz.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger("app.loop_watchdog")

STALL_SECONDS = 1.0
REPEAT_SECONDS = 5.0
TICK_SECONDS = 0.25


def _stack(thread_id: int) -> str:
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return "?"
    frames = traceback.extract_stack(frame)[-10:]
    return " <- ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}:{f.name}" for f in reversed(frames))


class LoopWatchdog:
    def __init__(self) -> None:
        self._last_tick = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop_thread_id = 0
        self._handle: asyncio.TimerHandle | None = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._last_tick = time.monotonic()

        def tick() -> None:
            self._last_tick = time.monotonic()
            if not self._stop.is_set():
                self._handle = loop.call_later(TICK_SECONDS, tick)

        self._handle = loop.call_later(TICK_SECONDS, tick)
        self._thread = threading.Thread(target=self._run, name="loop-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._handle is not None:
            self._handle.cancel()

    def _run(self) -> None:
        stalled_since = 0.0
        last_report = 0.0
        while not self._stop.wait(TICK_SECONDS):
            now = time.monotonic()
            lag = now - self._last_tick
            if lag >= STALL_SECONDS:
                if not stalled_since:
                    stalled_since = self._last_tick
                if now - last_report >= REPEAT_SECONDS or not last_report:
                    last_report = now
                    try:
                        logger.warning("LOOP_STALL lag_ms=%s stack=%s", round(lag * 1000), _stack(self._loop_thread_id))
                    except Exception:  # noqa: BLE001 - teşhis hiçbir şeyi bozmaz
                        pass
            elif stalled_since:
                logger.warning("LOOP_STALL_END total_ms=%s", round((now - stalled_since) * 1000))
                stalled_since = 0.0
                last_report = 0.0

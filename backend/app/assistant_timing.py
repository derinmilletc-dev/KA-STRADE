"""Asistan yanıt süresi ölçümü (yalnızca süre; kullanıcı verisi, mesaj veya anahtar içermez).

Aşamalar: count = token sayımı + ön kontrol, lock = aylık muhasebe kilidini bekleme, llm = model çağrısı, tool = araç çalıştırma.
Sonuç `Server-Timing` başlığına ve tek satırlık bir loga yazılır."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[dict[str, float] | None] = ContextVar("kais_timing", default=None)
logger = logging.getLogger("kais.timing")


def start() -> dict[str, float]:
    data: dict[str, float] = {"started": time.monotonic()}
    _current.set(data)
    return data


@contextmanager
def stage(name: str) -> Iterator[None]:
    data = _current.get()
    began = time.monotonic()
    try:
        yield
    finally:
        if data is not None:
            data[name] = data.get(name, 0.0) + (time.monotonic() - began) * 1000
            data[f"{name}_n"] = data.get(f"{name}_n", 0.0) + 1


def add(name: str, millis: float) -> None:
    data = _current.get()
    if data is not None:
        data[name] = data.get(name, 0.0) + millis
        data[f"{name}_n"] = data.get(f"{name}_n", 0.0) + 1


def header(data: dict[str, float] | None) -> dict[str, str]:
    if not data:
        return {}
    total = (time.monotonic() - data["started"]) * 1000
    parts = [f"total;dur={total:.0f}"] + [f"{key};dur={data[key]:.0f}" for key in ("count", "lock", "llm", "tool") if key in data]
    value = ", ".join(parts)
    turns = int(data.get("llm_n", 0))
    logger.info("KAIS_TIMING %s turns=%d tools=%d", value, turns, int(data.get("tool_n", 0)))
    return {"Server-Timing": value, "Access-Control-Expose-Headers": "Server-Timing"}

"""Aynı anahtara giden anlık-görüntü yazmalarını birleştirir.

Her çağrıda ayrı bir veritabanı yazma görevi açmak, aynı satıra onlarca eşzamanlı UPSERT'ün sıraya girmesine ve
her birinin havuzdan bir bağlantı tutmasına yol açıyordu (emir sonrası ekran ~20 sn donuyordu). Burada anahtar başına
en fazla BİR yazma çalışır; çalışırken gelen yeni görüntüler birbirinin üstüne yazılır ve yalnız en yenisi yazılır.
Son durum her zaman yazılır; ara durumlar atlanır (zaten bir sonraki yazma onların üstüne yazacaktı).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def queue(
    application: Any,
    bucket: str,
    key: str,
    payload: Any,
    writer: Callable[[Any, str, Any], Awaitable[None]],
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Task:
    attribute = f"_snapshot_writers_{bucket}"
    writers = getattr(application.state, attribute, None)
    if not isinstance(writers, dict):
        writers = {}
        setattr(application.state, attribute, writers)
    entry = writers.get(key)
    if entry is not None and not entry["task"].done():
        entry["latest"] = payload
        return entry["task"]
    entry = {"latest": payload}

    async def run() -> None:
        while entry["latest"] is not None:
            current, entry["latest"] = entry["latest"], None
            try:
                await writer(application, key, current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if on_error is None:
                    raise
                on_error(exc)

    entry["task"] = asyncio.get_running_loop().create_task(run())
    writers[key] = entry
    return entry["task"]

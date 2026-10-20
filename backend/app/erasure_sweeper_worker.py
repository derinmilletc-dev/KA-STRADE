"""Kesintiye uğramış hesap silmelerini periyodik süpüren döngü.

Varsayılan KAPALI. ERASURE_SWEEPER_ENABLED=true ile açılır; aralık ERASURE_SWEEPER_INTERVAL_SECONDS (en az 60 sn).
Her iterasyon yalnızca sonuç sayılarını günlüğe yazar; kişisel veri veya kimlik yazmaz.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

ENABLED_ENV = "ERASURE_SWEEPER_ENABLED"
INTERVAL_ENV = "ERASURE_SWEEPER_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60


def sweeper_enabled(environ: Any = None) -> bool:
    value = (environ if environ is not None else os.environ).get(ENABLED_ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def sweeper_interval(environ: Any = None) -> int:
    raw = (environ if environ is not None else os.environ).get(INTERVAL_ENV, "")
    try:
        value = int(str(raw).strip() or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


async def erasure_sweeper_loop(application: Any, *, interval: int | None = None, iterations: int | None = None) -> None:
    from .account_erasure import sweep_stale_erasures

    seconds = interval if interval is not None else sweeper_interval()
    done = 0
    while iterations is None or done < iterations:
        try:
            results = await sweep_stale_erasures(application)
            if results:
                logger.info("erasure sweeper outcomes: %s", dict(Counter(item["outcome"] for item in results)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("erasure sweeper iteration failed")
        done += 1
        if iterations is not None and done >= iterations:
            return
        await asyncio.sleep(seconds)

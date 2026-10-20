"""Kapatılmış hesapların saklama süresi dolunca silinmesi (varsayılan KAPALI, varsayılan DRY-RUN).

Açmak için CLOSED_ACCOUNT_RETENTION_ENABLED=true. Süre CLOSED_ACCOUNT_RETENTION_DAYS (varsayılan 1826 gün ≈ 5 yıl,
en az 365). CLOSED_ACCOUNT_RETENTION_DRY_RUN varsayılan true: yalnızca kaç hesabın silineceğini günlüğe yazar,
hiçbir şey silmez; gerçek silme için açıkça false verilmelidir. Silme mevcut `erase_user_account` ile yapılır
(kişisel veriler silinir, finansal kayıtlar kimliksizleştirilerek saklanır). Günlükler kimlik/e-posta yazmaz.
Yalnızca `active=false` ve `closed_at` dolu hesaplar; OWNER hesapları asla.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

ENABLED_ENV = "CLOSED_ACCOUNT_RETENTION_ENABLED"
DAYS_ENV = "CLOSED_ACCOUNT_RETENTION_DAYS"
DRY_RUN_ENV = "CLOSED_ACCOUNT_RETENTION_DRY_RUN"
INTERVAL_ENV = "CLOSED_ACCOUNT_RETENTION_INTERVAL_SECONDS"
DEFAULT_DAYS = 1826
MIN_DAYS = 365
DEFAULT_INTERVAL_SECONDS = 86400
MIN_INTERVAL_SECONDS = 3600
BATCH_LIMIT = 10


def _flag(environ: Any, name: str, default: bool) -> bool:
    raw = str((environ if environ is not None else os.environ).get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def retention_enabled(environ: Any = None) -> bool:
    return _flag(environ, ENABLED_ENV, False)


def dry_run(environ: Any = None) -> bool:
    return _flag(environ, DRY_RUN_ENV, True)


def retention_days(environ: Any = None) -> int:
    raw = (environ if environ is not None else os.environ).get(DAYS_ENV, "")
    try:
        value = int(str(raw).strip() or DEFAULT_DAYS)
    except ValueError:
        value = DEFAULT_DAYS
    return max(MIN_DAYS, value)


def retention_interval(environ: Any = None) -> int:
    raw = (environ if environ is not None else os.environ).get(INTERVAL_ENV, "")
    try:
        value = int(str(raw).strip() or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


def _parse_time(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_due(security: Any, now: datetime, days: int) -> bool:
    """Kapalı (active=false, closed_at dolu), OWNER olmayan ve süresi dolmuş mu? Belirsizse False (silme)."""
    if isinstance(security, str):
        try:
            security = json.loads(security)
        except ValueError:
            return False
    if not isinstance(security, dict):
        return False
    if security.get("active") is not False or security.get("role") == "OWNER":
        return False
    closed = _parse_time(security.get("closed_at"))
    return closed is not None and closed <= now - timedelta(days=days)


async def due_user_ids(pool: Any, now: datetime, days: int, limit: int = BATCH_LIMIT) -> list[str]:
    rows = await pool.fetch("SELECT user_id, security FROM commercial_auth_users")
    due = [row["user_id"] for row in rows if is_due(row["security"], now, days)]
    return due[:limit]


async def sweep_closed_accounts(application: Any, *, now: datetime | None = None,
                                simulate: bool | None = None) -> list[dict[str, Any]]:
    from fastapi import HTTPException

    from .account_erasure import _matches_auth_record, erase_user_account
    from .account_exposure import gate_before_erasure

    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        return []
    simulate = dry_run() if simulate is None else simulate
    now = now or datetime.now(timezone.utc)
    ids = await due_user_ids(pool, now, retention_days())
    rt = getattr(application.state, "v22_commercial", None) or {}
    results: list[dict[str, Any]] = []
    for uid in ids:
        if simulate:
            results.append({"outcome": "silinecek"})
            continue
        user = next((item for item in (rt.get("state") or {}).get("users", []) if item.get("id") == uid), None)
        if user is None or user.get("role") == "OWNER" or not await _matches_auth_record(pool, user):
            results.append({"outcome": "manuel_inceleme"})
            continue
        try:
            await gate_before_erasure(application, user, actor="RETENTION")
            await erase_user_account(SimpleNamespace(app=application), user)
            results.append({"outcome": "tamamlandı"})
        except HTTPException as exc:
            results.append({"outcome": "atlandı", "reason": str(exc.status_code)})
        except Exception:
            logger.exception("closed account retention erase failed")
            results.append({"outcome": "hata"})
    return results


async def closed_account_retention_loop(application: Any, *, interval: int | None = None, iterations: int | None = None) -> None:
    seconds = interval if interval is not None else retention_interval()
    done = 0
    while iterations is None or done < iterations:
        try:
            results = await sweep_closed_accounts(application)
            if results:
                logger.info("closed account retention (dry_run=%s) outcomes: %s", dry_run(),
                            dict(Counter(item["outcome"] for item in results)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("closed account retention iteration failed")
        done += 1
        if iterations is not None and done >= iterations:
            return
        await asyncio.sleep(seconds)

"""Bounded Chart AI result cleanup and budget-warning delivery."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import asyncpg

from . import email_service
from .chart_ai_store import ChartAiPool, ChartAiStorageError, ChartAiStore

logger = logging.getLogger(__name__)
POLL_SECONDS = 60
CLEANUP_BATCH_SIZE = 100
NOTIFICATION_BATCH_SIZE = 10
NOTIFICATION_MAX_ATTEMPTS = 8
NOTIFICATION_BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)
NOTIFICATION_LEASE_SECONDS = 120
NOTIFICATION_ERROR_CODES = {
    "provider_unavailable", "provider_timeout", "provider_rejected", "invalid_response", "delivery_error",
}
RECIPIENT_EMAIL_RE = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def _feature_enabled() -> bool:
    return os.getenv("CHART_AI_ENABLED", "false").strip().lower() in {"true", "1"}


def _budget_payload(value: Any) -> tuple[str, int, int]:
    payload = json.loads(value) if isinstance(value, str) else value
    if not isinstance(payload, dict) or set(payload) != {"month", "spent_microusd", "budget_microusd"}:
        raise ValueError("Invalid Chart AI budget notification payload")
    month = payload["month"]
    spent = payload["spent_microusd"]
    budget = payload["budget_microusd"]
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Invalid Chart AI budget notification month")
    if type(spent) is not int or type(budget) is not int or not 0 < spent <= budget:
        raise ValueError("Invalid Chart AI budget notification totals")
    return month, spent, budget


def _delivery_error_code(error: email_service.EmailDeliveryError) -> str:
    if error.code in NOTIFICATION_ERROR_CODES:
        return error.code
    if error.code == "timeout":
        return "provider_timeout"
    if error.code in {"401", "403", "429"}:
        return "provider_rejected"
    if error.code == "configuration":
        return "provider_unavailable"
    return "delivery_error"


async def _recover_expired_notifications(pool: ChartAiPool) -> int:
    async with pool.acquire(timeout=3) as connection:
        result = await connection.execute(
            """WITH expired AS (
                 SELECT id,attempts FROM notification_outbox
                 WHERE kind='chart_ai.budget_warning' AND status='sending'
                   AND next_attempt_at <= clock_timestamp()
                 ORDER BY next_attempt_at,id FOR UPDATE SKIP LOCKED LIMIT $1
               )
               UPDATE notification_outbox o SET
                 status=CASE WHEN e.attempts >= $2 THEN 'dead' ELSE 'pending' END,
                 last_error_code=CASE WHEN e.attempts >= $2 THEN 'attempts_exhausted' ELSE 'lease_expired' END,
                 next_attempt_at=clock_timestamp()
               FROM expired e WHERE o.id=e.id""",
            NOTIFICATION_BATCH_SIZE, NOTIFICATION_MAX_ATTEMPTS,
        )
    return int(result.rsplit(" ", 1)[-1])


async def _deliver_one(pool: ChartAiPool) -> bool:
    async with pool.acquire(timeout=3) as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """SELECT id,recipient_user_id,payload,attempts FROM notification_outbox
                   WHERE kind='chart_ai.budget_warning' AND status IN ('pending','failed')
                     AND attempts < $1 AND next_attempt_at <= clock_timestamp()
                   ORDER BY next_attempt_at,id FOR UPDATE SKIP LOCKED LIMIT 1""",
                NOTIFICATION_MAX_ATTEMPTS,
            )
            if row is None:
                return False

            attempt = row["attempts"] + 1
            await connection.execute(
                """UPDATE notification_outbox SET status='sending',attempts=$2,
                     next_attempt_at=clock_timestamp()+($3 * INTERVAL '1 second'),last_error_code=NULL
                   WHERE id=$1 AND status IN ('pending','failed')""",
                row["id"], attempt, NOTIFICATION_LEASE_SECONDS,
            )
            recipient = await connection.fetchrow(
                """SELECT security->>'email' AS email FROM commercial_auth_users u
                   WHERE user_id=$1 AND security->>'role'='OWNER'
                     AND security->'active'='true'::jsonb AND security->'email_verified'='true'::jsonb
                     AND NOT EXISTS (SELECT 1 FROM commercial_erased_users e
                       WHERE e.user_hash=encode(sha256(convert_to(u.user_id,'UTF8')),'hex'))""",
                row["recipient_user_id"],
            )
            email = recipient["email"] if recipient is not None else None
            if not isinstance(email, str) or RECIPIENT_EMAIL_RE.fullmatch(email) is None:
                await connection.execute(
                    """UPDATE notification_outbox SET status='dead',last_error_code='recipient_unavailable'
                       WHERE id=$1 AND status='sending' AND attempts=$2""",
                    row["id"], attempt,
                )
                logger.warning("Chart AI budget notification skipped code=recipient_unavailable")
                return True

            try:
                month, spent, budget = _budget_payload(row["payload"])
            except (TypeError, ValueError, json.JSONDecodeError):
                await connection.execute(
                    """UPDATE notification_outbox SET status='dead',last_error_code='invalid_response'
                       WHERE id=$1 AND status='sending' AND attempts=$2""",
                    row["id"], attempt,
                )
                logger.error("Chart AI budget notification rejected code=invalid_response")
                return True

            try:
                await asyncio.to_thread(
                    email_service.send_chart_ai_budget_warning_email,
                    to_email=email, month=month, spent_microusd=spent, budget_microusd=budget,
                    idempotency_key=row["id"],
                )
            except email_service.EmailDeliveryError as error:
                code = _delivery_error_code(error)
                terminal = attempt >= NOTIFICATION_MAX_ATTEMPTS
                status = "dead" if terminal else "failed"
                stored_code = "attempts_exhausted" if terminal else code
                delay = NOTIFICATION_BACKOFF_SECONDS[min(attempt - 1, len(NOTIFICATION_BACKOFF_SECONDS) - 1)]
                await connection.execute(
                    """UPDATE notification_outbox SET status=$3,last_error_code=$4,
                         next_attempt_at=clock_timestamp()+($5 * INTERVAL '1 second')
                       WHERE id=$1 AND status='sending' AND attempts=$2""",
                    row["id"], attempt, status, stored_code, delay,
                )
                logger.warning("Chart AI budget notification delivery failed code=%s attempt=%s", code, attempt)
                return True

            await connection.execute(
                """UPDATE notification_outbox SET status='sent',sent_at=clock_timestamp(),last_error_code=NULL
                   WHERE id=$1 AND status='sending' AND attempts=$2""",
                row["id"], attempt,
            )
            logger.info("Chart AI budget notification delivered")
            return True


async def process_budget_notifications(pool: ChartAiPool, *, limit: int = NOTIFICATION_BATCH_SIZE) -> int:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Notification batch size must be between 1 and 100")
    recovered = await _recover_expired_notifications(pool)
    if recovered:
        logger.warning("Chart AI budget notification leases recovered count=%s", recovered)
    if not email_service.configured():
        logger.warning("Chart AI budget mailer paused code=provider_unavailable")
        return 0
    delivered = 0
    for _ in range(limit):
        if not await _deliver_one(pool):
            break
        delivered += 1
    return delivered


async def maintenance_once(application: Any) -> tuple[int, int, int]:
    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        logger.warning("Chart AI maintenance skipped code=database_unavailable")
        return 0, 0, 0
    store = ChartAiStore(pool)
    recovered = await store.recover_expired(limit=CLEANUP_BATCH_SIZE)
    purged = await store.purge_results(limit=CLEANUP_BATCH_SIZE)
    notified = await process_budget_notifications(pool)
    if recovered or purged:
        logger.info("Chart AI maintenance completed recovered=%s expired_results=%s", recovered, purged)
    return recovered, purged, notified


async def chart_ai_maintenance_loop(application: Any) -> None:
    while True:
        if _feature_enabled():
            try:
                await maintenance_once(application)
            except (asyncpg.PostgresError, ChartAiStorageError) as error:
                logger.warning("Chart AI maintenance failed type=%s", type(error).__name__)
        await asyncio.sleep(POLL_SECONDS)

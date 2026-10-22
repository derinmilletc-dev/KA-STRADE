"""Web push (phone/browser) notifications for LIVE trade events.

Best-effort only: a push failure must never touch order execution, protection or recovery.
Needs VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT in the environment and the
push_subscriptions table (backend/migrations/20261017_002_web_push.sql). Without them it reports "not ready".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .v22_commercial import authenticated_user_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v22/push", tags=["Push notifications"])

# Same categories as notification-prefs.ts so the settings screen controls what is pushed.
CATEGORIES: dict[str, tuple[str, re.Pattern[str]]] = {
    "entry": ("Emir açıldı", re.compile(r"^LIVE_ENTRY$|^LIVE_ENTRY_RECOVERED$")),
    "close": ("Pozisyon kapandı", re.compile(r"^LIVE_CLOSE$|^LIVE_ENTRY_CANCELLED$|STOP_HIT|TP_HIT")),
    "protection": ("Stop / koruma sorunu", re.compile(
        r"^PROTECTION_(FAIL|BLOCK|REPAIR)$|LIVE_STOP_VERIFICATION_RETRY|OWNERSHIP_UNCERTAIN|LIVE_FOREIGN_ORDER|LIVE_ENTRY_REMAINDER_UNCERTAIN")),
    "connection": ("Bağlantı sorunu", re.compile(
        r"^(?!.*(RECOVERED|RESTORED|RESUMED)).*(STALE|RATE_LIMIT|RATE_LIMITED|AUTO_ERROR|MONITORING)")),
    "arm": ("İzin süresi / kilit", re.compile(
        r"LIVE_CONSENT_REAUTH_REQUIRED|LIVE_CONSENT_GRACE_EXPIRED|LIVE_AUTO_FAIL_CLOSED|^LIVE_DISARM$|^ORIGINAL_SESSION_EXPIRING$|^ORIGINAL_LIVE_PAUSED$")),
    "emergency": ("Acil durum", re.compile(r"^LIVE_EMERGENCY$|UNKNOWN_ORDER")),
}
DEFAULT_KINDS = list(CATEGORIES)
MAX_SUBSCRIPTIONS_PER_USER = 8
PUSH_HOSTS = (".googleapis.com", ".push.apple.com", ".mozilla.com", ".mozaws.net", ".windows.com", ".notify.windows.com")
_tasks: set[asyncio.Task[Any]] = set()


def configured() -> bool:
    return bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY") and os.getenv("VAPID_SUBJECT"))


def category_of(kind: str) -> str | None:
    return next((name for name, (_, pattern) in CATEGORIES.items() if pattern.search(kind)), None)


def trusted_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == suffix[1:] or host.endswith(suffix) for suffix in PUSH_HOSTS)


class Keys(BaseModel):
    model_config = ConfigDict(extra="ignore")
    p256dh: str = Field(min_length=20, max_length=256)
    auth: str = Field(min_length=8, max_length=128)


class SubscribeInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: Keys
    kinds: list[str] = Field(default_factory=lambda: list(DEFAULT_KINDS), max_length=len(CATEGORIES))


class UnsubscribeInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    endpoint: str = Field(min_length=20, max_length=2048)


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None or not configured():
        raise HTTPException(503, "Telefon bildirimi henüz hazır değil.")
    return pool


async def _guard(call: Any) -> Any:
    try:
        return await call
    except HTTPException:
        raise
    except Exception as exc:  # missing table, database down, ...
        logger.warning("Push storage unavailable (%s)", type(exc).__name__)
        raise HTTPException(503, "Telefon bildirimi henüz hazır değil.") from exc


@router.get("/status")
async def push_status(request: Request):
    await authenticated_user_async(request)
    ready = configured() and getattr(request.app.state, "db_pool", None) is not None
    return {"ready": ready, "public_key": os.getenv("VAPID_PUBLIC_KEY") if ready else None}


@router.post("/subscribe")
async def push_subscribe(payload: SubscribeInput, request: Request):
    user = await authenticated_user_async(request)
    pool = _pool(request)
    if not trusted_endpoint(payload.endpoint):
        raise HTTPException(422, "Bu bildirim adresi desteklenmiyor.")
    kinds = sorted({kind for kind in payload.kinds if kind in CATEGORIES})

    async def save() -> None:
        async with pool.acquire() as conn, conn.transaction():
            owner = await conn.fetchval("SELECT user_id FROM push_subscriptions WHERE endpoint=$1 FOR UPDATE", payload.endpoint)
            if owner is None:
                count = await conn.fetchval("SELECT COUNT(*) FROM push_subscriptions WHERE user_id=$1", str(user["id"]))
                if count >= MAX_SUBSCRIPTIONS_PER_USER:
                    raise HTTPException(429, "Bu hesapta çok fazla cihaz kayıtlı. Eski bir cihazı kapat.")
            await conn.execute(
                """INSERT INTO push_subscriptions (endpoint,user_id,p256dh,auth,kinds)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (endpoint) DO UPDATE SET user_id=EXCLUDED.user_id, p256dh=EXCLUDED.p256dh,
                     auth=EXCLUDED.auth, kinds=EXCLUDED.kinds, failures=0""",
                payload.endpoint, str(user["id"]), payload.keys.p256dh, payload.keys.auth, kinds,
            )
    await _guard(save())
    return {"ok": True, "kinds": kinds}


@router.post("/unsubscribe")
async def push_unsubscribe(payload: UnsubscribeInput, request: Request):
    user = await authenticated_user_async(request)
    pool = _pool(request)
    await _guard(pool.execute("DELETE FROM push_subscriptions WHERE endpoint=$1 AND user_id=$2", payload.endpoint, str(user["id"])))
    return {"ok": True}


@router.post("/test")
async def push_test(request: Request):
    user = await authenticated_user_async(request)
    pool = _pool(request)
    sent = await _guard(send_to_user(pool, str(user["id"]), None, "KaisTrade test", "Telefon bildirimi çalışıyor.", force=True))
    if not sent:
        raise HTTPException(409, "Bu hesapta kayıtlı cihaz bulunamadı veya gönderilemedi.")
    return {"ok": True, "sent": sent}


def _deliver(subscription: dict[str, Any], body: str) -> int:
    """Blocking send. Returns the HTTP status (0 on local failure)."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info=subscription, data=body, ttl=3600,
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": os.environ["VAPID_SUBJECT"]})
        return 201
    except WebPushException as exc:
        return int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
    except Exception as exc:
        logger.warning("Push delivery error (%s)", type(exc).__name__)
        return 0


async def send_to_user(pool: Any, user_id: str, category: str | None, title: str, body: str, *, force: bool = False, url: str = "/master-trade") -> int:
    rows = await pool.fetch("SELECT endpoint,p256dh,auth,kinds FROM push_subscriptions WHERE user_id=$1", user_id)
    message = json.dumps({"title": title[:80], "body": body[:160], "tag": f"kais-{category or 'test'}", "url": url}, ensure_ascii=False)
    sent = 0
    for row in rows:
        if not force and category not in (row["kinds"] or []):
            continue
        status = await asyncio.to_thread(_deliver, {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}, message)
        if status in (404, 410):
            await pool.execute("DELETE FROM push_subscriptions WHERE endpoint=$1", row["endpoint"])
        elif 200 <= status < 300:
            sent += 1
            await pool.execute("UPDATE push_subscriptions SET last_sent_at=clock_timestamp(), failures=0 WHERE endpoint=$1", row["endpoint"])
        else:
            await pool.execute("UPDATE push_subscriptions SET failures=failures+1 WHERE endpoint=$1", row["endpoint"])
            await pool.execute("DELETE FROM push_subscriptions WHERE endpoint=$1 AND failures>=20", row["endpoint"])
    return sent


async def _push_event(pool: Any, user_id: str, category: str, message: str) -> None:
    try:
        from .v25_execution import sanitized_exception_message
        title = CATEGORIES[category][0]
        body = sanitized_exception_message(message).replace("_", " ")
        await send_to_user(pool, user_id, category, title, body)
    except Exception as exc:
        logger.warning("Push for event failed (%s)", type(exc).__name__)


def queue_event_push(state: dict[str, Any], row: dict[str, Any]) -> None:
    """Called from add_event. Never raises and never blocks."""
    try:
        category = category_of(str(row.get("kind") or ""))
        application = state.get("_app")
        pool = getattr(getattr(application, "state", None), "db_pool", None)
        user_id = ((state.get("live_session_authorization") or {}).get("user_id")
                   or (state.get("auto_authorization") or {}).get("user_id") or row.get("actor"))
        if category is None or pool is None or not user_id or not configured():
            return
        task = asyncio.get_running_loop().create_task(_push_event(pool, str(user_id), category, str(row.get("message") or "")))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as exc:
        logger.debug("Push queue skipped (%s)", type(exc).__name__)

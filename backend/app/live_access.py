"""Who may use LIVE trading.

Before multi-user LIVE only the OWNER account could. With MULTI_USER_LIVE on, a member may
too, but only while they have an active paid plan (SMART_TRADE or MASTER_MODE). The free trial is demo-only. The OWNER
account is unchanged. Every check here is fail-closed.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from fastapi import HTTPException, Request

from . import live_registry

logger = logging.getLogger(__name__)
LIVE_MESSAGE = "LIVE işlem Smart Trade veya Master Mode ile açılır"


def role_ok(role: Any) -> bool:
    """The account type may hold a LIVE state. Entitlement is checked separately."""
    return role == "OWNER" or (live_registry.enabled() and role == "CUSTOMER")


def entitled(subscription: dict[str, Any]) -> bool:
    """Yalnız ücretli plan (Smart Trade / Master Mode). Ücretsiz deneme canlı işlem açamaz; demo kullanır."""
    return bool(subscription.get("master_trade_access") and subscription.get("plan") in {"MASTER_MODE", "SMART_TRADE"}
                and subscription.get("status") in {"ACTIVE", "PAST_DUE"})


TRIAL_LIVE_DAILY_LIMIT = 5
TRIAL_LIVE_LIMIT_MESSAGE = ("Deneme süresince günde 5 canlı işlem açabilirsin ve bugünkü hakkın doldu. "
                            "Hak gece 00:00'da (UTC) yenilenir; sınırsız için Smart Trade veya Master Mode'a geç.")
TRIAL_AUTOMATION_MESSAGE = "Canlı otomatik işlem Master Mode ile açılır; denemede elle canlı işlem açabilirsin."


def trial_live_enabled() -> bool:
    """Deneme üyesine elle canlı işlem: yalnız çoklu kullanıcı LIVE açıkken ve TRIAL_LIVE_ENABLED=true ise (varsayılan açık; TRIAL_LIVE_ENABLED=false ile kapanır)."""
    return live_registry.enabled() and os.getenv("TRIAL_LIVE_ENABLED", "true").strip().lower() in {"true", "1"}


def trial_manual(subscription: dict[str, Any]) -> bool:
    """Aktif ücretsiz deneme üyesi (elle canlı işlem hakkı, günlük sınırlı). Otomatik stratejiler bunun dışındadır."""
    return bool(trial_live_enabled() and subscription.get("mode") == "FREE_TRIAL" and subscription.get("plan") == "TRIAL"
                and subscription.get("status") == "TRIALING" and subscription.get("master_trade_access"))


def entitled_manual(subscription: dict[str, Any]) -> bool:
    return entitled(subscription) or trial_manual(subscription)


def _user_hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


async def consume_trial_entry(conn: Any, user_id: str) -> bool:
    """Bugünkü (UTC) haktan birini atomik olarak kullanır; hak kalmadıysa False."""
    value = await conn.fetchval(
        """INSERT INTO live_trial_entries(user_hash,day,count) VALUES ($1,(clock_timestamp() AT TIME ZONE 'UTC')::date,1)
           ON CONFLICT (user_hash,day) DO UPDATE SET count=live_trial_entries.count+1
           WHERE live_trial_entries.count < $2 RETURNING count""", _user_hash(user_id), TRIAL_LIVE_DAILY_LIMIT)
    return value is not None


async def refund_trial_entry(conn: Any, user_id: str) -> None:
    await conn.execute(
        """UPDATE live_trial_entries SET count=GREATEST(count-1,0)
           WHERE user_hash=$1 AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date""", _user_hash(user_id))


async def trial_entries_today(conn: Any, user_id: str) -> int:
    value = await conn.fetchval(
        "SELECT count FROM live_trial_entries WHERE user_hash=$1 AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date",
        _user_hash(user_id))
    return int(value or 0)


def require_live_member(request: Request) -> dict[str, Any]:
    """Authenticated OWNER, or an entitled member while multi-user LIVE is on."""
    from .member_entitlements import subscription_for_request
    from .v22_commercial import authenticated_user
    user = authenticated_user(request)
    if user.get("role") == "OWNER":
        return user
    if not role_ok(user.get("role")):
        raise HTTPException(403, "Yönetici yetkisi gerekli")
    if not entitled(subscription_for_request(request, user)):
        raise HTTPException(403, LIVE_MESSAGE)
    return user


def forbid_smart_trade(request: Request, user: dict[str, Any]) -> None:
    """Smart Trade yalnız elle işlemdir; Kais otomasyonunu başlatamaz (OWNER etkilenmez)."""
    if user.get("role") == "OWNER":
        return
    from .member_entitlements import subscription_for_request
    if subscription_for_request(request, user).get("plan") == "SMART_TRADE":
        from .premium_access import AUTOMATION_LOCKED_MESSAGE
        raise HTTPException(403, AUTOMATION_LOCKED_MESSAGE)


async def background_entitled(application: Any, user_id: str) -> bool:
    """Same rule for background loops (no request). Unknown or failing lookups deny."""
    pool = getattr(application.state, "db_pool", None)
    if not user_id or pool is None:
        return False
    try:
        from .member_entitlements import target_subscription
        async with pool.acquire() as conn:
            subscription = await target_subscription(conn, application.state.v22_commercial["state"], user_id)
        return entitled(subscription)
    except Exception as exc:
        logger.warning("Background LIVE entitlement unavailable type=%s", type(exc).__name__)
        return False

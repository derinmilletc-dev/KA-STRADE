"""Who may use LIVE trading.

Before multi-user LIVE only the OWNER account could. With MULTI_USER_LIVE on, a member may
too, but only while they have an active premium (MASTER_MODE) or an active free trial. The OWNER
account is unchanged. Every check here is fail-closed.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from . import live_registry

logger = logging.getLogger(__name__)
LIVE_MESSAGE = "LIVE işlem premium üyelik veya aktif deneme ile açılır"


def role_ok(role: Any) -> bool:
    """The account type may hold a LIVE state. Entitlement is checked separately."""
    return role == "OWNER" or (live_registry.enabled() and role == "CUSTOMER")


def entitled(subscription: dict[str, Any]) -> bool:
    """Paid premium, or (multi-user LIVE) an active free trial."""
    paid = bool(subscription.get("master_trade_access") and subscription.get("plan") in {"MASTER_MODE", "SMART_TRADE"}
                and subscription.get("status") in {"ACTIVE", "PAST_DUE"})
    trial = bool(live_registry.enabled() and subscription.get("mode") == "FREE_TRIAL"
                 and subscription.get("master_trade_access") and subscription.get("status") == "TRIALING")
    return paid or trial


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

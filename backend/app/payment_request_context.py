"""Read-only customer context shown next to manual Binance payment requests.

Pure SELECT fragments and row mapping shared by the OWNER queue and the
moderator queue. Nothing here writes, grants access or calls a provider.
The SQL expects the payment table aliased as ``r`` and ``commercial_auth_users``
aliased as ``u`` (joined on ``r.user_id``).
"""
from datetime import datetime
from typing import Any, Mapping

CONTEXT_COLUMNS = """
    COALESCE(u.security->'active' = 'true'::jsonb, FALSE) AS user_active,
    COALESCE(u.security->'email_verified' = 'true'::jsonb, FALSE) AS email_verified,
    COALESCE(acct.payload->'two_factor_enabled' = 'true'::jsonb, FALSE) AS mfa_enabled,
    registration.created_at AS registered_at,
    sub.plan AS plan, sub.status AS subscription_status, sub.current_period_end AS plan_period_end,
    manual.membership_end AS membership_end,
    COALESCE(history.approved_count, 0) AS approved_count,
    COALESCE(history.rejected_count, 0) AS rejected_count,
    COALESCE(history.expired_count, 0) AS expired_count
"""

CONTEXT_JOINS = """
    LEFT JOIN commercial_account_settings acct ON acct.user_id = r.user_id
    LEFT JOIN LATERAL (
        SELECT (entry->>'created_at')::timestamptz AS created_at
        FROM application_state_snapshots s,
             jsonb_array_elements(COALESCE(s.payload->'users', '[]'::jsonb)) entry
        WHERE s.state_key = 'v22-commercial' AND entry->>'id' = r.user_id
        LIMIT 1
    ) registration ON TRUE
    LEFT JOIN LATERAL (
        SELECT plan, status, current_period_end FROM subscriptions
        WHERE user_id = r.user_id ORDER BY updated_at DESC, id DESC LIMIT 1
    ) sub ON TRUE
    LEFT JOIN LATERAL (
        SELECT MAX(p.period_end) AS membership_end FROM binance_manual_periods p
        WHERE p.user_id = r.user_id AND p.revoked_at IS NULL
    ) manual ON TRUE
    LEFT JOIN LATERAL (
        SELECT COUNT(*) FILTER (WHERE h.status = 'approved') AS approved_count,
               COUNT(*) FILTER (WHERE h.status = 'rejected') AS rejected_count,
               COUNT(*) FILTER (WHERE h.status = 'expired') AS expired_count
        FROM binance_manual_payment_requests h
        WHERE h.user_id = r.user_id AND h.erased_at IS NULL AND h.id <> r.id
    ) history ON TRUE
"""


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def customer_context(row: Mapping[str, Any]) -> dict[str, Any]:
    """Account facts for one request row; identity fields are added by the caller."""
    return {
        "active": row["user_active"] is True,
        "email_verified": row["email_verified"] is True,
        "mfa_enabled": row["mfa_enabled"] is True,
        "registered_at": _iso(row["registered_at"]),
        "plan": row["plan"],
        "subscription_status": row["subscription_status"],
        "plan_period_end": _iso(row["plan_period_end"]),
        "membership_end": _iso(row["membership_end"]),
        "approved_count": int(row["approved_count"]),
        "rejected_count": int(row["rejected_count"]),
        "expired_count": int(row["expired_count"]),
    }

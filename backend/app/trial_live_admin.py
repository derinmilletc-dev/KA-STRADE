"""Deneme üyelerinin elle canlı işlem kullanımı (günlük 5 hak): admin ve moderatör izleme ekranı.

Sayaç kişi kimliği yerine SHA-256 özeti tutar; burada özet, kullanıcı kimliğinin aynı özetiyle eşleştirilir.
Yalnız okuma. Tablo yoksa (migration uygulanmadıysa) boş liste döner.
"""
from asyncpg import UndefinedTableError
from fastapi import APIRouter, Depends, Query, Request

from .email_service import masked_recipient
from .live_access import TRIAL_LIVE_DAILY_LIMIT, trial_live_enabled
from .moderator_access import ModeratorIdentity, require_permission
from .moderator_customers import customer_read_connection
from .nowpayments_routes import connection
from .v22_commercial import authenticated_user

admin_router = APIRouter(prefix="/api/v22/admin/trial-live", tags=["OWNER trial live"])
mod_router = APIRouter(prefix="/api/mod/trial-live", tags=["Moderator trial live"])

_SQL = """
SELECT u.user_id, u.security->>'email' AS email,
       COALESCE(SUM(e.count) FILTER (WHERE e.day = (clock_timestamp() AT TIME ZONE 'UTC')::date), 0)::int AS today,
       COALESCE(SUM(e.count), 0)::int AS week
FROM live_trial_entries e
JOIN commercial_auth_users u ON encode(sha256(convert_to(u.user_id, 'UTF8')), 'hex') = e.user_hash
WHERE e.day >= (clock_timestamp() AT TIME ZONE 'UTC')::date - 6 AND ($1::boolean OR u.security->>'role' = 'CUSTOMER')
GROUP BY u.user_id, u.security->>'email'
ORDER BY today DESC, week DESC, u.user_id LIMIT $2
"""


async def _rows(conn, include_staff: bool, limit: int):
    try:
        return await conn.fetch(_SQL, include_staff, limit)
    except UndefinedTableError:
        return []


def _payload(rows, mask: bool):
    return {"enabled": trial_live_enabled(), "daily_limit": TRIAL_LIVE_DAILY_LIMIT, "items": [
        {"user_id": row["user_id"], "email": masked_recipient(row["email"] or "") if mask else (row["email"] or ""),
         "today": row["today"], "week": row["week"], "limit_reached": row["today"] >= TRIAL_LIVE_DAILY_LIMIT}
        for row in rows]}


@admin_router.get("")
async def admin_list(request: Request, limit: int = Query(50, ge=1, le=100)):
    authenticated_user(request, owner=True)
    async with connection(request) as conn:
        return _payload(await _rows(conn, True, limit), mask=False)


@mod_router.get("")
async def moderator_list(request: Request, limit: int = Query(50, ge=1, le=100),
                         identity: ModeratorIdentity = Depends(require_permission("subscriptions.view"))):
    async with customer_read_connection(request, identity, "subscriptions.view") as conn:
        return _payload(await _rows(conn, False, limit), mask=True)

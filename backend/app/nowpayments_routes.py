"""NOWPayments (kripto) ödeme uçları: üye için fatura/durum, herkese açık imzalı bildirim, OWNER için liste."""
import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Literal

from asyncpg import PostgresError
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from . import binance_manual_payments as payments
from . import nowpayments_payments as nowpay
from .v22_commercial import authenticated_user, authenticated_user_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v22/payments/nowpayments", tags=["NOWPayments"])
admin_router = APIRouter(prefix="/api/v22/admin/nowpayments", tags=["OWNER NOWPayments"])

PlanCode = Literal["MASTER_MODE", "SMART_TRADE"]


class PlanOption(BaseModel):
    plan: PlanCode
    name: str
    amount: Decimal


class Configuration(BaseModel):
    enabled: bool
    plans: list[PlanOption] = []


class CreateInvoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: PlanCode = "MASTER_MODE"


@asynccontextmanager
async def connection(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Kripto ödeme kaydı şu anda kullanılamıyor.")
    try:
        async with pool.acquire() as conn:
            yield conn
    except HTTPException:
        raise
    except (PostgresError, OSError, RuntimeError) as exc:
        logger.warning("NOWPayments connection failed type=%s", type(exc).__name__)
        raise HTTPException(503, "Kripto ödeme kaydı şu anda kullanılamıyor.") from None


def require_enabled() -> None:
    if not nowpay.enabled():
        raise HTTPException(503, "Kripto ödeme yöntemi şu anda kullanılamıyor.")


@router.get("/config", response_model=Configuration)
async def configuration(request: Request, response: Response):
    await authenticated_user_async(request)
    response.headers["Cache-Control"] = "no-store"
    if not nowpay.enabled():
        return Configuration(enabled=False)
    return Configuration(enabled=True, plans=[
        PlanOption(plan=code, name=payments.PLAN_NAMES[code], amount=amount)  # type: ignore[arg-type]
        for code, amount in payments.PLAN_AMOUNTS.items()
    ])


@router.post("/invoices")
async def create_invoice(body: CreateInvoice, request: Request, response: Response):
    user = await authenticated_user_async(request)
    require_enabled()
    response.headers["Cache-Control"] = "no-store"
    async with connection(request) as conn:
        return await nowpay.create_invoice(conn, user["id"], body.plan)


@router.get("/current")
async def current(request: Request, response: Response):
    user = await authenticated_user_async(request)
    require_enabled()
    response.headers["Cache-Control"] = "no-store"
    async with connection(request) as conn:
        row = await nowpay.current_payment(conn, user["id"])
        period = await nowpay.active_period(conn, user["id"])
        paid_until = await nowpay.paid_until(conn, user["id"])
    return {
        "payment": None if row is None else {
            "id": row["id"], "plan": row["plan"], "amount": str(row["price_amount"]), "status": row["status"],
            "created_at": row["created_at"].isoformat(),
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        },
        "period": None if period is None else {
            "period_start": period["period_start"].isoformat(),
            "period_end": (paid_until or period["period_end"]).isoformat(),
        },
    }


@router.post("/ipn")
async def notification(request: Request):
    """NOWPayments sunucusundan gelir (oturum yok). Yalnız imzası doğru bildirim işlenir."""
    secret = nowpay.ipn_secret()
    if not secret:
        raise HTTPException(503, "Kripto ödeme bildirimi yapılandırılmadı.")
    raw = await request.body()
    if len(raw) > nowpay.MAX_IPN_BYTES:
        raise HTTPException(413, "Bildirim çok büyük.")
    if not nowpay.verify_signature(raw, request.headers.get("x-nowpayments-sig"), secret):
        logger.warning("NOWPayments notification rejected: invalid signature")
        raise HTTPException(401, "İmza doğrulanamadı.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Geçersiz bildirim.") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "Geçersiz bildirim.")
    async with connection(request) as conn:
        result, granted_user = await nowpay.process_notification(conn, data)
    logger.info("NOWPayments notification result=%s status=%s", result, str(data.get("payment_status"))[:20])
    if granted_user:
        # Kapalı üyelik: ödemesi alınan davetli üye aktif olur (hata ödeme kaydını bozmaz).
        try:
            from .founding_invites import advance_member
            await advance_member(request.app, granted_user, "active")
        except Exception as exc:  # noqa: BLE001
            logger.warning("NOWPayments founding advance failed type=%s", type(exc).__name__)
    if result == "granted":
        from .nowpayments_email import notify_paid
        async with connection(request) as conn:
            await notify_paid(conn, str(data.get("order_id") or ""))
    return {"ok": True, "result": result}


@admin_router.get("")
async def list_payments(request: Request, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
                        status: str | None = Query(None, pattern=r"^[a-z_]{1,20}$")):
    authenticated_user(request, owner=True)
    async with connection(request) as conn:
        rows = await conn.fetch(
            """SELECT r.id,r.plan,r.price_amount,r.status,r.pay_currency,r.note,r.created_at,r.completed_at,
                 u.security->>'email' AS email,p.period_end
               FROM nowpayments_payments r
               LEFT JOIN commercial_auth_users u ON u.user_id=r.user_id
               LEFT JOIN nowpayments_periods p ON p.payment_id=r.id
               WHERE r.erased_at IS NULL AND ($1::text IS NULL OR r.status=$1)
               ORDER BY r.created_at DESC,r.id DESC LIMIT $2 OFFSET $3""", status, limit, offset,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM nowpayments_payments WHERE erased_at IS NULL AND ($1::text IS NULL OR status=$1)", status,
        )
    return {"items": [{
        "id": row["id"], "plan": row["plan"], "amount": str(row["price_amount"]), "status": row["status"],
        "pay_currency": row["pay_currency"], "note": row["note"], "email": row["email"],
        "created_at": row["created_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
    } for row in rows], "total": total, "limit": limit, "offset": offset}

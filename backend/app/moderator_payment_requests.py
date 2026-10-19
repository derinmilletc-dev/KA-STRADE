"""Read-only manual Binance payment queue for moderators.

Requires ``payments.view`` (OWNER implicit), a verified email and MFA, the same
rate limit as other moderator customer reads. Decisions stay OWNER-only in
binance_manual_admin; this module has GET routes only. Customer e-mail is masked
and the Binance order number is reduced to its last four characters.
Each customer shown is recorded with the existing ``customer.payments.viewed`` audit action.

POST /{request_id}/recommend ("Onaya gönder", ``approvals.create``) only records a suggestion for the OWNER.
It never writes a membership period or changes the payment status; access opens solely when the OWNER approves.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import binance_manual_payments as payments
from .email_service import masked_recipient
from .moderator_access import ModeratorIdentity, require_permission
from .audit_log import write_audit
from .moderator_customers import CUSTOMER_WHERE, audit_customer_read, customer_read_connection
from .payment_request_context import CONTEXT_COLUMNS, CONTEXT_JOINS, customer_context

router = APIRouter(prefix="/api/mod/payment-requests", tags=["Moderator payment requests"])


class ReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueueCustomer(ReadModel):
    user_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    email_masked: str = Field(max_length=184, pattern=r"^(?:\*{3}|[A-Za-z0-9*]\*{3}@[A-Za-z0-9.-]+)$")
    active: bool = Field(strict=True)
    email_verified: bool = Field(strict=True)
    mfa_enabled: bool = Field(strict=True)
    registered_at: datetime | None
    plan: Literal["TRIAL", "MASTER_MODE"] | None
    subscription_status: Literal["TRIALING", "ACTIVE", "PAST_DUE", "UNPAID", "CANCELLED", "EXPIRED"] | None
    plan_period_end: datetime | None
    membership_end: datetime | None
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    expired_count: int = Field(ge=0)


class QueueItem(ReadModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    reference_code: str
    amount: str
    currency: Literal["USDT"]
    status: payments.Status
    transaction_hint: str | None
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    recommended_at: datetime | None
    customer: QueueCustomer


class QueueSummary(ReadModel):
    pending: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)


class QueuePage(ReadModel):
    items: list[QueueItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    summary: QueueSummary


def transaction_hint(value: str | None) -> str | None:
    return None if not value else "••••" + value[-4:]


@router.get("", response_model=QueuePage)
async def payment_request_queue(
    request: Request,
    identity: ModeratorIdentity = Depends(require_permission("payments.view")),
    status: payments.Status | None = Query(None),
    limit: int = Query(25, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    async with customer_read_connection(request, identity, "payments.view") as conn:
        rows = await conn.fetch(
            f"""SELECT r.id, r.user_id, r.reference_code, r.amount, r.currency, r.status, r.transaction_number,
                       r.created_at, r.submitted_at, r.decided_at, r.decision_note,
                       u.security->>'email' AS email, rec.created_at AS recommended_at, {CONTEXT_COLUMNS}
                FROM binance_manual_payment_requests r
                JOIN commercial_auth_users u ON u.user_id = r.user_id
                LEFT JOIN binance_manual_payment_recommendations rec ON rec.payment_request_id = r.id
                {CONTEXT_JOINS}
                WHERE {CUSTOMER_WHERE} AND r.erased_at IS NULL AND ($2::text IS NULL OR r.status = $2)
                ORDER BY r.created_at DESC, r.id DESC LIMIT $3 OFFSET $4""",
            identity.user_id, status, limit, offset,
        )
        total = await conn.fetchval(
            f"""SELECT COUNT(*) FROM binance_manual_payment_requests r
                JOIN commercial_auth_users u ON u.user_id = r.user_id
                WHERE {CUSTOMER_WHERE} AND r.erased_at IS NULL AND ($2::text IS NULL OR r.status = $2)""",
            identity.user_id, status,
        )
        summary = await conn.fetchrow(
            f"""SELECT COUNT(*) FILTER (WHERE r.status = 'pending') AS pending,
                       COUNT(*) FILTER (WHERE r.status = 'approved') AS approved,
                       COUNT(*) FILTER (WHERE r.status = 'rejected') AS rejected
                FROM binance_manual_payment_requests r
                JOIN commercial_auth_users u ON u.user_id = r.user_id
                WHERE {CUSTOMER_WHERE} AND r.erased_at IS NULL""",
            identity.user_id,
        )
        items = [QueueItem(
            id=row["id"], reference_code=row["reference_code"], amount=f"{row['amount']:.2f}",
            currency=row["currency"], status=row["status"], transaction_hint=transaction_hint(row["transaction_number"]),
            created_at=row["created_at"], submitted_at=row["submitted_at"], decided_at=row["decided_at"],
            decision_note=row["decision_note"], recommended_at=row["recommended_at"],
            customer=QueueCustomer(user_id=row["user_id"], email_masked=masked_recipient(row["email"] or ""),
                                   **customer_context(row)),
        ) for row in rows]
        for user_id in dict.fromkeys(item.customer.user_id for item in items):
            await audit_customer_read(conn, request, user_id, "customer.payments.viewed")
        return QueuePage(items=items, total=total, limit=limit, offset=offset, summary=QueueSummary(**dict(summary)))


class RecommendBody(ReadModel):
    note: str | None = Field(None, max_length=500)

    @field_validator("note")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class Recommendation(ReadModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    recommended_at: datetime


@router.post("/{request_id}/recommend", response_model=Recommendation)
async def recommend_payment(
    body: RecommendBody,
    request: Request,
    request_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
    identity: ModeratorIdentity = Depends(require_permission("approvals.create")),
):
    """Send a pending payment to the OWNER. Idempotent; changes no payment status and grants nothing."""
    async with customer_read_connection(request, identity, "approvals.create") as conn:
        row = await conn.fetchrow(
            f"""SELECT r.id, r.status FROM binance_manual_payment_requests r
                JOIN commercial_auth_users u ON u.user_id = r.user_id
                WHERE {CUSTOMER_WHERE} AND r.id = $2 AND r.erased_at IS NULL FOR UPDATE OF r""",
            identity.user_id, request_id,
        )
        if row is None:
            raise HTTPException(404, "Payment request not found")
        if row["status"] != "pending":
            raise HTTPException(409, "Only a pending payment request can be sent for approval")
        existing = await conn.fetchrow(
            "SELECT created_at FROM binance_manual_payment_recommendations WHERE payment_request_id = $1", request_id,
        )
        if existing is not None:
            return Recommendation(id=request_id, recommended_at=existing["created_at"])
        saved = await conn.fetchrow(
            """INSERT INTO binance_manual_payment_recommendations (payment_request_id, recommended_by, note)
               VALUES ($1, $2, $3) RETURNING created_at""",
            request_id, identity.user_id, body.note,
        )
        await write_audit(
            conn, request.state.mod_read_actor, "payment.binance.recommended", "BINANCE_PAYMENT", request_id,
            {"status": "pending"}, {"status": "pending"}, reason=body.note,
        )
        return Recommendation(id=request_id, recommended_at=saved["created_at"])

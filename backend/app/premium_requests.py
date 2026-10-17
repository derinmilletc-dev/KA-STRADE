"""Moderator asks for premium for a customer; only the owner's approval grants it."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from .moderator_access import ModeratorIdentity, require_permission
from .subscription_core import grant_admin_access
from .v22_commercial import add_audit, authenticated_user, now_iso, runtime, save_state

EXPIRY_HOURS = 72
MAX_DAYS = 90
MAX_PENDING_PER_MODERATOR = 5
KEEP_DECIDED_DAYS = 60
_tasks: set[Any] = set()
ID = r"^[A-Za-z0-9_-]+$"

mod_router = APIRouter(prefix="/api/mod/premium-requests", tags=["Moderator premium requests"])
admin_router = APIRouter(prefix="/api/v22/admin/premium-requests", tags=["Owner premium requests"])


class CreatePremiumRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: str = Field(pattern=ID, min_length=1, max_length=160)
    days: int = Field(ge=1, le=MAX_DAYS)
    reason: str = Field(min_length=10, max_length=500)


class DecisionNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    return state.setdefault("premium_requests", [])


def expire_and_prune(state: dict[str, Any]) -> bool:
    """Pending requests older than 72 h expire; old decided ones are dropped."""
    changed = False
    now = _now()
    keep: list[dict[str, Any]] = []
    for row in _rows(state):
        if row.get("status") == "pending":
            expires = _parse(row.get("expires_at"))
            if expires is None or expires <= now:
                row.update({"status": "expired", "decided_at": now_iso()})
                changed = True
        elif row.get("status") != "pending":
            decided = _parse(row.get("decided_at")) or now
            if now - decided > timedelta(days=KEEP_DECIDED_DAYS):
                changed = True
                continue
        keep.append(row)
    if len(keep) != len(_rows(state)):
        state["premium_requests"] = keep
    return changed


def _user(state: dict[str, Any], user_id: str) -> dict[str, Any] | None:
    return next((u for u in state["users"] if u.get("id") == user_id), None)


def _public(state: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    target = _user(state, row["target_user_id"]) or {}
    requester = _user(state, row["requester_user_id"]) or {}
    return {
        "id": row["id"], "status": row["status"], "days": row["days"], "reason": row["reason"],
        "target_user_id": row["target_user_id"],
        "target_email": target.get("email"), "target_name": target.get("name"),
        "requester_user_id": row["requester_user_id"],
        "requester_email": requester.get("email"),
        "created_at": row["created_at"], "expires_at": row["expires_at"],
        "decided_at": row.get("decided_at"), "decision_note": row.get("decision_note"),
    }


def apply_grant(state: dict[str, Any], user_id: str, days: int) -> dict[str, Any]:
    """Same effect as the owner's manual premium grant."""
    subscription = grant_admin_access(state, user_id, "MASTER_MODE", days)
    for item in state["licenses"]:
        if item.get("user_id") == user_id and item.get("source") == "MANUAL_ADMIN" and item.get("status") == "ACTIVE":
            item.update({"status": "REVOKED", "revoked_at": now_iso(), "revoked_reason": "Yeni erişim verildi"})
    state["licenses"].append({
        "id": uuid.uuid4().hex, "user_id": user_id, "plan": "MASTER_MODE", "status": "ACTIVE",
        "starts_at": subscription["currentPeriodStart"], "expires_at": subscription["currentPeriodEnd"],
        "source": "MANUAL_ADMIN", "demo_only": True,
    })
    return subscription


def notify_owner(request: Request, state: dict[str, Any], row: dict[str, Any]) -> None:
    """Phone notification to the owner for a new request. Never raises, never blocks."""
    try:
        import asyncio
        from . import web_push
        pool = getattr(request.app.state, "db_pool", None)
        owner_ids = {u["id"] for u in state["users"] if u.get("role") == "OWNER"}
        if pool is None or not owner_ids or not web_push.configured():
            return
        target = _user(state, row["target_user_id"]) or {}
        body = f"{target.get('email') or 'Bir müşteri'} için {row['days']} gün premium istendi. Onay bekliyor."

        async def send() -> None:
            try:
                for owner_id in owner_ids:
                    await web_push.send_to_user(pool, owner_id, "premium", "Premium talebi", body, force=True, url="/admin")
            except Exception:
                pass

        task = asyncio.get_running_loop().create_task(send())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:
        pass


# ---------------------------------------------------------------- moderator
@mod_router.post("")
async def create_request(payload: CreatePremiumRequest, request: Request,
                         identity: ModeratorIdentity = Depends(require_permission("approvals.create"))):
    if identity.role != "MODERATOR":
        raise HTTPException(403, "Only moderators may request premium")
    rt = runtime(request)
    async with rt["lock"]:
        state = rt["state"]
        expire_and_prune(state)
        target = _user(state, payload.target_user_id)
        if target is None or target.get("role") != "CUSTOMER" or payload.target_user_id == identity.user_id:
            raise HTTPException(404, "Customer not found")
        rows = _rows(state)
        if any(r["target_user_id"] == payload.target_user_id and r["status"] == "pending" for r in rows):
            raise HTTPException(409, "Target already has a pending premium request")
        if sum(1 for r in rows if r["requester_user_id"] == identity.user_id and r["status"] == "pending") >= MAX_PENDING_PER_MODERATOR:
            raise HTTPException(429, "Premium request limit reached")
        row = {
            "id": uuid.uuid4().hex, "status": "pending", "target_user_id": payload.target_user_id,
            "requester_user_id": identity.user_id, "days": payload.days, "reason": payload.reason.strip(),
            "created_at": now_iso(), "expires_at": (_now() + timedelta(hours=EXPIRY_HOURS)).isoformat(),
            "decided_at": None, "decided_by": None, "decision_note": None,
        }
        rows.insert(0, row)
        add_audit(state, "PREMIUM_REQUESTED", f"Moderatör {payload.days} gün premium istedi.",
                  actor=identity.user_id, subject=payload.target_user_id)
        save_state(state)
        notify_owner(request, state, row)
        return _public(state, row)


@mod_router.get("")
async def own_requests(request: Request, identity: ModeratorIdentity = Depends(require_permission("approvals.create"))):
    rt = runtime(request)
    async with rt["lock"]:
        state = rt["state"]
        if expire_and_prune(state):
            save_state(state)
        items = [_public(state, r) for r in _rows(state) if r["requester_user_id"] == identity.user_id]
        return {"items": items[:50], "pending_count": sum(1 for i in items if i["status"] == "pending")}


@mod_router.post("/{request_id}/cancel")
async def cancel_request(request: Request, request_id: str = Path(pattern=ID, max_length=160),
                         identity: ModeratorIdentity = Depends(require_permission("approvals.create"))):
    rt = runtime(request)
    async with rt["lock"]:
        state = rt["state"]
        expire_and_prune(state)
        row = next((r for r in _rows(state) if r["id"] == request_id and r["requester_user_id"] == identity.user_id), None)
        if row is None:
            raise HTTPException(404, "Request not found")
        if row["status"] != "pending":
            raise HTTPException(409, "Request is not pending")
        row.update({"status": "cancelled", "decided_at": now_iso()})
        add_audit(state, "PREMIUM_REQUEST_CANCELLED", "Moderatör premium talebini geri çekti.",
                  actor=identity.user_id, subject=row["target_user_id"])
        save_state(state)
        return _public(state, row)


# -------------------------------------------------------------------- owner
@admin_router.get("")
async def list_requests(request: Request, status: Literal["pending", "approved", "rejected", "cancelled", "expired"] | None = None):
    authenticated_user(request, owner=True)
    rt = runtime(request)
    async with rt["lock"]:
        state = rt["state"]
        if expire_and_prune(state):
            save_state(state)
        rows = [r for r in _rows(state) if status is None or r["status"] == status]
        return {"items": [_public(state, r) for r in rows[:100]],
                "pending_count": sum(1 for r in _rows(state) if r["status"] == "pending")}


async def _decide(request: Request, request_id: str, approve: bool, note: str) -> dict[str, Any]:
    owner = authenticated_user(request, owner=True)
    rt = runtime(request)
    async with rt["lock"]:
        state = rt["state"]
        expire_and_prune(state)
        row = next((r for r in _rows(state) if r["id"] == request_id), None)
        if row is None:
            raise HTTPException(404, "Request not found")
        if row["status"] != "pending":
            raise HTTPException(409, "Request is not pending")
        if row["requester_user_id"] == owner["id"]:
            raise HTTPException(403, "Requester cannot decide own request")
        if approve:
            target = _user(state, row["target_user_id"])
            if target is None or target.get("role") != "CUSTOMER":
                raise HTTPException(409, "Customer is no longer available")
            apply_grant(state, row["target_user_id"], int(row["days"]))
        row.update({"status": "approved" if approve else "rejected", "decided_at": now_iso(),
                    "decided_by": owner["id"], "decision_note": note.strip() or None})
        add_audit(state, "PREMIUM_REQUEST_APPROVED" if approve else "PREMIUM_REQUEST_REJECTED",
                  f"Premium talebi {'onaylandı' if approve else 'reddedildi'} ({row['days']} gün).",
                  actor=owner["id"], subject=row["target_user_id"])
        save_state(state)
        return _public(state, row)


@admin_router.post("/{request_id}/approve")
async def approve_request(request: Request, payload: DecisionNote | None = None,
                          request_id: str = Path(pattern=ID, max_length=160)):
    return await _decide(request, request_id, True, payload.note if payload else "")


@admin_router.post("/{request_id}/reject")
async def reject_request(request: Request, payload: DecisionNote | None = None,
                         request_id: str = Path(pattern=ID, max_length=160)):
    return await _decide(request, request_id, False, payload.note if payload else "")

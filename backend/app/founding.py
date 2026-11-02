"""Founding 30 – Erken Erişim başvuruları.

Başvurular müşteri hesaplarından ayrı tutulur; hesap yalnızca onaylanan kişiye, daha sonra açılır.
Akış: başvuru -> e-posta onayı -> (yönetici) onay / bekleme listesi / ret.
Kontenjan sunucuda sayılır; sayaç sahte değildir.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from asyncpg import UniqueViolationError
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import email_service
from .approval_service import owner_connection
from .web_security import client_ip

logger = logging.getLogger(__name__)
public_router = APIRouter(prefix="/api/v22/founding", tags=["Founding 30"])
admin_router = APIRouter(prefix="/api/v22/admin/founding", tags=["Founding 30 admin"])

Status = Literal["pending_email", "new", "approved", "waitlist", "rejected"]
EMAIL_TTL = timedelta(hours=48)
DEFAULT_SEATS = 30
MAX_SEATS = 200
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS founding_settings (
        id SMALLINT PRIMARY KEY CHECK (id = 1), seats INTEGER NOT NULL DEFAULT 30 CHECK (seats BETWEEN 1 AND 200),
        applications_open BOOLEAN NOT NULL DEFAULT TRUE, launch_at TIMESTAMPTZ, price_text TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """INSERT INTO founding_settings (id) VALUES (1) ON CONFLICT DO NOTHING""",
    """CREATE TABLE IF NOT EXISTS founding_applications (
        id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL, country TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'tr',
        status TEXT NOT NULL DEFAULT 'pending_email' CHECK (status IN ('pending_email','new','approved','waitlist','rejected')),
        email_token_hash TEXT, email_token_expires TIMESTAMPTZ, email_confirmed_at TIMESTAMPTZ,
        ip_hash TEXT, note TEXT NOT NULL DEFAULT '', decided_at TIMESTAMPTZ, decided_by TEXT,
        approved_seq INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE UNIQUE INDEX IF NOT EXISTS founding_applications_email_key ON founding_applications (lower(email))""",
    """CREATE INDEX IF NOT EXISTS founding_applications_status_idx ON founding_applications (status, created_at)""",
)


async def ensure_schema(pool: Any) -> None:
    if getattr(pool, "_founding_ready", False):
        return
    async with pool.acquire() as conn:
        for statement in SCHEMA:
            await conn.execute(statement)
    try:
        pool._founding_ready = True
    except AttributeError:  # asyncpg havuzu bazı sürümlerde öznitelik kabul etmeyebilir
        pass


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Başvuru depolaması şu anda kullanılamıyor.")
    return pool


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


class Apply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str = Field(min_length=5, max_length=254)
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=80)
    lang: str = Field(default="tr", max_length=8)
    website: str = Field(default="", max_length=200)  # bal tuzağı: dolu gelirse bot


def _app_base() -> str:
    return email_service.validate_app_base_url().rstrip("/")


def _send_confirmation(email: str, name: str, token: str, lang: str) -> None:
    url = f"{_app_base()}/api/v22/founding/confirm?token={token}"
    en = lang == "en"
    email_service.send_auth_email(
        to_email=email, display_name=name,
        subject="Confirm your Founding 30 application" if en else "Founding 30 başvurunu onayla",
        title="Confirm your application" if en else "Başvurunu onayla",
        action_url=url, action_label="Confirm my application" if en else "Başvurumu onayla",
        expiry="48 saat", language="en" if en else "tr",
    )


async def _settings(conn: Any) -> Any:
    return await conn.fetchrow("SELECT seats, applications_open, launch_at, price_text FROM founding_settings WHERE id = 1")


async def _taken(conn: Any) -> int:
    return int(await conn.fetchval("SELECT COUNT(*) FROM founding_applications WHERE status = 'approved'"))


@public_router.get("/status")
async def public_status(request: Request):
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        settings = await _settings(conn)
        taken = await _taken(conn)
    return {
        "open": bool(settings["applications_open"]), "seats": int(settings["seats"]), "taken": taken,
        "launch_at": settings["launch_at"].isoformat() if settings["launch_at"] else None,
        "price": settings["price_text"] or None,
    }


@public_router.post("/apply")
async def apply(body: Apply, request: Request):
    from . import v22_commercial as auth

    email = _clean(body.email, 254).lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Geçerli bir e-posta adresi girin.")
    ok = {"ok": True}
    if body.website.strip():  # bot: sessizce başarılı görün
        return ok
    await auth.enforce_auth_limit(request, "founding-apply", email)
    pool = _pool(request)
    await ensure_schema(pool)
    name, country = _clean(body.name, 120), _clean(body.country, 80)
    if len(name) < 2 or len(country) < 2:
        raise HTTPException(422, "Ad ve ülke zorunludur.")
    lang = "en" if body.lang == "en" else "tr"
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    send = False
    try:
        async with pool.acquire() as conn, conn.transaction():
            settings = await _settings(conn)
            if not settings["applications_open"]:
                raise HTTPException(409, "Başvurular şu anda kapalı." if lang == "tr" else "Applications are currently closed.")
            row = await conn.fetchrow("SELECT id, status FROM founding_applications WHERE lower(email) = $1 FOR UPDATE", email)
            if row is None:
                await conn.execute(
                    """INSERT INTO founding_applications (email, name, country, lang, email_token_hash, email_token_expires, ip_hash)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    email, name, country, lang, _hash(token), now + EMAIL_TTL, _hash(client_ip(request)),
                )
                send = True
            elif row["status"] == "pending_email":  # onay e-postası gelmediyse yeniden gönderilebilir
                await conn.execute(
                    """UPDATE founding_applications SET name=$2, country=$3, lang=$4, email_token_hash=$5, email_token_expires=$6, updated_at=now()
                       WHERE id=$1""", row["id"], name, country, lang, _hash(token), now + EMAIL_TTL,
                )
                send = True
    except UniqueViolationError:  # aynı anda iki istek: ikincisi sessizce başarılı sayılır
        return ok
    if send:
        try:
            _send_confirmation(email, name, token, lang)
        except Exception:  # noqa: BLE001 - kayıt yapıldı; e-posta hatası kullanıcıya sızdırılmaz
            logger.exception("Founding confirmation email failed")
            raise HTTPException(503, "Onay e-postası şu anda gönderilemedi. Lütfen biraz sonra tekrar deneyin.") from None
    return ok  # kayıtlı e-posta olup olmadığı açığa çıkmaz


@public_router.get("/confirm")
async def confirm(request: Request, token: str = Query(min_length=20, max_length=200)):
    pool = _pool(request)
    await ensure_schema(pool)
    base = _app_base()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, status, email_token_expires FROM founding_applications WHERE email_token_hash = $1 FOR UPDATE", _hash(token),
        )
        if row is None:
            return RedirectResponse(f"{base}/?founding=invalid", status_code=303)
        if row["status"] != "pending_email":
            return RedirectResponse(f"{base}/?founding=confirmed", status_code=303)
        if row["email_token_expires"] < datetime.now(timezone.utc):
            return RedirectResponse(f"{base}/?founding=expired", status_code=303)
        await conn.execute(
            """UPDATE founding_applications SET status='new', email_confirmed_at=now(), email_token_hash=NULL,
               email_token_expires=NULL, updated_at=now() WHERE id=$1""", row["id"],
        )
    return RedirectResponse(f"{base}/?founding=confirmed", status_code=303)


# ---------------------------------------------------------------- yönetici (yalnız OWNER)

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seats: int = Field(ge=1, le=MAX_SEATS)
    applications_open: bool
    launch_at: datetime | None = None
    price_text: str = Field(default="", max_length=40)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=500)


def _item(row: Any) -> dict:
    return {
        "id": int(row["id"]), "email": row["email"], "name": row["name"], "country": row["country"], "lang": row["lang"],
        "status": row["status"], "note": row["note"], "approved_seq": row["approved_seq"],
        "email_confirmed_at": row["email_confirmed_at"].isoformat() if row["email_confirmed_at"] else None,
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


@admin_router.get("")
async def admin_list(request: Request, status: Status | None = None, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        rows = await conn.fetch(
            """SELECT * FROM founding_applications WHERE ($1::text IS NULL OR status = $1)
               ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3""", status, limit, offset,
        )
        counts = {r["status"]: int(r["n"]) for r in await conn.fetch("SELECT status, COUNT(*) AS n FROM founding_applications GROUP BY status")}
        settings = await _settings(conn)
    return {
        "items": [_item(r) for r in rows], "counts": counts,
        "settings": {"seats": int(settings["seats"]), "applications_open": bool(settings["applications_open"]),
                     "launch_at": settings["launch_at"].isoformat() if settings["launch_at"] else None, "price_text": settings["price_text"]},
    }


@admin_router.put("/settings")
async def admin_settings(body: Settings, request: Request):
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        taken = await _taken(conn)
        if body.seats < taken:
            raise HTTPException(409, f"Zaten {taken} kişi onaylı; koltuk sayısı bunun altına düşürülemez.")
        await conn.execute(
            """UPDATE founding_settings SET seats=$1, applications_open=$2, launch_at=$3, price_text=$4, updated_at=now() WHERE id=1""",
            body.seats, body.applications_open, body.launch_at, _clean(body.price_text, 40),
        )
    return {"ok": True}


async def _decide(request: Request, app_id: int, new_status: str, note: str) -> dict:
    mail: tuple[str, str, str] | None = None
    async with owner_connection(request) as (conn, actor):
        await ensure_schema(request.app.state.db_pool)
        row = await conn.fetchrow("SELECT * FROM founding_applications WHERE id = $1 FOR UPDATE", app_id)
        if row is None:
            raise HTTPException(404, "Başvuru bulunamadı.")
        if row["status"] == "pending_email":
            raise HTTPException(409, "Başvuranın e-postası henüz onaylanmadı.")
        seq = row["approved_seq"]
        if new_status == "approved" and row["status"] != "approved":
            settings = await _settings(conn)
            await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
            taken = await _taken(conn)
            if taken >= int(settings["seats"]):
                raise HTTPException(409, "Tüm koltuklar dolu. Önce kontenjanı artırın veya başka bir onayı geri alın.")
            seq = int(await conn.fetchval("SELECT COALESCE(MAX(approved_seq),0)+1 FROM founding_applications"))
            mail = (row["email"], row["name"], row["lang"])
        if new_status != "approved":
            seq = None
        await conn.execute(
            """UPDATE founding_applications SET status=$2, note=CASE WHEN $3 <> '' THEN $3 ELSE note END, approved_seq=$4,
               decided_at=now(), decided_by=$5, updated_at=now() WHERE id=$1""",
            app_id, new_status, _clean(note, 500), seq, actor.user_id,
        )
        updated = await conn.fetchrow("SELECT * FROM founding_applications WHERE id = $1", app_id)
    if mail:
        try:
            _send_approval(*mail)
        except Exception:  # noqa: BLE001 - onay kaydedildi; e-posta hatası yöneticiye bildirilir
            logger.exception("Founding approval email failed")
            return {**_item(updated), "email_sent": False}
    return {**_item(updated), "email_sent": bool(mail)}


def _send_approval(email: str, name: str, lang: str) -> None:
    en = lang == "en"
    safe = html.escape(name)
    subject = "You're in: KaisTrade Founding 30" if en else "KaisTrade Founding 30: başvurun onaylandı"
    body = (f"Hi {name},\n\nYour Founding 30 application is approved. You have a founding seat reserved. "
            "We will email you the launch date and your private invitation shortly before launch. "
            "On launch day you will have 48 hours to complete payment; nothing is charged before that.\n\n— KaisTrade"
            if en else
            f"Merhaba {name},\n\nFounding 30 başvurun onaylandı; sana bir kurucu koltuğu ayrıldı. "
            "Lansman tarihini ve özel davetini lansmandan kısa süre önce e-postayla göndereceğiz. "
            "Lansman günü ödeme için 48 saatin olacak; o güne kadar hiçbir ücret alınmaz.\n\n— KaisTrade")
    paragraphs = "".join(f"<p style='margin:0 0 14px'>{html.escape(p)}</p>" for p in body.replace(name, "\u0000").split("\n\n"))
    paragraphs = paragraphs.replace("\u0000", safe)
    email_service.send_email_message(
        to_email=email, subject=subject, text=body, idempotency_prefix="founding-approved-",
        idempotency_key=_hash(email)[:32],
        html=f"<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.6;color:#111'>{paragraphs}</div>",
    )


@admin_router.post("/{app_id}/approve")
async def admin_approve(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "approved", body.note)


@admin_router.post("/{app_id}/waitlist")
async def admin_waitlist(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "waitlist", body.note)


@admin_router.post("/{app_id}/reject")
async def admin_reject(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "rejected", body.note)

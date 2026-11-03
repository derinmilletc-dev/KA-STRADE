"""KaisTrade kapalı üyelik: kişiye özel davetler ve üyelik durum zinciri.

Akış: başvuru -> yönetici onayı -> (lansmandan 7 gün önce otomatik ya da yönetici eliyle) davet ->
hesap açıldı -> (2FA) -> ödeme bekleniyor -> aktif.

Davet kuralları:
- Tek bir e-postaya bağlıdır; hesap yalnız o e-postayla açılabilir (Google'da doğrulanmış Google e-postası).
- Belirteç veritabanında yalnız SHA-256 özeti olarak durur; düz hali yalnız e-postadaki bağlantıdadır.
- Tek kullanımlıktır: hesap açılınca özet silinir. Varsayılan geçerlilik 48 saat.
- Yönetici iptal edebilir, süresini uzatabilir, yeniden gönderebilir (eski bağlantı geçersiz olur).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import founding_mail
from .approval_service import owner_connection
from .founding import (
    EMAIL_RE, MAX_SEATS, RESERVED_STATUSES, _app_base, _clean, _hash, _item, _notify, _pool, _settings, _taken,
    admin_router, ensure_schema, mail_language, new_reference, public_router,
)

logger = logging.getLogger(__name__)
INVITE_TTL = timedelta(hours=48)
REMIND_BEFORE = timedelta(hours=24)
INVITE_LEAD = timedelta(days=7)
SWEEP_SECONDS = 600
TOKEN_RE = r"^[A-Za-z0-9_-]{30,80}$"


def invite_url(token: str) -> str:
    return f"{_app_base()}/davet?t={token}"


def _send_invite(row: Any, token: str, kind: str = "invite") -> bool:
    try:
        founding_mail.send(kind, email=row["email"], name=row["name"], reference=row["reference"], lang=row["lang"],
                           action_url=invite_url(token), idempotency=f"{row['reference']}:{kind}:{_hash(token)[:16]}")
        return True
    except Exception:  # noqa: BLE001 - davet kaydı durur; yönetici yeniden gönderebilir
        logger.exception("Founding %s email failed", kind)
        return False


async def _issue(conn: Any, app_id: int) -> tuple[Any, str]:
    """Başvuruya yeni davet üretir (önceki bağlantı geçersiz olur). Satır kilitli olmalı."""
    token = secrets.token_urlsafe(32)
    row = await conn.fetchrow(
        """UPDATE founding_applications SET status='invited', invite_token_hash=$2, invite_sent_at=now(),
           invite_expires_at=now() + $3::interval, invite_reminded_at=NULL, invite_revoked_at=NULL,
           invite_count=invite_count + 1, updated_at=now(), reference=COALESCE(reference, $4)
           WHERE id=$1 RETURNING *""",
        app_id, _hash(token), INVITE_TTL, new_reference(),
    )
    return row, token


# ------------------------------------------------------------------ herkese açık: davet bağlantısı

@public_router.get("/invite")
async def invite_view(request: Request, t: str = Query(pattern=TOKEN_RE)):
    """Davet sayfası için: belirteç geçerliyse davetin e-postası ve adı döner (hesap açma formunda kilitli gösterilir)."""
    from . import v22_commercial as auth

    await auth.enforce_auth_limit(request, "founding-track", None)
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM founding_applications WHERE invite_token_hash = $1", _hash(t))
    if row is None or row["status"] != "invited":
        raise HTTPException(404, "Bu davet bağlantısı geçersiz ya da daha önce kullanılmış.")
    if row["invite_expires_at"] is None or row["invite_expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "Bu davetin süresi dolmuş. Destek ekibimizle iletişime geçebilirsin.")
    return {"email": row["email"], "name": row["name"], "reference": row["reference"], "lang": row["lang"],
            "expires_at": row["invite_expires_at"].isoformat()}


async def consume_invite(request: Request, email: str, user_id: str) -> bool:
    """Hesap açıldıktan hemen sonra çağrılır: daveti kullanılmış sayar (tek kullanım) ve e-posta gönderir."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return False
    try:
        await ensure_schema(pool)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE founding_applications SET status='account_created', invite_token_hash=NULL, invite_accepted_at=now(),
                   user_id=$2, updated_at=now() WHERE lower(email)=$1 AND status='invited' RETURNING *""",
                email.strip().lower(), user_id,
            )
    except Exception:  # noqa: BLE001 - hesap açıldı; durum kaydı hatası girişi engellemez
        logger.exception("Founding invite consume failed")
        return False
    if row is not None:
        _notify(request, "account_created", row)
    return row is not None


async def advance_member(request_or_app: Any, user_id: str, to: str) -> bool:
    """Durum zinciri: account_created -> payment_pending -> active. Yalnız ileri gider; e-posta bir kez gider."""
    allowed_from = {"payment_pending": ("account_created",), "active": ("account_created", "payment_pending")}[to]
    app = getattr(request_or_app, "app", request_or_app)
    pool = getattr(app.state, "db_pool", None)
    if pool is None or not user_id:
        return False
    try:
        await ensure_schema(pool)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""UPDATE founding_applications SET status=$2, updated_at=now(){", activated_at=now()" if to == "active" else ""}
                    WHERE user_id=$1 AND status = ANY($3::text[]) RETURNING *""",
                user_id, to, list(allowed_from),
            )
    except Exception:  # noqa: BLE001
        logger.exception("Founding member advance failed")
        return False
    if row is not None:
        _notify(SimpleRequest(app), to, row)
    return row is not None


class SimpleRequest:
    """_notify yalnız request.app'e ihtiyaç duyar; arka plan işlerinde request yoktur."""

    def __init__(self, app: Any):
        self.app = app


@public_router.get("/me")
async def my_membership(request: Request):
    """Giriş yapmış üyenin kurucu üyelik aşaması (uygulamadaki 'kaldığın yerden devam et' bandı için)."""
    from . import v22_commercial as auth

    user = await auth.authenticated_user_async(request)
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return {"status": None}
    await ensure_schema(pool)
    from .binance_manual_payments import FOUNDER_AMOUNT, founder_price_lock
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status, reference FROM founding_applications WHERE user_id=$1", user["id"])
        lock = await founder_price_lock(conn, user["id"]) if row else None
    return {"status": row["status"] if row else None, "reference": row["reference"] if row else None,
            "founder_price": str(FOUNDER_AMOUNT) if lock else None, "price_locked_until": lock.isoformat() if lock else None}


# ------------------------------------------------------------------ yönetici

class ManualInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(default="", max_length=80)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


async def _locked(conn: Any, app_id: int) -> Any:
    row = await conn.fetchrow("SELECT * FROM founding_applications WHERE id=$1 FOR UPDATE", app_id)
    if row is None:
        raise HTTPException(404, "Başvuru bulunamadı.")
    return row


@admin_router.post("/{app_id}/invite")
async def admin_invite(request: Request, app_id: int = Path(ge=1)):
    """Onaylı başvuruya davet gönderir ya da yeniden gönderir (eski bağlantı geçersiz olur)."""
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
        row = await _locked(conn, app_id)
        if row["status"] not in ("approved", "invited", "invite_expired"):
            raise HTTPException(409, "Davet yalnız onaylı başvurulara gönderilebilir.")
        if row["status"] == "invite_expired":
            settings = await _settings(conn)
            if await _taken(conn) >= int(settings["seats"]):
                raise HTTPException(409, "Tüm koltuklar dolu. Önce kontenjanı artırın.")
        updated, token = await _issue(conn, app_id)
    sent = _send_invite(updated, token)
    return {**_item(updated), "email_sent": sent}


@admin_router.post("/{app_id}/revoke-invite")
async def admin_revoke(request: Request, app_id: int = Path(ge=1)):
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        row = await _locked(conn, app_id)
        if row["status"] != "invited":
            raise HTTPException(409, "Açık bir davet yok.")
        updated = await conn.fetchrow(
            """UPDATE founding_applications SET status='approved', invite_token_hash=NULL, invite_revoked_at=now(), updated_at=now()
               WHERE id=$1 RETURNING *""", app_id)
    return {**_item(updated), "email_sent": False}


@admin_router.post("/{app_id}/extend-invite")
async def admin_extend(request: Request, app_id: int = Path(ge=1)):
    """Açık ya da süresi dolmuş daveti 48 saat uzatır (aynı bağlantı geçerli kalır)."""
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        row = await _locked(conn, app_id)
        if row["status"] not in ("invited", "invite_expired") or not row["invite_token_hash"]:
            raise HTTPException(409, "Uzatılacak bir davet yok; yeni davet gönderin.")
        if row["status"] == "invite_expired":
            await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
            settings = await _settings(conn)
            if await _taken(conn) >= int(settings["seats"]):
                raise HTTPException(409, "Tüm koltuklar dolu. Önce kontenjanı artırın.")
        updated = await conn.fetchrow(
            """UPDATE founding_applications SET status='invited',
               invite_expires_at=GREATEST(invite_expires_at, now()) + $2::interval, invite_reminded_at=NULL, updated_at=now()
               WHERE id=$1 RETURNING *""", app_id, INVITE_TTL)
    return {**_item(updated), "email_sent": False}


@admin_router.post("/invite")
async def admin_manual_invite(body: ManualInvite, request: Request):
    """Başvurusu olmayan birine yönetici daveti (kontenjan kuralı aynı)."""
    email = _clean(body.email, 254).lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Geçerli bir e-posta adresi girin.")
    async with owner_connection(request) as (conn, actor):
        await ensure_schema(request.app.state.db_pool)
        await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
        if await conn.fetchval("SELECT 1 FROM founding_applications WHERE lower(email)=$1", email):
            raise HTTPException(409, "Bu e-postayla zaten bir başvuru var; listeden davet gönderin.")
        settings = await _settings(conn)
        if await _taken(conn) >= int(settings["seats"]):
            raise HTTPException(409, "Tüm koltuklar dolu. Önce kontenjanı artırın.")
        country = _clean(body.country, 80) or "-"
        app_id = await conn.fetchval(
            """INSERT INTO founding_applications (email, name, country, country_code, lang, status, source, email_confirmed_at,
               decided_at, decided_by, reference) VALUES ($1,$2,$3,$4,$5,'approved','admin',now(),now(),$6,$7) RETURNING id""",
            email, _clean(body.name, 120), country, body.country_code, mail_language(body.country_code, country),
            actor.user_id, new_reference(),
        )
        updated, token = await _issue(conn, app_id)
    sent = _send_invite(updated, token)
    return {**_item(updated), "email_sent": sent}


# ------------------------------------------------------------------ arka plan: lansman davetleri, hatırlatma, süre dolumu

async def sweep(app: Any, now: datetime | None = None) -> dict[str, int]:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return {}
    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    out = {"invited": 0, "reminded": 0, "expired": 0}
    outbox: list[tuple[str, Any, str | None]] = []
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
        settings = await _settings(conn)
        launch = settings["launch_at"]
        if launch is not None and now >= launch - INVITE_LEAD:
            # Onaylı ve henüz davet edilmemiş herkes (koltukları zaten ayrılı) onay sırasıyla davet edilir.
            for pending in await conn.fetch(
                """SELECT id FROM founding_applications WHERE status='approved' AND invite_count = 0
                   ORDER BY approved_seq NULLS LAST, id FOR UPDATE"""):
                row, token = await _issue(conn, pending["id"])
                outbox.append(("invite", row, token)); out["invited"] += 1
        for due in await conn.fetch(
            """SELECT id FROM founding_applications WHERE status='invited' AND invite_reminded_at IS NULL
               AND invite_expires_at > $1 AND invite_expires_at - $1 <= $2::interval FOR UPDATE""", now, REMIND_BEFORE):
            # Belirtecin yalnız özeti saklı olduğundan hatırlatmada yeni bağlantı üretilir (süre değişmez, eskisi geçersiz).
            token = secrets.token_urlsafe(32)
            row = await conn.fetchrow(
                """UPDATE founding_applications SET invite_token_hash=$2, invite_reminded_at=now(), updated_at=now()
                   WHERE id=$1 RETURNING *""", due["id"], _hash(token))
            outbox.append(("reminder", row, token)); out["reminded"] += 1
        for row in await conn.fetch(
            """UPDATE founding_applications SET status='invite_expired', updated_at=now()
               WHERE status='invited' AND invite_expires_at <= $1 RETURNING *""", now):
            outbox.append(("invite_expired", row, None)); out["expired"] += 1
    if out["expired"]:
        from .founding import notify_owner_seat_freed
        async with pool.acquire() as conn:
            await notify_owner_seat_freed(conn, f"{out['expired']} davetin süresi doldu")
    request = SimpleRequest(app)
    for kind, row, token in outbox:
        if kind in ("invite", "reminder"):
            _send_invite(row, token, kind)
        else:
            _notify(request, "invite_expired", row)
    return out


async def sweep_loop(app: Any) -> None:
    while True:
        try:
            result = await sweep(app)
            if any(result.values()):
                logger.info("founding sweep: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("founding sweep failed")
        await asyncio.sleep(SWEEP_SECONDS)


def sweep_enabled() -> bool:
    return os.getenv("FOUNDING_SWEEP", "true").strip().lower() not in {"0", "false", "off"}


__all__ = ["consume_invite", "advance_member", "sweep", "sweep_loop", "sweep_enabled", "invite_view", "MAX_SEATS",
           "RESERVED_STATUSES"]

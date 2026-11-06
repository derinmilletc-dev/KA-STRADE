"""Founding 30 – Erken Erişim başvuruları.

Başvurular müşteri hesaplarından ayrı tutulur; hesap yalnızca onaylanan kişiye, daha sonra açılır.
Akış: başvuru -> e-posta onayı -> (yönetici) onay / bekleme listesi / ret.
Kontenjan sunucuda sayılır; sayaç sahte değildir.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from asyncpg import UniqueViolationError
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from . import email_service, founding_mail
from .email_check import HARD as HARD_EMAIL, check_email, rejection_message
from .approval_service import owner_connection
from .web_security import client_ip

logger = logging.getLogger(__name__)
public_router = APIRouter(prefix="/api/v22/founding", tags=["Founding 30"])
admin_router = APIRouter(prefix="/api/v22/admin/founding", tags=["Founding 30 admin"])

Status = Literal["pending_email", "new", "approved", "waitlist", "rejected", "invited", "invite_expired",
                 "account_created", "payment_pending", "active"]
ALL_STATUSES = ("pending_email", "new", "approved", "waitlist", "rejected", "invited", "invite_expired",
                "account_created", "payment_pending", "active")
# Kontenjandan koltuk tutan durumlar (onaylanmış ama davet bekleyenler de koltuk ayırır)
RESERVED_STATUSES = ("approved", "invited", "account_created", "payment_pending", "active")
# Hesap açılmış başvurularda yönetici kararı (onay/bekleme/ret) uygulanmaz
ACCOUNT_STATUSES = ("account_created", "payment_pending", "active")
HEARD_FROM = ("instagram", "tiktok", "x", "youtube", "google", "friend", "telegram", "other")
EMAIL_TTL = timedelta(hours=48)
DEFAULT_SEATS = 30
MAX_SEATS = 200
DUPLICATE_TR = "Bu e-posta adresiyle zaten bir başvuru yapılmış. Her e-posta adresi yalnızca bir kez başvurabilir."
DUPLICATE_EN = "An application already exists for this email. Each email address can apply only once."
RESENT_TR = ("Bu e-posta adresiyle zaten bir başvuru yapılmış ve henüz onaylanmamış. "
             "Onay bağlantısını e-postana yeniden gönderdik; gelen kutunu ve gereksiz klasörünü kontrol et.")
RESENT_EN = ("An application already exists for this email and is not confirmed yet. "
             "We have re-sent the confirmation link; check your inbox and spam folder.")
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
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS futures_experience TEXT CHECK (futures_experience IN ('active','past','never'))""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS trading_experience TEXT CHECK (trading_experience IN ('new','lt6m','6to12m','gt1y'))""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT ''""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS reference TEXT""",
    """CREATE UNIQUE INDEX IF NOT EXISTS founding_applications_reference_key ON founding_applications (reference)""",
    """CREATE TABLE IF NOT EXISTS founding_funnel (
        day DATE NOT NULL, event TEXT NOT NULL CHECK (event IN ('view','start','step2','submit','success')),
        n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, event))""",
    """ALTER TABLE founding_settings ADD COLUMN IF NOT EXISTS registration_open BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE founding_applications DROP CONSTRAINT IF EXISTS founding_applications_status_check""",
    """ALTER TABLE founding_applications ADD CONSTRAINT founding_applications_status_check CHECK (status IN ('pending_email','new','approved','waitlist','rejected','invited','invite_expired','account_created','payment_pending','active'))""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS country_code TEXT""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS heard_from TEXT""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'form'""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_token_hash TEXT""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_sent_at TIMESTAMPTZ""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_expires_at TIMESTAMPTZ""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_reminded_at TIMESTAMPTZ""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_accepted_at TIMESTAMPTZ""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_revoked_at TIMESTAMPTZ""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS invite_count INTEGER NOT NULL DEFAULT 0""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS user_id TEXT""",
    """ALTER TABLE founding_applications ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ""",
    """CREATE UNIQUE INDEX IF NOT EXISTS founding_applications_invite_key ON founding_applications (invite_token_hash)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS founding_applications_user_key ON founding_applications (user_id)""",
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


REGISTRATION_CLOSED_MESSAGE = ("KaisTrade yalnızca davetle üye kabul ediyor. Yeni hesap için geçerli, sana özel bir davet gerekir; "
                               "davetin yoksa kaistrade.com adresinden üyelik başvurusu yapabilirsin.")


async def registration_allowed(request: Request, email: str | None = None, invite_token: str | None = None) -> bool:
    """Yeni hesap yalnız geçerli davetle açılır (KaisTrade kapalı üyelik).

    - FOUNDING_REGISTRATION=open|closed ortam değişkeni her şeyi ezer (acil durum / yerel geliştirme).
    - Yönetici 'herkese açık kayıt' anahtarı (founding_settings.registration_open) açıksa izin verilir.
    - Aksi halde: e-postaya bağlı, süresi dolmamış, kullanılmamış bir davet gerekir. Parola kaydında davet
      bağlantısındaki belirteç de eşleşmelidir; Google kaydında doğrulanmış Google e-postası davet e-postası olmalıdır.
    Veritabanı yoksa (yerel geliştirme) açık kalır. Mevcut hesapların girişi etkilenmez.
    """
    forced = os.getenv("FOUNDING_REGISTRATION", "").strip().lower()
    if forced in {"open", "closed"}:
        return forced == "open"
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return True
    try:
        await ensure_schema(pool)
        async with pool.acquire() as conn:
            value = await conn.fetchval("SELECT registration_open FROM founding_settings WHERE id = 1")
            if value is True:
                return True
            if value is not False:  # ayar okunamadı (test/eski şema): mevcut davranış korunur
                return True
            if not email:
                return False
            row = await conn.fetchrow(
                """SELECT id FROM founding_applications WHERE lower(email) = $1 AND status = 'invited'
                   AND invite_token_hash IS NOT NULL AND invite_expires_at > now()
                   AND ($2::text IS NULL OR invite_token_hash = $2)""",
                email.strip().lower(), _hash(invite_token) if invite_token else None,
            )
            return row is not None
    except Exception:  # noqa: BLE001 - ayar okunamazsa kayıt akışı kendi hatasını verir
        logger.warning("Founding registration setting could not be read", exc_info=True)
        return True


async def require_registration_open(request: Request, email: str | None = None, invite_token: str | None = None) -> None:
    if not await registration_allowed(request, email, invite_token):
        raise HTTPException(403, REGISTRATION_CLOSED_MESSAGE)


class Apply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str = Field(min_length=5, max_length=254)
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(min_length=2, max_length=80)
    lang: str = Field(default="tr", max_length=8)
    futures_experience: Literal["active", "past", "never"]
    trading_experience: Literal["new", "lt6m", "6to12m", "gt1y"]
    reason: str = Field(min_length=10, max_length=600)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    heard_from: Literal["instagram", "tiktok", "x", "youtube", "google", "friend", "telegram", "other"] | None = None
    website: str = Field(default="", max_length=200)  # bal tuzağı: dolu gelirse bot
    email_typo_ok: bool = False  # kullanıcı "olası yazım hatası" uyarısında "adresim doğru" dedi


REF_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # karışan 0/O, 1/I yok


def new_reference() -> str:
    """Sıralı olmayan başvuru numarası (kaç başvuru olduğunu açığa çıkarmaz)."""
    return "KT-F30-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(5))


def mail_language(country_code: str | None, country: str = "") -> str:
    """E-posta dili seçilen ülkeye göre: Türkiye -> Türkçe, diğer tüm ülkeler -> İngilizce."""
    if country_code:
        return "tr" if country_code.upper() == "TR" else "en"
    return "tr" if country.strip().lower() in {"türkiye", "turkey", "turkiye", "türkei"} else "en"


def _app_base() -> str:
    return email_service.validate_app_base_url().rstrip("/")


def _secret(request: Request) -> str | None:
    state = getattr(request.app.state, "v22_commercial", None)
    try:
        value = state["secret"] if state else None
    except (KeyError, TypeError):
        value = None
    return value if isinstance(value, str) and len(value) >= 16 else None


def _send_confirmation(email: str, name: str, token: str, lang: str, reference: str) -> None:
    """Başvuru alındı e-postası: numara + e-posta onay düğmesi."""
    url = f"{_app_base()}/api/v22/founding/confirm?token={token}"
    founding_mail.send("received", email=email, name=name, reference=reference, lang=lang, action_url=url,
                       idempotency=f"{reference}:{_hash(token)[:16]}")


def _notify(request: Request, kind: founding_mail.Kind, row: Any) -> bool:
    """Durum e-postası (incelemede/onay/bekleme/ret). Hata olursa kayıt etkilenmez, False döner."""
    try:
        app_path = founding_mail.APP_PATHS.get(kind)
        url = _app_base() + app_path if app_path else founding_mail.status_url(_app_base(), _secret(request), row["reference"])
        founding_mail.send(kind, email=row["email"], name=row["name"], reference=row["reference"], lang=row["lang"],
                           action_url=url, idempotency=f"{row['reference']}:{kind}:{row['updated_at'].isoformat() if row['updated_at'] else ''}")
        return True
    except Exception:  # noqa: BLE001 - e-posta hatası akışı bozmaz
        logger.exception("Founding %s email failed", kind)
        return False


FUTURES_TR = {"active": "Aktif kullanıyor", "past": "Daha önce kullandı", "never": "Henüz kullanmadı"}
TRADING_TR = {"new": "Yeni başlıyor", "lt6m": "6 aydan az", "6to12m": "6–12 ay", "gt1y": "1 yıldan fazla"}


def owner_emails() -> list[str]:
    """Bildirim alacak yönetici adresleri: FOUNDING_NOTIFY_EMAIL (virgülle çoklu) yoksa kurucu sahip e-postası."""
    from . import v22_commercial as auth
    raw = os.getenv("FOUNDING_NOTIFY_EMAIL", "").strip() or auth.BOOTSTRAP_OWNER_EMAIL
    return [item.strip() for item in raw.split(",") if EMAIL_RE.match(item.strip())]


def notify_owner_new(row: Any) -> None:
    """Başvuran e-postasını onaylayınca (gerçek başvuru) sahibe haber verilir."""
    try:
        lines = [("Ad", row["name"]), ("E-posta", row["email"]), ("Ülke", row["country"]), ("Numara", row["reference"] or "-"),
                 ("Binance Futures", FUTURES_TR.get(row["futures_experience"] or "", "-")),
                 ("Trading", TRADING_TR.get(row["trading_experience"] or "", "-")), ("Nereden", row["heard_from"] or "-"),
                 ("Gerekçe", (row["reason"] or "-")[:300])]
        for to in owner_emails():
            founding_mail.owner_alert(to_email=to, subject=f"Yeni Founding 30 başvurusu · {row['name']}",
                                      intro="Yeni bir üyelik başvurusu incelemeni bekliyor.", lines=lines,
                                      action_url="https://admin.kaistrade.com/?section=founding", idempotency=f"new:{row['reference']}")
    except Exception:  # noqa: BLE001 - bildirim hatası başvuru akışını bozmaz
        logger.exception("Founding owner alert failed")


async def notify_owner_seat_freed(conn: Any, reason: str) -> None:
    """Koltuk boşalınca sahibe bekleme listesinden öneri gönderilir (otomatik onay yok)."""
    try:
        settings = await _settings(conn)
        free = int(settings["seats"]) - await _taken(conn)
        if free <= 0:
            return
        waiting = await conn.fetch("SELECT name, email, reference FROM founding_applications WHERE status='waitlist' ORDER BY created_at, id LIMIT 3")
        lines = [("Boş koltuk", str(free)), ("Sebep", reason)] + [
            (f"Öneri {i + 1}", f"{w['name']} · {w['email']} · {w['reference'] or '-'}") for i, w in enumerate(waiting)]
        if not waiting:
            lines.append(("Bekleme listesi", "boş"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        for to in owner_emails():
            founding_mail.owner_alert(to_email=to, subject=f"Founding 30: {free} koltuk boşaldı",
                                      intro="Bir koltuk boşaldı. Bekleme listesinden onaylamak istersen öneriler aşağıda.", lines=lines,
                                      action_url="https://admin.kaistrade.com/?section=founding", idempotency=f"free:{reason}:{stamp}")
    except Exception:  # noqa: BLE001
        logger.exception("Founding seat alert failed")


async def _ensure_reference(conn: Any, app_id: int) -> None:
    """Numarasız eski kayıtlara bir kez numara verir."""
    for _ in range(5):
        try:
            await conn.execute("UPDATE founding_applications SET reference=$2 WHERE id=$1 AND reference IS NULL", app_id, new_reference())
            return
        except UniqueViolationError:
            continue


async def _settings(conn: Any) -> Any:
    return await conn.fetchrow("SELECT seats, applications_open, launch_at, price_text, registration_open FROM founding_settings WHERE id = 1")


async def _taken(conn: Any) -> int:
    """Kontenjan: aktif üyeler + hesap/ödeme aşamasındakiler + gönderilmiş davetler + davet bekleyen onaylılar."""
    return int(await conn.fetchval("SELECT COUNT(*) FROM founding_applications WHERE status = ANY($1::text[])", list(RESERVED_STATUSES)))


@public_router.get("/status")
async def public_status(request: Request):
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        settings = await _settings(conn)
    # Kaç kişinin başvurduğu/onaylandığı herkese açık yanıtta YOKTUR (yalnız yönetici ekranında görünür).
    return {
        "open": bool(settings["applications_open"]), "seats": int(settings["seats"]),
        "launch_at": settings["launch_at"].isoformat() if settings["launch_at"] else None,
        "price": settings["price_text"] or None,
    }


@public_router.post("/apply")
async def apply(body: Apply, request: Request):
    from . import v22_commercial as auth

    email = _clean(body.email, 254).lower()
    checked = check_email(email)
    if not EMAIL_RE.match(email) or checked["status"] in HARD_EMAIL or (checked["status"] == "maybe" and not body.email_typo_ok):
        raise HTTPException(422, rejection_message(checked) if checked["status"] != "ok" else "Geçerli bir e-posta adresi girin.")
    reference = new_reference()
    ok = {"ok": True, "reference": reference}
    if body.website.strip():  # bot: sessizce başarılı görün
        return ok
    await auth.enforce_auth_limit(request, "founding-apply", email)
    pool = _pool(request)
    await ensure_schema(pool)
    name, country = _clean(body.name, 120), _clean(body.country, 80)
    reason = _clean(body.reason, 600)
    if len(name) < 2 or len(country) < 2:
        raise HTTPException(422, "Ad ve ülke zorunludur.")
    if len(reason) < 10:
        raise HTTPException(422, "Lütfen KaisTrade'i neden kullanmak istediğini kısaca yaz.")
    lang = mail_language(body.country_code, country)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    send = False
    existing = None
    try:
        async with pool.acquire() as conn, conn.transaction():
            settings = await _settings(conn)
            if not settings["applications_open"]:
                raise HTTPException(409, "Başvurular şu anda kapalı." if lang == "tr" else "Applications are currently closed.")
            row = await conn.fetchrow("SELECT id, status FROM founding_applications WHERE lower(email) = $1 FOR UPDATE", email)
            if row is None:
                await conn.execute(
                    """INSERT INTO founding_applications (email, name, country, lang, email_token_hash, email_token_expires, ip_hash,
                       futures_experience, trading_experience, reason, reference, country_code, heard_from)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                    email, name, country, lang, _hash(token), now + EMAIL_TTL, _hash(client_ip(request)),
                    body.futures_experience, body.trading_experience, reason, reference, body.country_code, body.heard_from,
                )
                send = True
            else:
                existing = row
    except UniqueViolationError:  # aynı anda iki istek: ikincisi aynı e-postayla başvuru sayılır
        raise HTTPException(409, DUPLICATE_TR if lang == "tr" else DUPLICATE_EN) from None
    if existing is not None:
        # Her e-posta en fazla bir kez başvurabilir; bilgiler değiştirilmez.
        if existing["status"] == "pending_email":
            # E-posta henüz onaylanmadıysa yalnız onay bağlantısı yeniden gönderilir (aynı numara, aynı bilgiler).
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute("UPDATE founding_applications SET email_token_hash=$2, email_token_expires=$3, updated_at=now() WHERE id=$1",
                                   existing["id"], _hash(token), now + EMAIL_TTL)
                await _ensure_reference(conn, existing["id"])
                saved = await conn.fetchrow("SELECT name, lang, reference FROM founding_applications WHERE id=$1", existing["id"])
            try:
                _send_confirmation(email, saved["name"], token, saved["lang"], saved["reference"])
            except Exception:  # noqa: BLE001
                logger.exception("Founding confirmation resend failed")
            raise HTTPException(409, RESENT_TR if lang == "tr" else RESENT_EN)
        raise HTTPException(409, DUPLICATE_TR if lang == "tr" else DUPLICATE_EN)
    if send:
        try:
            _send_confirmation(email, name, token, lang, ok["reference"])
        except Exception:  # noqa: BLE001 - kayıt yapıldı; e-posta hatası kullanıcıya sızdırılmaz
            logger.exception("Founding confirmation email failed")
            raise HTTPException(503, "Onay e-postası şu anda gönderilemedi. Lütfen biraz sonra tekrar deneyin.") from None
    return ok


FUNNEL_EVENTS = ("view", "start", "step2", "submit", "success")


class FunnelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["view", "start", "step2", "submit", "success"]


@public_router.post("/event", status_code=204)
async def funnel_event(body: FunnelEvent, request: Request):
    """Anonim sayaç: yalnız gün + olay sayısı tutulur (IP, çerez, kimlik yok). Her tarayıcı oturumu bir kez gönderir."""
    from . import v22_commercial as auth

    await auth.enforce_auth_limit(request, "founding-event", None)
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO founding_funnel (day, event, n) VALUES ((now() AT TIME ZONE 'UTC')::date, $1, 1)
               ON CONFLICT (day, event) DO UPDATE SET n = founding_funnel.n + 1""", body.event,
        )


async def _funnel(conn: Any) -> dict:
    rows = await conn.fetch(
        """SELECT event, SUM(n) FILTER (WHERE day >= (now() AT TIME ZONE 'UTC')::date - 6) AS d7,
                  SUM(n) FILTER (WHERE day >= (now() AT TIME ZONE 'UTC')::date - 29) AS d30, SUM(n) AS total
           FROM founding_funnel GROUP BY event""")
    found = {r["event"]: r for r in rows}
    return {window: {event: int((found[event][window] if event in found else 0) or 0) for event in FUNNEL_EVENTS}
            for window in ("d7", "d30", "total")}


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)
    reference: str = Field(pattern=r"^KT-F30-[2-9A-HJ-NP-Z]{5}$")


TRACK_COLUMNS = "reference, name, status, created_at, email_confirmed_at, decided_at"


def _track_view(row: Any) -> dict:
    iso = lambda value: value.isoformat() if value else None  # noqa: E731
    return {"reference": row["reference"], "first_name": (row["name"] or "").strip().split(" ")[0], "status": row["status"],
            "created_at": iso(row["created_at"]), "email_confirmed_at": iso(row["email_confirmed_at"]), "decided_at": iso(row["decided_at"])}


@public_router.post("/track")
async def track(body: Track, request: Request):
    """Başvuru numarası + e-posta ile durum. İkisi birlikte doğru değilse aynı 404 döner."""
    from . import v22_commercial as auth

    email = _clean(body.email, 254).lower()
    await auth.enforce_auth_limit(request, "founding-track", email)
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {TRACK_COLUMNS} FROM founding_applications WHERE reference = $1 AND lower(email) = $2", body.reference, email,
        )
    if row is None:
        raise HTTPException(404, "Başvuru bulunamadı.")
    return _track_view(row)


@public_router.get("/track-link")
async def track_link(request: Request, r: str = Query(pattern=r"^KT-F30-[2-9A-HJ-NP-Z]{5}$"), k: str = Query(pattern=r"^[0-9a-f]{32}$")):
    """E-postadaki kişiye özel bağlantı: imza doğruysa e-posta sormadan durumu gösterir."""
    from . import v22_commercial as auth

    secret = _secret(request)
    if not secret or not hmac.compare_digest(founding_mail.status_key(secret, r), k):
        raise HTTPException(404, "Başvuru bulunamadı.")
    await auth.enforce_auth_limit(request, "founding-track", None)
    pool = _pool(request)
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT {TRACK_COLUMNS} FROM founding_applications WHERE reference = $1", r)
    if row is None:
        raise HTTPException(404, "Başvuru bulunamadı.")
    return _track_view(row)


@public_router.get("/confirm")
async def confirm(request: Request, token: str = Query(min_length=20, max_length=200)):
    pool = _pool(request)
    await ensure_schema(pool)
    base = _app_base()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, status, email_token_expires FROM founding_applications WHERE email_token_hash = $1 FOR UPDATE", _hash(token),
        )
        confirmed = None
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
        await _ensure_reference(conn, row["id"])
        confirmed = await conn.fetchrow("SELECT * FROM founding_applications WHERE id=$1", row["id"])
    if confirmed is not None:
        _notify(request, "review", confirmed)
        notify_owner_new(confirmed)
    return RedirectResponse(f"{base}/?founding=confirmed", status_code=303)


# ---------------------------------------------------------------- yönetici (yalnız OWNER)

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seats: int = Field(ge=1, le=MAX_SEATS)
    applications_open: bool
    launch_at: datetime | None = None
    price_text: str = Field(default="", max_length=40)
    registration_open: bool = False


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=500)


def iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _item(row: Any) -> dict:
    return {
        "id": int(row["id"]), "email": row["email"], "name": row["name"], "country": row["country"], "lang": row["lang"],
        "status": row["status"], "note": row["note"], "approved_seq": row["approved_seq"],
        "futures_experience": row["futures_experience"], "trading_experience": row["trading_experience"],
        "reason": row["reason"] or "", "reference": row["reference"], "heard_from": row["heard_from"], "source": row["source"],
        "invite_sent_at": iso(row["invite_sent_at"]), "invite_expires_at": iso(row["invite_expires_at"]),
        "invite_accepted_at": iso(row["invite_accepted_at"]), "invite_count": int(row["invite_count"] or 0),
        "invite_open": bool(row["invite_token_hash"]) and row["status"] == "invited", "user_id": row["user_id"],
        "activated_at": iso(row["activated_at"]),
        "price_locked_until": iso(row["activated_at"] + timedelta(days=365)) if row["activated_at"] else None,
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
        funnel = await _funnel(conn)
        free = int(settings["seats"]) - await _taken(conn)
        suggestions = [_item(r) for r in await conn.fetch(
            "SELECT * FROM founding_applications WHERE status='waitlist' ORDER BY created_at, id LIMIT $1", max(0, min(free, 5)))] if free > 0 else []
    return {
        "items": [_item(r) for r in rows], "counts": counts, "funnel": funnel, "suggestions": suggestions,
        "settings": {"seats": int(settings["seats"]), "applications_open": bool(settings["applications_open"]),
                     "launch_at": settings["launch_at"].isoformat() if settings["launch_at"] else None, "price_text": settings["price_text"],
                     "registration_open": bool(settings["registration_open"])},
    }


@admin_router.put("/settings")
async def admin_settings(body: Settings, request: Request):
    async with owner_connection(request) as (conn, _actor):
        await ensure_schema(request.app.state.db_pool)
        taken = await _taken(conn)
        if body.seats < taken:
            raise HTTPException(409, f"Zaten {taken} kişi onaylı; koltuk sayısı bunun altına düşürülemez.")
        await conn.execute(
            """UPDATE founding_settings SET seats=$1, applications_open=$2, launch_at=$3, price_text=$4, registration_open=$5,
               updated_at=now() WHERE id=1""",
            body.seats, body.applications_open, body.launch_at, _clean(body.price_text, 40), body.registration_open,
        )
    return {"ok": True}


async def _decide(request: Request, app_id: int, new_status: str, note: str) -> dict:
    changed = False
    async with owner_connection(request) as (conn, actor):
        await ensure_schema(request.app.state.db_pool)
        row = await conn.fetchrow("SELECT * FROM founding_applications WHERE id = $1 FOR UPDATE", app_id)
        if row is None:
            raise HTTPException(404, "Başvuru bulunamadı.")
        if row["status"] == "pending_email":
            raise HTTPException(409, "Başvuranın e-postası henüz onaylanmadı.")
        if row["status"] in ACCOUNT_STATUSES:
            raise HTTPException(409, "Bu kişi davetle hesabını açmış; başvuru kararı artık değiştirilemez.")
        seq = row["approved_seq"]
        if new_status == "approved" and row["status"] not in RESERVED_STATUSES:
            settings = await _settings(conn)
            await conn.execute("SELECT pg_advisory_xact_lock(71010030)")
            taken = await _taken(conn)
            if taken >= int(settings["seats"]):
                raise HTTPException(409, "Tüm koltuklar dolu. Önce kontenjanı artırın veya başka bir onayı geri alın.")
            seq = int(await conn.fetchval("SELECT COALESCE(MAX(approved_seq),0)+1 FROM founding_applications"))
        if new_status != "approved":
            seq = None
        changed = row["status"] != new_status and not (new_status == "approved" and row["status"] in RESERVED_STATUSES)
        if new_status == "approved" and row["status"] in RESERVED_STATUSES:
            raise HTTPException(409, "Bu başvuru zaten onaylı.")
        await _ensure_reference(conn, app_id)
        await conn.execute(
            """UPDATE founding_applications SET status=$2, note=CASE WHEN $3 <> '' THEN $3 ELSE note END, approved_seq=$4,
               decided_at=now(), decided_by=$5, updated_at=now(),
               invite_token_hash=CASE WHEN $2 = 'approved' THEN invite_token_hash ELSE NULL END,
               invite_revoked_at=CASE WHEN $2 <> 'approved' AND invite_token_hash IS NOT NULL THEN now() ELSE invite_revoked_at END
               WHERE id=$1""",
            app_id, new_status, _clean(note, 500), seq, actor.user_id,
        )
        updated = await conn.fetchrow("SELECT * FROM founding_applications WHERE id = $1", app_id)
    sent = _notify(request, {"approved": "approved", "waitlist": "waitlist", "rejected": "rejected"}[new_status], updated) if changed else False
    return {**_item(updated), "email_sent": sent}


@admin_router.post("/{app_id}/approve")
async def admin_approve(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "approved", body.note)


@admin_router.post("/{app_id}/waitlist")
async def admin_waitlist(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "waitlist", body.note)


@admin_router.post("/{app_id}/reject")
async def admin_reject(request: Request, body: Decision, app_id: int = Path(ge=1)):
    return await _decide(request, app_id, "rejected", body.note)


from . import founding_invites  # noqa: E402,F401  (davet uçlarını aynı yönlendiricilere ekler)

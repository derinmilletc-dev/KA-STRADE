"""Hesap silme fence'i: silme süreci boyunca yeni giriş emirlerini reddeder.

- Kalıcı (veritabanı) ve süreçler arası geçerlidir; her emirde yeniden okunur.
- Yalnızca hesap silme sürecinde kurulur. Kurulum hata/kontrol başarısızlığında da kalır (yeni giriş olmaz).
- Kapatma (reduceOnly / closePosition) emirleri her zaman serbesttir. Parametreler katı ayrıştırılır;
  belirsiz veya çelişkili değer kapatma sayılmaz (kapalı başarısızlık).
- Uçuş kayıtları: her giriş emri gönderilmeden önce veritabanında kısa ömürlü bir kayıt açılır ve gönderim
  bitince silinir. Fence kurulumu bu kayıtlar bitene kadar bekler; böylece fence ile gönderim arasındaki
  yarış canlı kontrolü aşamaz. Kilit (advisory lock) kurulum ve giriş kaydını aynı hesapta seri hale getirir.
- Kilit kaldırma yalnızca temiz (açık pozisyon/emir yok, doğrulanmış) canlı kontrolden sonra yapılır;
  otomatik değildir ve her karar denetim kaydına yazılır.
- Fence yok (tablo hiç oluşturulmamış) ise giriş serbesttir; bu durum yalnızca hiçbir silme başlatılmadığında vardır.
  Diğer her veritabanı hatası giriş engeller (kapalı başarısızlık).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException

from .account_erasure import user_hash

logger = logging.getLogger(__name__)

FENCE_TABLE = "commercial_account_fences"
AUDIT_TABLE = "commercial_erasure_audit"
INFLIGHT_TABLE = "commercial_entry_inflight"
ENTRY_ORDER_PATHS = frozenset({"/fapi/v1/order", "/fapi/v1/order/test", "/fapi/v1/algoOrder"})
INFLIGHT_TTL_SECONDS = 120
DRAIN_TIMEOUT_SECONDS = 60
SCHEMA_LOCK_KEY = 4242001


class FenceError(Exception):
    """Giriş emri fence nedeniyle reddedildi veya fence okunamadı (kapalı başarısızlık)."""


def _flag(value: Any) -> bool | None:
    """Yalnızca gerçek bool ve açık metin değerleri kabul edilir. 1/0 gibi tamsayılar (1 == True) belirsiz sayılır."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        if value in ("true", "TRUE", "True"):
            return True
        if value in ("false", "FALSE", "False", ""):
            return False
    return None


def is_reduce_only_exit(params: dict[str, Any] | None) -> bool:
    """Yalnızca açıkça kapatma olan emirler True döner. Belirsiz/çelişkili değer -> False (giriş sayılır)."""
    params = params or {}
    reduce_only = _flag(params.get("reduceOnly", False))
    close_position = _flag(params.get("closePosition", False))
    if reduce_only is None or close_position is None:
        return False
    return reduce_only or close_position


async def ensure_fence_schema(pool: Any) -> None:
    """Şemayı süreçler arası seri oluşturur (CREATE IF NOT EXISTS yarışlarını önler)."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK_KEY)
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {FENCE_TABLE} (
              user_hash TEXT PRIMARY KEY,
              state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'RELEASED')),
              reason TEXT NOT NULL,
              set_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              released_at TIMESTAMPTZ
            );
            CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
              id BIGSERIAL PRIMARY KEY,
              user_hash TEXT NOT NULL,
              action TEXT NOT NULL,
              actor TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS {INFLIGHT_TABLE} (
              token TEXT PRIMARY KEY,
              user_hash TEXT NOT NULL,
              expires_at TIMESTAMPTZ NOT NULL
            );
            CREATE INDEX IF NOT EXISTS {INFLIGHT_TABLE}_user_idx ON {INFLIGHT_TABLE} (user_hash);
        """)


async def audit(pool: Any, user_id: str, action: str, actor: str, detail: str = "") -> None:
    # Denetim kaydında ham kullanıcı kimliği yok; yalnızca karma. Actor ya sistem ya yönetici kimliği.
    await pool.execute(
        f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, $2, $3, $4)",
        user_hash(user_id), action, actor[:120], detail[:300],
    )


async def engage_fence(pool: Any, user_id: str, *, actor: str, reason: str = "account_erasure") -> None:
    """Silme başlamadan önce çağrılır. Idempotent. Fence'i kurar, sonra bu hesabın uçuşta giriş emirlerinin
    bitmesini bekler. Bekleme bitmezse FenceError: fence aktif kalır, silme durur (yeniden denenebilir)."""
    await ensure_fence_schema(pool)
    uh = user_hash(user_id)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", uh)
        await conn.execute(
            f"""
            INSERT INTO {FENCE_TABLE} (user_hash, state, reason) VALUES ($1, 'ACTIVE', $2)
            ON CONFLICT (user_hash) DO UPDATE SET state = 'ACTIVE', reason = EXCLUDED.reason, released_at = NULL
            """,
            uh, reason,
        )
        await conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, 'FENCE_ENGAGED', $2, $3)",
            uh, actor[:120], reason[:300],
        )
    # Commit'ten sonra: bu noktadan sonra yeni giriş kaydı açılamaz; önceden açılmış olanları bekle.
    await drain_inflight_entries(pool, user_id, timeout=DRAIN_TIMEOUT_SECONDS)


async def drain_inflight_entries(pool: Any, user_id: str, *, timeout: float) -> None:
    uh = user_hash(user_id)
    deadline = time.monotonic() + timeout
    while True:
        await pool.execute(f"DELETE FROM {INFLIGHT_TABLE} WHERE user_hash = $1 AND expires_at <= NOW()", uh)
        pending = await pool.fetchval(
            f"SELECT count(*) FROM {INFLIGHT_TABLE} WHERE user_hash = $1 AND expires_at > NOW()", uh,
        )
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise FenceError("entry orders still in flight")
        await asyncio.sleep(0.5)


async def fence_active(pool: Any, user_id: str) -> bool:
    try:
        state = await pool.fetchval(f"SELECT state FROM {FENCE_TABLE} WHERE user_hash = $1", user_hash(user_id))
    except asyncpg.UndefinedTableError:
        return False  # tablo hiç oluşturulmadı: hiçbir silme başlatılmamış, fence yok
    return state == "ACTIVE"


async def assert_entries_allowed(application: Any, user_id: str) -> None:
    """Salt okuma kontrolü (uçuş kaydı açmaz). Gönderim yolu için begin_entry kullanılmalıdır."""
    if application is None or not user_id:
        raise FenceError("entry identity unavailable")
    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        raise FenceError("entry pool unavailable")
    try:
        active = await fence_active(pool, user_id)
    except Exception as exc:
        raise FenceError("fence state unreadable") from exc
    if active:
        raise FenceError("account erasure fence active")


async def begin_entry(application: Any, user_id: str) -> str:
    """Giriş emri göndermeden önce çağrılır. Fence aktif değilse uçuş kaydı açar ve belirteci döndürür.
    Her hata FenceError'dır (kapalı başarısızlık). Çağıran gönderimden sonra end_entry ile kaydı kapatmalıdır."""
    if application is None or not user_id:
        raise FenceError("entry identity unavailable")
    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        raise FenceError("entry pool unavailable")
    try:
        if not getattr(application.state, "_fence_schema_ready", False):
            await ensure_fence_schema(pool)
            application.state._fence_schema_ready = True
        uh = user_hash(user_id)
        token = uuid.uuid4().hex
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", uh)
            state = await conn.fetchval(f"SELECT state FROM {FENCE_TABLE} WHERE user_hash = $1", uh)
            if state == "ACTIVE":
                raise FenceError("account erasure fence active")
            await conn.execute(
                f"INSERT INTO {INFLIGHT_TABLE} (token, user_hash, expires_at) VALUES ($1, $2, NOW() + make_interval(secs => $3))",
                token, uh, INFLIGHT_TTL_SECONDS,
            )
        return token
    except FenceError:
        raise
    except Exception as exc:
        raise FenceError("fence unavailable") from exc


async def end_entry(application: Any, token: str) -> None:
    """Gönderim bitince uçuş kaydını siler. Silme başarısız olsa bile kayıt TTL ile kendiliğinden biter."""
    pool = getattr(getattr(application, "state", None), "db_pool", None)
    if pool is None or not token:
        return
    try:
        await pool.execute(f"DELETE FROM {INFLIGHT_TABLE} WHERE token = $1", token)
    except Exception:
        logger.warning("entry in-flight marker cleanup failed; it expires by TTL")


async def release_fence(pool: Any, user_id: str, *, actor: str, clean_live_check: bool) -> None:
    """Kontrollü kaldırma. Yalnızca bu çağrıdan hemen önce temiz canlı kontrol tamamlandıysa kaldırır.
    Temiz değilse reddeder ve bunu denetler; fence aktif kalır."""
    if clean_live_check is not True:
        await audit(pool, user_id, "FENCE_RELEASE_REFUSED", actor, "live check not clean")
        raise HTTPException(409, "Kilit kaldırılamaz: açık pozisyon/emir kontrolü temiz değil veya tamamlanmadı")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", user_hash(user_id))
        await conn.execute(
            f"UPDATE {FENCE_TABLE} SET state = 'RELEASED', released_at = NOW() WHERE user_hash = $1",
            user_hash(user_id),
        )
        await conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, 'FENCE_RELEASED', $2, 'clean live check')",
            user_hash(user_id), actor[:120],
        )

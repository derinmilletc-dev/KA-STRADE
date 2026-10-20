"""Hesap silme fence'i: silme süreci boyunca yeni giriş emirlerini reddeder.

- Kalıcı (veritabanı) ve süreçler arası geçerlidir; her emirde yeniden okunur.
- Yalnızca hesap silme sürecinde kurulur. Kurulum hata/kontrol başarısızlığında da kalır (yeni giriş olmaz).
- Kapatma (reduceOnly / closePosition) emirleri her zaman serbesttir. Parametreler katı ayrıştırılır;
  belirsiz veya çelişkili değer kapatma sayılmaz (kapalı başarısızlık).
- Kilit kaldırma yalnızca temiz (açık pozisyon/emir yok, doğrulanmış) canlı kontrolden sonra yapılır;
  otomatik değildir ve her karar denetim kaydına yazılır.
- Fence yok (tablo hiç oluşturulmamış) ise giriş serbesttir; bu durum yalnızca hiçbir silme başlatılmadığında vardır.
  Diğer her veritabanı hatası giriş engeller (kapalı başarısızlık).
"""
from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import HTTPException

from .account_erasure import user_hash

FENCE_TABLE = "commercial_account_fences"
AUDIT_TABLE = "commercial_erasure_audit"
ENTRY_ORDER_PATHS = frozenset({"/fapi/v1/order", "/fapi/v1/order/test", "/fapi/v1/algoOrder"})


class FenceError(Exception):
    """Giriş emri fence nedeniyle reddedildi veya fence okunamadı (kapalı başarısızlık)."""


def _flag(value: Any) -> bool | None:
    try:
        if value in (True, "true", "TRUE", "True"):
            return True
        if value in (False, None, "false", "FALSE", "False", ""):
            return False
    except TypeError:
        return None
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
    await pool.execute(f"""
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
    """)


async def audit(pool: Any, user_id: str, action: str, actor: str, detail: str = "") -> None:
    # Denetim kaydında ham kullanıcı kimliği yok; yalnızca karma. Actor ya sistem ya yönetici kimliği.
    await pool.execute(
        f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, $2, $3, $4)",
        user_hash(user_id), action, actor[:120], detail[:300],
    )


async def engage_fence(pool: Any, user_id: str, *, actor: str, reason: str = "account_erasure") -> None:
    """Silme başlamadan önce çağrılır. Idempotent; mevcut fence'i yeniden etkinleştirir ve denetler."""
    await ensure_fence_schema(pool)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            f"""
            INSERT INTO {FENCE_TABLE} (user_hash, state, reason) VALUES ($1, 'ACTIVE', $2)
            ON CONFLICT (user_hash) DO UPDATE SET state = 'ACTIVE', reason = EXCLUDED.reason, released_at = NULL
            """,
            user_hash(user_id), reason,
        )
        await conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, 'FENCE_ENGAGED', $2, $3)",
            user_hash(user_id), actor[:120], reason[:300],
        )


async def fence_active(pool: Any, user_id: str) -> bool:
    try:
        state = await pool.fetchval(f"SELECT state FROM {FENCE_TABLE} WHERE user_hash = $1", user_hash(user_id))
    except asyncpg.UndefinedTableError:
        return False  # tablo hiç oluşturulmadı: hiçbir silme başlatılmamış, fence yok
    return state == "ACTIVE"


async def assert_entries_allowed(application: Any, user_id: str) -> None:
    """Giriş emri öncesi kontrol. Her hata (kimlik yok, havuz yok, DB hatası) FenceError fırlatır."""
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


async def release_fence(pool: Any, user_id: str, *, actor: str, clean_live_check: bool) -> None:
    """Kontrollü kaldırma. Yalnızca bu çağrıdan hemen önce temiz canlı kontrol tamamlandıysa kaldırır.
    Temiz değilse reddeder ve bunu denetler; fence aktif kalır."""
    if clean_live_check is not True:
        await audit(pool, user_id, "FENCE_RELEASE_REFUSED", actor, "live check not clean")
        raise HTTPException(409, "Kilit kaldırılamaz: açık pozisyon/emir kontrolü temiz değil veya tamamlanmadı")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            f"UPDATE {FENCE_TABLE} SET state = 'RELEASED', released_at = NOW() WHERE user_hash = $1",
            user_hash(user_id),
        )
        await conn.execute(
            f"INSERT INTO {AUDIT_TABLE} (user_hash, action, actor, detail) VALUES ($1, 'FENCE_RELEASED', $2, 'clean live check')",
            user_hash(user_id), actor[:120],
        )

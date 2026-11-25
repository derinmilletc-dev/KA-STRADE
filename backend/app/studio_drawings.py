"""Analiz stüdyosu çizimleri: kullanıcı + sembol başına saklanır (telefon ↔ bilgisayar eşitlemesi).

Yalnız görüntü verisidir; emir/anahtar içermez. Coin başına son yazan kazanır (updated_at, ms): daha eski bir kayıt yeniden
yazılamaz, böylece iki cihaz birbirinin yeni çizimini sessizce ezmez. Boyut sınırları sunucuda zorlanır."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .v22_commercial import authenticated_user_async

router = APIRouter(prefix="/api/v22/studio", tags=["Studio drawings"])
TABLE = "kaistrade_studio_drawings"
SYMBOL = re.compile(r"^[A-Z0-9_.-]{2,40}$")
MAX_DRAWINGS = 50
MAX_ITEM_BYTES = 1500
MAX_SYMBOLS_PER_USER = 300
KINDS = {"hline", "trend", "fib", "rect", "channel", "measure", "text"}
_SCHEMA: set[int] = set()


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Çizim eşitleme şu an kullanılamıyor.")
    return pool


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA:
        return
    await pool.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
          user_id TEXT NOT NULL, symbol TEXT NOT NULL, data TEXT NOT NULL, updated_at BIGINT NOT NULL,
          PRIMARY KEY (user_id, symbol))"""
    )
    _SCHEMA.add(id(pool))


def validate_drawings(items: list[Any]) -> list[dict[str, Any]]:
    """Şekil türü ve boyutu doğrulanır; ayrıntılı alan kontrolünü istemci yapar (sunucu yalnız güvenli, küçük JSON saklar)."""
    if len(items) > MAX_DRAWINGS:
        raise HTTPException(422, f"En fazla {MAX_DRAWINGS} çizim saklanır.")
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in KINDS or not isinstance(item.get("id"), str) or len(item["id"]) > 40:
            raise HTTPException(422, "Geçersiz çizim kaydı.")
        if len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_ITEM_BYTES:
            raise HTTPException(422, "Çizim kaydı çok büyük.")
        out.append(item)
    return out


class DrawingsPut(BaseModel):
    symbol: str = Field(min_length=2, max_length=40)
    drawings: list[Any] = Field(default_factory=list, max_length=MAX_DRAWINGS)
    updated_at: int = Field(ge=1, le=4_102_444_800_000)


@router.get("/drawings")
async def get_drawings(request: Request, symbol: str = Query(min_length=2, max_length=40)) -> Any:
    user = await authenticated_user_async(request)
    pool = _pool(request)
    key = symbol.strip().upper()
    if not SYMBOL.match(key):
        raise HTTPException(422, "Sembol geçersiz.")
    await ensure_schema(pool)
    row = await pool.fetchrow(f"SELECT data, updated_at FROM {TABLE} WHERE user_id = $1 AND symbol = $2", str(user["id"]), key)
    if row is None:
        raise HTTPException(404, "Kayıt yok.")
    try:
        drawings = json.loads(row["data"])
    except ValueError:
        drawings = []
    return {"symbol": key, "drawings": drawings if isinstance(drawings, list) else [], "updated_at": int(row["updated_at"])}


@router.put("/drawings")
async def put_drawings(request: Request, body: DrawingsPut) -> Any:
    user = await authenticated_user_async(request)
    pool = _pool(request)
    key = body.symbol.strip().upper()
    if not SYMBOL.match(key):
        raise HTTPException(422, "Sembol geçersiz.")
    drawings = validate_drawings(body.drawings)
    await ensure_schema(pool)
    user_id = str(user["id"])
    existing = await pool.fetchrow(f"SELECT updated_at FROM {TABLE} WHERE user_id = $1 AND symbol = $2", user_id, key)
    if existing is not None and int(existing["updated_at"]) > body.updated_at:
        return {"ok": True, "applied": False, "updated_at": int(existing["updated_at"])}
    if existing is None:
        count = await pool.fetchval(f"SELECT COUNT(*) FROM {TABLE} WHERE user_id = $1", user_id)
        if int(count or 0) >= MAX_SYMBOLS_PER_USER:
            raise HTTPException(409, "Çok fazla coinde çizim var; bazılarını temizle.")
    if not drawings:
        await pool.execute(f"DELETE FROM {TABLE} WHERE user_id = $1 AND symbol = $2 AND updated_at <= $3", user_id, key, body.updated_at)
        # Silme de bir "yazma"dır: zaman damgası tutulur ki eski cihaz silinen çizimleri geri yüklemesin.
    await pool.execute(
        f"""INSERT INTO {TABLE} (user_id, symbol, data, updated_at) VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, symbol) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
            WHERE {TABLE}.updated_at <= EXCLUDED.updated_at""",
        user_id, key, json.dumps(drawings, ensure_ascii=False, separators=(",", ":")), body.updated_at,
    )
    return {"ok": True, "applied": True, "updated_at": body.updated_at}

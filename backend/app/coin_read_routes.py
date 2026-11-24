"""Coin yorumu ve pozisyon yorum alarmı rotaları (yalnız okuma/bildirim; emir göndermez)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import coin_read, coin_read_service, entry_watch, read_alarms, read_log
from .v22_commercial import authenticated_user_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v22", tags=["Coin read"])


class AlarmInput(BaseModel):
    exchange: str = Field(min_length=2, max_length=10)
    product_id: str = Field(min_length=2, max_length=60)
    symbol: str = Field(min_length=2, max_length=30)
    side: str = Field(min_length=4, max_length=5)
    entry: float | None = None
    mode: str = "against"
    min_tf: str = "15m"
    stop: float | None = None


class KeepInput(BaseModel):
    exchange: str = Field(min_length=2, max_length=10)
    product_ids: list[str] = Field(default_factory=list, max_length=60)


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Yorum alarmı şu an kullanılamıyor.")
    return pool


@router.get("/coin-read/{symbol}")
async def coin_read_get(symbol: str, request: Request, interval: str = Query("15m"), side: str | None = Query(None), entry: float | None = Query(None, gt=0)) -> Any:
    await authenticated_user_async(request)
    clean_symbol = coin_read_service.valid_symbol(symbol)
    if clean_symbol is None:
        raise HTTPException(422, "Sembol geçersiz.")
    tf = interval if interval in coin_read.TFS else "15m"
    try:
        data = await coin_read_service.read(request.app, clean_symbol, history_tf=tf)
    except Exception as exc:  # noqa: BLE001
        logger.info("Coin yorumu hazırlanamadı %s (%s)", clean_symbol, type(exc).__name__)
        raise HTTPException(502, "Yorum şu an hazırlanamadı, biraz sonra tekrar dene.") from exc
    out = dict(data)
    side_value = (side or "").upper()
    if side_value in ("LONG", "SHORT") and data["price"] > 0:
        reads = {r["tf"]: r for r in data["timeframes"]}
        out["position"] = {**coin_read.position_fit(data["summary"], data["levels"], data["price"], side_value, entry),
                           "events": coin_read.position_events(reads, data["summary"], side_value, min_tf="5m", mode="all")}
    return out


@router.get("/read-alarms")
async def alarms_list(request: Request, exchange: str = Query(...)) -> Any:
    user = await authenticated_user_async(request)
    if exchange not in ("binance", "coinbase"):
        raise HTTPException(422, "Borsa geçersiz.")
    alarms = await read_alarms.list_alarms(_pool(request), str(user["id"]), exchange)
    return {"alarms": [{k: v for k, v in a.items() if k != "last_push_at"} for a in alarms], "max": read_alarms.MAX_PER_USER}


@router.post("/read-alarms")
async def alarms_set(payload: AlarmInput, request: Request) -> Any:
    user = await authenticated_user_async(request)
    data = read_alarms.normalize(payload.exchange, payload.product_id, payload.symbol, payload.side, payload.entry, payload.mode, payload.min_tf, payload.stop)
    if data is None:
        raise HTTPException(422, "Alarm bilgileri geçersiz.")
    try:
        saved = await read_alarms.upsert(_pool(request), str(user["id"]), data)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"alarm": {k: v for k, v in saved.items() if k != "last_push_at"}}


@router.delete("/read-alarms")
async def alarms_delete(request: Request, exchange: str = Query(...), product_id: str = Query(..., min_length=2, max_length=60)) -> Any:
    user = await authenticated_user_async(request)
    if exchange not in ("binance", "coinbase"):
        raise HTTPException(422, "Borsa geçersiz.")
    return {"deleted": await read_alarms.delete(_pool(request), str(user["id"]), exchange, product_id)}


@router.post("/read-alarms/keep")
async def alarms_keep(payload: KeepInput, request: Request) -> Any:
    """Pozisyonlar ekranı açıkken açık pozisyonların alarmını uzatır; listede olmayan alarmlar silinir (pozisyon kapanmış)."""
    user = await authenticated_user_async(request)
    if payload.exchange not in ("binance", "coinbase"):
        raise HTTPException(422, "Borsa geçersiz.")
    pool, user_id = _pool(request), str(user["id"])
    open_ids = [p for p in payload.product_ids if 2 <= len(p) <= 60]
    for alarm in await read_alarms.list_alarms(pool, user_id, payload.exchange):
        if alarm["product_id"] not in open_ids:
            await read_alarms.delete(pool, user_id, payload.exchange, alarm["product_id"])
    await read_alarms.keep_alive(pool, user_id, payload.exchange, open_ids)
    return {"ok": True}


class WatchInput(BaseModel):
    symbol: str = Field(min_length=2, max_length=30)
    direction: str = "both"


@router.get("/entry-watch")
async def watch_list(request: Request) -> Any:
    user = await authenticated_user_async(request)
    items = await entry_watch.list_watch(_pool(request), str(user["id"]))
    return {"items": [{"symbol": i["symbol"], "direction": i["direction"]} for i in items], "max": entry_watch.MAX_PER_USER}


@router.post("/entry-watch")
async def watch_set(payload: WatchInput, request: Request) -> Any:
    user = await authenticated_user_async(request)
    data = entry_watch.normalize(payload.symbol, payload.direction)
    if data is None:
        raise HTTPException(422, "Sembol geçersiz.")
    try:
        saved = await entry_watch.upsert(_pool(request), str(user["id"]), data)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"item": {"symbol": saved["symbol"], "direction": saved["direction"]}}


@router.delete("/entry-watch")
async def watch_delete(request: Request, symbol: str = Query(..., min_length=2, max_length=30)) -> Any:
    user = await authenticated_user_async(request)
    clean_symbol = coin_read_service.valid_symbol(symbol)
    if clean_symbol is None:
        raise HTTPException(422, "Sembol geçersiz.")
    return {"deleted": await entry_watch.delete(_pool(request), str(user["id"]), clean_symbol)}


@router.get("/read-stats")
async def read_stats(request: Request, symbol: str | None = Query(None)) -> Any:
    """Son 7 günde kaydedilen yorumların 1 saat sonraki yön isabeti (genel, güven düzeyine göre, coin)."""
    await authenticated_user_async(request)
    clean_symbol = coin_read_service.valid_symbol(symbol) if symbol else None
    try:
        return await read_log.stats(_pool(request), clean_symbol)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info("Yorum doğruluğu okunamadı (%s)", type(exc).__name__)
        raise HTTPException(502, "Doğruluk bilgisi şu an alınamadı.") from exc

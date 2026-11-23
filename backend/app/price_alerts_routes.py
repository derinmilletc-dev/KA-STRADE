"""Binance tarafı fiyat alarmı rotaları (emir göndermez; yalnız bildirim). Coinbase'dekiyle aynı tablo ve döngü, exchange='binance'.

Fiyat herkese açık Binance vadeli verisinden gelir; anahtar gerekmez. Yön, kurulum anındaki fiyata göre sunucuda belirlenir."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from .coinbase import price_alerts
from .v22_commercial import authenticated_user_async

router = APIRouter(prefix="/api/v22/price-alerts", tags=["Price alerts (Binance)"])
EXCHANGE = "binance"
SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")


class AlertInput(BaseModel):
    product_id: str = Field(min_length=2, max_length=30)
    level: str = Field(min_length=1, max_length=30)


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(503, "Alarm şu an kullanılamıyor.")
    return pool


@router.get("/alerts")
async def list_alerts(request: Request) -> Any:
    user = await authenticated_user_async(request)
    pool, user_id = _pool(request), str(user["id"])
    return {"alerts": await price_alerts.list_alerts(pool, user_id, EXCHANGE), "fired": await price_alerts.list_fired(pool, user_id, EXCHANGE), "max": price_alerts.MAX_PER_USER}


@router.post("/alerts")
async def add_alert(payload: AlertInput, request: Request) -> Any:
    user = await authenticated_user_async(request)
    pool, user_id = _pool(request), str(user["id"])
    symbol = payload.product_id.strip().upper()
    if not SYMBOL.match(symbol):
        raise HTTPException(422, "Sembol geçersiz.")
    level = price_alerts.parse_level(payload.level)
    if level is None:
        raise HTTPException(422, "Alarm seviyesi sıfırdan büyük bir fiyat olmalı.")
    if await price_alerts.count_active(pool, user_id, EXCHANGE) >= price_alerts.MAX_PER_USER:
        raise HTTPException(409, f"En fazla {price_alerts.MAX_PER_USER} aktif alarm olabilir. Birini sil.")
    try:
        quote = (await price_alerts.binance_prices(request.app)).get(symbol) or {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Binance fiyatı şu an alınamadı; biraz sonra dene.") from exc
    price: Decimal | None = price_alerts.parse_level(quote.get("mid"))
    if price is None:
        raise HTTPException(409, "Bu sembolün fiyatı bulunamadı; alarm kurulamadı.")
    direction = price_alerts.direction_for(level, price)
    if direction is None:
        raise HTTPException(422, "Fiyat zaten bu seviyede. Farklı bir seviye seç.")
    return {"alert": await price_alerts.add_alert(pool, user_id, symbol, level, direction, price, EXCHANGE)}


@router.delete("/alerts/{alert_id}")
async def delete_alert(request: Request, alert_id: int = Path(ge=1)) -> Any:
    user = await authenticated_user_async(request)
    await price_alerts.delete_alert(_pool(request), str(user["id"]), alert_id)
    return {"ok": True}


@router.post("/alerts/ack")
async def ack_alerts(request: Request) -> Any:
    user = await authenticated_user_async(request)
    await price_alerts.ack_fired(_pool(request), str(user["id"]), EXCHANGE)
    return {"ok": True}

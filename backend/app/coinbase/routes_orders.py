"""Coinbase EMİR rotaları — C aşaması. Yalnızca COINBASE_ENABLED ve COINBASE_ORDERS_ENABLED birlikte açıkken yüklenir.

Her emir rotası: oturum + OWNER + izin listesi, onay metni, tek seferlik istek kimliği, tek-akış kilidi.
Hatalar 4xx + Türkçe mesaj (5xx gizlenir). Beklenmeyen hata bile açık mesajla döner; asla sessiz kalmaz.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import flow, journal, order_api, vault
from .errors import CoinbaseError
from .jwt_auth import load_private_key
from .limits import current_limits
from .routes import _error, _member, _pool
from .trading_client import CoinbaseTradingClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coinbase/orders", tags=["Coinbase manual (orders)"])

OPEN_CONFIRMATION = "COINBASE EMİR AÇ"
CLOSE_CONFIRMATION = "COINBASE POZİSYON KAPAT"
CHANGE_CONFIRMATION = "COINBASE DEĞİŞTİR"
PRODUCT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$"
_REQUEST_TTL = 600.0
_SEEN: dict[str, float] = {}


class PreflightPayload(BaseModel):
    product_id: str = Field(pattern=PRODUCT_PATTERN)
    side: Literal["BUY", "SELL"]
    stop_price: str = Field(min_length=1, max_length=30)
    margin_usd: str = Field(min_length=1, max_length=30)
    entry_limit_price: str | None = Field(default=None, max_length=30)
    take_profit_price: str | None = Field(default=None, max_length=30)
    rest: bool = False
    contracts: int | None = Field(default=None, ge=1, le=100000)


class OpenPayload(PreflightPayload):
    confirm: str = Field(max_length=80)
    request_id: str = Field(min_length=8, max_length=64)


class ClosePayload(BaseModel):
    product_id: str = Field(pattern=PRODUCT_PATTERN)
    confirm: str = Field(max_length=80)
    request_id: str = Field(min_length=8, max_length=64)


class StopPayload(ClosePayload):
    stop_price: str = Field(min_length=1, max_length=30)


class ReducePayload(ClosePayload):
    reduce_by: int = Field(ge=1, le=100000)


class CancelEntryPayload(ClosePayload):
    product_id: str = Field(default="", max_length=40)
    order_id: str = Field(min_length=3, max_length=80)


class CleanupPayload(BaseModel):
    confirm: str = Field(max_length=80)
    request_id: str = Field(min_length=8, max_length=64)


def _check_confirm(given: str, expected: str) -> None:
    if given.strip() != expected:
        raise HTTPException(422, f"Onay metni yanlış. Şunu aynen yaz: {expected}")


def _claim_request(user_id: str, request_id: str) -> None:
    """Aynı istek kimliği ikinci kez işlenmez (çift tıklama / yeniden gönderim)."""
    now = time.monotonic()
    for key in [k for k, t in _SEEN.items() if now - t > _REQUEST_TTL]:
        _SEEN.pop(key, None)
    key = f"{user_id}:{request_id}"
    if key in _SEEN:
        raise CoinbaseError("DUPLICATE_REQUEST", "Bu istek zaten işlendi. Sonucu 'koruma durumu'ndan kontrol et.", http_status=409)
    _SEEN[key] = now


async def _trading_client(request: Request, user: dict[str, Any]) -> CoinbaseTradingClient:
    stored = await vault.load_key(_pool(request), user["id"])
    if stored is None:
        raise CoinbaseError("KEY_MISSING", "Coinbase anahtarı kayıtlı değil. Önce anahtarı kaydet.", http_status=409)
    key_name, pem = stored
    return CoinbaseTradingClient(key_name, load_private_key(pem))


async def _guarded(request: Request, action: str, work: Any, *, claim: str | None = None) -> Any:
    """Üye + kilit + hata çevirisi. `work(client, pool, user_id)` çağrılır."""
    user = _member(request)
    pool = _pool(request)
    token = uuid.uuid4().hex
    locked = False
    try:
        if claim:
            _claim_request(user["id"], claim)
        client = await _trading_client(request, user)
        if not await journal.acquire_lock(pool, user["id"], token):
            raise CoinbaseError("FLOW_BUSY", "Başka bir emir işlemi sürüyor. Birkaç saniye bekleyip 'koruma durumu'nu kontrol et.", http_status=409)
        locked = True
        return await work(client, pool, user["id"])
    except CoinbaseError as exc:
        return _error(exc)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - beklenmeyen hata sessiz kalmasın
        logger.exception("Coinbase emir akışında beklenmeyen hata action=%s", action)
        return JSONResponse(
            {"detail": "Beklenmeyen bir hata oldu. Emrin gidip gitmediği bilinmiyor: Coinbase'de pozisyon ve emirleri hemen kontrol et.",
             "code": "UNEXPECTED_ERROR", "critical": True},
            status_code=424,
        )
    finally:
        if locked:
            await journal.release_lock(pool, user["id"], token)


@router.post("/preflight")
async def preflight(payload: PreflightPayload, request: Request) -> Any:
    request_dict = payload.model_dump()

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.preflight(client, current_limits(), request_dict)

    return await _guarded(request, "preflight", work)


@router.post("/open")
async def open_order(payload: OpenPayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, OPEN_CONFIRMATION)
    request_dict = payload.model_dump(exclude={"confirm", "request_id"})

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.open_position(client, pool, user_id, current_limits(), request_dict)

    return await _guarded(request, "open", work, claim=payload.request_id)


@router.post("/close")
async def close_order(payload: ClosePayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, CLOSE_CONFIRMATION)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.close_flow(client, pool, user_id, payload.product_id)

    return await _guarded(request, "close", work, claim=payload.request_id)


@router.post("/reduce")
async def reduce_order(payload: ReducePayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, CHANGE_CONFIRMATION)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.reduce_flow(client, pool, user_id, current_limits(), payload.product_id, payload.reduce_by)

    return await _guarded(request, "reduce", work, claim=payload.request_id)


@router.post("/stop")
async def move_stop(payload: StopPayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, CHANGE_CONFIRMATION)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.set_stop(client, pool, user_id, current_limits(), payload.product_id, payload.stop_price)

    return await _guarded(request, "stop", work, claim=payload.request_id)


@router.post("/cancel-entry")
async def cancel_entry(payload: CancelEntryPayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, CHANGE_CONFIRMATION)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.cancel_entry(client, pool, user_id, current_limits(), payload.order_id)

    return await _guarded(request, "cancel_entry", work, claim=payload.request_id)


@router.post("/reconcile")
async def reconcile(request: Request) -> Any:
    """Dolmuş ama korumasız bekleyen girişleri korur/kapatır. Emir verebildiği için üye + akış kilidi şart; onay metni gerekmez
    (kullanıcının zaten verdiği emrin koruması; yeni risk açmaz)."""
    _member(request)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.reconcile_flow(client, pool, user_id, current_limits())

    return await _guarded(request, "reconcile", work)


@router.post("/cleanup-orphans")
async def cleanup(payload: CleanupPayload, request: Request) -> Any:
    _member(request)
    _check_confirm(payload.confirm, CHANGE_CONFIRMATION)

    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.cleanup_orphans(client, pool, user_id)

    return await _guarded(request, "cleanup", work, claim=payload.request_id)


@router.get("/protection")
async def protection(request: Request) -> Any:
    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        return await flow.protection_report(client)

    return await _guarded(request, "protection", work)


@router.get("/pending")
async def pending(request: Request) -> Any:
    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        rows = await flow.pending_entries(client)
        return {"orders": [{k: (str(v) if v is not None and not isinstance(v, str) else v) for k, v in row.items()} for row in rows]}

    return await _guarded(request, "pending", work)


@router.get("/open")
async def open_orders(request: Request) -> Any:
    async def work(client: CoinbaseTradingClient, pool: Any, user_id: str) -> Any:
        rows = await order_api.list_active_orders(client)
        return {"orders": [
            {k: (str(v) if v is not None and not isinstance(v, str) else v) for k, v in row.items()} for row in rows
        ]}

    return await _guarded(request, "open_orders", work)

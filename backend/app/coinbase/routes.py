"""Coinbase rotaları — B aşaması: SALT OKUNUR. Emir, iptal, para aktarımı rotası YOKTUR.

Bu dosya yalnızca COINBASE_ENABLED açıkken yüklenir (bkz. __init__.include_coinbase_if_enabled).
Her rota: oturum açık + OWNER + izin listesinde (COINBASE_ALLOWED_EMAILS) şartını arar.
Coinbase kaynaklı her sorun 4xx ile ve Türkçe mesajla döner (5xx mesajı sunucuda gizlenir).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from . import service, vault
from .client import CoinbaseClient, clock_status
from .config import orders_enabled, resting_enabled, user_allowed
from .errors import CoinbaseError
from .jwt_auth import load_private_key, validate_key_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coinbase", tags=["Coinbase manual (read-only)"])

SAVE_CONFIRMATION = "COINBASE ANAHTARINI KAYDET"
DELETE_CONFIRMATION = "COINBASE ANAHTARINI SİL"


class KeyPayload(BaseModel):
    key_name: str = Field(min_length=10, max_length=300)
    private_key: SecretStr = Field(min_length=40, max_length=4000)
    confirm: str = Field(max_length=80)
    totp_code: str = Field(default="", max_length=12)


class DeletePayload(BaseModel):
    confirm: str = Field(max_length=80)
    totp_code: str = Field(default="", max_length=12)


def _member(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "member", None)
    allowed, reason = user_allowed(user, getattr(request.state, "premium", None))
    if not allowed:
        if not user:
            raise HTTPException(401, reason)
        raise HTTPException(403, reason)
    return user


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise CoinbaseError("VAULT_NOT_READY", "Veritabanı bağlantısı yok; Coinbase kasası kullanılamıyor.")
    return pool


async def _verify_totp(request: Request, user: dict[str, Any], code: str) -> None:
    from .. import account_settings  # tembel: yalnızca kaydet/sil işlemlerinde; Binance kasasıyla aynı 2FA kuralı

    await account_settings.require_totp(request, user, code, required=True)


def _client_for(key_name: str, private_key: Any) -> CoinbaseClient:
    return CoinbaseClient(key_name, private_key)


def _error(exc: CoinbaseError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.http_status)


async def _stored_client(request: Request, user: dict[str, Any]) -> CoinbaseClient:
    stored = await vault.load_key(_pool(request), user["id"])
    if stored is None:
        raise CoinbaseError("KEY_MISSING", "Coinbase anahtarı kayıtlı değil. Önce anahtarı kaydet.", http_status=409)
    key_name, pem = stored
    return _client_for(key_name, load_private_key(pem))


@router.get("/status")
async def coinbase_status(request: Request) -> Any:
    user = _member(request)
    try:
        ready, reason = vault.master_key_ready()
        meta = await vault.key_meta(_pool(request), user["id"]) if ready else None
    except CoinbaseError as exc:
        return _error(exc)
    return {
        "enabled": True,
        "orders_enabled": orders_enabled(), "resting_enabled": resting_enabled(),
        "mode": "READ_ONLY",
        "vault_ready": ready,
        "vault_reason": reason,
        "key_saved": meta is not None,
        "key": meta,
        "clock": clock_status(),
    }


@router.put("/key")
async def coinbase_save_key(payload: KeyPayload, request: Request) -> Any:
    user = _member(request)
    if payload.confirm.strip() != SAVE_CONFIRMATION:
        raise HTTPException(422, f"Onay metni yanlış. Şunu aynen yaz: {SAVE_CONFIRMATION}")
    await _verify_totp(request, user, payload.totp_code)
    try:
        ready, reason = vault.master_key_ready()
        if not ready:
            raise CoinbaseError("VAULT_NOT_READY", reason or "Coinbase kasası hazır değil.")
        pool = _pool(request)
        key_name = validate_key_name(payload.key_name)
        pem = payload.private_key.get_secret_value()
        private_key = load_private_key(pem)
        check = await service.check_key(_client_for(key_name, private_key))
        if check["can_transfer"] is True:
            raise CoinbaseError(
                "TRANSFER_PERMISSION",
                "Bu anahtarda 'transfer' izni açık. Güvenlik için kaydedilmedi: Coinbase'de anahtarı yalnızca "
                "'görüntüleme' ve 'işlem' izniyle yeniden oluştur.",
                http_status=422,
            )
        await vault.save_key(pool, user["id"], key_name, pem, {k: check[k] for k in ("can_view", "can_trade", "can_transfer", "permissions_checked")})
        meta = await vault.key_meta(pool, user["id"])
    except CoinbaseError as exc:
        return _error(exc)
    logger.info("Coinbase anahtarı kaydedildi user_id=%s", user["id"])
    return {"saved": True, "key": meta, "check": check}


@router.delete("/key")
async def coinbase_delete_key(payload: DeletePayload, request: Request) -> Any:
    user = _member(request)
    if payload.confirm.strip() != DELETE_CONFIRMATION:
        raise HTTPException(422, f"Onay metni yanlış. Şunu aynen yaz: {DELETE_CONFIRMATION}")
    await _verify_totp(request, user, payload.totp_code)
    try:
        deleted = await vault.delete_key(_pool(request), user["id"])
    except CoinbaseError as exc:
        return _error(exc)
    logger.info("Coinbase anahtarı silindi user_id=%s deleted=%s", user["id"], deleted)
    return {"deleted": deleted}


@router.get("/account")
async def coinbase_account(request: Request) -> Any:
    user = _member(request)
    try:
        client = await _stored_client(request, user)
        return {"mode": "READ_ONLY", **await service.fetch_account(client), "clock": clock_status()}
    except CoinbaseError as exc:
        return _error(exc)


@router.get("/positions")
async def coinbase_positions(request: Request) -> Any:
    user = _member(request)
    try:
        client = await _stored_client(request, user)
        return {"mode": "READ_ONLY", "positions": await service.fetch_positions(client), "clock": clock_status()}
    except CoinbaseError as exc:
        return _error(exc)

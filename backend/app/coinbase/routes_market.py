"""Coinbase piyasa verisi rotaları (SALT OKUNUR): ürün listesi ve anlık fiyat. Emir göndermez.

COINBASE_ENABLED açıkken yüklenir (emir bayrağından bağımsız); aynı erişim şartı: oturum + OWNER + izin listesi.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from . import market
from .errors import CoinbaseError
from .routes import _error, _member, _stored_client

router = APIRouter(prefix="/api/coinbase", tags=["Coinbase manual (market data)"])
PRODUCT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$"


async def _reader(request: Request, user: dict[str, Any]) -> Any:
    return await _stored_client(request, user)


@router.get("/products")
async def coinbase_products(request: Request) -> Any:
    user = _member(request)
    try:
        client = await _reader(request, user)
        return {"products": await market.fetch_products(client)}
    except CoinbaseError as exc:
        return _error(exc)


@router.get("/quote/{product_id}")
async def coinbase_quote(request: Request, product_id: str = Path(pattern=PRODUCT_PATTERN)) -> Any:
    user = _member(request)
    try:
        client = await _reader(request, user)
        return await market.fetch_quote(client, product_id)
    except CoinbaseError as exc:
        return _error(exc)

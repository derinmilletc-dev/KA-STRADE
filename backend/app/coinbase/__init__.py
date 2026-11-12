"""Coinbase (ABD vadeli) — SADECE MANUEL İŞLEM. Şu an: B aşaması, salt okunur.

COINBASE_ENABLED açık değilse bu paket hiçbir rota yüklemez ve hiçbir şey değiştirmez.
Mevcut Binance sistemi ve botlarla hiçbir bağı yoktur.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import allowed_emails, coinbase_enabled, orders_enabled

logger = logging.getLogger(__name__)

__all__ = ["include_coinbase_if_enabled"]


def _mount_load_failure(app: Any, message: str) -> None:
    """Modül yüklenemediyse sessiz kalma: durum rotası açık bir hata döndürsün."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    failure = APIRouter(prefix="/api/coinbase", tags=["Coinbase manual (load failure)"])

    @failure.get("/status")
    async def coinbase_load_failure() -> Any:
        return JSONResponse({"detail": message, "code": "COINBASE_LOAD_FAILED"}, status_code=424)

    app.include_router(failure)


def _mount_orders_load_failure(app: Any, message: str) -> None:
    """Emir modülü yüklenemediyse: emir rotaları 424 + açık mesaj versin (404 ile kafa karıştırmasın)."""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    failure = APIRouter(prefix="/api/coinbase/orders", tags=["Coinbase manual (orders load failure)"])

    @failure.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def coinbase_orders_load_failure(path: str) -> Any:
        return JSONResponse({"detail": message, "code": "COINBASE_ORDERS_LOAD_FAILED"}, status_code=424)

    app.include_router(failure)


def include_coinbase_if_enabled(app: Any) -> bool:
    """Bayrak açıksa Coinbase rotalarını ekler. Kapalıysa HİÇBİR ŞEY yapmaz (False döner)."""
    if not coinbase_enabled():
        return False
    try:
        from .routes import router

        app.include_router(router)
    except Exception as exc:  # siteyi düşürme; ama hatayı gizleme
        message = f"Coinbase modülü yüklenemedi: {type(exc).__name__}. Sunucu günlüğüne bak."
        logger.exception("COINBASE_ENABLED açık ama Coinbase rotaları yüklenemedi")
        app.state.coinbase_load_error = message
        try:
            _mount_load_failure(app, message)
        except Exception:
            logger.exception("Coinbase yükleme hatası rotası da eklenemedi")
        return False
    app.state.coinbase_enabled = True
    logger.warning("Coinbase (salt okunur) AÇIK; izinli hesap sayısı: %d", len(allowed_emails()))
    if orders_enabled():
        try:
            from .routes_orders import router as orders_router

            app.include_router(orders_router)
            app.state.coinbase_orders_enabled = True
            logger.warning("Coinbase EMİR gönderme AÇIK (COINBASE_ORDERS_ENABLED)")
        except Exception as exc:  # emir rotaları yüklenemezse salt okunur çalışır ama bunu açıkça söyler
            message = f"Coinbase emir modülü yüklenemedi: {type(exc).__name__}. Sunucu günlüğüne bak."
            logger.exception("COINBASE_ORDERS_ENABLED açık ama emir rotaları yüklenemedi")
            app.state.coinbase_orders_load_error = message
            try:
                _mount_orders_load_failure(app, message)
            except Exception:
                logger.exception("Coinbase emir yükleme hatası rotası da eklenemedi")
    return True

"""Coinbase (ABD vadeli) — SADECE MANUEL İŞLEM. Şu an: B aşaması, salt okunur.

COINBASE_ENABLED açık değilse bu paket hiçbir rota yüklemez ve hiçbir şey değiştirmez.
Mevcut Binance sistemi ve botlarla hiçbir bağı yoktur.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import allowed_emails, coinbase_enabled

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
    return True

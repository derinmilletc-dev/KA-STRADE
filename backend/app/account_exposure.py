"""Silme öncesi canlı borsa açık-pozisyon/emir kontrolü (salt okuma, kapalı başarısızlık).

Kurallar:
- Yalnızca imzalı GET istekleri: positionRisk, openOrders, openAlgoOrders. Emir gönderilmez, iptal edilmez.
- Kasadaki kullanıcının TÜM kayıtları (LIVE ve TESTNET, aktif olsun ya da olmasın) kontrol edilir.
- Borsaya ulaşılamazsa, anahtar geçersiz/iptal ise, zaman aşımı, imza, oran sınırı, beklenmeyen veya
  şekli bozuk yanıt olursa: silme durdurulur (ExposureUnverifiable -> 503). Bu bir "temiz" sonuç değildir.
- Açık pozisyon veya emir bulunursa: silme durdurulur (ExposureFound -> 409).
- Hiçbir gizli anahtar veya ham yanıt hata mesajına/loga yazılmaz.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import HTTPException

from . import exchange_connections as vault

logger = logging.getLogger(__name__)

CHECKS = (
    ("/fapi/v3/positionRisk", "positions"),
    ("/fapi/v1/openOrders", "open_orders"),
    ("/fapi/v1/openAlgoOrders", "open_algo_orders"),
)
CHECK_TIMEOUT_SECONDS = 15.0


class ExposureUnverifiable(Exception):
    """Kontrol tamamlanamadı: silme yapılmaz."""


class ExposureFound(Exception):
    """Açık pozisyon veya emir var: silme yapılmaz."""

    def __init__(self, reasons: list[str]):
        super().__init__("open exposure")
        self.reasons = reasons


def assess(payloads: dict[str, Any]) -> list[str]:
    """Ham yanıtları yorumlar. Şekil beklenenden farklıysa belirsiz sayılır (kapalı başarısızlık)."""
    reasons: list[str] = []
    positions = payloads.get("positions")
    if not isinstance(positions, list):
        raise ExposureUnverifiable("positionRisk shape")
    for row in positions:
        if not isinstance(row, dict) or "positionAmt" not in row:
            raise ExposureUnverifiable("positionRisk row shape")
        try:
            amount = Decimal(str(row["positionAmt"]))
        except (InvalidOperation, ValueError):
            raise ExposureUnverifiable("positionAmt value") from None
        if not amount.is_finite():
            raise ExposureUnverifiable("positionAmt not finite")
        if amount != 0:
            reasons.append("positions")
            break
    for key in ("open_orders", "open_algo_orders"):
        orders = payloads.get(key)
        if not isinstance(orders, list):
            raise ExposureUnverifiable(f"{key} shape")
        if orders and key not in reasons:
            reasons.append(key)
    return reasons


async def check_live_exposure(http: httpx.AsyncClient, mode: str, api_key: str, secret_key: str, *, time_offset_ms: int) -> None:
    """Tek bir anahtar kaydını kontrol eder. Başarılı dönerse yalnızca 'açık yok' anlamına gelir."""
    host = vault.HOSTS[vault.normalize_mode(mode)]
    payloads: dict[str, Any] = {}
    for path, key in CHECKS:
        try:
            payloads[key] = await vault._signed_get(http, host, path, api_key, secret_key, time_offset_ms)
        except vault.VaultError:
            raise ExposureUnverifiable(f"{path} rejected or unavailable") from None
        except Exception:
            raise ExposureUnverifiable(f"{path} failed") from None
    reasons = assess(payloads)
    if reasons:
        raise ExposureFound(reasons)


async def _stored_records(pool: Any, user_id: str) -> list[tuple[str, bytes]]:
    rows = await pool.fetch(
        """
        SELECT mode, encrypted_payload FROM protrebot_exchange_account_vault WHERE user_id = $1
        UNION ALL
        SELECT mode, encrypted_payload FROM protrebot_exchange_session_vault WHERE user_id = $1
        """,
        user_id,
    )
    return [(str(row["mode"]), row["encrypted_payload"]) for row in rows]


async def _gate(application: Any, user: dict[str, Any], http: httpx.AsyncClient) -> None:
    pool = getattr(application.state, "db_pool", None)
    if pool is None:
        return
    try:
        records = await _stored_records(pool, user["id"])
    except Exception:
        raise ExposureUnverifiable("vault read failed") from None
    for mode, ciphertext in records:
        try:
            normalized = vault.normalize_mode(mode)
            api_key, secret_key = vault.decrypt_credentials(ciphertext, mode=normalized)
            offset = await vault._server_time_offset(http, normalized)
        except Exception:
            raise ExposureUnverifiable("credential or clock unavailable") from None
        await check_live_exposure(http, normalized, api_key, secret_key, time_offset_ms=offset)


async def gate_before_erasure(application: Any, user: dict[str, Any], *, http: httpx.AsyncClient | None = None) -> None:
    """Silme rotalarından çağrılır. Kontrol geçmezse HTTPException fırlatır; geçerse döner."""
    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS)
    try:
        await _gate(application, user, client)
    except ExposureFound as exc:
        logger.info("Erasure blocked: open exposure reasons=%s", ",".join(exc.reasons))
        raise HTTPException(409, "Açık pozisyon veya emir bulunuyor; önce kapatın, silme yapılmadı") from None
    except ExposureUnverifiable as exc:
        logger.warning("Erasure blocked: exposure could not be verified (%s)", type(exc).__name__)
        raise HTTPException(503, "Borsa durumu doğrulanamadı; silme durduruldu, anahtarlarınızı kontrol edip tekrar deneyin") from None
    finally:
        if owns_client:
            await client.aclose()

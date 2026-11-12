"""Coinbase emir uçları için ince sarmalayıcılar. Cevap biçimini doğrular; beklenmedik cevap = açık hata.

Önemli: create/close cevabı HTTP 200 ile `success: false` olabilir. Burada bu her zaman hataya çevrilir.
Ağ/5xx hatasında emrin ulaşıp ulaşmadığı BİLİNMEZ: aynı client_order_id ile BİR kez yeniden denenir
(Coinbase aynı kimliği ikinci kez açmaz, mevcut emri döndürür); yine olmazsa ORDER_OUTCOME_UNKNOWN verilir.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from .errors import CoinbaseError, clean_remote_message
from .money import to_decimal
from .trading_client import (
    BATCH_CANCEL_PATH, BEST_BID_ASK_PATH, CLOSE_POSITION_PATH, ORDER_GET_PREFIX, ORDER_LIST_PATH,
    ORDERS_PATH, CoinbaseTradingClient,
)

ACTIVE_STATUSES = ("OPEN", "PENDING", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED")
STOP_TYPES = ("STOP_LIMIT", "STOP", "BRACKET")
_MAX_PAGES = 5

# new_order_failure_reason -> sade Türkçe. Listede yoksa kod aynen gösterilir (gizlenmez).
FAILURE_TR = {
    "INSUFFICIENT_FUND": "Bakiye yetersiz.",
    "INSUFFICIENT_FUNDS": "Bakiye yetersiz.",
    "BREACHED_RISK_LIMIT": "Coinbase'in risk sınırı aşılıyor.",
    "IN_LIQUIDATION": "Hesap zorla kapatma (likidasyon) durumunda.",
    "ORDER_ENTRY_DISABLED": "Coinbase şu an emir girişini kapatmış.",
    "TRADING_DISABLED": "Bu ürün için işlem kapalı.",
    "PRODUCT_TRADING_HALTED": "Bu ürün şu an durdurulmuş.",
    "UNTRADABLE_PRODUCT": "Bu ürün işlem görmüyor.",
    "INVALID_SIZE_PRECISION": "Miktar adımı yanlış.",
    "INVALID_PRICE_PRECISION": "Fiyat adımı yanlış.",
    "INVALID_LEVERAGE": "Kaldıraç değeri geçersiz.",
    "INVALID_MARGIN_TYPE": "Marjin türü geçersiz.",
    "INVALID_FCM_TRADING_SESSION": "Vadeli işlem oturumu bu emre uygun değil.",
    "FUTURES_AFTER_HOUR_INVALID_ORDER_TYPE": "Gün içi oturum dışında bu emir türü kabul edilmiyor.",
    "FUTURES_AFTER_HOUR_INVALID_TIME_IN_FORCE": "Gün içi oturum dışında bu emir süresi kabul edilmiyor.",
    "GEOFENCING_RESTRICTION": "Coinbase bu bölgeden bu işleme izin vermiyor.",
    "LIMIT_ORDER_PRICE_EXCEEDS_PRICE_BAND_ON_BUY": "Fiyat, izin verilen bandın dışında (alış).",
    "LIMIT_ORDER_PRICE_EXCEEDS_PRICE_BAND_ON_SELL": "Fiyat, izin verilen bandın dışında (satış).",
    "LIMIT_PRICE_TOO_FAR_FROM_MARKET": "Limit fiyat piyasadan çok uzak.",
    "STOP_LOSS_PRICE_TOO_LOW": "Stop fiyatı çok düşük.",
    "STOP_LOSS_PRICE_TOO_HIGH": "Stop fiyatı çok yüksek.",
    "STOP_ALREADY_TRIGGERED": "Stop fiyatı zaten geçildi.",
    "UNSUPPORTED_ORDER_CONFIGURATION": "Coinbase bu emir ayarını desteklemiyor.",
    "INVALID_LIMIT_PRICE": "Limit fiyat geçersiz.",
    "INVALID_REQUEST": "Coinbase isteği geçersiz buldu.",
    "DUPLICATE_CLIENT_ORDER_ID": "Bu emir kimliği daha önce kullanılmış.",
    "CANNOT_CLOSE_ZERO_POSITION": "Kapatılacak pozisyon yok.",
    "POSITION_SIZE_INCREASE_REJECT": "Emir pozisyonu büyütecekti, reddedildi.",
    "REDUCE_ONLY_INCREASED_POSITION_SIZE": "Emir pozisyonu büyütecekti, reddedildi.",
    "REDUCE_ONLY_NOT_ALLOWED_ON_VENUE": "Bu borsada yalnız-azalt emri desteklenmiyor.",
    "TOO_MANY_OPEN_ORDERS": "Çok fazla açık emir var.",
}


class OrderRejected(CoinbaseError):
    """Coinbase emri açıkça reddetti (hiçbir şey açılmadı)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        text = FAILURE_TR.get(reason, f"Neden kodu: {reason}")
        suffix = f" Coinbase mesajı: {detail}" if detail else ""
        super().__init__("ORDER_REJECTED", f"Coinbase emri reddetti. {text}{suffix}", http_status=422, extra={"reason": reason})


def _unknown_outcome(what: str) -> CoinbaseError:
    return CoinbaseError(
        "ORDER_OUTCOME_UNKNOWN",
        f"{what} emrinin sonucu belirsiz (ağ/Coinbase hatası). Coinbase'de pozisyon ve emirleri kontrol et.",
        extra={"critical": True},
    )


def _parse_success(payload: dict[str, Any], what: str) -> str:
    if payload.get("success") is not True:
        error = payload.get("error_response")
        error = error if isinstance(error, dict) else {}
        reason = str(error.get("new_order_failure_reason") or error.get("error") or "UNKNOWN_FAILURE_REASON")
        detail = clean_remote_message(error.get("message") or error.get("error_details"))
        raise OrderRejected(reason, detail)
    success = payload.get("success_response")
    order_id = success.get("order_id") if isinstance(success, dict) else None
    if not isinstance(order_id, str) or not order_id:
        raise CoinbaseError("BAD_RESPONSE", f"Coinbase {what} cevabı beklenen biçimde değil: order_id yok.")
    return order_id


async def _post_idempotent(client: CoinbaseTradingClient, path: str, body: dict[str, Any], what: str) -> dict[str, Any]:
    """Ağ/5xx'te AYNI gövde (aynı client_order_id) ile bir kez daha dener; sonra belirsizlik bildirir."""
    sleep = getattr(client, "_sleep", asyncio.sleep)
    for attempt in (1, 2):
        try:
            return await client.post(path, body)
        except CoinbaseError as exc:
            if exc.code not in ("NETWORK", "UPSTREAM"):
                raise
            if attempt == 2:
                raise _unknown_outcome(what) from None
            await sleep(0.5)
    raise AssertionError("ulaşılamaz")


async def create_order(client: CoinbaseTradingClient, body: dict[str, Any], what: str) -> str:
    return _parse_success(await _post_idempotent(client, ORDERS_PATH, body, what), what)


async def close_position(client: CoinbaseTradingClient, product_id: str, size: Decimal, client_order_id: str) -> str:
    body = {"client_order_id": client_order_id, "product_id": product_id, "size": _plain(size)}
    return _parse_success(await _post_idempotent(client, CLOSE_POSITION_PATH, body, "Kapatma"), "kapatma")


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text or "0"


async def cancel_orders(client: CoinbaseTradingClient, order_ids: list[str]) -> dict[str, tuple[bool, str]]:
    """Her emir için (başarılı mı, neden). Hata bir istisna değil, sonuçta görünür: çağıran doğrulamak zorunda."""
    if not order_ids:
        return {}
    payload = await client.post(BATCH_CANCEL_PATH, {"order_ids": list(order_ids)})
    results = payload.get("results")
    if not isinstance(results, list):
        raise CoinbaseError("BAD_RESPONSE", "Coinbase iptal cevabı beklenen biçimde değil: results yok.")
    outcome: dict[str, tuple[bool, str]] = {}
    for item in results:
        if isinstance(item, dict) and isinstance(item.get("order_id"), str):
            outcome[item["order_id"]] = (item.get("success") is True, str(item.get("failure_reason") or ""))
    for order_id in order_ids:
        outcome.setdefault(order_id, (False, "CEVAPTA_YOK"))
    return outcome


def normalize_order(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("order_id"), str) or not isinstance(raw.get("status"), str):
        raise CoinbaseError("BAD_RESPONSE", "Coinbase emir kaydı beklenen biçimde değil (order_id/status yok).")
    configuration = raw.get("order_configuration")
    stop_cfg: dict[str, Any] = {}
    if isinstance(configuration, dict):
        for key, value in configuration.items():
            if key.startswith("stop_limit") and isinstance(value, dict):
                stop_cfg = value

    def num(value: Any) -> Decimal | None:
        return to_decimal(value, "sayı") if value not in (None, "") else None

    return {
        "order_id": raw["order_id"],
        "client_order_id": raw.get("client_order_id"),
        "product_id": raw.get("product_id"),
        "side": raw.get("side"),
        "status": raw["status"].upper(),
        "order_type": str(raw.get("order_type") or "").upper(),
        "filled_size": num(raw.get("filled_size")),
        "average_filled_price": num(raw.get("average_filled_price")),
        "base_size": num(stop_cfg.get("base_size")),
        "stop_price": num(stop_cfg.get("stop_price")),
        "limit_price": num(stop_cfg.get("limit_price")),
        "stop_direction": stop_cfg.get("stop_direction"),
    }


async def get_order(client: CoinbaseTradingClient, order_id: str) -> dict[str, Any]:
    payload = await client.get(ORDER_GET_PREFIX + order_id)
    return normalize_order(payload.get("order"))


async def list_active_orders(client: CoinbaseTradingClient, product_id: str | None = None) -> list[dict[str, Any]]:
    """Aktif (açık/bekleyen) vadeli emirler. Sayfa sayısı aşılırsa HATA verir (eksik liste ile karar verilmez)."""
    params: dict[str, Any] = {"order_status": list(ACTIVE_STATUSES), "product_type": "FUTURE", "limit": 250}
    if product_id:
        params["product_ids"] = [product_id]
    orders: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGES):
        payload = await client.get(ORDER_LIST_PATH, params=dict(params))
        rows = payload.get("orders")
        if not isinstance(rows, list):
            raise CoinbaseError("BAD_RESPONSE", "Coinbase emir listesi beklenen biçimde değil: orders yok.")
        orders.extend(normalize_order(row) for row in rows)
        if payload.get("has_next") is not True:
            return orders
        cursor = payload.get("cursor")
        if not isinstance(cursor, str) or not cursor:
            raise CoinbaseError("BAD_RESPONSE", "Coinbase emir listesi sayfalaması beklenen biçimde değil.")
        params["cursor"] = cursor
    raise CoinbaseError("BAD_RESPONSE", "Açık emir sayısı beklenenden çok fazla; liste tamamlanamadı.")


async def best_bid_ask(client: CoinbaseTradingClient, product_id: str) -> tuple[Decimal, Decimal]:
    payload = await client.get(BEST_BID_ASK_PATH, params={"product_ids": [product_id]})
    books = payload.get("pricebooks")
    book = next((b for b in books if isinstance(b, dict) and b.get("product_id") == product_id), None) if isinstance(books, list) else None
    if book is None:
        raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı beklenen biçimde değil: ürün fiyatı yok.")

    def top(side: str) -> Decimal:
        levels = book.get(side)
        if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
            raise CoinbaseError("BAD_RESPONSE", f"Coinbase fiyat cevabı beklenen biçimde değil: {side} boş.")
        price = to_decimal(levels[0].get("price"), "fiyat")
        if price <= 0:
            raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı geçersiz (sıfır/negatif).")
        return price

    bid, ask = top("bids"), top("asks")
    if bid > ask:
        raise CoinbaseError("BAD_RESPONSE", "Coinbase fiyat cevabı tutarsız (alış > satış).")
    return bid, ask


async def get_product(client: CoinbaseTradingClient, product_id: str) -> dict[str, Any]:
    """Ürün kuralları. Eksik/bozuk alan = açık hata; tahmin yok."""
    payload = await client.get(f"/api/v3/brokerage/products/{product_id}")
    details = payload.get("future_product_details")
    if not isinstance(details, dict):
        raise CoinbaseError("BAD_RESPONSE", "Bu ürün bir vadeli (futures) ürün değil veya Coinbase cevabı eksik: future_product_details yok.")
    status = payload.get("status")
    return {
        "product_id": payload.get("product_id") or product_id,
        "contract_size": to_decimal(details.get("contract_size"), "sözleşme boyutu"),
        "base_increment": to_decimal(payload.get("base_increment"), "miktar adımı"),
        "price_increment": to_decimal(payload.get("quote_increment") or payload.get("price_increment"), "fiyat adımı"),
        "base_min_size": to_decimal(payload.get("base_min_size"), "en küçük miktar"),
        "status": status.lower() if isinstance(status, str) else None,
        "trading_disabled": payload.get("trading_disabled") is True,
    }

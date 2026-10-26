"""Canlı Binance Futures adaptörü (grid motoru için ExchangePort).

Bu dosya HİÇ ağ isteği kurmaz. Her istek, uygulamanın mevcut canlı istemcisinden (BinanceLiveClient) geçer:
- adres istemcide sabittir (bu dosyada hiçbir adres yoktur), yönlendirme izlenmez,
- anahtarlar kullanıcının mevcut bağlantısındandır; bu dosya anahtar okumaz, istemez, saklamaz,
- ARM / izin / kilit / hız sınırı kapıları istemcide olduğu gibi çalışır.

Motor eşzamanlıdır (senkron), istemci asenkron çalışır. Motor ayrı bir iş parçacığında koşar ve her çağrı
uygulamanın olay döngüsüne gönderilip sonucu beklenir. Olay döngüsünün kendi iş parçacığından çağrılırsa hata verir.

Koşullu (stop) emirler Binance'in "algo" ucundan gider; normal emirler /fapi/v1/order'dan.
Hata sınıflandırması motorun beklediği gibidir: belirsizse Timeout/Unreachable, kesin ret ise Rejected(kod).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
from decimal import Decimal
from typing import Any, Awaitable, Protocol

from .ports import (CANCELED, FILLED, LIMIT, MARKET, NEW, PARTIAL, REJECTED, STOP_MARKET, UNKNOWN_ORDER, ExchangeRejected,
                    ExchangeTimeout, ExchangeUnreachable, OrderInfo)

ROLE_PATTERN = re.compile(r"-[LS]\d{2}([ETSC])\d+$")
ORDER_NOT_FOUND = (-2011, -2013)
_STATUS = {"NEW": NEW, "PARTIALLY_FILLED": PARTIAL, "FILLED": FILLED, "CANCELED": CANCELED, "EXPIRED": CANCELED,
           "EXPIRED_IN_MATCH": CANCELED, "REJECTED": REJECTED}
_ALGO_LIVE = {"NEW", "WORKING", "PENDING_NEW"}
_ALGO_FIRED = {"TRIGGERED", "FINISHED", "EXECUTED", "FILLED"}
_ALGO_DEAD = {"CANCELED", "CANCELLED", "EXPIRED"}


class LiveClientLike(Protocol):
    async def signed(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any: ...


def _num(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def order_role(client_id: str) -> str:
    match = ROLE_PATTERN.search(client_id or "")
    return match.group(1) if match else ""


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if out == out else 0.0


def translate_error(exc: Exception, *, read: bool) -> Exception:
    """LiveExchangeError → motorun anladığı hata. Belirsiz olan asla 'kesin ret' sayılmaz."""
    code = getattr(exc, "exchange_code", None)
    text = str(exc)
    if getattr(exc, "unknown_execution", False) or getattr(exc, "timed_out", False):
        return ExchangeTimeout(text)
    if getattr(exc, "transient_read_failure", False) or exc.__class__.__name__ == "LiveRateLimitError":
        return ExchangeUnreachable(text)
    if isinstance(code, int):
        return ExchangeRejected(code, text)
    if read or int(getattr(exc, "http_status", 0) or 0) >= 500:
        return ExchangeUnreachable(text)        # kodsuz okuma hatası: 'yok' diye yorumlanmaz
    return ExchangeRejected(0, text)            # kapı/izin reddi: emir hiç gitmedi


class BinanceLiveGridAdapter:
    def __init__(self, client: LiveClientLike, loop: asyncio.AbstractEventLoop, *, timeout: float = 30.0) -> None:
        self._client, self._loop, self._timeout = client, loop, timeout

    def __repr__(self) -> str:
        return "BinanceLiveGridAdapter(canlı, anahtar gizli)"

    # ---------- köprü ----------
    def _call(self, make: Awaitable[Any], *, read: bool) -> Any:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            make.close() if hasattr(make, "close") else None
            raise RuntimeError("Canlı grid adaptörü olay döngüsü iş parçacığından çağrılamaz.")
        future = asyncio.run_coroutine_threadsafe(make, self._loop)       # type: ignore[arg-type]
        try:
            return future.result(self._timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ExchangeTimeout("yanıt alınamadı") from exc
        except (ExchangeRejected, ExchangeTimeout, ExchangeUnreachable):
            raise
        except Exception as exc:
            if exc.__class__.__name__.endswith("ExchangeError") or hasattr(exc, "exchange_code") or hasattr(exc, "unknown_execution"):
                raise translate_error(exc, read=read) from exc
            raise ExchangeUnreachable(f"beklenmeyen hata: {exc.__class__.__name__}") from exc

    def _signed(self, method: str, path: str, params: dict[str, Any] | None = None, *, read: bool) -> Any:
        return self._call(self._client.signed(method, path, params), read=read)

    # ---------- dönüştürme ----------
    @staticmethod
    def _info(order: dict[str, Any]) -> OrderInfo:
        status = _STATUS.get(str(order.get("status") or "").upper())
        if status is None:
            raise ExchangeUnreachable(f"bilinmeyen emir durumu: {order.get('status')}")
        return OrderInfo(
            client_id=str(order.get("clientOrderId") or order.get("origClientOrderId") or ""), symbol=str(order.get("symbol") or ""),
            side=str(order.get("side") or ""), type=str(order.get("type") or order.get("origType") or ""), price=_float(order.get("price")),
            stop_price=_float(order.get("stopPrice")), qty=_float(order.get("origQty")), filled=_float(order.get("executedQty")),
            status=status, reduce_only=bool(order.get("reduceOnly")))

    @staticmethod
    def _algo_open_info(row: dict[str, Any]) -> OrderInfo:
        return OrderInfo(
            client_id=str(row.get("clientAlgoId") or ""), symbol=str(row.get("symbol") or ""), side=str(row.get("side") or ""),
            type=STOP_MARKET, price=0.0, stop_price=_float(row.get("triggerPrice", row.get("stopPrice"))),
            qty=_float(row.get("quantity", row.get("origQty"))), filled=0.0, status=NEW,
            reduce_only=str(row.get("reduceOnly")).lower() == "true")

    # ---------- stop (algo) ----------
    def _open_algo_rows(self, symbol: str) -> list[dict[str, Any]]:
        payload = self._signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, read=True)
        if not isinstance(payload, list):
            raise ExchangeUnreachable("açık koşullu emir listesi doğrulanamadı")
        return _rows(payload)

    def _algo_lookup(self, symbol: str, client_id: str) -> OrderInfo | None:
        for row in self._open_algo_rows(symbol):
            if row.get("clientAlgoId") == client_id:
                return self._algo_open_info(row)
        payload = self._signed("GET", "/fapi/v1/allAlgoOrders", {"symbol": symbol, "limit": 200}, read=True)
        if not isinstance(payload, list):
            raise ExchangeUnreachable("koşullu emir geçmişi doğrulanamadı")
        for row in _rows(payload):
            if row.get("clientAlgoId") != client_id:
                continue
            state = str(row.get("algoStatus", row.get("status")) or "").upper()
            base = self._algo_open_info(row)
            if state in _ALGO_LIVE:
                return base
            if state in _ALGO_DEAD:
                return OrderInfo(**{**vars(base), "status": CANCELED})
            if state in _ALGO_FIRED:
                actual = str(row.get("actualOrderId") or "")
                if actual.isdigit() and int(actual) > 0:
                    try:
                        done = self._signed("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": int(actual)}, read=True)
                    except ExchangeRejected as exc:
                        if exc.code not in ORDER_NOT_FOUND:
                            raise
                        done = None
                    if isinstance(done, dict) and done.get("orderId"):
                        executed = _float(done.get("executedQty"))
                        status = FILLED if executed >= base.qty - 1e-9 and executed > 0 else PARTIAL
                        return OrderInfo(**{**vars(base), "filled": min(executed, base.qty), "status": status})
                # tetiklendi ama gerçekleşen emir okunamadı: 'dolmadı' say; pozisyon farkı grid'i durdurur (güvenli taraf)
                return OrderInfo(**{**vars(base), "filled": 0.0, "status": PARTIAL})
            if state == "REJECTED":
                return OrderInfo(**{**vars(base), "status": REJECTED})
            raise ExchangeUnreachable(f"bilinmeyen koşullu emir durumu: {state}")
        return None

    # ---------- ExchangePort ----------
    def place_order(self, *, client_id: str, symbol: str, side: str, type: str, qty: float,
                    price: float = 0.0, stop_price: float = 0.0, reduce_only: bool = False) -> OrderInfo:
        if type == STOP_MARKET:
            params = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": "STOP_MARKET", "quantity": _num(qty),
                      "triggerPrice": _num(stop_price), "workingType": "MARK_PRICE", "priceProtect": "TRUE",
                      "reduceOnly": "true", "clientAlgoId": client_id}
            body = self._signed("POST", "/fapi/v1/algoOrder", params, read=False)
            if not isinstance(body, dict) or not body.get("algoId"):
                raise ExchangeTimeout("koşullu emir yanıtı eksik; durum sorulacak")
            state = str(body.get("algoStatus", body.get("status")) or "NEW").upper()
            if state in _ALGO_DEAD or state == "REJECTED":
                raise ExchangeRejected(0, f"koşullu emir kabul edilmedi ({state})")
            return OrderInfo(client_id=client_id, symbol=symbol, side=side, type=STOP_MARKET, price=0.0, stop_price=float(stop_price),
                             qty=float(qty), filled=0.0, status=NEW, reduce_only=True)
        if type not in (LIMIT, MARKET):
            raise ExchangeRejected(0, f"desteklenmeyen emir türü: {type}")
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": type, "quantity": _num(qty), "newClientOrderId": client_id,
                                  "newOrderRespType": "RESULT"}
        if type == LIMIT:
            params.update({"price": _num(price), "timeInForce": "GTC"})
        if reduce_only:
            params["reduceOnly"] = "true"
        body = self._signed("POST", "/fapi/v1/order", params, read=False)
        if not isinstance(body, dict) or not body.get("orderId"):
            raise ExchangeTimeout("emir yanıtı eksik; durum sorulacak")
        return self._info(body)

    def get_order(self, symbol: str, client_id: str) -> OrderInfo | None:
        if order_role(client_id) == "S":
            return self._algo_lookup(symbol, client_id)
        try:
            body = self._signed("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}, read=True)
        except ExchangeRejected as exc:
            if exc.code in ORDER_NOT_FOUND:
                return None
            raise
        rows = [row for row in ([body] if isinstance(body, dict) else _rows(body)) if row.get("orderId")]
        if not rows:
            return None
        if len(rows) > 1:
            raise ExchangeUnreachable("aynı kimlikle birden fazla emir döndü; sonuç belirsiz")
        return self._info(rows[0])

    def open_orders(self, symbol: str) -> list[OrderInfo]:
        payload = self._signed("GET", "/fapi/v1/openOrders", {"symbol": symbol}, read=True)
        if not isinstance(payload, list):
            raise ExchangeUnreachable("açık emir listesi doğrulanamadı")
        return [self._info(row) for row in _rows(payload)] + [self._algo_open_info(row) for row in self._open_algo_rows(symbol)]

    def cancel_order(self, symbol: str, client_id: str) -> OrderInfo:
        try:
            if order_role(client_id) == "S":
                self._signed("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "clientAlgoId": client_id}, read=False)
                info = self._algo_lookup(symbol, client_id)
                if info is None:
                    raise ExchangeRejected(UNKNOWN_ORDER, "koşullu emir bulunamadı")
                return info
            body = self._signed("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}, read=False)
            if not isinstance(body, dict) or not body.get("orderId"):
                raise ExchangeTimeout("iptal yanıtı eksik; durum sorulacak")
            return self._info(body)
        except ExchangeRejected as exc:
            if exc.code in ORDER_NOT_FOUND:
                raise ExchangeRejected(UNKNOWN_ORDER, str(exc)) from exc
            raise

    def position(self, symbol: str) -> float:
        payload = self._signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol}, read=True)
        rows = _rows(payload)
        if not isinstance(payload, list):
            raise ExchangeUnreachable("pozisyon yanıtı doğrulanamadı")
        total = Decimal("0")
        for row in rows:
            if row.get("symbol") != symbol:
                continue
            if str(row.get("positionSide", "BOTH")).upper() != "BOTH":
                raise ExchangeUnreachable("hesap tek yönlü (one-way) modda değil")
            total += Decimal(str(row.get("positionAmt", "0")))
        return float(round(total, 8))

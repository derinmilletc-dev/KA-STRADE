"""Binance Futures TESTNET adaptörü. SADECE Testnet; canlı adrese bağlanması koddan mümkün değildir.

- Adres sabit sabittir (TESTNET_BASE_URL). Kurucuya adres verilmez, ortam değişkeninden okunmaz.
- Her istekten önce adres yeniden doğrulanır; yönlendirme (redirect) izlenmez.
- Anahtarlar yalnızca BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET adlı değişkenlerden okunabilir.
- Bu dosya henüz GERÇEK Testnet'e karşı çalıştırılmadı (bu ortamda dış ağ yok); davranış varsayımları
  Testnet'te ayrıca doğrulanacak (bkz. README).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any, Callable, Mapping

from .ports import (CANCELED, FILLED, LIMIT, NEW, PARTIAL, REJECTED, STOP_MARKET, UNKNOWN_ORDER, ExchangeRejected,
                    ExchangeTimeout, ExchangeUnreachable, OrderInfo)

TESTNET_HOST = "testnet.binancefuture.com"
TESTNET_BASE_URL = "https://testnet.binancefuture.com"
ENV_KEY, ENV_SECRET = "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"


class TestnetOnlyError(RuntimeError):
    """Adres Testnet değil ya da güvenli değil."""


class TransportTimeout(Exception):
    """İstek gönderildi ama yanıt gelmedi."""


class TransportConnectError(Exception):
    """Bağlantı kurulamadı."""


Transport = Callable[[str, str, Mapping[str, str], Mapping[str, str]], "tuple[int, Any]"]


def assert_testnet_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != TESTNET_HOST or parts.username or parts.password
            or parts.port not in (None, 443)):
        raise TestnetOnlyError("Adres Testnet değil; istek yapılmadı.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        raise TestnetOnlyError("Yönlendirme izlenmez.")


def default_transport(method: str, url: str, params: Mapping[str, str], headers: Mapping[str, str]) -> "tuple[int, Any]":
    assert_testnet_url(url)
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}" if query else url, method=method, headers=dict(headers))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body or b"null")
        except ValueError:
            return exc.code, {"code": 0, "msg": "gövde okunamadı"}
    except (socket.timeout, TimeoutError) as exc:
        raise TransportTimeout(str(exc)) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TransportTimeout(str(exc)) from exc
        raise TransportConnectError(str(exc)) from exc


def _num(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


_STATUS = {"NEW": NEW, "PARTIALLY_FILLED": PARTIAL, "FILLED": FILLED, "CANCELED": CANCELED, "EXPIRED": CANCELED, "REJECTED": REJECTED}


class BinanceTestnetAdapter:
    def __init__(self, api_key: str, api_secret: str, *, transport: Transport | None = None,
                 clock: Callable[[], float] = time.time, recv_window: int = 5000) -> None:
        if not api_key or not api_secret:
            raise ValueError("Testnet anahtarı eksik")
        assert_testnet_url(TESTNET_BASE_URL)           # başlangıçta sabit adres yine de doğrulanır
        self._key, self._secret = api_key, api_secret
        self._transport = transport or default_transport
        self._clock, self._recv = clock, recv_window

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None, **kw: Any) -> "BinanceTestnetAdapter":
        env = os.environ if environ is None else environ
        key, secret = env.get(ENV_KEY, ""), env.get(ENV_SECRET, "")
        if not key or not secret:
            raise ValueError(f"{ENV_KEY} ve {ENV_SECRET} tanımlı olmalı (yalnızca Testnet anahtarı)")
        return cls(key, secret, **kw)

    def __repr__(self) -> str:
        return "BinanceTestnetAdapter(testnet, anahtar gizli)"

    # ---------- düşük seviye ----------
    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, *, signed: bool = True) -> Any:
        url = TESTNET_BASE_URL + path
        assert_testnet_url(url)
        data = {k: (_num(v) if isinstance(v, float) else str(v)) for k, v in (params or {}).items()}
        if signed:
            data["recvWindow"] = str(self._recv)
            data["timestamp"] = str(int(self._clock() * 1000))
            query = urllib.parse.urlencode(data)
            data["signature"] = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        try:
            status, body = self._transport(method, url, data, {"X-MBX-APIKEY": self._key})
        except TransportTimeout as exc:
            raise ExchangeTimeout("yanıt alınamadı") from exc
        except TransportConnectError as exc:
            raise ExchangeUnreachable("bağlantı kurulamadı") from exc
        if status in (301, 302, 303, 307, 308):
            raise TestnetOnlyError("Yönlendirme yanıtı alındı; izlenmedi.")
        if status >= 500 or (isinstance(body, dict) and body.get("code") == -1007):
            raise ExchangeTimeout(f"sunucu durumu belirsiz (HTTP {status})")   # emir işlenmiş olabilir
        if status >= 400 or (isinstance(body, dict) and isinstance(body.get("code"), int) and body["code"] < 0):
            code = int(body.get("code", 0)) if isinstance(body, dict) else 0
            raise ExchangeRejected(code, str(body.get("msg", "")) if isinstance(body, dict) else "")
        return body

    @staticmethod
    def _info(order: Mapping[str, Any]) -> OrderInfo:
        return OrderInfo(
            client_id=str(order.get("clientOrderId") or order.get("origClientOrderId")), symbol=str(order["symbol"]),
            side=str(order["side"]), type=str(order["type"]), price=float(order.get("price") or 0),
            stop_price=float(order.get("stopPrice") or 0), qty=float(order["origQty"]), filled=float(order.get("executedQty") or 0),
            status=_STATUS.get(str(order["status"]), NEW), reduce_only=bool(order.get("reduceOnly")))

    # ---------- ExchangePort ----------
    def place_order(self, *, client_id, symbol, side, type, qty, price=0.0, stop_price=0.0, reduce_only=False):
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": type, "quantity": float(qty), "newClientOrderId": client_id,
                                  "newOrderRespType": "RESULT"}
        if type == LIMIT:
            params.update(price=float(price), timeInForce="GTC")
        elif type == STOP_MARKET:
            params.update(stopPrice=float(stop_price), workingType="MARK_PRICE")
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._info(self._request("POST", "/fapi/v1/order", params))

    def get_order(self, symbol, client_id):
        try:
            return self._info(self._request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}))
        except ExchangeRejected as exc:
            if exc.code == -2013:                        # Order does not exist
                return None
            raise

    def open_orders(self, symbol):
        return [self._info(o) for o in self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol})]

    def cancel_order(self, symbol, client_id):
        try:
            return self._info(self._request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}))
        except ExchangeRejected as exc:
            if exc.code in (-2011, -2013):
                raise ExchangeRejected(UNKNOWN_ORDER, exc.args[0]) from exc
            raise

    def position(self, symbol):
        rows = self._request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
        return round(sum(float(r["positionAmt"]) for r in rows if r.get("symbol") == symbol and r.get("positionSide", "BOTH") == "BOTH"), 8)

    def mark_price(self, symbol: str) -> float:
        body = self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol}, signed=False)
        price = float(body["price"]) if isinstance(body, dict) else float("nan")
        if not price > 0:
            raise ValueError(f"{symbol} fiyatı okunamadı")
        return price

    # ---------- başlangıç doğrulamaları ----------
    def assert_one_way_mode(self) -> None:
        body = self._request("GET", "/fapi/v1/positionSide/dual")
        if str(body.get("dualSidePosition")).lower() != "false":
            raise RuntimeError("Hesap 'hedge mode' (çift yönlü) durumda; motor tek yönlü (one-way) mod ister.")

    def symbol_filters(self, symbol: str) -> dict[str, float]:
        info = self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
        for item in info.get("symbols", []):
            if item.get("symbol") == symbol:
                f = {x["filterType"]: x for x in item.get("filters", [])}
                return {"tickSize": float(f["PRICE_FILTER"]["tickSize"]), "stepSize": float(f["LOT_SIZE"]["stepSize"]),
                        "minNotional": float(f.get("MIN_NOTIONAL", {}).get("notional", 0))}
        raise ValueError(f"{symbol} Testnet'te bulunamadı")

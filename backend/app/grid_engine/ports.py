"""Motorun borsa ile konuştuğu sözleşme. Motor yalnızca bu arayüzü bilir.

Gerçek (Testnet/canlı) adaptör sonraki aşamada aynı arayüzü uygulayacak.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

LONG, SHORT = "LONG", "SHORT"
BUY, SELL = "BUY", "SELL"
NEW, PARTIAL, FILLED, CANCELED, REJECTED = "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"
LIMIT, MARKET, STOP_MARKET = "LIMIT", "MARKET", "STOP_MARKET"
EPS = 1e-9


class ExchangeError(Exception):
    """Borsa hatası (temel)."""


class ExchangeUnreachable(ExchangeError):
    """Borsaya ulaşılamadı. Emir GİTMEMİŞ olabilir de, gitmiş olabilir de: durum bilinmez gibi davran."""


class ExchangeTimeout(ExchangeError):
    """İstek gitti, yanıt gelmedi. Emir borsada olabilir."""


class ExchangeRejected(ExchangeError):
    def __init__(self, code: int, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


DUPLICATE_CLIENT_ID = -4116
UNKNOWN_ORDER = -2011
REDUCE_ONLY_REJECTED = -2022
WOULD_TRIGGER_IMMEDIATELY = -2021  # stop fiyatı zaten geçilmiş


@dataclass
class OrderInfo:
    client_id: str
    symbol: str
    side: str
    type: str
    price: float
    stop_price: float
    qty: float
    filled: float
    status: str
    reduce_only: bool


class ExchangePort(Protocol):
    def place_order(self, *, client_id: str, symbol: str, side: str, type: str, qty: float,
                    price: float = 0.0, stop_price: float = 0.0, reduce_only: bool = False) -> OrderInfo: ...
    def get_order(self, symbol: str, client_id: str) -> OrderInfo | None: ...
    def open_orders(self, symbol: str) -> list[OrderInfo]: ...
    def cancel_order(self, symbol: str, client_id: str) -> OrderInfo: ...
    def position(self, symbol: str) -> float: ...  # işaretli: LONG +, SHORT -

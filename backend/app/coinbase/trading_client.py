"""Coinbase EMİR istemcisi: salt okunur istemciyi dar bir izin listesiyle genişletir.

Yazabildiği (POST) yollar yalnızca bunlardır: emir ver, pozisyon kapat, emir iptal et.
Para aktarımı (sweep), emir düzenleme ve başka hiçbir yazma yolu YOK.
Her POST'tan önce COINBASE_ORDERS_ENABLED kontrol edilir (kapalıyken ağa hiçbir şey çıkmaz).
"""

from __future__ import annotations

import re
from typing import Any

from . import config
from .client import READ_ONLY_PATTERNS, CoinbaseClient
from .errors import CoinbaseError

ORDERS_PATH = "/api/v3/brokerage/orders"
CLOSE_POSITION_PATH = "/api/v3/brokerage/orders/close_position"
BATCH_CANCEL_PATH = "/api/v3/brokerage/orders/batch_cancel"
ORDER_LIST_PATH = "/api/v3/brokerage/orders/historical/batch"
ORDER_GET_PREFIX = "/api/v3/brokerage/orders/historical/"
BEST_BID_ASK_PATH = "/api/v3/brokerage/best_bid_ask"

WRITE_PATTERNS = (
    re.compile(re.escape(ORDERS_PATH)),
    re.compile(re.escape(CLOSE_POSITION_PATH)),
    re.compile(re.escape(BATCH_CANCEL_PATH)),
)
TRADING_READ_PATTERNS = READ_ONLY_PATTERNS + (
    re.compile(re.escape(ORDER_LIST_PATH)),
    re.compile(r"/api/v3/brokerage/orders/historical/(?!batch$)[A-Za-z0-9][A-Za-z0-9-]{7,63}"),
    re.compile(re.escape(BEST_BID_ASK_PATH)),
)


class CoinbaseTradingClient(CoinbaseClient):
    def _read_patterns(self) -> tuple[re.Pattern[str], ...]:
        return TRADING_READ_PATTERNS

    def _write_patterns(self) -> tuple[re.Pattern[str], ...]:
        return WRITE_PATTERNS

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not config.orders_enabled():
            raise CoinbaseError(
                "ORDERS_DISABLED",
                "Coinbase emir gönderme kapalı (COINBASE_ORDERS_ENABLED açık değil). Hiçbir emir gönderilmedi.",
                http_status=403,
            )
        if not isinstance(body, dict) or not body:
            raise CoinbaseError("BAD_REQUEST", "Boş emir gövdesi gönderilmez.", http_status=422)
        return await self._execute("POST", path, None, body)

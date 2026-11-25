"""Olay listesi: okuma sorgusundaki "emir yok" cevabı gerçek ret sayılmaz; yazma (POST) davranışı değişmez."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as execution  # noqa: E402

code = execution.BinanceLiveClient._exchange_error_code


def test_get_order_not_found_is_lookup_miss():
    assert code(400, -2013, "GET") == "ORDER_LOOKUP_MISS"
    assert code(400, -2011, "GET") == "ORDER_LOOKUP_MISS"


def test_post_rejection_unchanged():
    assert code(400, -2013, "POST") == "ORDER_REJECTED"
    assert code(400, -2010) == "ORDER_REJECTED"
    assert code(400, -2019, "POST") == "INSUFFICIENT_BALANCE"


def test_rate_limit_and_down_unchanged_for_get():
    assert code(429, None, "GET") == "RATE_LIMITED"
    assert code(503, None, "GET") == "EXCHANGE_DOWN"


def test_unknown_get_rejection_is_not_order_rejected():
    assert code(400, -1000, "GET") == "EXCHANGE_REQUEST_REJECTED"

"""Emir API sarmalayıcıları: cevap doğrulama, red nedenleri, tekrar deneme, sayfalama. Ağ yok."""
import asyncio
import json
import os
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import NOW, FakeExchange, _resp  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import order_api  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402
from app.coinbase.money import fmt, to_decimal  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class Scripted(FakeExchange):
    """Verilen sırayla cevap döner (sadece cevap biçimini denemek için)."""

    def __init__(self, *responses):
        super().__init__()
        self.responses = list(responses)

    async def transport(self, method, url, headers, params, timeout, content=None):
        self.calls.append((method, url, json.loads(content) if content else None))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ApiTests(unittest.TestCase):
    def setUp(self):
        cb_client.reset_limiters()
        patcher = patch.dict(os.environ, {"COINBASE_ENABLED": "true", "COINBASE_ORDERS_ENABLED": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, *responses):
        self.ex = Scripted(*responses)
        return self.ex.client()

    def test_success_false_becomes_turkish_rejection(self):
        c = self.client(_resp({"success": False, "error_response": {"new_order_failure_reason": "INSUFFICIENT_FUND", "message": "x"}}))
        with self.assertRaises(order_api.OrderRejected) as ctx:
            run(order_api.create_order(c, {"client_order_id": "kais-e-1"}, "Giriş"))
        self.assertIn("Bakiye yetersiz", ctx.exception.message)
        self.assertEqual(ctx.exception.http_status, 422)

    def test_unknown_reason_code_is_shown_not_hidden(self):
        c = self.client(_resp({"success": False, "error_response": {"new_order_failure_reason": "NEW_THING"}}))
        with self.assertRaises(order_api.OrderRejected) as ctx:
            run(order_api.create_order(c, {"client_order_id": "kais-e-1"}, "Giriş"))
        self.assertIn("NEW_THING", ctx.exception.message)

    def test_success_without_order_id_is_bad_response(self):
        c = self.client(_resp({"success": True, "success_response": {}}))
        with self.assertRaises(CoinbaseError) as ctx:
            run(order_api.create_order(c, {"client_order_id": "kais-e-1"}, "Giriş"))
        self.assertEqual(ctx.exception.code, "BAD_RESPONSE")

    def test_network_error_retries_once_with_same_client_order_id(self):
        c = self.client(CoinbaseError("NETWORK", "kopuk"), _resp({"success": True, "success_response": {"order_id": "abc"}}))
        body = {"client_order_id": "kais-e-same", "product_id": "X"}
        self.assertEqual(run(order_api.create_order(c, body, "Giriş")), "abc")
        sent = [call[2] for call in self.ex.calls]
        self.assertEqual(sent[0], sent[1])

    def test_two_network_errors_is_unknown_outcome_critical(self):
        c = self.client(CoinbaseError("NETWORK", "kopuk"), CoinbaseError("NETWORK", "kopuk"))
        with self.assertRaises(CoinbaseError) as ctx:
            run(order_api.create_order(c, {"client_order_id": "kais-e-1"}, "Giriş"))
        self.assertEqual(ctx.exception.code, "ORDER_OUTCOME_UNKNOWN")
        self.assertTrue(ctx.exception.body()["critical"])

    def test_cancel_missing_result_counts_as_failure(self):
        c = self.client(_resp({"results": [{"order_id": "a", "success": True}]}))
        out = run(order_api.cancel_orders(c, ["a", "b"]))
        self.assertEqual(out["a"][0], True)
        self.assertEqual(out["b"], (False, "CEVAPTA_YOK"))

    def test_cancel_bad_shape(self):
        c = self.client(_resp({"nope": 1}))
        with self.assertRaises(CoinbaseError):
            run(order_api.cancel_orders(c, ["a"]))

    def test_normalize_order_requires_id_and_status(self):
        with self.assertRaises(CoinbaseError):
            order_api.normalize_order({"order_id": "a"})
        with self.assertRaises(CoinbaseError):
            order_api.normalize_order(None)

    def test_list_pagination_follows_cursor_and_fails_when_endless(self):
        row = {"order_id": "a", "status": "OPEN"}
        c = self.client(_resp({"orders": [row], "has_next": True, "cursor": "c1"}), _resp({"orders": [row], "has_next": False}))
        self.assertEqual(len(run(order_api.list_active_orders(c))), 2)
        endless = [_resp({"orders": [row], "has_next": True, "cursor": "c"}) for _ in range(10)]
        c = self.client(*endless)
        with self.assertRaises(CoinbaseError):
            run(order_api.list_active_orders(c))

    def test_list_has_next_without_cursor_fails(self):
        c = self.client(_resp({"orders": [], "has_next": True}))
        with self.assertRaises(CoinbaseError):
            run(order_api.list_active_orders(c))

    def test_best_bid_ask_validation(self):
        good = {"pricebooks": [{"product_id": "X", "bids": [{"price": "99"}], "asks": [{"price": "100"}]}]}
        self.assertEqual(run(order_api.best_bid_ask(self.client(_resp(good)), "X")), (D("99"), D("100")))
        for bad in (
            {"pricebooks": []},
            {"pricebooks": [{"product_id": "X", "bids": [], "asks": [{"price": "1"}]}]},
            {"pricebooks": [{"product_id": "X", "bids": [{"price": "5"}], "asks": [{"price": "4"}]}]},
            {"pricebooks": [{"product_id": "X", "bids": [{"price": "0"}], "asks": [{"price": "4"}]}]},
            {"pricebooks": [{"product_id": "X", "bids": [{"price": "abc"}], "asks": [{"price": "4"}]}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(CoinbaseError):
                run(order_api.best_bid_ask(self.client(_resp(bad)), "X"))

    def test_product_must_be_future(self):
        with self.assertRaises(CoinbaseError):
            run(order_api.get_product(self.client(_resp({"product_id": "BTC-USD"})), "BTC-USD"))

    def test_money_helpers(self):
        self.assertEqual(fmt(D("1E+2")), "100")
        self.assertEqual(fmt(D("0.50")), "0.5")
        for bad in (None, True, "", "abc", "NaN", "Infinity"):
            with self.subTest(bad=bad), self.assertRaises(CoinbaseError):
                to_decimal(bad)


if __name__ == "__main__":
    unittest.main()

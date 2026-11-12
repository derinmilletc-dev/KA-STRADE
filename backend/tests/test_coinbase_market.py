"""Coinbase piyasa verisi: ürün listesi ve fiyat (salt okunur). Ağ yok."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import FakeExchange, _resp  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import market  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class MarketTests(unittest.TestCase):
    def setUp(self):
        cb_client.reset_limiters()
        market.reset_cache()
        patcher = patch.dict(os.environ, {"COINBASE_ENABLED": "true"})  # emir bayrağı KAPALI: okuma yine çalışmalı
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ex = FakeExchange()

    def read_client(self):
        c = self.ex.client()
        return cb_client.CoinbaseClient(c._key_name, c._private_key, transport=self.ex.transport, clock=c._clock,
                                        monotonic=c._mono, sleep=c._sleep, min_interval=0.0)

    def test_products_normalized_sorted_by_volume(self):
        rows = run(market.fetch_products(self.read_client()))
        self.assertEqual([r["product_id"] for r in rows], ["BIP-20DEC30-CDE", "ETP-20DEC30-CDE", "XXX-20DEC30-CDE"])
        eth = rows[1]
        self.assertEqual((eth["root"], eth["display"], eth["price"], eth["change_pct"], eth["tradable"]), ("ETH", "ETH PERP", "3000.5", "-1.25", True))
        self.assertEqual(eth["contract_size"], "0.01")

    def test_bad_number_is_none_not_zero_and_disabled_is_not_tradable(self):
        bad = run(market.fetch_products(self.read_client()))[2]
        self.assertIsNone(bad["price"])
        self.assertIsNone(bad["root"])
        self.assertFalse(bad["tradable"])

    def test_request_uses_future_filter_and_get_only(self):
        run(market.fetch_products(self.read_client()))
        self.assertEqual([(m, p) for m, p, _ in self.ex.calls], [("GET", "/api/v3/brokerage/products")])

    def test_cache_avoids_second_call_until_expiry(self):
        client = self.read_client()
        times = iter([100.0, 105.0, 130.0])
        run(market.fetch_products(client, monotonic=lambda: next(times)))
        run(market.fetch_products(client, monotonic=lambda: next(times)))
        self.assertEqual(self.ex.product_list_calls, 1)
        run(market.fetch_products(client, monotonic=lambda: next(times)))
        self.assertEqual(self.ex.product_list_calls, 2)

    def test_bad_shapes_raise(self):
        self.ex.product_rows = "nope"
        with self.assertRaises(CoinbaseError):
            run(market.fetch_products(self.read_client()))
        market.reset_cache()
        self.ex.product_rows = [{"display_name": "x"}]
        with self.assertRaises(CoinbaseError):
            run(market.fetch_products(self.read_client()))
        market.reset_cache()
        self.ex.product_rows = ["x"]
        with self.assertRaises(CoinbaseError):
            run(market.fetch_products(self.read_client()))

    def test_quote(self):
        quote = run(market.fetch_quote(self.read_client(), "ETP-20DEC30-CDE"))
        self.assertEqual((quote["bid"], quote["ask"]), ("3000", "3000.5"))
        self.assertEqual(quote["mid"], "3000.25")
        self.assertEqual(quote["spread_pct"], "0.017")

    def test_quote_rejects_crossed_or_empty(self):
        self.ex.bid, self.ex.ask = "3001", "3000"
        with self.assertRaises(CoinbaseError):
            run(market.fetch_quote(self.read_client(), "ETP-20DEC30-CDE"))
        self.ex.bid, self.ex.ask = "0", "3000"
        with self.assertRaises(CoinbaseError):
            run(market.fetch_quote(self.read_client(), "ETP-20DEC30-CDE"))

    def test_read_client_still_cannot_post(self):
        client = self.read_client()
        with self.assertRaises(Exception):
            run(client._execute("POST", "/api/v3/brokerage/orders", None, {"a": 1}))
        self.assertEqual(self.ex.posts(), [])

    def test_allowlist_still_blocks_traversal_and_other_paths(self):
        client = self.read_client()
        for path in ("/api/v3/brokerage/products/..", "/api/v3/brokerage/productsX", "/api/v3/brokerage/orders", "/api/v3/brokerage/transfers"):
            with self.subTest(path=path), self.assertRaises(Exception):
                run(client.get(path))


if __name__ == "__main__":
    unittest.main()

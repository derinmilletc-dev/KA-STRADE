"""Coinbase piyasa rotaları (salt okunur): erişim, liste, fiyat, hatalar, bayrak. CI'da FastAPI ile çalışır."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import FakeExchange  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import include_coinbase_if_enabled, market, routes_market  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402

ME = {"x-test-email": "me@example.com"}


class MarketRouteCase(unittest.TestCase):
    def setUp(self):
        cb_client.reset_limiters()
        market.reset_cache()
        patcher = patch.dict(os.environ, {"COINBASE_ENABLED": "true", "COINBASE_ALLOWED_EMAILS": "me@example.com"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ex = FakeExchange()

        async def reader(request, user):
            c = self.ex.client()
            return cb_client.CoinbaseClient(c._key_name, c._private_key, transport=self.ex.transport, clock=c._clock,
                                            monotonic=c._mono, sleep=c._sleep, min_interval=0.0)

        p = patch.object(routes_market, "_reader", reader)
        p.start()
        self.addCleanup(p.stop)
        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request, call_next):
            email_value = request.headers.get("x-test-email")
            if email_value is not None:
                request.state.member = {"id": "u1", "role": request.headers.get("x-test-role", "OWNER"), "email": email_value}
            return await call_next(request)

        app.include_router(routes_market.router)
        self.http = TestClient(app)


class AccessAndData(MarketRouteCase):
    def test_access_rules(self):
        for path in ("/api/coinbase/products", "/api/coinbase/quote/ETP-20DEC30-CDE"):
            with self.subTest(path=path):
                self.assertEqual(self.http.get(path).status_code, 401)
                self.assertEqual(self.http.get(path, headers={"x-test-email": "other@example.com"}).status_code, 403)
                self.assertEqual(self.http.get(path, headers={**ME, "x-test-role": "CUSTOMER"}).status_code, 403)
        self.assertEqual(self.ex.calls, [])

    def test_exact_route_set_is_read_only(self):
        pairs = {(m, r.path) for r in routes_market.router.routes for m in r.methods}
        self.assertEqual(pairs, {("GET", "/api/coinbase/products"), ("GET", "/api/coinbase/quote/{product_id}")})

    def test_products_work_with_orders_flag_off(self):
        r = self.http.get("/api/coinbase/products", headers=ME)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["products"][0]["product_id"], "BIP-20DEC30-CDE")
        self.assertEqual(self.ex.posts(), [])

    def test_quote(self):
        r = self.http.get("/api/coinbase/quote/ETP-20DEC30-CDE", headers=ME)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["ask"], "3000.5")

    def test_bad_product_id_is_422(self):
        for bad in ("a", "x" * 60, "a%20b"):
            self.assertEqual(self.http.get(f"/api/coinbase/quote/{bad}", headers=ME).status_code, 422)
        self.assertEqual(self.ex.calls, [])

    def test_upstream_error_is_clear_4xx(self):
        async def broken(request, user):
            raise CoinbaseError("KEY_MISSING", "Coinbase anahtarı kayıtlı değil. Önce anahtarı kaydet.", http_status=409)

        with patch.object(routes_market, "_reader", broken):
            r = self.http.get("/api/coinbase/products", headers=ME)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "KEY_MISSING")


class Mounting(unittest.TestCase):
    def test_market_routes_follow_main_flag_not_orders_flag(self):
        with patch.dict(os.environ, {"COINBASE_ENABLED": "true", "COINBASE_ORDERS_ENABLED": "false", "COINBASE_ALLOWED_EMAILS": "me@example.com"}):
            app = FastAPI()
            include_coinbase_if_enabled(app)
        http = TestClient(app)
        self.assertEqual(http.get("/api/coinbase/products").status_code, 401)
        self.assertEqual(http.get("/api/coinbase/orders/protection").status_code, 404)
        with patch.dict(os.environ, {"COINBASE_ENABLED": "false"}):
            app = FastAPI()
            include_coinbase_if_enabled(app)
        self.assertEqual(TestClient(app).get("/api/coinbase/products").status_code, 404)


if __name__ == "__main__":
    unittest.main()

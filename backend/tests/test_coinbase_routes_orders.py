"""Coinbase emir rotaları (C): erişim, onay metni, çift istek, kilit, hata çevirisi, bayrak/yükleme davranışı.

Gerçek ağ yok: durumlu sahte borsa kullanılır. Bu dosya CI'da (FastAPI ile) çalışır.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from coinbase_fake_exchange import PRODUCT, FakeExchange, FakePool  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.coinbase import client as cb_client  # noqa: E402
from app.coinbase import include_coinbase_if_enabled, journal, routes_orders  # noqa: E402

ME = {"x-test-email": "me@example.com"}
OPEN_BODY = {
    "product_id": PRODUCT, "side": "BUY", "stop_price": "2940", "margin_usd": "60",
    "confirm": "COINBASE EMİR AÇ", "request_id": "req-00000001",
}
URL = "/api/coinbase/orders"


def build_app(pool):
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request, call_next):
        email_value = request.headers.get("x-test-email")
        if email_value is not None:
            request.state.member = {"id": "u1", "role": request.headers.get("x-test-role", "OWNER"), "email": email_value}
        return await call_next(request)

    app.state.db_pool = pool
    app.include_router(routes_orders.router)
    return app


class OrderRouteCase(unittest.TestCase):
    def setUp(self):
        cb_client.reset_limiters()
        journal.reset_schema_cache()
        routes_orders._SEEN.clear()
        patcher = patch.dict(os.environ, {
            "COINBASE_ENABLED": "true", "COINBASE_ORDERS_ENABLED": "true", "COINBASE_ALLOWED_EMAILS": "me@example.com",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ex = FakeExchange()
        self.pool = FakePool()

        async def fake_trading_client(request, user):
            return self.ex.client()

        p = patch.object(routes_orders, "_trading_client", fake_trading_client)
        p.start()
        self.addCleanup(p.stop)
        self.http = TestClient(build_app(self.pool))

    def post(self, path, body, headers=ME):
        return self.http.post(f"{URL}{path}", json=body, headers=headers)


class AccessTests(OrderRouteCase):
    ROUTES = (
        ("POST", "/preflight", {"product_id": PRODUCT, "side": "BUY", "stop_price": "2940", "margin_usd": "60"}),
        ("POST", "/open", OPEN_BODY),
        ("POST", "/close", {"product_id": PRODUCT, "confirm": "COINBASE POZİSYON KAPAT", "request_id": "req-00000002"}),
        ("POST", "/reduce", {"product_id": PRODUCT, "reduce_by": 1, "confirm": "COINBASE DEĞİŞTİR", "request_id": "req-00000003"}),
        ("POST", "/stop", {"product_id": PRODUCT, "stop_price": "2950", "confirm": "COINBASE DEĞİŞTİR", "request_id": "req-00000004"}),
        ("POST", "/cleanup-orphans", {"confirm": "COINBASE DEĞİŞTİR", "request_id": "req-00000005"}),
        ("GET", "/protection", None),
        ("GET", "/open", None),
    )

    def test_no_session_is_401_and_other_user_403(self):
        for method, path, body in self.ROUTES:
            with self.subTest(path=path):
                r = self.http.request(method, URL + path, json=body)
                self.assertEqual(r.status_code, 401)
                r = self.http.request(method, URL + path, json=body, headers={"x-test-email": "other@example.com"})
                self.assertEqual(r.status_code, 403)
                r = self.http.request(method, URL + path, json=body, headers={**ME, "x-test-role": "CUSTOMER"})
                self.assertEqual(r.status_code, 403)
        self.assertEqual(self.ex.calls, [])

    def test_exact_route_set(self):
        pairs = {(m, r.path) for r in routes_orders.router.routes for m in r.methods}
        self.assertEqual(pairs, {
            ("POST", f"{URL}/preflight"), ("POST", f"{URL}/open"), ("POST", f"{URL}/close"), ("POST", f"{URL}/reduce"),
            ("POST", f"{URL}/stop"), ("POST", f"{URL}/cleanup-orphans"), ("GET", f"{URL}/protection"), ("GET", f"{URL}/open"),
            ("POST", f"{URL}/cancel-entry"), ("POST", f"{URL}/reconcile"), ("GET", f"{URL}/pending"),
        })

    def test_wrong_confirmation_is_422_and_sends_nothing(self):
        for path, body in (("/open", OPEN_BODY), ("/close", self.ROUTES[2][2]), ("/reduce", self.ROUTES[3][2]),
                           ("/stop", self.ROUTES[4][2]), ("/cleanup-orphans", self.ROUTES[5][2])):
            with self.subTest(path=path):
                r = self.post(path, {**body, "confirm": "evet"})
                self.assertEqual(r.status_code, 422)
                self.assertIn("Şunu aynen yaz", r.json()["detail"])
        self.assertEqual(self.ex.calls, [])

    def test_bad_inputs_are_422(self):
        self.assertEqual(self.post("/open", {**OPEN_BODY, "side": "LONG"}).status_code, 422)
        self.assertEqual(self.post("/open", {**OPEN_BODY, "product_id": "../x"}).status_code, 422)
        self.assertEqual(self.post("/open", {**OPEN_BODY, "request_id": "a"}).status_code, 422)
        self.assertEqual(self.ex.calls, [])


class BehaviourTests(OrderRouteCase):
    def test_open_happy_path(self):
        r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "opened")
        self.assertEqual(len(self.ex.active_stops()), 1)
        self.assertIsNone(self.pool.lock)  # kilit bırakıldı

    def test_same_request_id_is_not_processed_twice(self):
        self.assertEqual(self.post("/open", OPEN_BODY).status_code, 200)
        r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "DUPLICATE_REQUEST")
        self.assertEqual(len([o for o in self.ex.orders.values() if o["order_type"] == "MARKET"]), 1)

    def test_preflight_endpoint_and_failure_message(self):
        r = self.post("/preflight", {k: OPEN_BODY[k] for k in ("product_id", "side", "stop_price", "margin_usd")})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        r = self.post("/open", {**OPEN_BODY, "stop_price": "3100", "request_id": "req-00000009"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "PREFLIGHT_FAILED")
        self.assertTrue(r.json()["reasons"])

    def test_stop_failure_returns_409_with_clear_message(self):
        self.ex.stop_mode = "reject"
        r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "STOP_FAILED_POSITION_CLOSED")
        self.assertEqual(self.ex.positions, {})

    def test_critical_is_marked(self):
        self.ex.stop_mode = "reject"
        self.ex.close_fails = True
        r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["critical"])
        self.assertIn("ACİL", r.json()["detail"])
        self.assertIsNone(self.pool.lock)

    def test_busy_flow_returns_409_and_sends_nothing(self):
        self.pool.lock = "baska-akis"
        r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "FLOW_BUSY")
        self.assertEqual(self.ex.posts(), [])
        self.assertEqual(self.pool.lock, "baska-akis")  # başkasının kilidine dokunulmadı

    def test_unexpected_error_is_loud_and_releases_lock(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("beklenmedik")

        with patch.object(routes_orders.flow, "open_position", boom):
            r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 424)
        self.assertEqual(r.json()["code"], "UNEXPECTED_ERROR")
        self.assertTrue(r.json()["critical"])
        self.assertIsNone(self.pool.lock)

    def test_close_and_protection_and_open_orders(self):
        self.post("/open", OPEN_BODY)
        self.assertTrue(self.http.get(f"{URL}/protection", headers=ME).json()["all_protected"])
        self.assertEqual(len(self.http.get(f"{URL}/open", headers=ME).json()["orders"]), 1)
        r = self.post("/close", {"product_id": PRODUCT, "confirm": "COINBASE POZİSYON KAPAT", "request_id": "req-00000010"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.ex.positions, {})

    def test_status_tells_ui_whether_orders_are_on(self):
        from app.coinbase import routes

        app = build_app(self.pool)
        app.include_router(routes.router)
        http = TestClient(app)
        self.assertTrue(http.get("/api/coinbase/status", headers=ME).json()["orders_enabled"])
        with patch.dict(os.environ, {"COINBASE_ORDERS_ENABLED": "false"}):
            self.assertFalse(http.get("/api/coinbase/status", headers=ME).json()["orders_enabled"])

    def test_orders_flag_off_blocks_even_if_route_is_mounted(self):
        with patch.dict(os.environ, {"COINBASE_ORDERS_ENABLED": "false"}):
            r = self.post("/open", OPEN_BODY)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "ORDERS_DISABLED")
        self.assertEqual(self.ex.posts(), [])


class MountingTests(unittest.TestCase):
    def app_with(self, **env):
        base = {"COINBASE_ENABLED": "true", "COINBASE_ORDERS_ENABLED": "false", "COINBASE_ALLOWED_EMAILS": "me@example.com"}
        with patch.dict(os.environ, {**base, **env}):
            app = FastAPI()
            include_coinbase_if_enabled(app)
        return app

    def test_orders_routes_absent_when_orders_flag_off(self):
        http = TestClient(self.app_with())
        self.assertEqual(http.get(f"{URL}/protection").status_code, 404)
        self.assertEqual(http.get("/api/coinbase/status").status_code, 401)  # B hâlâ var

    def test_orders_routes_present_when_both_flags_on(self):
        http = TestClient(self.app_with(COINBASE_ORDERS_ENABLED="true"))
        self.assertEqual(http.get(f"{URL}/protection").status_code, 401)

    def test_orders_flag_alone_without_main_flag_mounts_nothing(self):
        http = TestClient(self.app_with(COINBASE_ENABLED="false", COINBASE_ORDERS_ENABLED="true"))
        self.assertEqual(http.get(f"{URL}/protection").status_code, 404)
        self.assertEqual(http.get("/api/coinbase/status").status_code, 404)

    def test_orders_load_failure_is_loud_not_404(self):
        with patch.dict(sys.modules, {"app.coinbase.routes_orders": None}):
            http = TestClient(self.app_with(COINBASE_ORDERS_ENABLED="true"))
            r = http.get(f"{URL}/protection")
        self.assertEqual(r.status_code, 424)
        self.assertEqual(r.json()["code"], "COINBASE_ORDERS_LOAD_FAILED")
        self.assertEqual(http.get("/api/coinbase/status").status_code, 401)  # salt okunur kısım çalışmaya devam eder


if __name__ == "__main__":
    unittest.main()

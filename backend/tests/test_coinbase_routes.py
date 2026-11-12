"""Coinbase rotaları (B, salt okunur): erişim, kaydet/sil, okuma, hata durumları, bayrak kapalıyken yok olma.

Gerçek Coinbase'e ağ çağrısı yok: istemci sahte aktarımla kurulur. 2FA doğrulaması sahtelenir.
"""
import email.utils
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.coinbase import client as cb  # noqa: E402
from app.coinbase import include_coinbase_if_enabled, routes, vault  # noqa: E402

MASTER = "coinbase-vault-master-key-0123456789"
KEY_NAME = "organizations/org-123/apiKeys/key-456"
PEM = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
).decode()
ME = {"x-test-email": "me@example.com"}
SAVE = {"key_name": KEY_NAME, "private_key": PEM, "confirm": "COINBASE ANAHTARINI KAYDET", "totp_code": "123456"}

BALANCE = {"balance_summary": {
    "futures_buying_power": {"value": "90", "currency": "USD"}, "available_margin": {"value": "110", "currency": "USD"},
    "initial_margin": {"value": "10", "currency": "USD"},
    "intraday_margin_window_measure": {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_INTRADAY", "margin_level": "MARGIN_LEVEL_TYPE_BASE"},
    "overnight_margin_window_measure": {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT", "margin_level": "MARGIN_LEVEL_TYPE_WARNING"},
}}
WINDOW = {"margin_window": {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_INTRADAY", "end_time": "2026-11-12T21:00:00Z"}}


class FakePool:
    def __init__(self):
        self.rows = {}

    async def execute(self, sql, *args):
        if "CREATE TABLE" in sql:
            return "CREATE TABLE"
        if "INSERT INTO" in sql:
            user_id, blob, fingerprint, hint, permissions = args
            self.rows[user_id] = {
                "encrypted_payload": blob, "fingerprint": fingerprint, "key_hint": hint, "permissions": permissions,
                "last_test_at": "t", "created_at": "t", "updated_at": "t",
            }
            return "INSERT 0 1"
        if sql.strip().startswith("DELETE"):
            return "DELETE 1" if self.rows.pop(args[0], None) is not None else "DELETE 0"
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        return self.rows.get(args[0])


def now_header():
    return {"date": email.utils.formatdate(usegmt=True)}


class FakeCoinbase:
    """Yol -> cevap. Yoksa 404 (Coinbase'in bulamadığı uç gibi)."""

    def __init__(self, **by_path):
        self.by_path = by_path
        self.calls = []

    async def transport(self, method, url, headers, params, timeout):
        path = url.split("api.coinbase.com", 1)[1]
        self.calls.append((method, path))
        item = self.by_path.get(path)
        if item is None:
            return 404, now_header(), b'{"message":"not found"}'
        status, payload = item if isinstance(item, tuple) else (200, item)
        return status, now_header(), json.dumps(payload).encode()


GOOD_KEY = {
    "/api/v3/brokerage/accounts": {"accounts": []},
    "/api/v3/brokerage/key_permissions": {"can_view": True, "can_trade": True, "can_transfer": False},
}


class RouteTestCase(unittest.TestCase):
    def setUp(self):
        cb.reset_clock()
        cb.reset_limiters()
        vault.reset_schema_cache()
        patcher = patch.dict(os.environ, {
            "COINBASE_ALLOWED_EMAILS": "me@example.com", "COINBASE_VAULT_MASTER_KEY": MASTER,
            "COINBASE_MIN_REQUEST_INTERVAL_SECONDS": "0",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pool = FakePool()
        self.totp_codes = []
        self.coinbase = FakeCoinbase(**GOOD_KEY)

        async def fake_totp(request, user, code):
            self.totp_codes.append(code)

        def fake_client(key_name, private_key):
            return cb.CoinbaseClient(key_name, private_key, transport=self.coinbase.transport)

        for name, value in (("_verify_totp", fake_totp), ("_client_for", fake_client)):
            p = patch.object(routes, name, value)
            p.start()
            self.addCleanup(p.stop)

        app = FastAPI()

        @app.middleware("http")
        async def fake_auth(request, call_next):
            email_value = request.headers.get("x-test-email")
            if email_value is not None:
                request.state.member = {"id": "u1", "role": request.headers.get("x-test-role", "OWNER"), "email": email_value}
            return await call_next(request)

        app.state.db_pool = self.pool
        app.include_router(routes.router)
        self.http = TestClient(app)

    def save_key(self, **overrides):
        return self.http.put("/api/coinbase/key", json={**SAVE, **overrides}, headers=ME)

    def delete_key(self, **body):
        return self.http.request("DELETE", "/api/coinbase/key", json=body, headers=ME)


class AccessTests(RouteTestCase):
    def test_no_session_is_401(self):
        self.assertEqual(self.http.get("/api/coinbase/status").status_code, 401)

    def test_non_owner_unlisted_and_empty_allowlist_are_403(self):
        self.assertEqual(self.http.get("/api/coinbase/status", headers={**ME, "x-test-role": "CUSTOMER"}).status_code, 403)
        self.assertEqual(self.http.get("/api/coinbase/status", headers={"x-test-email": "other@example.com"}).status_code, 403)
        with patch.dict(os.environ, {"COINBASE_ALLOWED_EMAILS": ""}):
            response = self.http.get("/api/coinbase/status", headers=ME)
        self.assertEqual(response.status_code, 403)
        self.assertIn("COINBASE_ALLOWED_EMAILS", response.json()["detail"])

    def test_every_route_enforces_access(self):
        bodies = {"PUT": SAVE, "DELETE": {"confirm": "COINBASE ANAHTARINI SİL", "totp_code": "123456"}}
        for method, path in (("GET", "/status"), ("PUT", "/key"), ("DELETE", "/key"), ("GET", "/account"), ("GET", "/positions")):
            with self.subTest(path=path, method=method):
                response = self.http.request(method, "/api/coinbase" + path, json=bodies.get(method),
                                             headers={"x-test-email": "other@example.com"})
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.totp_codes, [])
        self.assertEqual(self.coinbase.calls, [])

    def test_router_has_no_trading_or_transfer_routes(self):
        pairs = {(method, route.path) for route in routes.router.routes for method in route.methods}
        self.assertEqual(pairs, {
            ("GET", "/api/coinbase/status"), ("PUT", "/api/coinbase/key"), ("DELETE", "/api/coinbase/key"),
            ("GET", "/api/coinbase/account"), ("GET", "/api/coinbase/positions"),
        })


class StatusAndKeyTests(RouteTestCase):
    def test_status_without_key(self):
        body = self.http.get("/api/coinbase/status", headers=ME).json()
        self.assertEqual((body["enabled"], body["mode"], body["key_saved"], body["vault_ready"]), (True, "READ_ONLY", False, True))

    def test_status_reports_missing_master_key(self):
        with patch.dict(os.environ, {"COINBASE_VAULT_MASTER_KEY": ""}):
            body = self.http.get("/api/coinbase/status", headers=ME).json()
        self.assertFalse(body["vault_ready"])
        self.assertIn("COINBASE_VAULT_MASTER_KEY", body["vault_reason"])

    def test_save_success_stores_ciphertext_and_leaks_nothing(self):
        response = self.save_key()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["saved"])
        self.assertTrue(body["check"]["permissions_checked"])
        self.assertEqual(self.totp_codes, ["123456"])
        for secret in (PEM, "org-123", "BEGIN EC PRIVATE KEY"):
            self.assertNotIn(secret, response.text)
            self.assertNotIn(secret.encode(), self.pool.rows["u1"]["encrypted_payload"])
        status = self.http.get("/api/coinbase/status", headers=ME)
        self.assertTrue(status.json()["key_saved"])
        self.assertEqual(status.json()["key"]["key_hint"], "ey-456")
        self.assertNotIn(PEM, status.text)

    def test_save_rejects_wrong_confirmation_before_anything_else(self):
        response = self.save_key(confirm="evet")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.totp_codes, [])
        self.assertEqual(self.pool.rows, {})
        self.assertEqual(self.coinbase.calls, [])

    def test_save_rejects_bad_keys_without_storing_or_calling_coinbase(self):
        wrong_curve = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        ).decode()
        cases = (
            {"private_key": "-----BEGIN EC PRIVATE KEY-----\n" + "A" * 60 + "\n-----END EC PRIVATE KEY-----"},
            {"private_key": wrong_curve},
            {"key_name": "organizations/x/apiKeys/"},
        )
        for overrides in cases:
            with self.subTest(overrides=list(overrides)):
                response = self.save_key(**overrides)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "KEY_FORMAT")
        self.assertEqual(self.pool.rows, {})
        self.assertEqual(self.coinbase.calls, [])

    def test_coinbase_rejecting_the_key_is_424_not_401_and_nothing_is_stored(self):
        self.coinbase.by_path["/api/v3/brokerage/accounts"] = (401, {"message": "Unauthorized"})
        response = self.save_key()
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.json()["code"], "AUTH_FAILED")
        self.assertEqual(self.pool.rows, {})

    def test_key_with_transfer_permission_is_refused(self):
        self.coinbase.by_path["/api/v3/brokerage/key_permissions"] = {"can_view": True, "can_trade": True, "can_transfer": True}
        response = self.save_key()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "TRANSFER_PERMISSION")
        self.assertEqual(self.pool.rows, {})

    def test_unverifiable_permissions_save_with_a_visible_warning(self):
        del self.coinbase.by_path["/api/v3/brokerage/key_permissions"]
        body = self.save_key().json()
        self.assertTrue(body["saved"])
        self.assertFalse(body["check"]["permissions_checked"])
        self.assertIn("transfer", body["check"]["warning"])

    def test_save_without_vault_key_is_a_clear_error(self):
        with patch.dict(os.environ, {"COINBASE_VAULT_MASTER_KEY": ""}):
            response = self.save_key()
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.json()["code"], "VAULT_NOT_READY")
        self.assertEqual(self.coinbase.calls, [])

    def test_delete(self):
        self.save_key()
        self.assertEqual(self.delete_key(confirm="x").status_code, 422)
        self.assertEqual(len(self.pool.rows), 1)
        response = self.delete_key(confirm="COINBASE ANAHTARINI SİL", totp_code="654321")
        self.assertEqual(response.json(), {"deleted": True})
        self.assertEqual(self.totp_codes[-1], "654321")
        self.assertEqual(self.pool.rows, {})
        self.assertFalse(self.http.get("/api/coinbase/status", headers=ME).json()["key_saved"])


class ReadTests(RouteTestCase):
    def test_account_without_key_is_409(self):
        response = self.http.get("/api/coinbase/account", headers=ME)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "KEY_MISSING"))

    def test_account_and_positions_for_saved_key(self):
        self.save_key()
        self.coinbase.by_path.update({
            "/api/v3/brokerage/cfm/balance_summary": BALANCE,
            "/api/v3/brokerage/cfm/intraday/current_margin_window": WINDOW,
            "/api/v3/brokerage/cfm/positions": {"positions": [{"product_id": "BIT-28JUL23-CDE", "side": "LONG", "number_of_contracts": "1"}]},
        })
        self.coinbase.calls.clear()
        account = self.http.get("/api/coinbase/account", headers=ME).json()
        self.assertEqual(account["mode"], "READ_ONLY")
        self.assertEqual(account["balance"]["worst_margin_window"], "overnight")
        self.assertEqual(account["margin_window"]["margin_window_type"], "FCM_MARGIN_WINDOW_TYPE_INTRADAY")
        positions = self.http.get("/api/coinbase/positions", headers=ME).json()
        self.assertEqual(positions["positions"][0]["product_id"], "BIT-28JUL23-CDE")
        self.assertTrue(all(method == "GET" for method, _ in self.coinbase.calls))

    def test_coinbase_failures_are_clear_4xx_never_5xx_or_401(self):
        self.save_key()
        cases = (
            ((403, {"message": "no"}), 424, "PERMISSION_DENIED"),
            ((404, {"message": "nf"}), 424, "NOT_FOUND"),
            ((500, {}), 424, "UPSTREAM"),
            ((401, {}), 424, "AUTH_FAILED"),
            ((200, {"unexpected": True}), 424, "BAD_RESPONSE"),
        )
        for reply, status, code in cases:
            with self.subTest(code=code):
                cb.reset_limiters()
                self.coinbase.by_path["/api/v3/brokerage/cfm/balance_summary"] = reply
                response = self.http.get("/api/coinbase/account", headers=ME)
                self.assertEqual((response.status_code, response.json()["code"]), (status, code))
                self.assertTrue(response.json()["detail"])

    def test_rate_limit_is_429_with_wait_time(self):
        self.save_key()
        self.coinbase.by_path["/api/v3/brokerage/cfm/positions"] = (429, {"message": "slow"})
        response = self.http.get("/api/coinbase/positions", headers=ME)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "RATE_LIMITED")
        self.assertGreaterEqual(response.json()["retry_after_seconds"], 1)

    def test_unreadable_stored_key_is_a_clear_error(self):
        self.save_key()
        with patch.dict(os.environ, {"COINBASE_VAULT_MASTER_KEY": "a-completely-different-master-key-xyz"}):
            response = self.http.get("/api/coinbase/account", headers=ME)
        self.assertEqual((response.status_code, response.json()["code"]), (424, "VAULT_DECRYPT"))


def probe(app):
    """Her Coinbase rotasına gerçek istek at (FastAPI'nin iç yol listesine bağlı kalma). Oturum yok."""
    http = TestClient(app)
    body = {**SAVE}
    return {
        "GET /status": http.get("/api/coinbase/status").status_code,
        "GET /account": http.get("/api/coinbase/account").status_code,
        "GET /positions": http.get("/api/coinbase/positions").status_code,
        "PUT /key": http.put("/api/coinbase/key", json=body).status_code,
        "DELETE /key": http.request("DELETE", "/api/coinbase/key", json={"confirm": "x"}).status_code,
    }


class FlagTests(unittest.TestCase):
    def test_flag_off_leaves_the_app_untouched(self):
        app = FastAPI()
        with patch.dict(os.environ, {"COINBASE_ENABLED": "false"}):
            self.assertFalse(include_coinbase_if_enabled(app))
        self.assertEqual(set(probe(app).values()), {404})  # rota hiç yok
        self.assertFalse(hasattr(app.state, "coinbase_enabled"))
        self.assertFalse(hasattr(app.state, "coinbase_load_error"))

    def test_flag_on_mounts_the_router(self):
        app = FastAPI()
        with patch.dict(os.environ, {"COINBASE_ENABLED": "true", "COINBASE_ALLOWED_EMAILS": "me@example.com"}):
            self.assertTrue(include_coinbase_if_enabled(app))
        # Rota var ve oturum yok: 404 değil 401 (erişim kontrolü çalışıyor)
        self.assertEqual(set(probe(app).values()), {401})
        self.assertTrue(app.state.coinbase_enabled)

    def test_load_failure_is_visible_not_silent_and_does_not_crash(self):
        app = FastAPI()
        with patch.dict(os.environ, {"COINBASE_ENABLED": "true"}), patch.dict(sys.modules, {"app.coinbase.routes": None}):
            self.assertFalse(include_coinbase_if_enabled(app))
        self.assertIn("yüklenemedi", app.state.coinbase_load_error)
        response = TestClient(app).get("/api/coinbase/status")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.json()["code"], "COINBASE_LOAD_FAILED")


if __name__ == "__main__":
    unittest.main()

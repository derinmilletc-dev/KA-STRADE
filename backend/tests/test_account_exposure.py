"""Silme öncesi canlı borsa kontrolü: sahte borsa yanıtlarıyla (gerçek anahtar ve gerçek emir yok)."""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import httpx  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import account_exposure as exposure  # noqa: E402
from app import exchange_connections as vault  # noqa: E402

FAKE_MASTER = "fake-master-key-for-tests-only-0001"
FAKE_KEY = "FAKEAPIKEY" + "0" * 22
FAKE_SECRET = "FAKESECRET" + "0" * 22
USER = {"id": "exposure-user-1"}
ALLOWED_PATHS = {"/fapi/v3/positionRisk", "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders", "/fapi/v1/time"}


def clean_responses(path):
    if path == "/fapi/v3/positionRisk":
        return [{"symbol": "BTCUSDT", "positionAmt": "0.00000000"}]
    return []


class FakeBinance:
    """Yalnızca GET kabul eder; istek yollarını kaydeder."""

    def __init__(self, responder):
        self.responder = responder
        self.seen = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path))
        if request.method != "GET" or request.url.path not in ALLOWED_PATHS:
            raise AssertionError(f"unexpected request {request.method} {request.url.path}")
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": int(time.time() * 1000)})
        return self.responder(request.url.path)


def client_for(responder) -> tuple[httpx.AsyncClient, FakeBinance]:
    fake = FakeBinance(responder)
    return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)), fake


class FakePool:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail

    async def fetch(self, *args):
        if self.fail:
            raise RuntimeError("db down")
        return self.rows


def record(mode, api_key=FAKE_KEY, secret=FAKE_SECRET):
    with patch.dict(os.environ, {"PROTREBOT_VAULT_MASTER_KEY": FAKE_MASTER}):
        blob = vault.encrypt_credentials(api_key, secret, mode=mode)
    return {"mode": mode, "encrypted_payload": blob}


class ExposureCheckTests(unittest.IsolatedAsyncioTestCase):
    async def run_check(self, responder):
        client, fake = client_for(responder)
        try:
            await exposure.check_live_exposure(client, "LIVE", FAKE_KEY, FAKE_SECRET, time_offset_ms=0)
        finally:
            await client.aclose()
        return fake

    async def test_clean_account_passes_with_read_only_gets(self):
        fake = await self.run_check(lambda path: httpx.Response(200, json=clean_responses(path)))
        self.assertEqual({p for _, p in fake.seen}, {"/fapi/v3/positionRisk", "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"})
        self.assertTrue(all(method == "GET" for method, _ in fake.seen))

    async def test_open_position_blocks(self):
        def responder(path):
            if path == "/fapi/v3/positionRisk":
                return httpx.Response(200, json=[{"symbol": "BTCUSDT", "positionAmt": "0.5"}])
            return httpx.Response(200, json=[])
        with self.assertRaises(exposure.ExposureFound) as caught:
            await self.run_check(responder)
        self.assertIn("positions", caught.exception.reasons)

    async def test_open_order_blocks(self):
        def responder(path):
            if path == "/fapi/v1/openOrders":
                return httpx.Response(200, json=[{"orderId": 1}])
            return httpx.Response(200, json=clean_responses(path))
        with self.assertRaises(exposure.ExposureFound) as caught:
            await self.run_check(responder)
        self.assertIn("open_orders", caught.exception.reasons)

    async def test_open_algo_order_blocks(self):
        def responder(path):
            if path == "/fapi/v1/openAlgoOrders":
                return httpx.Response(200, json=[{"algoId": 7}])
            return httpx.Response(200, json=clean_responses(path))
        with self.assertRaises(exposure.ExposureFound) as caught:
            await self.run_check(responder)
        self.assertIn("open_algo_orders", caught.exception.reasons)

    async def test_invalid_or_revoked_key_is_unverifiable_not_clean(self):
        def responder(path):
            return httpx.Response(400, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."})
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)

    async def test_timeout_is_unverifiable(self):
        def responder(path):
            raise httpx.ReadTimeout("timed out")
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)

    async def test_non_json_success_is_unverifiable(self):
        def responder(path):
            return httpx.Response(200, content=b"<html>maintenance</html>")
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)

    async def test_malformed_position_payload_is_unverifiable(self):
        def responder(path):
            if path == "/fapi/v3/positionRisk":
                return httpx.Response(200, json={"unexpected": True})
            return httpx.Response(200, json=[])
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)

    async def test_non_list_algo_orders_is_unverifiable(self):
        def responder(path):
            if path == "/fapi/v1/openAlgoOrders":
                return httpx.Response(200, json={"orders": []})
            return httpx.Response(200, json=clean_responses(path))
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)

    async def test_non_numeric_position_amount_is_unverifiable(self):
        def responder(path):
            if path == "/fapi/v3/positionRisk":
                return httpx.Response(200, json=[{"positionAmt": "NaN"}])
            return httpx.Response(200, json=[])
        with self.assertRaises(exposure.ExposureUnverifiable):
            await self.run_check(responder)


class ErasureGateTests(unittest.IsolatedAsyncioTestCase):
    def app(self, rows=None, fail=False):
        return SimpleNamespace(state=SimpleNamespace(db_pool=FakePool(rows, fail)))

    async def gate(self, app, responder):
        client, fake = client_for(responder)
        try:
            with patch.dict(os.environ, {"PROTREBOT_VAULT_MASTER_KEY": FAKE_MASTER}):
                await exposure.gate_before_erasure(app, USER, http=client)
        finally:
            await client.aclose()
        return fake

    async def test_no_stored_keys_passes(self):
        fake = await self.gate(self.app(rows=[]), lambda path: httpx.Response(200, json=[]))
        self.assertEqual(fake.seen, [])

    async def test_clean_stored_key_passes(self):
        app = self.app(rows=[record("LIVE")])
        await self.gate(app, lambda path: httpx.Response(200, json=clean_responses(path)))

    async def test_open_position_gate_returns_409(self):
        app = self.app(rows=[record("LIVE")])
        def responder(path):
            if path == "/fapi/v3/positionRisk":
                return httpx.Response(200, json=[{"positionAmt": "-1.2"}])
            return httpx.Response(200, json=[])
        with self.assertRaises(HTTPException) as caught:
            await self.gate(app, responder)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_unverifiable_gate_returns_503_without_leaking_secrets(self):
        app = self.app(rows=[record("LIVE")])
        with self.assertRaises(HTTPException) as caught:
            await self.gate(app, lambda path: httpx.Response(400, json={"code": -2015, "msg": "Invalid API-key"}))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn(FAKE_KEY, caught.exception.detail)
        self.assertNotIn(FAKE_SECRET, caught.exception.detail)

    async def test_every_stored_record_is_checked_including_testnet(self):
        app = self.app(rows=[record("LIVE"), record("TESTNET")])
        def responder(path):
            # Kayıtlar sırayla işlenir (her kayıt 3 GET). 4. çağrıdan sonrası ikinci kaydın openOrders'ı.
            responder.calls = getattr(responder, "calls", 0) + 1
            if responder.calls > 3 and path == "/fapi/v1/openOrders":
                return httpx.Response(200, json=[{"orderId": 9}])
            return httpx.Response(200, json=clean_responses(path))
        with self.assertRaises(HTTPException) as caught:
            await self.gate(app, responder)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_vault_read_failure_is_503(self):
        with self.assertRaises(HTTPException) as caught:
            await self.gate(self.app(fail=True), lambda path: httpx.Response(200, json=[]))
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()

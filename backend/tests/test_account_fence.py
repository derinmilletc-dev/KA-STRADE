"""Hesap silme fence'i (birim testleri, veritabanı gerektirmez): katı kapatma ayrıştırması, giriş kontrolü, canlı istemci."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402
import httpx  # noqa: E402

from app import account_fence as fence  # noqa: E402
from app import v25_execution as live  # noqa: E402

FAKE_KEY = "FAKEAPIKEY" + "0" * 22
FAKE_SECRET = "FAKESECRET" + "0" * 22


class FakePool:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    async def fetchval(self, sql, *args):
        if self.error is not None:
            raise self.error
        return self.value


def app_with(pool):
    return SimpleNamespace(state=SimpleNamespace(db_pool=pool))


class ReduceOnlyParsingTests(unittest.TestCase):
    def test_explicit_exit_flags_are_exempt(self):
        self.assertTrue(fence.is_reduce_only_exit({"reduceOnly": "true"}))
        self.assertTrue(fence.is_reduce_only_exit({"reduceOnly": True}))
        self.assertTrue(fence.is_reduce_only_exit({"closePosition": "TRUE"}))

    def test_entries_and_missing_flags_are_not_exempt(self):
        self.assertFalse(fence.is_reduce_only_exit({}))
        self.assertFalse(fence.is_reduce_only_exit(None))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": "false"}))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": False, "closePosition": "false"}))

    def test_ambiguous_or_conflicting_values_fail_closed(self):
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": "yes"}))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": 1}))
        self.assertFalse(fence.is_reduce_only_exit({"closePosition": 1}))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": 0}))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": ["true"]}))
        self.assertFalse(fence.is_reduce_only_exit({"reduceOnly": "true", "closePosition": "garbage"}))

    def test_entry_paths_cover_normal_and_algo_orders(self):
        self.assertIn("/fapi/v1/order", fence.ENTRY_ORDER_PATHS)
        self.assertIn("/fapi/v1/order/test", fence.ENTRY_ORDER_PATHS)
        self.assertIn("/fapi/v1/algoOrder", fence.ENTRY_ORDER_PATHS)


class EntryGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_application_or_identity_fails_closed(self):
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(None, "u1")
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(app_with(FakePool(None)), "")

    async def test_missing_pool_fails_closed(self):
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(SimpleNamespace(state=SimpleNamespace(db_pool=None)), "u1")

    async def test_active_fence_blocks(self):
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(app_with(FakePool("ACTIVE")), "u1")

    async def test_released_or_absent_fence_allows(self):
        await fence.assert_entries_allowed(app_with(FakePool("RELEASED")), "u1")
        await fence.assert_entries_allowed(app_with(FakePool(None)), "u1")

    async def test_database_error_blocks_entries(self):
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(app_with(FakePool(error=RuntimeError("db down"))), "u1")

    async def test_undefined_fence_table_means_no_fence_exists(self):
        error = asyncpg.UndefinedTableError("relation does not exist")
        self.assertFalse(await fence.fence_active(FakePool(error=error), "u1"))


class LiveClientFenceTests(unittest.IsolatedAsyncioTestCase):
    def client(self, application):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"orderId": 1}))
        return live.BinanceLiveClient(
            httpx.AsyncClient(transport=transport), FAKE_KEY, FAKE_SECRET,
            diagnostic_state={"live_session_authorization": {"user_id": "u1"}},
            application=application,
        )

    async def test_entry_order_is_refused_before_any_http_call(self):
        client = self.client(app_with(FakePool("ACTIVE")))
        with self.assertRaises(live.LiveExchangeError) as caught:
            await client._request("POST", "/fapi/v1/algoOrder", {"symbol": "BTCUSDT", "side": "BUY"}, signed=True)
        self.assertEqual(caught.exception.http_status, 423)

    async def test_entry_order_without_application_is_refused(self):
        client = self.client(None)
        with self.assertRaises(live.LiveExchangeError) as caught:
            await client._request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT", "side": "BUY"}, signed=True)
        self.assertEqual(caught.exception.http_status, 423)

    async def test_exit_order_skips_the_entry_check(self):
        client = self.client(app_with(FakePool("ACTIVE")))
        check = AsyncMock()
        with patch("app.account_fence.assert_entries_allowed", new=check):
            try:
                await client._request("POST", "/fapi/v1/order", {"symbol": "BTCUSDT", "side": "SELL", "reduceOnly": "true"}, signed=True)
            except live.LiveExchangeError as exc:
                self.assertNotEqual(exc.http_status, 423)
            except Exception:
                pass  # downstream transport details are not under test here
        check.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

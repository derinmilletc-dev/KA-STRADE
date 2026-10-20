"""Gerçek PostgreSQL: giriş emri uçuş kayıtları ile fence kurulumu arasındaki yarış.

ERASURE_PG_DSN gerekir (CI'da geçici PostgreSQL). Yalnızca kendi fence tablolarını oluşturup siler.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402

from app import account_fence as fence  # noqa: E402
from app.account_erasure import user_hash  # noqa: E402

TEST_TABLES = ("commercial_account_fences", "commercial_erasure_audit", "commercial_entry_inflight")


class EntryInflightPgTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TEST_TABLES) + " CASCADE")
        await fence.ensure_fence_schema(self.pool)
        self.app = SimpleNamespace(state=SimpleNamespace(db_pool=self.pool))

    async def test_engage_waits_for_pending_entry_and_fails_closed(self):
        token = await fence.begin_entry(self.app, "inflight-user-1")
        with patch.object(fence, "DRAIN_TIMEOUT_SECONDS", 0.5):
            with self.assertRaises(fence.FenceError):
                await fence.engage_fence(self.pool, "inflight-user-1", actor="test")
        self.assertTrue(await fence.fence_active(self.pool, "inflight-user-1"))
        await fence.end_entry(self.app, token)
        with patch.object(fence, "DRAIN_TIMEOUT_SECONDS", 0.5):
            await fence.engage_fence(self.pool, "inflight-user-1", actor="test")
        with self.assertRaises(fence.FenceError):
            await fence.begin_entry(self.app, "inflight-user-1")

    async def test_expired_marker_does_not_block_engage(self):
        await self.pool.execute(
            "INSERT INTO commercial_entry_inflight (token, user_hash, expires_at) "
            "VALUES ('stale', $1, NOW() - interval '1 second')",
            user_hash("inflight-user-2"),
        )
        with patch.object(fence, "DRAIN_TIMEOUT_SECONDS", 0.5):
            await fence.engage_fence(self.pool, "inflight-user-2", actor="test")
        self.assertTrue(await fence.fence_active(self.pool, "inflight-user-2"))

    async def test_fence_on_one_account_does_not_block_another(self):
        with patch.object(fence, "DRAIN_TIMEOUT_SECONDS", 0.5):
            await fence.engage_fence(self.pool, "inflight-user-3", actor="test")
        token = await fence.begin_entry(self.app, "inflight-user-4")
        await fence.end_entry(self.app, token)

    async def test_concurrent_begin_and_engage_exactly_one_wins(self):
        for i in range(5):
            uid = f"race-user-{i}"
            with patch.object(fence, "DRAIN_TIMEOUT_SECONDS", 0.5):
                begin_res, engage_res = await asyncio.gather(
                    fence.begin_entry(self.app, uid),
                    fence.engage_fence(self.pool, uid, actor="test"),
                    return_exceptions=True,
                )
            begin_ok = not isinstance(begin_res, BaseException)
            engage_ok = not isinstance(engage_res, BaseException)
            self.assertNotEqual(begin_ok, engage_ok, f"iteration {i}: begin={begin_res!r} engage={engage_res!r}")
            if begin_ok:
                self.assertIsInstance(engage_res, fence.FenceError)
                await fence.end_entry(self.app, begin_res)
            else:
                self.assertIsInstance(begin_res, fence.FenceError)


if __name__ == "__main__":
    unittest.main()

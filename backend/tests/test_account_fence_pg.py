"""Hesap silme fence'i gerçek PostgreSQL üzerinde (ERASURE_PG_DSN; CI'da geçici servis)."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import account_erasure as erasure  # noqa: E402
from app import account_fence as fence  # noqa: E402

TABLES = ("commercial_account_fences", "commercial_erasure_audit", "commercial_account_erasure_jobs",
          "commercial_erased_users", "commercial_auth_users", "application_state_snapshots")


class FenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        self.other_process = await asyncpg.create_pool(dsn, min_size=1, max_size=2)  # ayrı süreç simülasyonu
        self.addAsyncCleanup(self.pool.close)
        self.addAsyncCleanup(self.other_process.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
        await self.pool.execute("CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, email TEXT NOT NULL)")
        await self.pool.execute("CREATE TABLE application_state_snapshots (state_key TEXT PRIMARY KEY, payload JSONB NOT NULL)")
        await erasure.ensure_erasure_schema(self.pool)
        await fence.ensure_fence_schema(self.pool)
        self.user_id = "pg-fence-user"

    async def test_fence_set_in_one_process_blocks_entries_in_another(self):
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        self.assertTrue(await fence.fence_active(self.other_process, self.user_id))
        with self.assertRaises(fence.FenceError):
            await fence.assert_entries_allowed(
                type("App", (), {"state": type("S", (), {"db_pool": self.other_process})})(), self.user_id,
            )

    async def test_engage_is_idempotent_and_audited(self):
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        await fence.engage_fence(self.pool, self.user_id, actor="admin-1")
        self.assertTrue(await fence.fence_active(self.pool, self.user_id))
        count = await self.pool.fetchval("SELECT count(*) FROM commercial_erasure_audit WHERE action = 'FENCE_ENGAGED'")
        self.assertEqual(count, 2)

    async def test_release_refused_without_clean_live_check_and_stays_active(self):
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        with self.assertRaises(HTTPException) as caught:
            await fence.release_fence(self.pool, self.user_id, actor="admin-1", clean_live_check=False)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(await fence.fence_active(self.pool, self.user_id))
        refused = await self.pool.fetchval("SELECT count(*) FROM commercial_erasure_audit WHERE action = 'FENCE_RELEASE_REFUSED'")
        self.assertEqual(refused, 1)

    async def test_release_after_clean_live_check_is_audited(self):
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        await fence.release_fence(self.pool, self.user_id, actor="admin-1", clean_live_check=True)
        self.assertFalse(await fence.fence_active(self.pool, self.user_id))
        released = await self.pool.fetchval("SELECT count(*) FROM commercial_erasure_audit WHERE action = 'FENCE_RELEASED'")
        self.assertEqual(released, 1)

    async def test_entries_that_race_with_engage_are_refused_once_engage_returns(self):
        checks = [
            asyncio.create_task(fence.fence_active(self.other_process, self.user_id)) for _ in range(10)
        ]
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        await asyncio.gather(*checks)  # yarış sırasındaki okumalar hata vermemeli
        self.assertTrue(await fence.fence_active(self.other_process, self.user_id))

    async def test_audit_rows_do_not_store_raw_user_id(self):
        await fence.engage_fence(self.pool, self.user_id, actor="system")
        rows = await self.pool.fetch("SELECT user_hash, detail FROM commercial_erasure_audit")
        for row in rows:
            self.assertNotIn(self.user_id, row["user_hash"])
            self.assertNotIn(self.user_id, row["detail"])


if __name__ == "__main__":
    unittest.main()

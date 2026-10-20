"""Gerçek PostgreSQL üzerinde hesap silme veritabanı akışı (account_erasure.erase_database).

ERASURE_PG_DSN ortam değişkeni gerekir (CI'da geçici bir PostgreSQL servisi). Değişken yoksa testler atlanır;
Backend Security Tests kapısı bu dosyayı zorunlu listelediği için CI'da atlanma kırmızı sayılır.
Testler yalnızca kendi tablolarını oluşturup siler; üretim verisine dokunmaz.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402

from app import account_erasure as erasure  # noqa: E402

TEST_TABLES = (
    "commercial_auth_users", "commercial_account_settings", "subscriptions",
    "application_state_snapshots", "commercial_erased_users", "commercial_account_erasure_jobs",
)


class ErasureDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TEST_TABLES) + " CASCADE")
        await self.pool.execute("CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, auth_version BIGINT NOT NULL DEFAULT 1, security JSONB NOT NULL)")
        await self.pool.execute("CREATE TABLE commercial_account_settings (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        await self.pool.execute(
            "CREATE TABLE subscriptions (id SERIAL PRIMARY KEY, user_id TEXT, stripe_customer_id TEXT, "
            "stripe_subscription_id TEXT, stripe_price_id TEXT)"
        )
        await self.pool.execute(
            "CREATE TABLE application_state_snapshots (state_key TEXT PRIMARY KEY, payload JSONB NOT NULL)"
        )
        await erasure.ensure_erasure_schema(self.pool)
        self.app = SimpleNamespace(state=SimpleNamespace(db_pool=self.pool))
        self.user = {"id": "pg-user-1", "email": "pg-user-1@example.test"}
        self.other = {"id": "pg-user-2", "email": "pg-user-2@example.test"}
        await self.pool.execute(
            "INSERT INTO commercial_auth_users (user_id, security) VALUES ($1, jsonb_build_object('email', $2::text)), ($3, jsonb_build_object('email', $4::text))",
            self.user["id"], self.user["email"], self.other["id"], self.other["email"],
        )
        await self.pool.execute(
            "INSERT INTO commercial_account_settings (user_id, data) VALUES ($1, 'personal'), ($2, 'other')",
            self.user["id"], self.other["id"],
        )
        await self.pool.execute(
            "INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, stripe_price_id) "
            "VALUES ($1, 'cus_pg', 'sub_pg', 'price_pg')",
            self.user["id"],
        )

    async def count(self, sql, *args):
        return await self.pool.fetchval(sql, *args)

    async def test_erasure_removes_personal_rows_scrubs_subscription_and_tombstones_user(self):
        sessions = await erasure.erase_database(self.app, self.user)

        self.assertEqual(sessions, [])
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_auth_users WHERE user_id = $1", self.user["id"]), 0)
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_account_settings WHERE user_id = $1", self.user["id"]), 0)
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_account_settings WHERE user_id = $1", self.other["id"]), 1)
        scrubbed = await self.pool.fetchrow("SELECT user_id, stripe_customer_id, stripe_subscription_id, stripe_price_id FROM subscriptions")
        self.assertEqual(scrubbed["user_id"], erasure.ERASED)
        self.assertIsNone(scrubbed["stripe_customer_id"])
        self.assertIsNone(scrubbed["stripe_subscription_id"])
        self.assertIsNone(scrubbed["stripe_price_id"])
        self.assertEqual(
            await self.count("SELECT count(*) FROM commercial_erased_users WHERE user_hash = $1", erasure.user_hash(self.user["id"])),
            1,
        )

    async def test_guard_blocks_reinsertion_for_erased_user_and_keeps_other_users(self):
        await erasure.erase_database(self.app, self.user)

        await self.pool.execute(
            "INSERT INTO commercial_account_settings (user_id, data) VALUES ($1, 'late')", self.user["id"],
        )
        await self.pool.execute(
            "INSERT INTO commercial_auth_users (user_id, security) VALUES ($1, jsonb_build_object('email', $2::text))", self.user["id"], self.user["email"],
        )
        await self.pool.execute(
            "INSERT INTO subscriptions (user_id, stripe_customer_id) VALUES ($1, 'cus_late')", self.user["id"],
        )

        self.assertEqual(await self.count("SELECT count(*) FROM commercial_account_settings WHERE user_id = $1", self.user["id"]), 0)
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_auth_users WHERE user_id = $1", self.user["id"]), 0)
        late = await self.pool.fetchrow("SELECT user_id, stripe_customer_id FROM subscriptions WHERE stripe_customer_id IS NOT NULL")
        self.assertIsNone(late)
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_auth_users WHERE user_id = $1", self.other["id"]), 1)

    async def test_repeated_erasure_is_idempotent(self):
        await erasure.erase_database(self.app, self.user)
        await erasure.erase_database(self.app, self.user)

        self.assertEqual(
            await self.count("SELECT count(*) FROM commercial_erased_users WHERE user_hash = $1", erasure.user_hash(self.user["id"])),
            1,
        )
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_account_settings WHERE user_id = $1", self.other["id"]), 1)

    async def test_failure_mid_erasure_rolls_back_every_database_change(self):
        real_table_exists = erasure.table_exists

        async def fail_on_subscriptions(connection, name):
            if name == "subscriptions":
                raise RuntimeError("injected failure after earlier erasure writes")
            return await real_table_exists(connection, name)

        with patch.object(erasure, "table_exists", new=fail_on_subscriptions):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                await erasure.erase_database(self.app, self.user)

        self.assertEqual(await self.count("SELECT count(*) FROM commercial_auth_users WHERE user_id = $1", self.user["id"]), 1)
        self.assertEqual(await self.count("SELECT count(*) FROM commercial_account_settings WHERE user_id = $1", self.user["id"]), 1)
        kept = await self.pool.fetchrow("SELECT user_id, stripe_customer_id, stripe_subscription_id FROM subscriptions")
        self.assertEqual(kept["user_id"], self.user["id"])
        self.assertEqual(kept["stripe_customer_id"], "cus_pg")
        self.assertEqual(kept["stripe_subscription_id"], "sub_pg")
        self.assertEqual(
            await self.count("SELECT count(*) FROM commercial_erased_users WHERE user_hash = $1", erasure.user_hash(self.user["id"])),
            0,
        )


class ErasureLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TEST_TABLES) + " CASCADE")
        await self.pool.execute("CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, auth_version BIGINT NOT NULL DEFAULT 1, security JSONB NOT NULL)")
        await self.pool.execute(
            "CREATE TABLE application_state_snapshots (state_key TEXT PRIMARY KEY, payload JSONB NOT NULL)"
        )
        await erasure.ensure_erasure_schema(self.pool)
        self.user = {"id": "pg-lease-user", "email": "pg-lease@example.test"}

    async def test_second_owner_cannot_take_an_active_lease(self):
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-a"))
        self.assertFalse(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-b"))

    async def test_expired_lease_can_be_taken_over(self):
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-a"))
        await self.pool.execute(
            "UPDATE commercial_account_erasure_jobs SET lease_expires_at = NOW() - interval '1 second' WHERE user_hash = $1",
            erasure.user_hash(self.user["id"]),
        )
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-b"))

    async def test_failed_release_allows_retry(self):
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-a"))
        await erasure.release_erasure_lease(self.pool, self.user["id"], "owner-a", done=False)
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-b"))

    async def test_done_release_blocks_further_erasure_and_wrong_owner_cannot_release(self):
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-a"))
        await erasure.release_erasure_lease(self.pool, self.user["id"], "owner-b", done=True)
        status = await self.pool.fetchval(
            "SELECT status FROM commercial_account_erasure_jobs WHERE user_hash = $1", erasure.user_hash(self.user["id"]),
        )
        self.assertEqual(status, "RUNNING")
        await erasure.release_erasure_lease(self.pool, self.user["id"], "owner-a", done=True)
        self.assertFalse(await erasure.acquire_erasure_lease(self.pool, self.user["id"], "owner-c"))


if __name__ == "__main__":
    unittest.main()

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

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402

from app import account_erasure as erasure  # noqa: E402

TEST_TABLES = (
    "commercial_auth_users", "commercial_account_settings", "subscriptions",
    "application_state_snapshots", "commercial_erased_users",
)


class ErasureDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TEST_TABLES) + " CASCADE")
        await self.pool.execute("CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, email TEXT NOT NULL)")
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
            "INSERT INTO commercial_auth_users (user_id, email) VALUES ($1, $2), ($3, $4)",
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
            "INSERT INTO commercial_auth_users (user_id, email) VALUES ($1, $2)", self.user["id"], self.user["email"],
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


if __name__ == "__main__":
    unittest.main()

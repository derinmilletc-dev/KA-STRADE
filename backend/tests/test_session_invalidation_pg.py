"""Gerçek uygulama fonksiyonu (v22_commercial.invalidate_user_sessions) ve gerçek PostgreSQL.

ERASURE_PG_DSN gerekir. Yalnızca commercial_auth_users tablosunu oluşturur ve siler.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import v22_commercial as auth  # noqa: E402


class SessionInvalidationPgTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS commercial_auth_users CASCADE")
        await self.pool.execute(
            "CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "auth_version INTEGER NOT NULL DEFAULT 1, security JSONB NOT NULL DEFAULT '{}'::jsonb)"
        )
        await self.pool.execute(
            "INSERT INTO commercial_auth_users (user_id, email, auth_version, security) "
            "VALUES ('sess-user-1', 'sess-user-1@example.test', 3, '{\"active\": true}'::jsonb)"
        )
        self.state = {"lock": None, "state": {"users": []}}
        self.app = SimpleNamespace(state=SimpleNamespace(db_pool=self.pool, v22_commercial=self.state))

    async def row(self):
        row = await self.pool.fetchrow(
            "SELECT auth_version, security FROM commercial_auth_users WHERE user_id = 'sess-user-1'"
        )
        security = row["security"]
        return {"auth_version": row["auth_version"],
                "security": json.loads(security) if isinstance(security, str) else security}

    async def test_revocation_bumps_version_and_deactivates(self):
        user = {"id": "sess-user-1", "email": "sess-user-1@example.test", "auth_version": 3}
        await auth.invalidate_user_sessions(
            SimpleNamespace(app=self.app), user, security_updates={"active": False}, expected_version=3,
        )
        row = await self.row()
        self.assertEqual(row["auth_version"], 4)
        self.assertEqual(row["security"]["active"], False)
        self.assertEqual(user["auth_version"], 4)

    async def test_stale_version_is_refused_and_nothing_changes(self):
        user = {"id": "sess-user-1", "email": "sess-user-1@example.test", "auth_version": 2}
        with self.assertRaises(HTTPException) as ctx:
            await auth.invalidate_user_sessions(
                SimpleNamespace(app=self.app), user, security_updates={"active": False}, expected_version=2,
            )
        self.assertEqual(ctx.exception.status_code, 401)
        row = await self.row()
        self.assertEqual((row["auth_version"], row["security"]["active"]), (3, True))

    async def test_missing_user_row_is_refused(self):
        user = {"id": "no-such-user", "email": "x@example.test", "auth_version": 1}
        with self.assertRaises(HTTPException) as ctx:
            await auth.invalidate_user_sessions(
                SimpleNamespace(app=self.app), user, security_updates={"active": False},
            )
        self.assertEqual(ctx.exception.status_code, 401)

    async def test_unsupported_security_field_is_rejected_before_database(self):
        user = {"id": "sess-user-1", "email": "sess-user-1@example.test", "auth_version": 3}
        with self.assertRaises(ValueError):
            await auth.invalidate_user_sessions(
                SimpleNamespace(app=self.app), user, security_updates={"unknown_field": 1},
            )
        self.assertEqual((await self.row())["auth_version"], 3)


if __name__ == "__main__":
    unittest.main()

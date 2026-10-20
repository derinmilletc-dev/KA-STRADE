"""Gerçek PostgreSQL: kesintiye uğramış silmelerin sürdürülmesi, kilit devralma yarışı, kalp atışı ve operatör raporu.

ERASURE_PG_DSN gerekir (CI'da geçici PostgreSQL). Yalnızca kendi tablolarını oluşturup siler.
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import asyncpg  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import account_erasure as erasure  # noqa: E402

TEST_TABLES = (
    "commercial_auth_users", "commercial_account_settings", "subscriptions",
    "application_state_snapshots", "commercial_erased_users", "commercial_account_erasure_jobs",
)
USER = {"id": "sweep-pg-1", "email": "sweep-pg-1@example.test", "role": "CUSTOMER"}


class SweeperPgTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dsn = os.environ.get("ERASURE_PG_DSN")
        if not dsn:
            raise unittest.SkipTest("ERASURE_PG_DSN is not set")
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6)
        self.addAsyncCleanup(self.pool.close)
        await self.pool.execute("DROP TABLE IF EXISTS " + ", ".join(TEST_TABLES) + " CASCADE")
        await self.pool.execute(
            "CREATE TABLE commercial_auth_users (user_id TEXT PRIMARY KEY, "
            "auth_version BIGINT NOT NULL DEFAULT 1, security JSONB NOT NULL)"
        )
        await self.pool.execute("CREATE TABLE commercial_account_settings (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        await self.pool.execute(
            "CREATE TABLE subscriptions (id SERIAL PRIMARY KEY, user_id TEXT, stripe_customer_id TEXT, "
            "stripe_subscription_id TEXT, stripe_price_id TEXT)"
        )
        await self.pool.execute(
            "CREATE TABLE application_state_snapshots (state_key TEXT PRIMARY KEY, payload JSONB NOT NULL)"
        )
        await erasure.ensure_erasure_schema(self.pool)
        self.user_hash = erasure.user_hash(USER["id"])
        self.app = self.make_app(self.pool)

    def make_app(self, pool):
        return SimpleNamespace(state=SimpleNamespace(
            db_pool=pool, v22_commercial={"state": {"users": [dict(USER)]}, "lock": None},
        ))

    async def insert_expired_job(self, status="RUNNING"):
        await self.pool.execute(
            "INSERT INTO commercial_auth_users (user_id, security) VALUES ($1, jsonb_build_object('email', $2::text)) ON CONFLICT DO NOTHING",
            USER["id"], USER["email"],
        )
        await self.pool.execute(
            """INSERT INTO commercial_account_erasure_jobs
               (user_hash, status, lease_owner, lease_expires_at, attempts)
               VALUES ($1, $2, 'dead-owner', NOW() - interval '1 minute', 1)""",
            self.user_hash, status,
        )

    async def job(self):
        return await self.pool.fetchrow(
            "SELECT status, resume_user_id, attempts FROM commercial_account_erasure_jobs WHERE user_hash = $1",
            self.user_hash,
        )

    async def test_acquire_keeps_resume_id_on_failure_and_clears_on_done(self):
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, USER["id"], "a"))
        self.assertIsNone((await self.job())["resume_user_id"])
        await erasure.release_erasure_lease(self.pool, USER["id"], "a", done=False)
        row = await self.job()
        self.assertEqual(row["status"], "FAILED")
        self.assertTrue(await erasure.acquire_erasure_lease(self.pool, USER["id"], "b"))
        await erasure.release_erasure_lease(self.pool, USER["id"], "b", done=True)
        row = await self.job()
        self.assertEqual(row["status"], "DONE")

    async def test_concurrent_takeover_of_expired_lease_has_one_winner(self):
        await self.insert_expired_job()
        results = await asyncio.gather(
            erasure.acquire_erasure_lease(self.pool, USER["id"], "owner-a"),
            erasure.acquire_erasure_lease(self.pool, USER["id"], "owner-b"),
            erasure.acquire_erasure_lease(self.pool, USER["id"], "owner-c"),
        )
        self.assertEqual(sum(results), 1)

    async def test_done_job_cannot_be_taken_over(self):
        await self.insert_expired_job(status="DONE")
        self.assertFalse(await erasure.acquire_erasure_lease(self.pool, USER["id"], "late"))

    async def test_expired_lease_is_resumed_after_restart_and_completes(self):
        await self.insert_expired_job()
        restarted_pool = await asyncpg.create_pool(os.environ["ERASURE_PG_DSN"], min_size=1, max_size=2)
        self.addAsyncCleanup(restarted_pool.close)
        restarted_app = self.make_app(restarted_pool)
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()) as gate, \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(return_value={})) as erase:
            results = await erasure.sweep_stale_erasures(restarted_app)
        self.assertEqual(results[0]["outcome"], "tamamlandı")
        gate.assert_awaited_once()
        erase.assert_awaited_once()
        row = await self.job()
        self.assertEqual(row["status"], "DONE")

    async def test_failed_resume_is_never_marked_done(self):
        await self.insert_expired_job()
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(side_effect=RuntimeError("x"))):
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results[0]["outcome"], "hata")
        row = await self.job()
        self.assertEqual(row["status"], "FAILED")
        self.assertIsNone(row["resume_user_id"])

    async def test_open_exposure_blocks_resume_without_erasing(self):
        await self.insert_expired_job()
        with patch("app.account_exposure.gate_before_erasure",
                   new=AsyncMock(side_effect=HTTPException(409, "Açık pozisyon"))), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results[0]["outcome"], "durduruldu")
        erase.assert_not_awaited()
        self.assertEqual((await self.job())["status"], "FAILED")

    async def test_concurrent_sweepers_run_erasure_once(self):
        await self.insert_expired_job()
        calls = []

        async def slow_erase(request, user):
            calls.append(1)
            await asyncio.sleep(0.2)
            return {}

        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=slow_erase):
            await asyncio.gather(erasure.sweep_stale_erasures(self.app), erasure.sweep_stale_erasures(self.app))
        self.assertEqual(len(calls), 1)
        self.assertEqual((await self.job())["status"], "DONE")

    async def test_heartbeat_keeps_long_erasure_leased(self):
        async def slow_erase(request, user):
            await asyncio.sleep(1.6)
            return {}

        takeover = {}
        with patch.object(erasure, "LEASE_SECONDS", 1), patch.object(erasure, "HEARTBEAT_SECONDS", 0.1), \
                patch.object(erasure, "_erase_user_account_locked", new=slow_erase):
            task = asyncio.ensure_future(erasure.erase_user_account(
                SimpleNamespace(app=self.app), dict(USER)))
            await asyncio.sleep(1.3)  # kilit süresi (1 sn) geçti ama kalp atışı uzattı
            takeover["ok"] = await erasure.acquire_erasure_lease(self.pool, USER["id"], "intruder")
            result = await task
        self.assertFalse(takeover["ok"])
        self.assertEqual(result, {})
        self.assertEqual((await self.job())["status"], "DONE")

    async def test_lost_lease_during_erasure_cancels_and_leaves_job_resumable(self):
        state = {"cancelled": False}

        async def slow_erase(request, user):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            return {}

        with patch.object(erasure, "HEARTBEAT_SECONDS", 0.1), \
                patch.object(erasure, "_erase_user_account_locked", new=slow_erase):
            task = asyncio.ensure_future(erasure.erase_user_account(
                SimpleNamespace(app=self.app), dict(USER)))
            await asyncio.sleep(0.3)
            await self.pool.execute(
                "UPDATE commercial_account_erasure_jobs SET lease_owner = 'intruder', "
                "lease_expires_at = NOW() + interval '1 hour' WHERE user_hash = $1", self.user_hash,
            )
            with self.assertRaises(HTTPException) as ctx:
                await task
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertTrue(state["cancelled"])
        row = await self.pool.fetchrow(
            "SELECT status, lease_owner FROM commercial_account_erasure_jobs WHERE user_hash = $1", self.user_hash,
        )
        self.assertEqual((row["status"], row["lease_owner"]), ("RUNNING", "intruder"))

    async def test_operator_report_shows_unfinished_without_identifiers(self):
        await self.insert_expired_job()
        await self.pool.execute(
            "INSERT INTO commercial_account_erasure_jobs (user_hash, status, attempts) VALUES ('f' || repeat('0', 63), 'DONE', 1)"
        )
        report = await erasure.stale_erasure_report(self.pool)
        self.assertEqual(len(report), 1)
        text = erasure.format_stale_report(report)
        self.assertIn(self.user_hash[:12], text)
        self.assertNotIn(USER["id"], text)
        self.assertNotIn(USER["email"], text)
        self.assertTrue(report[0]["resumable"])
        self.assertIsInstance(datetime.fromisoformat(report[0]["updated_at"]), datetime)


    async def test_retry_budget_is_exhausted_and_left_for_manual_review(self):
        await self.pool.execute(
            """INSERT INTO commercial_account_erasure_jobs
               (user_hash, status, attempts, updated_at)
               VALUES ($1, 'FAILED', $2, NOW())""",
            self.user_hash, erasure.MAX_SWEEP_ATTEMPTS,
        )
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()) as gate, \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results, [])
        gate.assert_not_awaited()
        erase.assert_not_awaited()
        report = await erasure.stale_erasure_report(self.pool)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["attempts"], erasure.MAX_SWEEP_ATTEMPTS)

    async def test_auth_email_is_read_from_security_document(self):
        # Üretimde e-posta commercial_auth_users.security JSON içindedir; ayrı bir sütun yoktur.
        await self.insert_expired_job()
        self.assertTrue(await erasure._matches_auth_record(self.pool, dict(USER)))
        self.assertTrue(await erasure._matches_auth_record(self.pool, {**USER, "email": USER["email"].upper()}))
        self.assertFalse(await erasure._matches_auth_record(self.pool, {**USER, "id": "missing-user"}))

    async def test_resume_finds_user_by_hash_without_any_stored_id(self):
        await self.insert_expired_job()
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(return_value={})) as erase:
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results[0]["outcome"], "tamamlandı")
        self.assertEqual(erase.await_args.args[1]["id"], USER["id"])

    async def test_hash_resolves_only_the_owning_user(self):
        other = {"id": "sweep-pg-other", "email": "sweep-pg-other@example.test", "role": "CUSTOMER"}
        await self.pool.execute(
            "INSERT INTO commercial_auth_users (user_id, security) VALUES ($1, jsonb_build_object('email', $2::text))",
            other["id"], other["email"],
        )
        await self.insert_expired_job()
        app = self.make_app(self.pool)
        app.state.v22_commercial["state"]["users"].append(dict(other))
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(return_value={})) as erase:
            await erasure.sweep_stale_erasures(app)
        self.assertEqual(erase.await_count, 1)
        self.assertEqual(erase.await_args.args[1]["id"], USER["id"])

    async def test_missing_auth_record_is_manual_and_job_untouched(self):
        await self.insert_expired_job()
        await self.pool.execute("DELETE FROM commercial_auth_users WHERE user_id = $1", USER["id"])
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()) as gate, \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        gate.assert_not_awaited()
        erase.assert_not_awaited()
        self.assertEqual((await self.job())["status"], "RUNNING")

    async def test_email_mismatch_in_auth_store_blocks_resume(self):
        await self.insert_expired_job()
        await self.pool.execute(
            "UPDATE commercial_auth_users SET security = jsonb_set(security, '{email}', to_jsonb('changed@example.test'::text)) WHERE user_id = $1",
            USER["id"],
        )
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()) as gate, \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(self.app)
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        gate.assert_not_awaited()
        erase.assert_not_awaited()
        row = await self.job()
        # Manuel inceleme işi değiştirmez: durum ve sürdürme kimliği olduğu gibi kalır.
        self.assertEqual(row["status"], "RUNNING")


if __name__ == "__main__":
    unittest.main()

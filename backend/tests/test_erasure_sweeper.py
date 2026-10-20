"""Kesintiye uğramış hesap silmelerini süpürme ve kilit kalp atışı (veritabanı gerektirmez, sahte havuz kullanır)."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi import HTTPException  # noqa: E402

from app import account_erasure as erasure  # noqa: E402
from app import account_exposure  # noqa: E402,F401

USER = {"id": "sweep-user-1", "email": "sweep-user-1@example.test", "role": "CUSTOMER"}


class FakePool:
    """Yalnızca kullanılan çağrıları kaydeden sahte havuz."""

    def __init__(self, *, fetch_rows=(), fetchrow_value=None, fetchrow_error=None, auth_row=None):
        self.fetch_rows = list(fetch_rows)
        self.fetchrow_value = fetchrow_value
        self.fetchrow_error = fetchrow_error
        # Kimlik deposu satırı: varsayılan olarak kullanıcının kendi e-postası (eşleşen kayıt).
        self.auth_row = auth_row if auth_row is not None else {"user_id": USER["id"], "email": USER["email"]}
        self.executed = []
        self.fetchrow_calls = []

    async def fetch(self, sql, *args):
        return self.fetch_rows

    async def fetchrow(self, sql, *args):
        if "commercial_auth_users" in sql:
            if isinstance(self.auth_row, Exception):
                raise self.auth_row
            return self.auth_row
        self.fetchrow_calls.append(args)
        if self.fetchrow_error is not None:
            raise self.fetchrow_error
        return self.fetchrow_value

    async def execute(self, sql, *args):
        self.executed.append(args)


def app_with(pool, users=()):
    return SimpleNamespace(state=SimpleNamespace(
        db_pool=pool, v22_commercial={"state": {"users": list(users)}, "lock": None},
    ))


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_has_no_raw_id_or_email(self):
        rows = [{"hash_prefix": "abcdef123456", "status": "FAILED", "attempts": 2,
                 "lease_expired": True, "updated_at": __import__("datetime").datetime(2026, 10, 20),
                 "resumable": True}]
        pool = FakePool()
        pool.fetch = AsyncMock(return_value=[dict(r) for r in rows])
        report = await erasure.stale_erasure_report(pool)
        text = erasure.format_stale_report(report)
        self.assertIn("abcdef123456", text)
        self.assertNotIn(USER["id"], text)
        self.assertNotIn("@", text)
        self.assertNotIn("resume_user_id", text)

    def test_empty_report(self):
        self.assertEqual(erasure.format_stale_report([]), "Tamamlanmamış hesap silme işi yok.")


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_work_result_when_lease_is_kept(self):
        pool = FakePool(fetchrow_value={"lease_owner": "me"})

        async def work():
            return {"ok": True}

        with patch.object(erasure, "HEARTBEAT_SECONDS", 0.01):
            result = await erasure._run_with_heartbeat(pool, USER["id"], "me", work)
        self.assertEqual(result, {"ok": True})

    async def test_lost_lease_cancels_work_and_fails_closed(self):
        pool = FakePool(fetchrow_value=None)  # sahiplik kaybedildi
        finished = {"cancelled": False}

        async def work():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                finished["cancelled"] = True
                raise

        with patch.object(erasure, "HEARTBEAT_SECONDS", 0.01):
            with self.assertRaises(HTTPException) as ctx:
                await erasure._run_with_heartbeat(pool, USER["id"], "me", work)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertTrue(finished["cancelled"])

    async def test_renewal_database_error_fails_closed(self):
        pool = FakePool(fetchrow_error=RuntimeError("db down"))

        async def work():
            await asyncio.sleep(5)

        with patch.object(erasure, "HEARTBEAT_SECONDS", 0.01):
            with self.assertRaises(HTTPException) as ctx:
                await erasure._run_with_heartbeat(pool, USER["id"], "me", work)
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_work_error_propagates_unchanged(self):
        pool = FakePool(fetchrow_value={"lease_owner": "me"})

        async def work():
            raise ValueError("erase failed")

        with patch.object(erasure, "HEARTBEAT_SECONDS", 0.01):
            with self.assertRaises(ValueError):
                await erasure._run_with_heartbeat(pool, USER["id"], "me", work)


class SweeperTests(unittest.IsolatedAsyncioTestCase):
    def job_row(self):
        return {"user_hash": erasure.user_hash(USER["id"])}

    async def test_missing_user_record_is_left_for_manual_review(self):
        pool = FakePool(fetch_rows=[self.job_row()])
        app = app_with(pool, users=[])
        with patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(app)
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        erase.assert_not_awaited()
        self.assertEqual(pool.fetchrow_calls, [])  # kilit bile alınmadı

    async def test_exposure_block_keeps_job_failed_and_does_not_erase(self):
        pool = FakePool(fetch_rows=[self.job_row()], fetchrow_value={"lease_owner": "x"})
        app = app_with(pool, users=[dict(USER)])
        gate = AsyncMock(side_effect=HTTPException(409, "Açık pozisyon"))
        with patch("app.account_exposure.gate_before_erasure", new=gate), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(app)
        self.assertEqual(results[0]["outcome"], "durduruldu")
        self.assertEqual(results[0]["reason"], "HTTP 409")
        erase.assert_not_awaited()
        done_flags = [args[2] for args in pool.executed]
        self.assertEqual(done_flags, [False])  # FAILED olarak bırakılır, DONE değil

    async def test_success_releases_as_done(self):
        pool = FakePool(fetch_rows=[self.job_row()], fetchrow_value={"lease_owner": "x"})
        app = app_with(pool, users=[dict(USER)])
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(return_value={})):
            results = await erasure.sweep_stale_erasures(app)
        self.assertEqual(results[0]["outcome"], "tamamlandı")
        self.assertEqual([args[2] for args in pool.executed], [True])

    async def test_unexpected_error_is_not_marked_done(self):
        pool = FakePool(fetch_rows=[self.job_row()], fetchrow_value={"lease_owner": "x"})
        app = app_with(pool, users=[dict(USER)])
        with patch("app.account_exposure.gate_before_erasure", new=AsyncMock()), \
                patch.object(erasure, "_erase_user_account_locked", new=AsyncMock(side_effect=RuntimeError("boom"))):
            results = await erasure.sweep_stale_erasures(app)
        self.assertEqual(results[0]["outcome"], "hata")
        self.assertEqual([args[2] for args in pool.executed], [False])

    async def test_lock_held_elsewhere_is_skipped(self):
        pool = FakePool(fetch_rows=[self.job_row()], fetchrow_value=None)
        app = app_with(pool, users=[dict(USER)])
        gate = AsyncMock()
        with patch("app.account_exposure.gate_before_erasure", new=gate):
            results = await erasure.sweep_stale_erasures(app)
        self.assertEqual(results[0]["outcome"], "atlandı")
        gate.assert_not_awaited()
        self.assertEqual(pool.executed, [])


class AuthMatchTests(unittest.IsolatedAsyncioTestCase):
    def job_row(self):
        return {"user_hash": erasure.user_hash(USER["id"])}

    async def test_email_mismatch_is_manual_and_takes_no_lease(self):
        pool = FakePool(fetch_rows=[self.job_row()], fetchrow_value={"lease_owner": "x"},
                        auth_row={"user_id": USER["id"], "email": "someone-else@example.test"})
        with patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(app_with(pool, users=[dict(USER)]))
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        erase.assert_not_awaited()
        self.assertEqual(pool.fetchrow_calls, [])

    async def test_missing_auth_row_is_manual(self):
        pool = FakePool(fetch_rows=[self.job_row()], auth_row=None)
        pool.auth_row = None
        with patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(app_with(pool, users=[dict(USER)]))
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        erase.assert_not_awaited()

    async def test_auth_store_error_is_manual_not_erased(self):
        pool = FakePool(fetch_rows=[self.job_row()], auth_row=RuntimeError("store down"))
        with patch.object(erasure, "_erase_user_account_locked", new=AsyncMock()) as erase:
            results = await erasure.sweep_stale_erasures(app_with(pool, users=[dict(USER)]))
        self.assertEqual(results[0]["outcome"], "manuel_inceleme")
        erase.assert_not_awaited()


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_disabled_by_default(self):
        from app import erasure_sweeper_worker as worker

        self.assertFalse(worker.sweeper_enabled({}))
        self.assertFalse(worker.sweeper_enabled({"ERASURE_SWEEPER_ENABLED": "no"}))
        self.assertTrue(worker.sweeper_enabled({"ERASURE_SWEEPER_ENABLED": "true"}))

    def test_interval_has_a_floor_and_default(self):
        from app import erasure_sweeper_worker as worker

        self.assertEqual(worker.sweeper_interval({}), 300)
        self.assertEqual(worker.sweeper_interval({"ERASURE_SWEEPER_INTERVAL_SECONDS": "5"}), 60)
        self.assertEqual(worker.sweeper_interval({"ERASURE_SWEEPER_INTERVAL_SECONDS": "x"}), 300)

    async def test_loop_survives_iteration_errors(self):
        from app import erasure_sweeper_worker as worker

        calls = []

        async def flaky(application):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return []

        with patch("app.account_erasure.sweep_stale_erasures", new=flaky):
            await worker.erasure_sweeper_loop(SimpleNamespace(), interval=0, iterations=2)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

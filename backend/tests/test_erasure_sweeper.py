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

    def __init__(self, *, fetch_rows=(), fetchrow_value=None, fetchrow_error=None):
        self.fetch_rows = list(fetch_rows)
        self.fetchrow_value = fetchrow_value
        self.fetchrow_error = fetchrow_error
        self.executed = []
        self.fetchrow_calls = []

    async def fetch(self, sql, *args):
        return self.fetch_rows

    async def fetchrow(self, sql, *args):
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
        return {"user_hash": erasure.user_hash(USER["id"]), "resume_user_id": USER["id"]}

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


if __name__ == "__main__":
    unittest.main()

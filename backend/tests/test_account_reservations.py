"""Kalıcı (SQLite) hesap rezervasyonu — AĞ YOK. Coin + risk rezervasyonu, yaşam döngüsü, çökme sonrası mutabakat."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import account_reservations as ar
from app import v25_execution as live
import test_v25_risk_gate_repairs as gate_tests
import test_v25_auto_symbol_scope as scope_tests

BACKEND = Path(__file__).resolve().parents[1]


class Registry(unittest.TestCase):
    def test_same_coin_cannot_be_reserved_twice_and_is_freed_on_release(self):
        token = ar.reserve("u1", "MANUAL", "btcusdt", 2.0)
        with self.assertRaises(ar.ReservationConflict) as ctx:
            ar.reserve("u1", "GRID", "BTCUSDT", 1.0)
        self.assertEqual(ctx.exception.kind, "symbol")
        ar.reserve("u1", "GRID", "ETHUSDT", 1.0)                      # başka coin serbest
        ar.reserve("u2", "GRID", "BTCUSDT", 1.0)                      # başka kullanıcı bağımsız
        ar.release(token)
        ar.reserve("u1", "GRID", "BTCUSDT", 1.0)

    def test_budget_is_shared_between_concurrent_entries_but_a_lone_entry_is_unchanged(self):
        ar.reserve("u1", "MANUAL", "BTCUSDT", 8.0, budget_left=10.0)         # tek başına: yalnız mevcut kapılar
        with self.assertRaises(ar.ReservationConflict) as ctx:
            ar.reserve("u1", "AUTO", "ETHUSDT", 3.0, budget_left=10.0)
        self.assertEqual(ctx.exception.kind, "risk")
        ar.reserve("u1", "AUTO", "ETHUSDT", 2.0, budget_left=10.0)
        self.assertEqual(ar.others_worst_loss("u1"), 10.0)
        ar.reserve("lone", "MANUAL", "BTCUSDT", 99.0, budget_left=10.0)       # başkası yok → davranış değişmez

    def test_running_grid_risk_counts_as_committed(self):
        with self.assertRaises(ar.ReservationConflict):
            ar.reserve("u1", "MANUAL", "BTCUSDT", 4.0, budget_left=10.0, committed=7.0)
        ar.reserve("u1", "MANUAL", "BTCUSDT", 3.0, budget_left=10.0, committed=7.0)

    def test_parallel_threads_exactly_one_wins(self):
        wins, errors = [], []

        def worker(i):
            try:
                wins.append(ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0))
            except ar.ReservationConflict:
                errors.append(i)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(64)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual((len(wins), len(errors)), (1, 63))

    def test_parallel_threads_on_different_coins_never_exceed_the_budget(self):
        ok = []

        def worker(i):
            try:
                ar.reserve("u1", "AUTO", f"C{i}USDT", 3.0, budget_left=10.0)
                ok.append(i)
            except ar.ReservationConflict:
                pass
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(len(ok), 3)
        self.assertLessEqual(ar.others_worst_loss("u1"), 10.0)

    def test_hold_always_releases(self):
        with self.assertRaises(RuntimeError), ar.hold("u1", "MANUAL", "BTCUSDT", 1.0):
            raise RuntimeError("x")
        self.assertEqual(ar.snapshot("u1"), [])

    def test_update_changes_reserved_loss(self):
        token = ar.reserve("u1", "GRID", "BTCUSDT")
        self.assertTrue(ar.update(token, 6.0))
        self.assertEqual((ar.others_worst_loss("u1"), ar.others_worst_loss("u1", exclude=token)), (6.0, 0.0))


class StateMachine(unittest.TestCase):
    def test_a_reservation_can_only_be_used_once(self):
        token = ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
        self.assertTrue(ar.mark_submitted(token, "cid-1"))
        self.assertTrue(ar.mark_submitted(token, "cid-1"))                      # idempotent (find_order yolu tekrar çağırır)
        self.assertTrue(ar.consume(token))
        self.assertFalse(ar.consume(token))                                     # ikinci kullanım
        self.assertFalse(ar.release(token))                                     # kapanmış satır yeniden açılmaz
        self.assertFalse(ar.mark_submitted(token, "cid-2"))
        self.assertEqual(ar.get(token)["state"], ar.CONSUMED)

    def test_database_itself_refuses_two_active_rows_for_one_coin(self):
        ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
        with self.assertRaises(sqlite3.IntegrityError), ar.get_store().tx() as db:
            db.execute("INSERT INTO reservations(token,owner,account,kind,symbol,state,created_at,updated_at,expires_at) "
                       "VALUES('x','u1','LIVE','GRID','BTCUSDT','RESERVED',1,1,9e12)")

    def test_unsent_reservation_expires_but_a_submitted_one_never_does(self):
        gone = ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0, ttl=-1)
        kept = ar.reserve("u1", "MANUAL", "ETHUSDT", 1.0, ttl=-1)
        # ETHUSDT için reserve çağrısı BTCUSDT'nin süresi dolmuş RESERVED satırını temizledi
        self.assertEqual(ar.get(gone)["state"], ar.RELEASED)
        ar.mark_submitted(kept, "cid")
        ar.get_store().db.execute("UPDATE reservations SET expires_at=1 WHERE token=?", (kept,))
        ar.reserve("u1", "MANUAL", "SOLUSDT", 1.0)                              # temizlik tetiklenir
        self.assertEqual(ar.get(kept)["state"], ar.SUBMITTED)
        with self.assertRaises(ar.ReservationConflict):
            ar.reserve("u1", "MANUAL", "ETHUSDT", 1.0)                          # emir gitmiş olabilir: coin hâlâ bloklu

    def test_plan_status_decides_the_end_state(self):
        for plan_status, expected in [("DOLUM BEKLİYOR", ar.SUBMITTED), ("İPTAL", ar.RELEASED), ("KORUMA AKTİF", ar.CONSUMED),
                                      ("KAPANDI", ar.CONSUMED), (None, ar.SUBMITTED)]:
            with self.subTest(plan_status=plan_status):
                token = ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
                ar.mark_submitted(token, f"cid-{plan_status}")
                self.assertEqual(ar.settle_by_plan_status(token, plan_status), expected)
                ar.release(token)
        token = ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)                      # emir hiç gitmediyse serbest
        self.assertEqual(ar.settle_by_plan_status(token, None), ar.RELEASED)

    def test_failure_to_write_means_fail_closed(self):
        ar.get_store().db.close()
        with self.assertRaises(ar.ReservationUnavailable):
            ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
        with patch.object(ar, "get_store", side_effect=ar.ReservationUnavailable("açılamadı")), self.assertRaises(ar.ReservationUnavailable):
            ar.snapshot("u1")


class CrashAndRestart(unittest.TestCase):
    """Süreç çökmesi/yeniden başlatma: dosya tabanlı defter, farklı EPOCH ile yeniden açılır."""

    def _reopen_as_new_process(self, path):
        ar.use_store(ar.ReservationStore(path))
        return patch.object(ar, "EPOCH", "restarted-process")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "reservations_live.sqlite3")
        ar.use_store(ar.ReservationStore(self.path))

    def tearDown(self):
        self.dir.cleanup()

    def test_order_sent_but_record_not_updated_blocks_new_entries_until_reconciled(self):
        token = ar.reserve("u1", "MANUAL", "BTCUSDT", 2.0)
        ar.mark_submitted(token, "kt-entry-1")                                   # emir gitti, ardından süreç çöktü
        with self._reopen_as_new_process(self.path):
            self.assertEqual([r["client_order_id"] for r in ar.unreconciled("u1")], ["kt-entry-1"])
            with self.assertRaises(ar.ReservationConflict) as ctx:
                ar.reserve("u1", "AUTO", "ETHUSDT", 1.0)                         # başka coin bile: mutabakat bitene kadar giriş yok
            self.assertEqual(ctx.exception.kind, "reconcile")
            ar.reserve("u2", "AUTO", "ETHUSDT", 1.0)                             # başka kullanıcı etkilenmez

    def test_reconcile_outcomes_after_restart(self):
        for outcome, state, coin_free in [("PENDING", ar.SUBMITTED, False), ("FILLED", ar.CONSUMED, True), ("GONE", ar.RELEASED, True)]:
            with self.subTest(outcome=outcome):
                token = ar.reserve("u1", "MANUAL", "BTCUSDT", 2.0)
                ar.mark_submitted(token, f"kt-{outcome}")
                with self._reopen_as_new_process(self.path):
                    self.assertEqual(ar.resolve(token, outcome), state)
                    self.assertEqual(ar.unreconciled("u1"), [])                  # engel kalktı
                    if coin_free:
                        ar.release(ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0))
                    else:
                        with self.assertRaises(ar.ReservationConflict):          # bekleyen LIMIT coini tutmaya devam eder
                            ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
                ar.release(token)

    def test_unresolvable_exchange_keeps_the_block(self):
        token = ar.reserve("u1", "MANUAL", "BTCUSDT", 2.0)
        ar.mark_submitted(token, "kt-x")
        with self._reopen_as_new_process(self.path):
            self.assertEqual(ar.resolve(token, "UNKNOWN"), ar.SUBMITTED)         # Binance'e ulaşılamadı
            with self.assertRaises(ar.ReservationConflict):
                ar.reserve("u1", "AUTO", "ETHUSDT", 1.0)

    def test_unsent_reservation_from_dead_process_frees_after_its_ttl(self):
        token = ar.reserve("u1", "MANUAL", "BTCUSDT", 2.0)                        # SUBMITTED yazılmadan çöküş: emir gitmedi
        with self._reopen_as_new_process(self.path):
            with self.assertRaises(ar.ReservationConflict):                      # taze satır başka canlı süreçten de olabilir: süreyi bekler
                ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
            ar.get_store().db.execute("UPDATE reservations SET expires_at=1 WHERE token=?", (token,))
            ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0)
            self.assertEqual(ar.get(token)["state"], ar.RELEASED)

    def test_sweep_uses_plan_status_and_leaves_unknowns_for_the_exchange(self):
        a = ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0); ar.mark_submitted(a, "kt-a")
        b = ar.reserve("u1", "MANUAL", "ETHUSDT", 1.0); ar.mark_submitted(b, "kt-b")
        c = ar.reserve("u1", "MANUAL", "SOLUSDT", 1.0); ar.mark_submitted(c, "kt-c")
        with self._reopen_as_new_process(self.path):
            left = ar.sweep("u1", {"kt-a": "KORUMA AKTİF", "kt-b": "DOLUM BEKLİYOR"}.get)
            self.assertEqual([r["client_order_id"] for r in left], ["kt-c"])
            self.assertEqual((ar.get(a)["state"], ar.get(b)["state"]), (ar.CONSUMED, ar.SUBMITTED))
            self.assertEqual([r["client_order_id"] for r in ar.unreconciled("u1")], ["kt-c"])

    def test_two_real_processes_race_on_the_same_database_file(self):
        code = ("import sys; sys.path.insert(0, %r); from app import account_reservations as ar; "
                "ar.use_store(ar.ReservationStore(sys.argv[1]))\n"
                "try:\n    ar.reserve('u1','MANUAL','BTCUSDT',1.0); print('WIN')\n"
                "except ar.ReservationConflict:\n    print('LOSE')\n") % str(BACKEND)
        procs = [subprocess.Popen([sys.executable, "-c", code, self.path], stdout=subprocess.PIPE, text=True, cwd=BACKEND, env=os.environ.copy())
                 for _ in range(6)]
        results = [p.communicate(timeout=60)[0].strip().splitlines()[-1] for p in procs]
        self.assertEqual(sorted(results), ["LOSE"] * 5 + ["WIN"])


class DemoSeparation(unittest.TestCase):
    def test_demo_code_never_touches_the_live_reservation_book(self):
        for name in ("binance_demo.py", "grid_live.py"):
            self.assertNotIn("account_reservations", (BACKEND / "app" / name).read_text(encoding="utf-8"), name)
        self.assertTrue(all(r["account"] == "LIVE" for r in [ar.get(ar.reserve("u1", "MANUAL", "BTCUSDT", 1.0))]))


class GridStartEndpoint(unittest.TestCase):
    def _call(self, symbols, delay=0.05, fail=False):
        state = {}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)), state=SimpleNamespace(member={"id": "u1"}))

        async def inner(req, body, token):
            await asyncio.sleep(delay)
            if fail:
                raise HTTPException(409, "red")
            return {"ok": True, "symbol": body.symbol}

        async def run():
            bodies = [SimpleNamespace(symbol=s, preview=False) for s in symbols]
            return await asyncio.gather(*(live.v25_grid_live_start(request, b) for b in bodies), return_exceptions=True)
        with patch.object(live, "execution_owner", lambda r: {"id": "u1"}), patch.object(live, "_grid_live_start", inner):
            return asyncio.run(run())

    def test_two_starts_on_the_same_coin_only_one_runs(self):
        results = self._call(["BTCUSDT", "BTCUSDT"])
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual([r.status_code for r in results if isinstance(r, HTTPException)], [409])
        self.assertEqual([r["state"] for r in ar.rows("u1", states=(ar.RESERVED, ar.SUBMITTED, ar.CONSUMED, ar.RELEASED))], [ar.CONSUMED])

    def test_different_coins_both_start_and_failure_releases(self):
        self.assertTrue(all(isinstance(r, dict) for r in self._call(["BTCUSDT", "ETHUSDT"])))
        ar.get_store().db.execute("DELETE FROM reservations")
        results = self._call(["BTCUSDT"], fail=True)
        self.assertIsInstance(results[0], HTTPException)
        self.assertEqual(ar.snapshot("u1"), [])

    def test_unreadable_book_refuses_the_start(self):
        with patch.object(ar, "get_store", side_effect=ar.ReservationUnavailable("açılamadı")):
            results = self._call(["BTCUSDT"])
        self.assertEqual(results[0].status_code, 503)


class ManualEntry(gate_tests.LiquidationGateTests):
    """Giriş yolu (V25_AUTO kaynağıyla): rezervasyon alınır, emirden önce SUBMITTED yazılır, çakışmada emir gitmez."""
    test_entry_with_unsafe_liquidation_buffer_never_reaches_submit = None
    test_safe_entry_records_liquidation_proof_without_rewriting_initial_risk = None

    def _execute(self, submit_effect=None, seed=None, budget=None, positions=None, daily_limit=None, ledger_net=None, plans=None, runner=None, source="V25_AUTO", extra_patches=None, strategy_result=None, original_patches=None):
        application, state = scope_tests.application_for(["BTCUSDT"])
        state["policy"]["max_leverage"] = 50
        if daily_limit is not None:
            state["policy"]["daily_loss_limit"] = daily_limit
        if plans:
            state["plans"] = plans
        if runner is not None:
            application.state.live_grid_runner = runner
        if ledger_net is not None:
            from datetime import datetime, timezone
            from app import daily_ledger
            state[daily_ledger.KEY] = {"day": datetime.now(timezone.utc).date().isoformat(), "fetched_at": time.time(), "rows": [],
                                       "totals": {"net": ledger_net}, "source": "BINANCE_INCOME"}
        spec = {**scope_tests.spec_for("BTCUSDT"), "quantity": "2.5", "notional_usdt": 250, "margin_usdt": 5, "leverage": 50,
                "stop_loss": "99.2", "estimated_stop_loss_usdt": 2}
        client = SimpleNamespace(signed=AsyncMock(return_value=gate_tests.maintenance_brackets()))
        seen = {}

        async def fake_submit(cl, sp, client_id, **kwargs):
            callback = kwargs.get("on_submission_started")
            if callback:
                callback()
            seen["rows_at_submit"] = ar.snapshot(owner)
            return submit_effect() if submit_effect else {"orderId": 42, "status": "FILLED"}
        submit = AsyncMock(side_effect=fake_submit)
        owner = live.live_reservation_owner(state, None)
        if seed:
            ar.reserve(owner, *seed)
        body = live.LiveOrderRequest(symbol="BTCUSDT", direction="LONG", margin_usdt=5, leverage=50, stop_loss=99.2, tp1=102, tp2=104, tp3=106)
        patches = dict(
            readiness_for=lambda *a, **k: {"ready": True}, client_for_with_credentials=lambda *a, **k: client,
            account_snapshot=AsyncMock(return_value=gate_tests.snapshot(multi_assets_mode=False, positions=positions or [])), spread_bps=AsyncMock(return_value=1),
            build_live_spec=AsyncMock(return_value=spec), set_live_isolated_margin=AsyncMock(return_value="ISOLATED"),
            apply_live_verified_leverage=AsyncMock(return_value={"applied_leverage": 50, "margin_type": "isolated"}),
            fresh_auto_submission_credentials=AsyncMock(return_value=scope_tests.CREDENTIALS), submit_entry=submit,
            install_protection=AsyncMock(), persist_state=lambda *a: None)
        if budget is not None:
            patches["shared_daily_budget"] = lambda *a, **k: budget
        patches.update(extra_patches or {})
        from app import original_v2_live as _o
        import contextlib
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.multiple(live, **patches))
            if original_patches:
                stack.enter_context(patch.multiple(_o, **original_patches))
            try:
                result = asyncio.run(live.execute_live_order(application, body, source=source, credentials=scope_tests.CREDENTIALS, strategy_result=strategy_result))
            except live.HTTPException as exc:
                result = exc
        return result, submit, state, owner, seen

    def _all(self, owner):
        return [r["state"] for r in ar.rows(owner, states=(ar.RESERVED, ar.SUBMITTED, ar.CONSUMED, ar.RELEASED))]

    def test_submitted_is_written_before_the_order_and_filled_market_entry_is_consumed(self):
        result, submit, state, owner, seen = self._execute()
        self.assertEqual(submit.await_count, 1)
        row = seen["rows_at_submit"][0]
        self.assertEqual((row["state"], row["symbol"], row["kind"]), (ar.SUBMITTED, "BTCUSDT", "V25_AUTO"))
        self.assertTrue(row["client_order_id"])
        self.assertEqual(self._all(owner), [ar.CONSUMED])
        self.assertEqual(ar.snapshot(owner), [])                                 # aktif rezervasyon kalmadı: risk açık pozisyon olarak görülür

    def test_resting_limit_entry_keeps_its_reservation_until_it_ends(self):
        result, submit, state, owner, seen = self._execute(submit_effect=lambda: {"orderId": 7, "status": "NEW"})
        self.assertEqual(self._all(owner), [ar.SUBMITTED])                       # bekleyen emir coini ve riski tutar
        with self.assertRaises(ar.ReservationConflict):
            ar.reserve(owner, "MANUAL", "BTCUSDT", 1.0)
        plan = next(iter(state["plans"].values()))
        for status, expected in [("DOLUM BEKLİYOR", [ar.SUBMITTED]), ("İPTAL", [ar.RELEASED]), ("KORUMA AKTİF", [ar.CONSUMED])]:
            with self.subTest(plan_status=status):
                ar.get_store().db.execute("UPDATE reservations SET state='SUBMITTED'")
                plan["status"] = status
                asyncio.run(live.reconcile_account_reservations(SimpleNamespace(), state, owner))
                self.assertEqual(self._all(owner), expected)

    def test_submit_that_raises_after_the_send_marker_keeps_the_block(self):
        def explode():
            raise live.LiveExchangeError("yanıt yok", http_status=502, unknown_execution=True)
        result, submit, state, owner, seen = self._execute(submit_effect=explode)
        self.assertEqual(self._all(owner), [ar.SUBMITTED])                       # emir gitmiş olabilir: mutabakata kadar bloklu

    def test_rejection_before_send_releases_the_reservation(self):
        result, submit, state, owner, seen = self._execute(seed=("GRID", "BTCUSDT", 1.0))
        self.assertEqual(result.status_code, 409)
        owner_rows = ar.rows(owner)
        self.assertEqual([r["kind"] for r in owner_rows], ["GRID"])              # yalnız tohum satırı; yeni giriş satır bırakmadı

    def test_concurrent_entry_on_same_coin_is_refused_before_submit(self):
        result, submit, state, owner, _ = self._execute(seed=("GRID", "BTCUSDT", 1.0))
        self.assertIsInstance(result, live.HTTPException)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(submit.await_count, 0)

    def test_budget_already_used_by_another_entry_refuses_this_one(self):
        result, submit, state, owner, _ = self._execute(seed=("AUTO", "ETHUSDT", 2.0), budget={"budget_left": 3.0, "committed": 0.0})
        self.assertIsInstance(result, live.HTTPException)
        self.assertEqual(submit.await_count, 0)

    def test_unreconciled_order_from_before_restart_blocks_entry_when_exchange_is_unreachable(self):
        application, state = scope_tests.application_for(["BTCUSDT"])
        owner = live.live_reservation_owner(state, None)
        token = ar.reserve(owner, "MANUAL", "SOLUSDT", 2.0)
        ar.mark_submitted(token, "kt-old")
        with patch.object(ar, "EPOCH", "new-process"), patch.object(live, "find_order", AsyncMock(side_effect=live.LiveExchangeError("ağ", http_status=502))):
            result, submit, _, _, _ = self._execute()
            self.assertEqual((result.status_code, submit.await_count), (503, 0))
        with patch.object(ar, "EPOCH", "new-process"), patch.object(live, "find_order", AsyncMock(return_value=None)):
            result, submit, _, _, _ = self._execute()       # Binance: emir yok → serbest bırakılır, giriş devam eder
            self.assertEqual(submit.await_count, 1)
        self.assertEqual(ar.get(token)["state"], ar.RELEASED)

    def test_unreadable_reservation_book_refuses_entry(self):
        with patch.object(ar, "get_store", side_effect=ar.ReservationUnavailable("açılamadı")):
            result, submit, *_ = self._execute()
        self.assertEqual((result.status_code, submit.await_count), (503, 0))


class ReconcileSafety(unittest.TestCase):
    """Yeniden başlatma sonrası mutabakat: yalnız KESİN kanıtla serbest bırakır; belirsizlikte satır kalır ve giriş durur."""

    def submitted_row(self, client_id="kt-1", symbol="BTCUSDT"):
        token = ar.reserve("u1", "MANUAL", symbol, 2.0)
        ar.mark_submitted(token, client_id)
        return token

    def reconcile(self, token, order=None, error=None, snapshot=None, plans=None):
        async def fake_find_order(client, symbol, client_id):
            if error is not None:
                raise error
            return order
        state = {"plans": plans or {}}
        with patch.object(ar, "EPOCH", "restarted"), patch.object(live, "find_order", fake_find_order):
            asyncio.run(live.reconcile_account_reservations(SimpleNamespace(), state, "u1", snapshot))
            return ar.get(token)["state"], bool(ar.unreconciled("u1"))

    def test_exchange_errors_never_release_and_never_raise(self):
        for error in (live.LiveExchangeError("502", http_status=502), live.LiveExchangeError("t", timed_out=True),
                      live.LiveExchangeError("?", unknown_execution=True), asyncio.TimeoutError(), ConnectionError("down"), RuntimeError("x")):
            with self.subTest(error=type(error).__name__):
                token = self.submitted_row(client_id=f"kt-{type(error).__name__}")
                self.assertEqual(self.reconcile(token, error=error), (ar.SUBMITTED, True))
                ar.release(token)

    def test_ambiguous_order_states_keep_the_row(self):
        for order in ({"status": "PENDING_CANCEL", "executedQty": "0"}, {"status": "SOMETHING_NEW", "executedQty": "0"},
                      {"status": "CANCELED", "executedQty": "garbage"}, {"status": "", "executedQty": "0"}, {}):
            with self.subTest(order=order):
                token = self.submitted_row()
                self.assertEqual(self.reconcile(token, order=order), (ar.SUBMITTED, True))
                ar.release(token)

    def test_definite_exchange_answers(self):
        cases = [({"status": "NEW", "executedQty": "0"}, ar.SUBMITTED, False), ({"status": "PARTIALLY_FILLED", "executedQty": "1"}, ar.SUBMITTED, False),
                 ({"status": "FILLED", "executedQty": "2"}, ar.CONSUMED, False), ({"status": "CANCELED", "executedQty": "0"}, ar.RELEASED, False),
                 ({"status": "EXPIRED", "executedQty": "0"}, ar.RELEASED, False), ({"status": "REJECTED", "executedQty": "0"}, ar.RELEASED, False),
                 ({"status": "CANCELED", "executedQty": "0.5"}, ar.CONSUMED, False)]
        for order, state, blocked in cases:
            with self.subTest(order=order):
                token = self.submitted_row()
                self.assertEqual(self.reconcile(token, order=order), (state, blocked))
                ar.release(token)

    def test_unknown_to_exchange_is_released_only_when_the_account_shows_nothing_on_that_coin(self):
        token = self.submitted_row()
        self.assertEqual(self.reconcile(token, order=None, snapshot={"positions": [], "open_orders": []}), (ar.RELEASED, False))
        for snap in ({"positions": [{"symbol": "BTCUSDT"}], "open_orders": []}, {"positions": [], "open_orders": [{"symbol": "BTCUSDT", "client_order_id": "x"}]}):
            with self.subTest(snap=snap):
                token = self.submitted_row()
                self.assertEqual(self.reconcile(token, order=None, snapshot=snap), (ar.SUBMITTED, True))
                ar.release(token)
        token = self.submitted_row(client_id="kt-live")                           # hesapta bu kimlikle açık emir var → bekliyor, doğrulandı
        snap = {"positions": [], "open_orders": [{"symbol": "BTCUSDT", "client_order_id": "kt-live"}]}
        self.assertEqual(self.reconcile(token, order=None, snapshot=snap), (ar.SUBMITTED, False))

    def test_unrecognised_plan_status_is_never_treated_as_finished(self):
        for status, expected in [("BİLİNMEYEN DURUM", ar.SUBMITTED), ("DOLUM BEKLİYOR", ar.SUBMITTED), ("KORUMA AKTİF", ar.CONSUMED), ("İPTAL", ar.RELEASED)]:
            with self.subTest(status=status):
                token = self.submitted_row()
                plans = {"p": {"entry_client_order_id": "kt-1", "status": status}}
                state, _ = self.reconcile(token, error=RuntimeError("Binance'e gerek olmamalı") if expected != ar.SUBMITTED else None, plans=plans, order={"status": "NEW"})
                self.assertEqual(state, expected)
                ar.release(token)

    def test_a_failing_reconciliation_blocks_entry_but_does_not_lock_live_execution(self):
        manual = ManualEntry("test_concurrent_entry_on_same_coin_is_refused_before_submit")
        application, state = scope_tests.application_for(["BTCUSDT"])
        owner = live.live_reservation_owner(state, None)
        token = ar.reserve(owner, "MANUAL", "SOLUSDT", 2.0)
        ar.mark_submitted(token, "kt-old")
        with patch.object(ar, "EPOCH", "new-process"), patch.object(live, "find_order", AsyncMock(side_effect=asyncio.TimeoutError())):
            result, submit, st, _, _ = manual._execute()
        self.assertEqual((result.status_code, submit.await_count), (503, 0))
        self.assertFalse(live.live_execution_blocked(st))                          # zaman aşımı yürütmeyi kilitlemedi
        self.assertEqual(ar.get(token)["state"], ar.SUBMITTED)


class SharedBudget(unittest.TestCase):
    def test_budget_and_committed_grid_risk(self):
        # Eski (sıkı kapı öncesi) bileşimi sınar; bayrak varsayılan AÇIK olduğundan burada açıkça kapatılır.
        env = patch.dict(os.environ, {live.ACCOUNT_RISK_BUDGET_FLAG: "0"})
        env.start()
        self.addCleanup(env.stop)
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": 4.0})
        app = SimpleNamespace(state=SimpleNamespace(live_grid_runner=runner))
        state = {"policy": {"daily_loss_limit": 10.0}, "events": [], "plans": {}}
        got = live.shared_daily_budget(app, state, SimpleNamespace(state=SimpleNamespace(), headers={}, cookies={}))
        self.assertEqual((got["committed"], got["budget_left"]), (4.0, 10.0))
        self.assertIsNone(live.shared_daily_budget(app, {"policy": {}}, None)["budget_left"])


if __name__ == "__main__":
    unittest.main()

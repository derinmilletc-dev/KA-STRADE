"""Hesap seviyesinde ortak risk ve coin rezervasyonu — AĞ YOK."""
from __future__ import annotations

import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import account_reservations as ar
from app import v25_execution as live
import test_v25_risk_gate_repairs as gate_tests


class Registry(unittest.TestCase):
    def test_same_coin_cannot_be_reserved_twice_and_is_freed_on_release(self):
        state = {}
        token = ar.reserve(state, "MANUAL", "btcusdt", 2.0)
        with self.assertRaises(ar.ReservationConflict) as ctx:
            ar.reserve(state, "GRID", "BTCUSDT", 1.0)
        self.assertEqual(ctx.exception.kind, "symbol")
        ar.reserve(state, "GRID", "ETHUSDT", 1.0)                 # başka coin serbest
        ar.release(state, token)
        ar.reserve(state, "GRID", "BTCUSDT", 1.0)

    def test_budget_is_shared_between_concurrent_entries_but_a_lone_entry_is_unchanged(self):
        state = {}
        ar.reserve(state, "MANUAL", "BTCUSDT", 8.0, budget_left=10.0)         # tek başına: yalnız mevcut kapılar
        with self.assertRaises(ar.ReservationConflict) as ctx:                # ikincisi aynı bütçeyi tekrar kullanamaz
            ar.reserve(state, "AUTO", "ETHUSDT", 3.0, budget_left=10.0)
        self.assertEqual(ctx.exception.kind, "risk")
        ar.reserve(state, "AUTO", "ETHUSDT", 2.0, budget_left=10.0)           # sığıyor
        self.assertEqual(ar.others_worst_loss(state), 10.0)
        lone = {}
        ar.reserve(lone, "MANUAL", "BTCUSDT", 99.0, budget_left=10.0)          # başkası yok → davranış değişmez

    def test_running_grid_risk_counts_as_committed(self):
        state = {}
        with self.assertRaises(ar.ReservationConflict):
            ar.reserve(state, "MANUAL", "BTCUSDT", 4.0, budget_left=10.0, committed=7.0)
        ar.reserve(state, "MANUAL", "BTCUSDT", 3.0, budget_left=10.0, committed=7.0)

    def test_parallel_threads_exactly_one_wins(self):
        state, wins, errors = {}, [], []

        def worker(i):
            try:
                wins.append(ar.reserve(state, "MANUAL", "BTCUSDT", 1.0))
            except ar.ReservationConflict:
                errors.append(i)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual((len(wins), len(errors)), (1, 31))

    def test_forgotten_reservation_expires_and_hold_always_releases(self):
        state = {}
        token = ar.reserve(state, "MANUAL", "BTCUSDT", 1.0)
        state[ar.KEY][token]["at"] = time.monotonic() - ar.STALE_SECONDS - 1
        ar.reserve(state, "GRID", "BTCUSDT", 1.0)
        fresh = {}
        with self.assertRaises(RuntimeError), ar.hold(fresh, "MANUAL", "BTCUSDT", 1.0):
            raise RuntimeError("x")
        self.assertEqual(ar.snapshot(fresh), [])

    def test_update_changes_reserved_loss(self):
        state = {}
        token = ar.reserve(state, "GRID", "BTCUSDT")
        ar.update(state, token, 6.0)
        self.assertEqual(ar.others_worst_loss(state), 6.0)
        self.assertEqual(ar.others_worst_loss(state, exclude=token), 0.0)


class GridStartEndpoint(unittest.TestCase):
    def _call(self, symbols, delay=0.05, fail=False):
        state = {}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
        seen = []

        async def inner(req, body, token):
            seen.append(token)
            await asyncio.sleep(delay)
            if fail:
                raise HTTPException(409, "red")
            return {"ok": True, "symbol": body.symbol}

        async def run():
            bodies = [SimpleNamespace(symbol=s, preview=False) for s in symbols]
            return await asyncio.gather(*(live.v25_grid_live_start(request, b) for b in bodies), return_exceptions=True)
        with patch.object(live, "execution_owner", lambda r: {"id": "u"}), patch.object(live, "_grid_live_start", inner):
            return asyncio.run(run()), state, seen

    def test_two_starts_on_the_same_coin_only_one_runs(self):
        results, state, seen = self._call(["BTCUSDT", "BTCUSDT"])
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertEqual([r.status_code for r in results if isinstance(r, HTTPException)], [409])
        self.assertEqual((len(seen), ar.snapshot(state)), (1, []))

    def test_different_coins_both_start_and_failure_releases(self):
        results, state, _ = self._call(["BTCUSDT", "ETHUSDT"])
        self.assertTrue(all(isinstance(r, dict) for r in results))
        results, state, _ = self._call(["BTCUSDT"], fail=True)
        self.assertEqual(ar.snapshot(state), [])
        self.assertIsInstance(results[0], HTTPException)


class ManualEntry(gate_tests.LiquidationGateTests):
    """Manuel/otomatik giriş yolu: rezervasyon alınır, çakışmada emir gitmez, çıkışta serbest bırakılır."""
    test_entry_with_unsafe_liquidation_buffer_never_reaches_submit = None
    test_safe_entry_records_liquidation_proof_without_rewriting_initial_risk = None

    def _run(self, seed=None):
        captured = {}
        real = ar.reserve

        def spy(state, *args, **kwargs):
            captured["state"], captured["args"], captured["kwargs"] = state, args, kwargs
            if seed:
                real(state, *seed)
            return real(state, *args, **kwargs)
        with patch.object(ar, "reserve", spy):
            result, submit, client, state = self.execute()
        return result, submit, state, captured

    def test_entry_takes_reservation_and_releases_it_afterwards(self):
        result, submit, state, captured = self._run()
        self.assertEqual(captured["args"][:2], ("V25_AUTO", "BTCUSDT"))
        self.assertEqual(ar.snapshot(state), [])
        self.assertTrue(submit.await_count == 1)

    def test_concurrent_entry_on_same_coin_is_refused_before_submit(self):
        result, submit, state, _ = self._run(seed=("GRID", "BTCUSDT", 1.0))
        self.assertIsInstance(result, live.HTTPException)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(submit.await_count, 0)

    def test_budget_already_used_by_another_entry_refuses_this_one(self):
        with patch.object(live, "shared_daily_budget", lambda *a, **k: {"budget_left": 3.0, "committed": 0.0}):
            result, submit, state, _ = self._run(seed=("AUTO", "ETHUSDT", 2.0))
        self.assertIsInstance(result, live.HTTPException)
        self.assertEqual(submit.await_count, 0)


class SharedBudget(unittest.TestCase):
    def test_budget_and_committed_grid_risk(self):
        runner = SimpleNamespace(reserved_risk=lambda uid, owner=True: {"worst_loss_usdt": 4.0})
        app = SimpleNamespace(state=SimpleNamespace(live_grid_runner=runner))
        state = {"policy": {"daily_loss_limit": 10.0}, "events": [], "plans": {}}
        got = live.shared_daily_budget(app, state, SimpleNamespace(state=SimpleNamespace(), headers={}, cookies={}))
        self.assertEqual(got["committed"], 4.0)
        self.assertEqual(got["budget_left"], 10.0)
        self.assertIsNone(live.shared_daily_budget(app, {"policy": {}}, None)["budget_left"])


if __name__ == "__main__":
    unittest.main()

"""DCA akıllı başlama — SAHTE borsa, AĞ YOK, GERÇEK EMİR YOK.
Çalıştırma: python -m unittest tests.test_dca_entry_gate -v
Kural: kapı yalnız GİRİŞİ geciktirir/engeller. Belirsizlikte (analiz okunamadı, coin listede yok) "uygun değil" sayılır; tur sonunda bot CYCLE_DONE'da
(pozisyon/emir yok) bekler, süre dolarsa durur; bayrak kapalıysa kapı hiç çalışmaz.
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import dca_live  # noqa: E402
from app.dca_engine import entry_gate  # noqa: E402
from app.dca_engine.engine import CYCLE_DONE  # noqa: E402
from app.dca_engine.plan import prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import RUNNING, STOPPED  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}
PARAMS = dict(base_margin_usdt=10.0, safety_orders=2, step_pct=1.5, volume_multiplier=1.5, take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3)


class TestEvaluate(unittest.TestCase):
    def row(self, direction="LONG", confidence=85):
        return {"symbol": SYM, "direction": direction, "confidence": confidence}

    def test_matching_strong_signal_passes(self):
        ok, why = entry_gate.evaluate(self.row(), SYM, "LONG", 80)
        self.assertTrue(ok)
        self.assertIn("uygun", why)

    def test_opposite_direction_fails(self):
        ok, why = entry_gate.evaluate(self.row("SHORT"), SYM, "LONG", 80)
        self.assertFalse(ok)
        self.assertIn("SHORT", why)

    def test_weak_confidence_fails(self):
        ok, why = entry_gate.evaluate(self.row(confidence=79), SYM, "LONG", 80)
        self.assertFalse(ok)
        self.assertIn("%79", why)

    def test_neutral_or_missing_or_garbage_fails_closed(self):
        self.assertFalse(entry_gate.evaluate(self.row("NEUTRAL"), SYM, "LONG", 80)[0])
        self.assertFalse(entry_gate.evaluate(None, SYM, "LONG", 80)[0])
        self.assertFalse(entry_gate.evaluate({"symbol": SYM, "direction": "LONG", "confidence": "abc"}, SYM, "LONG", 80)[0])
        self.assertFalse(entry_gate.evaluate({"symbol": SYM, "direction": "LONG", "confidence": float("nan")}, SYM, "LONG", 80)[0])

    def test_threshold_env_is_clamped(self):
        with mock.patch.dict(os.environ, {entry_gate.MIN_CONFIDENCE_ENV: "10"}):
            self.assertEqual(entry_gate.min_confidence(), 50.0)
        with mock.patch.dict(os.environ, {entry_gate.MIN_CONFIDENCE_ENV: "bozuk"}):
            self.assertEqual(entry_gate.min_confidence(), entry_gate.DEFAULT_MIN_CONFIDENCE)

    def test_flag_default_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(entry_gate.ENABLE_ENV, None)
            self.assertTrue(entry_gate.enabled())
        with mock.patch.dict(os.environ, {entry_gate.ENABLE_ENV: "0"}):
            self.assertFalse(entry_gate.enabled())


class TestCheck(unittest.TestCase):
    def test_check_finds_symbol_case_and_slash_insensitive(self):
        async def fetch():
            return [{"symbol": "ethusdt", "direction": "LONG", "confidence": 90}]
        ok, _ = asyncio.run(entry_gate.check("ETH/USDT", "LONG", fetch=fetch, threshold=80))
        self.assertTrue(ok)

    def test_symbol_not_in_universe_fails(self):
        async def fetch():
            return [{"symbol": "BTCUSDT", "direction": "LONG", "confidence": 90}]
        ok, why = asyncio.run(entry_gate.check(SYM, "LONG", fetch=fetch, threshold=80))
        self.assertFalse(ok)
        self.assertIn("listesinde yok", why)

    def test_fetch_error_fails_closed(self):
        async def fetch():
            raise RuntimeError("ağ yok")
        ok, why = asyncio.run(entry_gate.check(SYM, "LONG", fetch=fetch))
        self.assertFalse(ok)
        self.assertIn("okunamadı", why)


class TestOptions(unittest.TestCase):
    class Body:
        def __init__(self, smart=False, trail=0.0):
            self.smart_entry, self.trailing_pct = smart, trail

    def test_disabled_options_are_rejected_not_ignored(self):
        with mock.patch.dict(os.environ, {entry_gate.ENABLE_ENV: "0", dca_live.TRAILING_ENV: "0"}):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                dca_live.require_options(self.Body(smart=True))
            with self.assertRaises(HTTPException):
                dca_live.require_options(self.Body(trail=0.5))
            dca_live.require_options(self.Body())                       # hiçbiri istenmediyse sorun yok

    def test_enabled_options_pass_and_are_stored_in_params(self):
        with mock.patch.dict(os.environ, {entry_gate.ENABLE_ENV: "1", dca_live.TRAILING_ENV: "1"}):
            dca_live.require_options(self.Body(smart=True, trail=0.5))
        body = type("B", (), {**PARAMS, "smart_entry": True, "trailing_pct": 0.5})()
        params = dca_live.plan_params(body)
        self.assertTrue(params["smart_entry"])
        self.assertEqual(params["trailing_pct"], 0.5)
        self.assertEqual(params["leverage"], 3)

    def test_old_style_body_without_new_fields_still_works(self):
        body = type("B", (), dict(PARAMS))()
        params = dca_live.plan_params(body)
        self.assertFalse(params["smart_entry"])
        self.assertEqual(params["trailing_pct"], 0.0)


def make_plan(price=100.0, recycle=True, smart=True):
    plan = prepare_dca_plan(direction="LONG", price=price, rules=RULES, policy=POLICY, recycle=recycle, **PARAMS)
    plan["tick"] = RULES["tick"]
    plan["params"] = {**PARAMS, "smart_entry": smart, "trailing_pct": 0.0}
    return plan


class TestRunnerWait(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.runner = dca_live.DcaRunner(GridStore(check_same_thread=False), account="DEMO")
        self.state: dict = {"policy": {}}
        self.runner._record = lambda *a, **k: None
        self.runner.entry_authority = lambda state: True
        self.runner.blocked_check = lambda state: False
        self.planned = 0

        async def planner(engine, state):
            self.planned += 1
            self.ex.set_price(SYM, 100.0)
            return make_plan()
        self.runner.cycle_planner = planner
        self.env = mock.patch.dict(os.environ, {entry_gate.ENABLE_ENV: "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def done_engine(self):
        async def go():
            app = mock.Mock()
            with mock.patch.object(self.runner, "_ensure_loop"):
                await self.runner.start(app, self.state, self.ex, make_plan(), SYM, user_id="u1", owner=False)
        asyncio.run(go())
        engine = self.runner._engines[self.runner.occupying_row("u1", SYM, owner=False)["grid_id"]]
        engine.tick()
        self.ex.set_price(SYM, engine.tp_price())
        engine.tick()
        self.assertEqual(engine.status, CYCLE_DONE)
        return engine

    def after(self, engine):
        return asyncio.run(self.runner._after_tick(engine, self.state, engine.status))

    def checker(self, ok, why="x"):
        async def check(symbol, direction):
            return ok, why
        self.runner.entry_checker = check

    def test_not_ready_waits_without_planning_or_orders(self):
        engine = self.done_engine()
        self.checker(False, "Kais güveni %60; en az %80 gerekir")
        orders_before = len(self.ex.orders)
        self.assertEqual(self.after(engine), CYCLE_DONE)
        self.assertEqual(self.planned, 0)                                       # ağır plan hesabına bile girmez
        self.assertEqual(len(self.ex.orders), orders_before)                    # emir yok
        self.assertIn("bekleniyor", self.runner.store.grid(engine.gid)["halt_reason"])
        self.assertEqual(self.after(engine), CYCLE_DONE)                        # sonraki turda da bekler

    def test_ready_starts_new_cycle(self):
        engine = self.done_engine()
        self.checker(True)
        self.assertEqual(self.after(engine), RUNNING)
        self.assertEqual(self.planned, 1)
        self.assertEqual(engine._cycle(), 1)

    def test_wait_then_ready(self):
        engine = self.done_engine()
        self.checker(False)
        self.assertEqual(self.after(engine), CYCLE_DONE)
        self.checker(True)
        self.assertEqual(self.after(engine), RUNNING)

    def test_wait_times_out_and_stops(self):
        engine = self.done_engine()
        self.checker(False)
        with mock.patch.object(entry_gate, "max_wait_seconds", return_value=0.0):
            self.assertEqual(self.after(engine), CYCLE_DONE)                    # sayaç başlar
            self.assertEqual(self.after(engine), STOPPED)                       # süre doldu
        self.assertIn("uygun an gelmediği", self.runner.store.grid(engine.gid)["halt_reason"])

    def test_checker_exception_fails_closed(self):
        engine = self.done_engine()

        async def boom(symbol, direction):
            raise RuntimeError("beklenmeyen")
        self.runner.entry_checker = boom
        self.assertEqual(self.after(engine), CYCLE_DONE)
        self.assertEqual(self.planned, 0)

    def test_flag_off_ignores_gate(self):
        engine = self.done_engine()
        self.checker(False)
        with mock.patch.dict(os.environ, {entry_gate.ENABLE_ENV: "0"}):
            self.assertEqual(self.after(engine), RUNNING)

    def test_bot_without_smart_entry_is_unaffected(self):
        engine = self.done_engine()
        engine.spec.meta["params"]["smart_entry"] = False
        self.checker(False)
        self.assertEqual(self.after(engine), RUNNING)

    def test_other_gates_still_apply_while_waiting(self):
        engine = self.done_engine()
        self.checker(False)
        self.runner.entry_authority = lambda state: False                       # ARM kapandı: bekleyen bot durur (mevcut kural)
        self.assertEqual(self.after(engine), STOPPED)


if __name__ == "__main__":
    unittest.main()

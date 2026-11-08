"""DCA çalıştırıcısı (Faz 3) — SAHTE borsa, AĞ YOK, GERÇEK EMİR YOK.
Çalıştırma: python -m pytest tests/test_dca_live_runner.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi import HTTPException  # noqa: E402

from app import dca_live  # noqa: E402
from app.dca_engine.engine import CYCLE_DONE, DcaEngine  # noqa: E402
from app.dca_engine.plan import DcaPlanError, prepare_dca_plan  # noqa: E402
from app.grid_engine.engine import PAUSED, RUNNING, STOPPED  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.store import GridStore  # noqa: E402

SYM = "ETHUSDT"
RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 50, "daily_loss_limit": 100, "max_total_exposure_usdt": 5000, "max_positions": 5, "max_stop_distance_pct": 5}
PARAMS = dict(base_margin_usdt=10.0, safety_orders=2, step_pct=1.5, volume_multiplier=1.5, take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3)


def make_plan(price=100.0, recycle=True):
    plan = prepare_dca_plan(direction="LONG", price=price, rules=RULES, policy=POLICY, recycle=recycle, **PARAMS)
    plan["tick"] = RULES["tick"]
    plan["params"] = dict(PARAMS)
    return plan


class Env(unittest.TestCase):
    def setUp(self):
        self.ex = FakeExchange(tick=0.01, step=0.001)
        self.ex.prices[SYM] = 100.0
        self.runner = dca_live.DcaRunner(GridStore(check_same_thread=False), account="DEMO")
        self.state: dict = {"policy": {}}
        self.runner._record = lambda *a, **k: None            # olay günlüğü bu testlerde gerekmiyor

    def run_async(self, coro):
        return asyncio.run(coro)

    def start(self, recycle=True, user="u1"):
        async def go():
            app = mock.Mock()
            with mock.patch.object(self.runner, "_ensure_loop"):      # döngü zamanlayıcısı yok: motoru elle süreceğiz
                await self.runner.start(app, self.state, self.ex, make_plan(recycle=recycle), SYM, user_id=user, owner=False)
        self.run_async(go())
        row = self.runner.occupying_row("u1", SYM, owner=False)
        return self.runner._engines[row["grid_id"]]

    def finish_cycle(self, engine):
        """Taban dolmuş, hedef emri konmuş; fiyat hedefe gelir -> tur biter (CYCLE_DONE)."""
        engine.tick()
        self.ex.set_price(SYM, engine.tp_price())
        engine.tick()


class TestRunner(Env):
    def test_engine_is_dca_with_kd_prefix_and_separate_store(self):
        engine = self.start()
        self.assertIsInstance(engine, DcaEngine)
        self.assertTrue(engine.prefix.startswith("kd-"))
        self.assertEqual(engine.status, RUNNING)
        self.assertEqual(self.runner.store.grid(engine.gid)["symbol"], SYM)

    def test_params_are_persisted_in_spec_and_engine_rebuilds_from_row(self):
        engine = self.start()
        row = self.runner.store.grid(engine.gid)
        rebuilt = self.runner.build_engine(row, self.ex, self.state)
        self.assertIsInstance(rebuilt, DcaEngine)
        self.assertEqual(rebuilt.spec.meta["params"]["step_pct"], 1.5)
        self.assertEqual(rebuilt.prefix, engine.prefix)

    def test_payload_has_kind_and_dca_stats(self):
        engine = self.start()
        engine.tick()
        payload = self.runner.grid_payload(self.runner.store.grid(engine.gid))
        self.assertEqual(payload["kind"], "DCA")
        self.assertIn("avg_entry", payload["stats"])
        self.assertEqual(payload["params"]["leverage"], 3)

    def test_reserved_risk_counts_worst_loss(self):
        self.start()
        info = self.runner.reserved_risk("u1", owner=False)
        self.assertGreater(info["worst_loss_usdt"], 0)
        self.assertEqual(info["symbols"], [SYM])

    def test_second_bot_same_symbol_refused(self):
        self.start()
        with self.assertRaises(Exception):
            self.start()


class TestNextCycle(Env):
    def done_engine(self, recycle=True):
        engine = self.start(recycle=recycle)
        self.finish_cycle(engine)
        return engine

    def planner_ok(self):
        async def planner(engine, state):
            self.ex.set_price(SYM, 100.0)
            return make_plan(price=100.0)
        return planner

    def gates_open(self):
        self.runner.entry_authority = lambda state: True
        self.runner.blocked_check = lambda state: False

    def after(self, engine):
        return self.run_async(self.runner._after_tick(engine, self.state, engine.status))

    def test_cycle_done_reached(self):
        engine = self.done_engine()
        self.assertEqual(engine.status, CYCLE_DONE)

    def test_new_cycle_starts_when_all_gates_pass(self):
        engine = self.done_engine()
        self.gates_open()
        self.runner.cycle_planner = self.planner_ok()
        self.assertEqual(self.after(engine), RUNNING)
        self.assertEqual(engine._cycle(), 1)

    def test_no_new_cycle_without_entry_authority(self):
        engine = self.done_engine()
        self.runner.entry_authority = lambda state: False
        self.runner.cycle_planner = self.planner_ok()
        self.assertEqual(self.after(engine), STOPPED)
        self.assertIn("ARM", self.runner.store.grid(engine.gid)["halt_reason"])

    def test_no_new_cycle_when_blocked(self):
        engine = self.done_engine()
        self.gates_open()
        self.runner.blocked_check = lambda state: True
        self.runner.cycle_planner = self.planner_ok()
        self.assertEqual(self.after(engine), STOPPED)

    def test_no_new_cycle_without_planner(self):
        engine = self.done_engine()
        self.gates_open()
        self.assertEqual(self.after(engine), STOPPED)

    def test_planner_rejection_stops_bot_with_reason(self):
        engine = self.done_engine()
        self.gates_open()

        async def planner(engine, state):
            raise DcaPlanError("bugün kalan zarar hakkı yetmiyor")
        self.runner.cycle_planner = planner
        self.assertEqual(self.after(engine), STOPPED)
        self.assertIn("kalan zarar", self.runner.store.grid(engine.gid)["halt_reason"])

    def test_unexpected_planner_error_stops_bot(self):
        engine = self.done_engine()
        self.gates_open()

        async def planner(engine, state):
            raise RuntimeError("beklenmeyen")
        self.runner.cycle_planner = planner
        self.assertEqual(self.after(engine), STOPPED)

    def test_daily_limit_reached_stops_bot(self):
        engine = self.done_engine()
        self.gates_open()
        self.runner.cycle_planner = self.planner_ok()
        with mock.patch.object(self.runner, "daily_limit_of", return_value=5.0), \
                mock.patch.object(self.runner, "day_summary", return_value={"reached": True}):
            self.assertEqual(self.after(engine), STOPPED)

    def test_leftover_position_blocks_new_cycle(self):
        engine = self.done_engine()
        self.gates_open()
        self.runner.cycle_planner = self.planner_ok()
        self.ex.positions[SYM] = self.ex.positions.get(SYM, 0.0) + 0.5      # borsada kalıntı
        self.assertEqual(self.after(engine), STOPPED)

    def test_non_cycle_status_untouched(self):
        engine = self.start()
        self.assertEqual(self.run_async(self.runner._after_tick(engine, self.state, RUNNING)), RUNNING)


class TestFlags(unittest.TestCase):
    def test_live_on_by_default_but_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(dca_live.ENV_FLAG, None)
            self.assertTrue(dca_live.live_enabled())
        with mock.patch.dict(os.environ, {dca_live.ENV_FLAG: "0"}):
            self.assertFalse(dca_live.live_enabled())
            with self.assertRaises(HTTPException) as ctx:
                dca_live.require_enabled()
            self.assertEqual(ctx.exception.status_code, 403)

    def test_live_on_with_flag(self):
        with mock.patch.dict(os.environ, {dca_live.ENV_FLAG: "1"}):
            self.assertTrue(dca_live.live_enabled())
            dca_live.require_enabled()

    def test_demo_on_by_default_and_can_be_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(dca_live.DEMO_ENV_FLAG, None)
            self.assertTrue(dca_live.demo_enabled())
        with mock.patch.dict(os.environ, {dca_live.DEMO_ENV_FLAG: "0"}):
            self.assertFalse(dca_live.demo_enabled())

    def test_live_margin_cap(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(dca_live.MAX_MARGIN_ENV, None)
            self.assertEqual(dca_live.live_max_margin_usdt(), 10.0)
            dca_live.check_live_margin({"total_margin_usdt": 9.9})
            with self.assertRaises(DcaPlanError):
                dca_live.check_live_margin({"total_margin_usdt": 10.5})
        with mock.patch.dict(os.environ, {dca_live.MAX_MARGIN_ENV: "abc"}):
            self.assertEqual(dca_live.live_max_margin_usdt(), 10.0)



class TestEndpointWiring(unittest.TestCase):
    """Kaynak kodu denetimi: uç noktalar grid ile aynı kapıları, aynı sırayla ve emirden ÖNCE geçer."""

    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).parents[1] / "app" / "v25_execution.py").read_text(encoding="utf-8")
        start = cls.text.index("async def _dca_live_start")
        cls.start = cls.text[start:cls.text.index("def grid_live_error_types", start)]

    def test_gates_before_any_order(self):
        order = [self.start.index(token) for token in (
            "require_enabled()", "CONFIRM_START", "require_fill_unlocked", "guard_new_entry", "recovery_ready", "is_armed(state)",
            "live_execution_blocked", "real_trading_locked", "readiness_for", "symbol_busy", "plan = prepare_dca_plan(", "check_live_margin",
            "set_live_isolated_margin", "apply_live_verified_leverage", "runner.start(")]
        self.assertEqual(order, sorted(order))

    def test_preview_never_reaches_order_code(self):
        preview = self.start.index("if body.preview:\n                return")
        self.assertLess(preview, self.start.index("set_live_isolated_margin"))
        self.assertLess(preview, self.start.index("runner.start("))

    def test_reservation_and_entitlement(self):
        self.assertIn('account_reservations.reserve(owner_id, "GRID"', self.text[self.text.index("async def v25_dca_live_start"):][:1500])
        self.assertIn('"/api/v25/dca/live/start"', self.text[self.text.index("master_trade_access"):][:2500])

    def test_resume_requires_flag_but_stop_and_emergency_do_not(self):
        def chunk(name):
            i = self.text.index(f"async def {name}")
            return self.text[i:i + 900]
        self.assertIn("require_enabled", chunk("v25_dca_live_resume"))
        self.assertNotIn("require_enabled", chunk("v25_dca_live_stop"))
        self.assertNotIn("require_enabled", chunk("v25_dca_live_emergency"))

    def test_grid_and_dca_see_each_other(self):
        self.assertIn("live_bot_runners(application)", self.text[self.text.index("def grid_symbol_busy"):][:900])
        self.assertIn("combined_reserved_risk", self.text[self.text.index("def shared_daily_budget"):][:2500])
        grid_start = self.text[self.text.index("async def _grid_live_start"):][:7000]
        self.assertIn("combined_reserved_risk", grid_start)
        self.assertIn("live_bot_runners(application)", grid_start)

    def test_recovery_covers_dca(self):
        task = self.text[self.text.index("def grid_recovery_task"):][:3500]
        self.assertIn("dca_live.runner_for(application)", task)
        self.assertIn("dca_live_cycle_planner", task)


if __name__ == "__main__":
    unittest.main()

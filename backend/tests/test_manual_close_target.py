import asyncio
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from app import manual_close_target as mct  # noqa: E402
from app import v25_execution as live  # noqa: E402

FEE = D("0.0005")


class CloseTargetMathTests(unittest.TestCase):
    def test_profit_target_is_net_of_both_fees(self):
        target = mct.build_target("profit", 1, None, "t")
        # SHORT 124.7 FIL @0.8013: 0.8013->0.79 = 1.409 brüt; komisyon (0.8013+0.79)*124.7*0.0005 = 0.0992 -> net 1.31 >= 1
        self.assertTrue(mct.reached(target, "SHORT", "0.79", "0.8013", "124.7", FEE))
        # 0.795: brüt 0.785, net 0.686 < 1 -> kapanmaz
        self.assertFalse(mct.reached(target, "SHORT", "0.795", "0.8013", "124.7", FEE))
        # LONG simetrik
        self.assertTrue(mct.reached(target, "LONG", "110", "100", "1", FEE))
        self.assertFalse(mct.reached(target, "LONG", "100.5", "100", "1", FEE))

    def test_price_target_direction_comes_from_current_price(self):
        above = mct.build_target("price", "0.81", "0.8014", "t")
        self.assertEqual(above["side"], "ABOVE")
        self.assertFalse(mct.reached(above, "SHORT", "0.805", "0.8013", "1", FEE))
        self.assertTrue(mct.reached(above, "SHORT", "0.8100", "0.8013", "1", FEE))
        below = mct.build_target("price", "0.79", "0.8014", "t")
        self.assertEqual(below["side"], "BELOW")
        self.assertTrue(mct.reached(below, "SHORT", "0.789", "0.8013", "1", FEE))
        self.assertFalse(mct.reached(below, "SHORT", "0.80", "0.8013", "1", FEE))

    def test_invalid_inputs_are_rejected(self):
        for mode, value, mark in (("x", 1, 1), ("profit", 0, None), ("profit", -1, None), ("profit", "abc", None),
                                  ("profit", 200000, None), ("price", 1, None), ("price", 1, 1), ("price", "NaN", 1)):
            with self.assertRaises(ValueError, msg=(mode, value, mark)):
                mct.build_target(mode, value, mark, "t")
        self.assertFalse(mct.reached({"mode": "profit", "value": 1}, "LONG", None, "1", "1", FEE))
        self.assertFalse(mct.reached({"mode": "profit", "value": 1}, "FLAT", "2", "1", "1", FEE))
        self.assertFalse(mct.reached(None, "LONG", "2", "1", "1", FEE))


class CloseTargetStepTests(unittest.TestCase):
    def setUp(self):
        self.book = {"bidPrice": "0.789", "askPrice": "0.79"}
        self.client = SimpleNamespace(public_get=AsyncMock(side_effect=lambda *a, **k: dict(self.book)))

    def test_profit_target_waits_for_real_exit_price(self):
        # İşaret fiyatı hedefe ulaştı ama borsada gerçek çıkış (SHORT için ask) hâlâ kârı tutmuyor: kapatma yok.
        state = live.initial_state()
        plan = self._plan()
        state["plans"][plan["id"]] = plan
        self.book = {"bidPrice": "0.79", "askPrice": "0.80"}
        close = AsyncMock(return_value={"orderId": 77, "avgPrice": "0.80", "status": "FILLED"})
        position = {"mark_price": "0.79", "entry_price": "0.8013", "quantity": "124.7"}
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            self.assertFalse(asyncio.run(live.manual_close_target_step(self.client, state, plan, position)))
            close.assert_not_awaited()
            self.assertIn("close_target", plan)
            self.assertEqual(plan["close_target"].get("failures", 0), 0)
            self.book = {"bidPrice": "0.789", "askPrice": "0.79"}
            self.assertTrue(asyncio.run(live.manual_close_target_step(self.client, state, plan, position)))
        close.assert_awaited_once()

    def test_profit_target_does_not_close_when_book_unreadable(self):
        state = live.initial_state()
        plan = self._plan()
        state["plans"][plan["id"]] = plan
        self.client.public_get = AsyncMock(side_effect=RuntimeError("down"))
        close = AsyncMock()
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertFalse(hit)
        close.assert_not_awaited()
        self.assertIn("close_target", plan)

    def _plan(self, **target):
        return {"id": "plan-123456", "symbol": "FILUSDT", "direction": "SHORT", "source": "MANUAL", "status": "KORUMA AKTİF",
                "entry_price": "0.8013", "close_target": {"mode": "profit", "value": 1.0, "set_at": "2026-11-07T11:20:00Z", **target}}

    def test_step_closes_only_when_reached(self):
        state = live.initial_state()
        plan = self._plan()
        state["plans"][plan["id"]] = plan
        close = AsyncMock(return_value={"orderId": 77, "avgPrice": "0.79", "status": "FILLED"})
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.795", "entry_price": "0.8013", "quantity": "124.7"}))
            self.assertFalse(hit)
            close.assert_not_awaited()
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.79", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertTrue(hit)
        close.assert_awaited_once()
        self.assertTrue(close.await_args.args[2].startswith("manual-target-plan-123456"))
        self.assertEqual(plan["close_reason"], "TARGET")
        self.assertNotIn("close_target", plan)
        self.assertEqual(live.close_reason_for_intent(close.await_args.args[2]), "TARGET")

    def test_step_ignores_auto_plans_and_locked_execution(self):
        state = live.initial_state()
        plan = self._plan()
        plan["source"] = "ORIGINAL_V2_LIVE"
        close = AsyncMock()
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            self.assertFalse(asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"})))
            plan["source"] = "MANUAL"
            with patch.object(live, "account_rate_limit_active", return_value=True):
                self.assertFalse(asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"})))
        close.assert_not_awaited()

    def test_reconciliation_flag_or_emergency_does_not_block_a_reduce_only_close(self):
        state = live.initial_state()
        state["reconciliation_required"] = True
        state["execution_state"] = "UNKNOWN"
        state["emergency"]["active"] = True
        plan = self._plan()
        close = AsyncMock(return_value={"orderId": 9, "status": "FILLED"})
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertTrue(hit)
        close.assert_awaited_once()

    def test_single_failures_keep_the_target(self):
        state = live.initial_state()
        plan = self._plan()
        close = AsyncMock(side_effect=live.LiveExchangeError("boom"))
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            for _ in range(3):
                asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertIn("close_target", plan)

    def test_repeated_exchange_failures_remove_the_target(self):
        state = live.initial_state()
        plan = self._plan()
        close = AsyncMock(side_effect=live.LiveExchangeError("boom"))
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            for _ in range(live.MANUAL_TARGET_MAX_FAILURES):
                asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertNotIn("close_target", plan)
        self.assertTrue(any(event["kind"] == "MANUAL_TARGET_DISABLED" for event in state["events"]))

    def test_unfilled_close_with_open_position_keeps_target_and_sends_no_closed_event(self):
        state = live.initial_state()
        plan = self._plan()
        close = AsyncMock(return_value={"orderId": 5, "status": "NEW"})
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "verify_symbol_flat", AsyncMock(return_value=False)), patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertFalse(hit)
        self.assertIn("close_target", plan)
        self.assertEqual(plan["status"], "KORUMA AKTİF")
        self.assertFalse(any(event["kind"] == "LIVE_CLOSE" for event in state["events"]))
        self.assertEqual(plan["close_target"]["failures"], 1)

    def test_no_order_but_position_open_is_not_reported_closed(self):
        state = live.initial_state()
        plan = self._plan()
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", AsyncMock(return_value=None)), \
                patch.object(live, "verify_symbol_flat", AsyncMock(return_value=False)), patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertFalse(hit)
        self.assertFalse(any(event["kind"] == "LIVE_CLOSE" for event in state["events"]))

    def test_unfilled_status_but_flat_account_counts_as_closed(self):
        state = live.initial_state()
        plan = self._plan()
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", AsyncMock(return_value={"orderId": 5, "status": "NEW"})), \
                patch.object(live, "verify_symbol_flat", AsyncMock(return_value=True)), patch.object(live, "persist_state", lambda s: None):
            hit = asyncio.run(live.manual_close_target_step(self.client, state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertTrue(hit)
        self.assertTrue(any(event["kind"] == "LIVE_CLOSE" for event in state["events"]))


    def test_runner_checks_target_regardless_of_protection_state(self):
        state = live.initial_state()
        plan = self._plan()
        plan["protection_state"] = "MISSING"
        state["plans"][plan["id"]] = plan
        close = AsyncMock(return_value={"orderId": 9, "avgPrice": "0.79", "status": "FILLED"})
        positions = {"FILUSDT": {"mark_price": "0.79", "entry_price": "0.8013", "quantity": "124.7"}}
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            asyncio.run(live.run_manual_close_targets(self.client, state, positions))
        close.assert_awaited_once()
        self.assertEqual(plan["close_reason"], "TARGET")

    def test_runner_skips_plans_without_position_or_target(self):
        state = live.initial_state()
        plan = self._plan()
        state["plans"][plan["id"]] = plan
        other = {**self._plan(), "id": "plan-other"}
        other.pop("close_target")
        state["plans"]["plan-other"] = other
        close = AsyncMock()
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            asyncio.run(live.run_manual_close_targets(self.client, state, {}))
            asyncio.run(live.run_manual_close_targets(self.client, state, {"FILUSDT": {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}}))
        self.assertEqual(close.await_count, 1)


if __name__ == "__main__":
    unittest.main()


class CloseTargetFastLoopTests(unittest.TestCase):
    def _app(self, state):
        return SimpleNamespace(state=SimpleNamespace(v25_execution=state, v25_execution_stopping=False))

    def _run(self, plans, positions):
        async def go():
            state = live.initial_state()
            state["lock"] = asyncio.Lock()
            state["plans"] = plans
            app = self._app(state)
            seen = []

            async def fake_runner(client, st, pos):
                seen.append(dict(pos))
                app.state.v25_execution_stopping = True

            async def fake_position(client, symbol):
                return positions.get(symbol)

            async def stopper():
                await asyncio.sleep(0.2)
                app.state.v25_execution_stopping = True

            with patch.object(live, "CLOSE_TARGET_FAST_SECONDS", 0.01), \
                    patch.object(live, "auto_session_credentials", AsyncMock(return_value=("a" * 12, "b" * 12))), \
                    patch.object(live, "client_for_with_credentials", lambda *a, **k: object()), \
                    patch.object(live, "live_plan_position", fake_position), \
                    patch.object(live, "run_manual_close_targets", fake_runner):
                await asyncio.gather(live.close_target_fast_loop(app), stopper())
            return seen
        return asyncio.run(go())

    def test_fast_loop_checks_only_symbols_with_a_target(self):
        plans = {
            "a": {"id": "a", "symbol": "SOPHUSDT", "source": "MANUAL", "status": "KORUMA AKTİF", "close_target": {"mode": "profit", "value": 0.15}},
            "b": {"id": "b", "symbol": "ETHUSDT", "source": "MANUAL", "status": "KORUMA AKTİF"},
        }
        seen = self._run(plans, {"SOPHUSDT": {"mark_price": "1"}, "ETHUSDT": {"mark_price": "2"}})
        self.assertEqual(seen, [{"SOPHUSDT": {"mark_price": "1"}}])

    def test_fast_loop_does_nothing_without_targets(self):
        plans = {"b": {"id": "b", "symbol": "ETHUSDT", "source": "MANUAL", "status": "KORUMA AKTİF"}}
        self.assertEqual(self._run(plans, {"ETHUSDT": {"mark_price": "2"}}), [])

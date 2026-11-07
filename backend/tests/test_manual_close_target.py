import asyncio
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
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
            hit = asyncio.run(live.manual_close_target_step(object(), state, plan, {"mark_price": "0.795", "entry_price": "0.8013", "quantity": "124.7"}))
            self.assertFalse(hit)
            close.assert_not_awaited()
            hit = asyncio.run(live.manual_close_target_step(object(), state, plan, {"mark_price": "0.79", "entry_price": "0.8013", "quantity": "124.7"}))
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
            self.assertFalse(asyncio.run(live.manual_close_target_step(object(), state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"})))
            plan["source"] = "MANUAL"
            with patch.object(live, "live_execution_blocked", return_value=True):
                self.assertFalse(asyncio.run(live.manual_close_target_step(object(), state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"})))
        close.assert_not_awaited()

    def test_repeated_exchange_failures_remove_the_target(self):
        state = live.initial_state()
        plan = self._plan()
        close = AsyncMock(side_effect=live.LiveExchangeError("boom"))
        with patch.object(live, "live_plan_can_mutate", return_value=True), patch.object(live, "close_tracked_symbol", close), \
                patch.object(live, "persist_state", lambda s: None):
            for _ in range(3):
                asyncio.run(live.manual_close_target_step(object(), state, plan, {"mark_price": "0.70", "entry_price": "0.8013", "quantity": "124.7"}))
        self.assertNotIn("close_target", plan)
        self.assertTrue(any(event["kind"] == "MANUAL_TARGET_DISABLED" for event in state["events"]))


if __name__ == "__main__":
    unittest.main()

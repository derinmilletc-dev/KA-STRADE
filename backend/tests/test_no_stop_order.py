"""Stopsuz (bilerek) manuel emir: model, hedef doğrulama, koruma sınıflaması, kapı kuralları ve stop ekleme."""
from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import v25_execution as live


def order(**kw):
    base = dict(symbol="BTCUSDT", direction="LONG", margin_usdt=10, leverage=5, tp1=102, tp2=104, tp3=106)
    base.update(kw)
    return live.LiveOrderRequest(**base)


def no_stop_plan(**kw):
    plan = {"id": "p1abcdef", "intent_id": "manual-1", "symbol": "BTCUSDT", "direction": "LONG", "source": "MANUAL",
            "no_stop": True, "stop_loss": None, "stop_algo_id": None, "targets": ["102", "104", "106"],
            "provenance_state": "CONFIRMED", "protection_state": "MATCHED", "status": "STOPSUZ · TP AKTİF"}
    plan.update(kw)
    return plan


class ModelTests(unittest.TestCase):
    def test_stop_required_without_no_stop(self):
        with self.assertRaises(ValidationError):
            order()

    def test_no_stop_accepts_missing_stop_and_drops_given_stop(self):
        self.assertIsNone(order(no_stop=True).stop_loss)
        self.assertIsNone(order(no_stop=True, stop_loss=99).stop_loss)

    def test_normal_order_unchanged(self):
        body = order(stop_loss=99)
        self.assertFalse(body.no_stop)
        self.assertEqual(body.stop_loss, 99)


class ProtectionTests(unittest.TestCase):
    def test_classify_no_stop_is_matched_with_user_reason(self):
        self.assertEqual(live.classify_plan_protection(no_stop_plan(), []), ("MATCHED", [], "USER_NO_STOP"))

    def test_classify_after_stop_added_is_normal(self):
        plan = no_stop_plan(no_stop=False, stop_loss="99", stop_algo_id=7, stop_client_id="x")
        self.assertEqual(live.classify_plan_protection(plan, [])[0], "MISSING")

    def test_reason_gate(self):
        self.assertTrue(live.protection_reason_ok(no_stop_plan(protection_match_reason="USER_NO_STOP")))
        self.assertFalse(live.protection_reason_ok(no_stop_plan(protection_match_reason="USER_NO_STOP", stop_loss="99")))
        self.assertTrue(live.protection_reason_ok({"protection_match_reason": "EXACT_IDENTITY"}))
        self.assertFalse(live.protection_reason_ok({"protection_match_reason": "USER_NO_STOP"}))

    def test_exchange_view_never_reports_stop(self):
        snapshot = {"open_algo_orders_available": True, "open_algo_orders": []}
        self.assertFalse(live.exchange_protection_view(no_stop_plan(), snapshot)["stop"])

    def test_readiness_checks_targets_only(self):
        spec = {"entry_price": "100", "quantity": "1", "targets": ["102", "104", "106"], "direction": "LONG", "no_stop": True}
        live.validate_protection_readiness(spec, {})
        with self.assertRaises(live.LiveExchangeError):
            live.validate_protection_readiness({**spec, "targets": ["99", "104", "106"]}, {})


class BuildSpecTests(unittest.TestCase):
    rules = {"tick": Decimal("0.1"), "step": Decimal("0.001"), "min_qty": Decimal("0.001"),
             "min_notional": Decimal("5"), "max_qty": Decimal("1000")}

    def build(self, body, manual=True):
        async def run():
            with patch.object(live, "ticker_price", AsyncMock(return_value=Decimal("100"))), \
                 patch.object(live, "live_symbol_rules", AsyncMock(return_value=self.rules)):
                return await live.build_live_spec(SimpleNamespace(), body, {}, allowed_symbols=[], manual_targets=manual)
        return asyncio.run(run())

    def test_no_stop_spec(self):
        spec = self.build(order(no_stop=True))
        self.assertTrue(spec["no_stop"])
        self.assertIsNone(spec["stop_loss"])
        self.assertEqual(spec["estimated_stop_loss_usdt"], 0.0)
        self.assertEqual(Decimal(spec["quantity"]), Decimal("0.5"))  # 10 USDT x 5 / 100

    def test_no_stop_rejected_for_non_manual(self):
        with self.assertRaises(live.LiveExchangeError):
            self.build(order(no_stop=True), manual=False)

    def test_targets_on_wrong_side_rejected(self):
        with self.assertRaises(live.LiveExchangeError):
            self.build(order(no_stop=True, tp1=98, tp2=97, tp3=96))


class AddStopTests(unittest.TestCase):
    def test_add_stop_success_makes_plan_protected(self):
        plan = no_stop_plan(protection_ids=[11, 12])
        state = {"events": []}
        with patch.object(live, "install_verified_stop", AsyncMock(return_value={"algoId": 99})), \
             patch.object(live, "persist_state", lambda s: None):
            result = asyncio.run(live._add_manual_stop(SimpleNamespace(), state, plan, Decimal("98.5")))
        self.assertEqual(result, "ADDED")
        self.assertEqual((plan["stop_loss"], plan["stop_algo_id"], plan["no_stop"]), ("98.5", 99, False))
        self.assertEqual(plan["protection_ids"][0], 99)
        self.assertFalse(live.plan_is_user_no_stop(plan))

    def test_add_stop_failure_leaves_position_untouched(self):
        plan = no_stop_plan()
        state = {"events": []}
        with patch.object(live, "install_verified_stop", AsyncMock(side_effect=live.LiveExchangeError("x"))), \
             patch.object(live, "persist_state", lambda s: None):
            with self.assertRaises(live.ManualPlanActionError):
                asyncio.run(live._add_manual_stop(SimpleNamespace(), state, plan, Decimal("98.5")))
        self.assertTrue(live.plan_is_user_no_stop(plan))
        self.assertEqual(plan["protection_state"], "MATCHED")

    def test_guard_accepts_no_stop_plan(self):
        plan = no_stop_plan()
        with patch.object(live, "live_execution_blocked", lambda s: False):
            self.assertIs(live._manual_plan_guard({}, plan), plan)


if __name__ == "__main__":
    unittest.main()

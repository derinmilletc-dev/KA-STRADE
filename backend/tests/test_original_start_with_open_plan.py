"""Original V2 botu, sağlıklı ve korumalı açık işlem (ör. elle açılan) varken başlatılabilir; sağlıksızsa başlatılamaz."""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as live  # noqa: E402

BASE_MESSAGE = "Aktif LIVE plan varken otomasyon açılamaz."


def make_state(*, plan=None, positions=None, emergency=False):
    state = live.initial_state()
    first = state["policy"]["allowed_symbols"][0]
    state.update({"real_trading_locked": False, "recovery_ready": True, "armed_until": time.time() + 300})
    held = positions if positions is not None else ([{"symbol": first, "direction": "LONG", "quantity": "1", "mark_price": "10"}] if plan else [])
    state["snapshot"] = {
        "wallet_balance": 1000, "available_balance": 1000, "positions": held, "open_orders": [], "open_algo_orders": [],
        "open_algo_orders_available": True, "algo_orders_quality": "VALID_EMPTY", "hedge_mode": False, "multi_assets_mode": False,
    }
    if plan is not None:
        state["plans"] = {"p1": {"id": "p1", "symbol": first, "direction": "LONG", "status": "AÇIK", "source": "MANUAL",
                                 "provenance_state": "CONFIRMED", "protection_state": "MATCHED", **plan}}
    if emergency:
        state["emergency"] = {"active": True}
    return state


def gate(state, **kwargs):
    with patch.object(live, "readiness", return_value={"ready": True}):
        return live.live_auto_start_gate(SimpleNamespace(), state, **kwargs)


class OriginalStartWithOpenPlanTests(unittest.TestCase):
    def test_healthy_protected_open_plan_does_not_block_original_start(self):
        allowed, reason = gate(make_state(plan={}), unlimited_daily_trades=True, tolerate_healthy_plans=True)
        self.assertTrue(allowed, reason)

    def test_other_layers_keep_the_old_block(self):
        allowed, reason = gate(make_state(plan={}))
        self.assertFalse(allowed)
        self.assertEqual(reason, BASE_MESSAGE)

    def test_unhealthy_plans_still_block_and_name_the_symbol(self):
        cases = [
            {"protection_state": "MISSING"}, {"protection_state": "UNKNOWN"},
            {"provenance_state": "BROKEN"}, {"provenance_state": "NO_PROVENANCE"},
            {"mutation_policy": "READ_ONLY_EXTERNAL"}, {"status": "PNL DOĞRULANIYOR"},
            {"pnl_verified": False}, {"tp_protection_state": "UNPROTECTED"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                state = make_state(plan=overrides)
                allowed, reason = gate(state, unlimited_daily_trades=True, tolerate_healthy_plans=True)
                self.assertFalse(allowed)
                self.assertIn("Açık işlem varken bot başlatılamaz", reason)
                self.assertIn(state["plans"]["p1"]["symbol"], reason)

    def test_pending_entry_order_without_position_is_allowed(self):
        state = make_state(plan={"status": "DOLUM BEKLİYOR", "protection_state": "UNKNOWN", "provenance_state": "PROVISIONAL"}, positions=[])
        allowed, reason = gate(state, unlimited_daily_trades=True, tolerate_healthy_plans=True)
        self.assertTrue(allowed, reason)

    def test_untracked_position_still_blocks(self):
        state = make_state(positions=[{"symbol": "RAYSOLUSDT", "direction": "LONG", "quantity": "1", "mark_price": "10"}])
        allowed, reason = gate(state, unlimited_daily_trades=True, tolerate_healthy_plans=True)
        self.assertFalse(allowed)
        self.assertIn("V25 dışı", reason)

    def test_emergency_stop_still_blocks_even_with_a_healthy_plan(self):
        allowed, reason = gate(make_state(plan={}, emergency=True), unlimited_daily_trades=True, tolerate_healthy_plans=True)
        self.assertFalse(allowed)
        self.assertIn("Acil durdurma", reason)

    def test_closed_plans_never_block(self):
        allowed, reason = gate(make_state(plan={"status": "KAPANDI"}, positions=[]), unlimited_daily_trades=True, tolerate_healthy_plans=True)
        self.assertTrue(allowed, reason)

    def test_only_original_call_sites_enable_the_relaxation(self):
        app = Path(__file__).parents[1] / "app"
        original = (app / "original_v2_live.py").read_text(encoding="utf-8")
        self.assertIn("tolerate_healthy_plans=True", original)
        core = (app / "v25_execution.py").read_text(encoding="utf-8")
        self.assertEqual(core.count("tolerate_healthy_plans=unlimited_daily_trades"), 2)
        self.assertIn("tolerate_healthy_plans: bool = False", core)


if __name__ == "__main__":
    unittest.main()

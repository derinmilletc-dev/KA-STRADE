"""KAIS FARMER C6 — marjin/slot/giriş denemesi testleri (ağ yok)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine.margin import HARD_CAP_USDT, MarginGuard, effective_capacity  # noqa: E402
from app.farmer_engine.store import NO_SLOT, FarmerStore  # noqa: E402
from tests.test_farmer_confirm import ACC, cand, params  # noqa: E402

NO_RISK = c.RiskView()


def guard(store=None, **kw):
    base = dict(max_positions=5, total_margin_cap=100.0, position_margin=50.0)
    base.update(kw)
    return MarginGuard(store or FarmerStore(), ACC, **base)


class TestCapacity(unittest.TestCase):
    def test_formula_example(self):
        self.assertEqual(effective_capacity(5, 100, 50), 2)

    def test_max_positions_lower_wins(self):
        self.assertEqual(effective_capacity(1, 100, 10), 1)

    def test_floor(self):
        self.assertEqual(effective_capacity(5, 100, 30), 3)
        self.assertEqual(effective_capacity(5, 100, 60), 1)
        self.assertEqual(effective_capacity(5, 100, 101), 0)

    def test_hard_cap_cannot_be_raised(self):
        self.assertEqual(effective_capacity(5, 500, 50), 2)
        self.assertEqual(guard(total_margin_cap=1000).total_margin_cap, HARD_CAP_USDT)

    def test_invalid_inputs_zero(self):
        for args in ((0, 100, 50), (5, 0, 50), (5, 100, 0), (-1, 100, 50)):
            self.assertEqual(effective_capacity(*args), 0)


class TestAttempt(unittest.TestCase):
    def test_confirmed_creates_hold_and_attempt_atomically(self):
        g = guard()
        d = g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g1")
        self.assertEqual(d.status, c.CONFIRMED)
        self.assertEqual(g.store.attempt(ACC, "BTCUSDT", "15m", 1000)["grid_id"], "g1")
        self.assertEqual(g.store.held_margin(ACC), 50.0)
        self.assertEqual(g.slots().as_dict(), {"capacity": 2, "held": 1, "open": 0, "closing": 0, "free": 1})

    def test_confirmation_rejection_creates_no_claim(self):
        g = guard()
        d = g.attempt_entry(cand("LONG", analysis={"confidence": 50}), params(), NO_RISK, grid_id="g1")
        self.assertEqual(d.status, c.REJECTED)
        self.assertIsNone(g.store.attempt(ACC, "BTCUSDT", "15m", 1000))
        self.assertEqual(g.store.held_margin(ACC), 0.0)

    def test_gate_rejections_create_no_claim(self):
        g = guard()
        for risk in (c.RiskView(arm_blocked=("ARM",)), c.RiskView(safety_blocked=("X",)), c.RiskView(occupied=("BTCUSDT",))):
            self.assertEqual(g.attempt_entry(cand("LONG"), params(), risk, grid_id="g").status, c.REJECTED)
        self.assertIsNone(g.store.attempt(ACC, "BTCUSDT", "15m", 1000))
        self.assertEqual(g.slots().used, 0)

    def test_slot_full_rejected_as_margin_cap_no_row(self):
        g = guard()
        for i, sym in enumerate(("AAAUSDT", "BBBUSDT")):
            self.assertEqual(g.attempt_entry(cand("LONG", symbol=sym), params(), NO_RISK, grid_id=f"g{i}").status, c.CONFIRMED)
        d = g.attempt_entry(cand("LONG", symbol="CCCUSDT"), params(), NO_RISK, grid_id="g3")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.MARGIN_CAP))
        self.assertIsNone(g.store.attempt(ACC, "CCCUSDT", "15m", 1000))

    def test_store_no_slot_result(self):
        s = FarmerStore()
        self.assertEqual(s.claim_entry(ACC, "A", "15m", 1, "g1", 10, cap_usdt=100, max_slots=1), "CLAIMED")
        self.assertEqual(s.claim_entry(ACC, "B", "15m", 1, "g2", 10, cap_usdt=100, max_slots=1), NO_SLOT)
        self.assertEqual(len(s.holds(ACC)), 1)

    def test_one_confirmed_of_two_slots_opens_only_one(self):
        g = guard()
        weak = cand("LONG", symbol="WEAKUSDT", analysis={"confidence": 80})
        strong = cand("LONG", symbol="GOODUSDT")
        self.assertEqual(g.attempt_entry(weak, params(), NO_RISK, grid_id="g1").status, c.REJECTED)
        self.assertEqual(g.attempt_entry(strong, params(), NO_RISK, grid_id="g2").status, c.CONFIRMED)
        self.assertEqual(g.slots().used, 1)
        self.assertEqual(g.slots().free, 1)

    def test_same_symbol_same_candle_second_attempt_never(self):
        g = guard()
        self.assertEqual(g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g1").status, c.CONFIRMED)
        d = g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g2")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.ENTRY_LOCKED))

    def test_temporary_reject_releases_margin_but_no_retry_same_candle(self):
        g = guard()
        self.assertEqual(g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g1").status, c.CONFIRMED)
        self.assertTrue(g.entry_failed("g1"))
        self.assertEqual(g.store.held_margin(ACC), 0.0)
        self.assertEqual(g.store.attempt(ACC, "BTCUSDT", "15m", 1000)["outcome"], "REJECTED_TEMP")
        d = g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g2")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.ENTRY_LOCKED))
        self.assertEqual(g.attempt_entry(cand("LONG", ms=2000), params(), NO_RISK, grid_id="g3").status, c.CONFIRMED)

    def test_daily_loss_blocks_without_claim(self):
        g = guard(daily_loss=lambda: ("DAILY_LOSS_LIMIT",))
        d = g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g1")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.SAFETY_GATE))
        self.assertIsNone(g.store.attempt(ACC, "BTCUSDT", "15m", 1000))

    def test_margin_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            guard().attempt_entry(cand("LONG"), params(margin_usdt=40.0), NO_RISK, grid_id="g")


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.g = guard()
        self.g.attempt_entry(cand("LONG"), params(), NO_RISK, grid_id="g1")
        self.g.entry_filled("g1")

    def test_open_counts(self):
        self.assertEqual(self.g.slots().as_dict()["open"], 1)

    def test_closing_still_counts_slot_and_margin(self):
        self.assertTrue(self.g.closing_started("g1"))
        v = self.g.slots()
        self.assertEqual((v.open, v.closing, v.used), (0, 1, 1))
        self.assertEqual(self.g.store.held_margin(ACC), 50.0)
        self.g.attempt_entry(cand("LONG", symbol="BBBUSDT"), params(), NO_RISK, grid_id="g2")
        d = self.g.attempt_entry(cand("LONG", symbol="CCCUSDT"), params(), NO_RISK, grid_id="g3")
        self.assertEqual(d.reason, c.MARGIN_CAP)

    def test_not_flat_keeps_slot(self):
        self.g.closing_started("g1")
        self.assertFalse(self.g.release_when_flat("g1", False))
        self.assertEqual(self.g.slots().closing, 1)

    def test_flat_releases_regardless_of_pnl_state(self):
        self.g.store.open_result("g1", ACC, "BTCUSDT", "LONG", target_usdt=1, max_loss_usdt=1.5, est_cost_usdt=0.1)
        self.g.closing_started("g1")
        self.g.store.close_result("g1", "TARGET")
        self.assertEqual(self.g.store.result("g1")["pnl_state"], "PENDING")
        self.assertTrue(self.g.release_when_flat("g1", True))
        self.assertEqual(self.g.slots().used, 0)
        self.assertFalse(self.g.release_when_flat("g1", True))

    def test_freed_slot_allows_new_entry(self):
        self.g.attempt_entry(cand("LONG", symbol="BBBUSDT"), params(), NO_RISK, grid_id="g2")
        self.g.closing_started("g1")
        self.g.release_when_flat("g1", True)
        self.assertEqual(self.g.attempt_entry(cand("LONG", symbol="CCCUSDT"), params(), NO_RISK, grid_id="g3").status, c.CONFIRMED)


if __name__ == "__main__":
    unittest.main()

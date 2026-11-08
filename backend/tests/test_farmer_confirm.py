"""KAIS FARMER C5 — giriş onayı testleri (ağ yok). Çalıştırma: cd backend && python -B -m unittest tests.test_farmer_confirm -v"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402

ACC = "u1:live"


def legacy(direction="LONG", **over):
    a = {"direction": direction, "confidence": 94, "momentum": "Pozitif" if direction == "LONG" else "Negatif", "entry": 100.0, "atr": 1.0,
         "radar": {"trap_score": 20, "breakout_quality": 70}}
    m = {"direction": direction, "entry_permission": True, "alignment": 80, "verdict": "GÜÇLÜ ONAY"}
    if direction == "SHORT":
        m["short_filter_v2"] = {"accepted": True, "reason": None, "reasons": []}
    a.update(over.pop("analysis", {}))
    m.update(over.pop("mtf", {}))
    return {"analysis": a, "mtf": m}


def params(**over):
    base = dict(variant=c.CONFIDENCE, min_score=92, margin_usdt=50.0, leverage=3.0, target_usdt=1.0, max_loss_usdt=1.5)
    base.update(over)
    return c.ConfirmParams(**base)


def cand(direction="LONG", ms=1000, symbol="BTCUSDT", **over):
    return c.FarmerCandidate.from_legacy(symbol, "15m", ms, legacy(direction, **over))


class TestConfirm(unittest.TestCase):
    def test_valid_long_confirms(self):
        r = c.confirm(cand("LONG"), params())
        self.assertEqual((r.status, r.reasons), (c.CONFIRMED, ()))

    def test_valid_short_confirms(self):
        self.assertTrue(c.confirm(cand("SHORT"), params()).confirmed)

    def test_bekle_rejected(self):
        r = c.confirm(cand("BEKLE", analysis={"momentum": "Nötr"}), params())
        self.assertEqual(r.reasons, (c.INVALID_DIRECTION,))

    def test_unknown_direction_rejected(self):
        self.assertEqual(c.confirm(cand("SIDEWAYS"), params()).reject_reason, c.INVALID_DIRECTION)

    def test_long_momentum_mismatch(self):
        for m in ("Negatif", "Nötr", None):
            r = c.confirm(cand("LONG", analysis={"momentum": m}), params())
            self.assertEqual(r.reasons, (c.MOMENTUM_MISMATCH,), m)

    def test_short_momentum_mismatch(self):
        for m in ("Pozitif", "Nötr"):
            self.assertEqual(c.confirm(cand("SHORT", analysis={"momentum": m}), params()).reasons, (c.MOMENTUM_MISMATCH,), m)

    def test_mtf_permission_false(self):
        self.assertEqual(c.confirm(cand("LONG", mtf={"entry_permission": False}), params()).reasons, (c.MTF_NOT_CONFIRMED,))

    def test_mtf_direction_not_supporting(self):
        self.assertEqual(c.confirm(cand("LONG", mtf={"direction": "BEKLE"}), params()).reasons, (c.MTF_NOT_CONFIRMED,))

    def test_mtf_alignment_number_is_not_a_direction(self):
        self.assertTrue(c.confirm(cand("LONG", mtf={"alignment": 10}), params()).confirmed)

    def test_short_filter_false_rejects_short(self):
        r = c.confirm(cand("SHORT", mtf={"short_filter_v2": {"accepted": False, "reason": "SHORT_SUPPORT_TOO_CLOSE"}}), params())
        self.assertEqual(r.reasons, (c.SHORT_FILTER_REJECTED,))

    def test_short_filter_missing_rejects_short(self):
        item = legacy("SHORT")
        del item["mtf"]["short_filter_v2"]
        r = c.confirm(c.FarmerCandidate.from_legacy("X", "15m", 1, item), params())
        self.assertEqual(r.reasons, (c.SHORT_FILTER_REJECTED,))

    def test_short_filter_required_for_short_not_long(self):
        long_with_rejecting_filter = cand("LONG", mtf={"short_filter_v2": {"accepted": False}})
        self.assertTrue(c.confirm(long_with_rejecting_filter, params()).confirmed)
        self.assertNotIn("short_filter", c.confirm(cand("LONG"), params()).checks)
        self.assertIn("short_filter", c.confirm(cand("SHORT"), params()).checks)

    def test_score_below_threshold(self):
        r = c.confirm(cand("LONG", analysis={"confidence": 91}), params(min_score=92))
        self.assertEqual(r.reasons, (c.SCORE_TOO_LOW,))
        self.assertTrue(c.confirm(cand("LONG", analysis={"confidence": 92}), params(min_score=92)).confirmed)

    def test_score_96_unreachable_because_repo_caps_at_95(self):
        self.assertEqual(c.confirm(cand("LONG", analysis={"confidence": 95}), params(min_score=96)).reasons, (c.SCORE_TOO_LOW,))

    def test_missing_score_rejected(self):
        item = legacy("LONG")
        del item["analysis"]["confidence"]
        self.assertEqual(c.confirm(c.FarmerCandidate.from_legacy("X", "15m", 1, item), params()).reasons, (c.SCORE_TOO_LOW,))

    def test_reasons_collected_deterministically(self):
        r = c.confirm(cand("LONG", analysis={"confidence": 50, "momentum": "Negatif"}, mtf={"entry_permission": False}), params())
        self.assertEqual(r.reasons, (c.SCORE_TOO_LOW, c.MOMENTUM_MISMATCH, c.MTF_NOT_CONFIRMED))
        self.assertEqual(r, c.confirm(cand("LONG", analysis={"confidence": 50, "momentum": "Negatif"}, mtf={"entry_permission": False}), params()))

    def test_input_not_mutated(self):
        item = legacy("SHORT")
        before = copy.deepcopy(item)
        c.confirm(c.FarmerCandidate.from_legacy("X", "15m", 1, item), params(variant=c.STRATEGY_QUALITY))
        self.assertEqual(item, before)


class TestVariants(unittest.TestCase):
    def test_confidence_variant_ignores_trap_and_breakout(self):
        item = cand("LONG", analysis={"radar": {"trap_score": 99, "breakout_quality": 1}})
        self.assertTrue(c.confirm(item, params(variant=c.CONFIDENCE)).confirmed)

    def test_quality_variant_rejects_trap(self):
        r = c.confirm(cand("LONG", analysis={"radar": {"trap_score": 36, "breakout_quality": 70}}), params(variant=c.STRATEGY_QUALITY))
        self.assertEqual(r.reasons, (c.TRAP_REJECTED,))
        self.assertTrue(c.confirm(cand("LONG", analysis={"radar": {"trap_score": 35, "breakout_quality": 50}}), params(variant=c.STRATEGY_QUALITY)).confirmed)

    def test_quality_variant_rejects_breakout(self):
        r = c.confirm(cand("LONG", analysis={"radar": {"trap_score": 10, "breakout_quality": 49}}), params(variant=c.STRATEGY_QUALITY))
        self.assertEqual(r.reasons, (c.BREAKOUT_QUALITY_REJECTED,))

    def test_quality_variant_missing_radar_rejected(self):
        r = c.confirm(cand("LONG", analysis={"radar": {}}), params(variant=c.STRATEGY_QUALITY))
        self.assertEqual(r.reasons, (c.TRAP_REJECTED, c.BREAKOUT_QUALITY_REJECTED))

    def test_unknown_variant_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            params(variant="APF")

    def test_repo_thresholds(self):
        self.assertEqual((c.REPO_MAX_TRAP_SCORE, c.REPO_MIN_BREAKOUT_QUALITY), (35, 50))


class TestFeasibility(unittest.TestCase):
    def test_matches_faz_b_table(self):
        f = c.feasibility(legacy()["analysis"], params(margin_usdt=50, leverage=3, target_usdt=1.0, max_loss_usdt=1.5))
        self.assertAlmostEqual(f.est_cost_usdt, 0.200, places=6)
        self.assertAlmostEqual(f.target_pct, 0.80, places=2)
        self.assertAlmostEqual(f.stop_pct, 0.8667, places=3)
        self.assertAlmostEqual(f.cost_over_target, 0.2, places=6)
        self.assertAlmostEqual(f.target_over_atr, 0.80, places=2)       # ATR %1
        self.assertAlmostEqual(f.stop_over_atr, 0.8667, places=3)

    def test_cost_not_below_max_loss_is_infeasible(self):
        r = c.confirm(cand("LONG"), params(max_loss_usdt=0.2))           # maliyet 0.2 → stop mesafesi 0
        self.assertEqual(r.reasons, (c.COST_INFEASIBLE,))

    def test_zero_target_or_notional_infeasible(self):
        self.assertEqual(c.confirm(cand("LONG"), params(target_usdt=0)).reasons, (c.COST_INFEASIBLE,))
        self.assertEqual(c.confirm(cand("LONG"), params(margin_usdt=0)).reasons, (c.COST_INFEASIBLE,))

    def test_cost_over_target_threshold_is_backtest_parameter(self):
        self.assertTrue(c.confirm(cand("LONG"), params()).confirmed)                    # None = uygulanmaz
        self.assertEqual(c.confirm(cand("LONG"), params(max_cost_over_target=0.1)).reasons, (c.COST_INFEASIBLE,))
        self.assertTrue(c.confirm(cand("LONG"), params(max_cost_over_target=0.25)).confirmed)

    def test_missing_atr_rejected_always(self):
        r = c.confirm(cand("LONG", analysis={"atr": None}), params())
        self.assertEqual(r.reasons, (c.ATR_INFEASIBLE,))
        self.assertEqual(c.confirm(cand("LONG", analysis={"atr": 0}), params()).reasons, (c.ATR_INFEASIBLE,))

    def test_atr_thresholds_are_backtest_parameters(self):
        self.assertEqual(c.confirm(cand("LONG"), params(max_target_over_atr=0.5)).reasons, (c.ATR_INFEASIBLE,))
        self.assertEqual(c.confirm(cand("LONG"), params(min_stop_over_atr=1.0)).reasons, (c.ATR_INFEASIBLE,))
        self.assertTrue(c.confirm(cand("LONG"), params(max_target_over_atr=1.0, min_stop_over_atr=0.5)).confirmed)

    def test_metrics_exposed_in_result(self):
        r = c.confirm(cand("LONG"), params())
        for key in ("cost_over_target", "target_over_atr", "stop_over_atr", "target_pct", "stop_pct", "est_cost_usdt"):
            self.assertIn(key, r.feasibility.as_dict())


class TestDecideEntry(unittest.TestCase):
    def setUp(self):
        self.s = FarmerStore()

    def decide(self, item=None, risk=None, grid="F1", cap=100.0, p=None):
        return c.decide_entry(self.s, ACC, item or cand("LONG"), p or params(), risk or c.RiskView(), grid_id=grid, cap_usdt=cap)

    def test_confirmed_claims_before_order_and_holds_margin(self):
        d = self.decide()
        self.assertEqual(d.status, c.CONFIRMED)
        self.assertIsNotNone(self.s.attempt(ACC, "BTCUSDT", "15m", 1000))
        self.assertAlmostEqual(self.s.held_margin(ACC), 50.0)
        self.assertEqual(self.s.candidates(ACC)[0]["status"], "CONFIRMING")

    def test_rejection_is_recorded_with_reason_and_burns_no_lock(self):
        d = self.decide(cand("LONG", analysis={"confidence": 10}))
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.SCORE_TOO_LOW))
        row = self.s.candidates(ACC)[0]
        self.assertEqual((row["status"], row["reject_reason"]), ("REJECTED", c.SCORE_TOO_LOW))
        self.assertIsNone(self.s.attempt(ACC, "BTCUSDT", "15m", 1000))
        self.assertEqual(self.s.held_margin(ACC), 0)

    def test_occupied_symbol_rejected_without_claim(self):
        d = self.decide(risk=c.RiskView(occupied=("Original V2 pozisyonu",)))
        self.assertEqual(d.reason, c.OCCUPIED)
        self.assertIsNone(self.s.attempt(ACC, "BTCUSDT", "15m", 1000))
        self.assertEqual(self.s.held_margin(ACC), 0)

    def test_arm_and_safety_gates_block(self):
        self.assertEqual(self.decide(risk=c.RiskView(arm_blocked=("ARM süresi doldu",))).reason, c.ARM_BLOCKED)
        self.assertEqual(self.decide(risk=c.RiskView(safety_blocked=("account_fence",))).reason, c.SAFETY_GATE)
        self.assertEqual(self.s.held_margin(ACC), 0)

    def test_gate_precedence_arm_then_safety_then_occupied(self):
        risk = c.RiskView(occupied=("x",), arm_blocked=("y",), safety_blocked=("z",))
        self.assertEqual(self.decide(risk=risk).reason, c.ARM_BLOCKED)
        self.assertEqual(self.decide(risk=c.RiskView(occupied=("x",), safety_blocked=("z",))).reason, c.SAFETY_GATE)

    def test_same_candle_second_attempt_rejected(self):
        self.assertEqual(self.decide(grid="F1").status, c.CONFIRMED)
        d = self.decide(grid="F2")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.ENTRY_LOCKED))
        self.assertAlmostEqual(self.s.held_margin(ACC), 50.0)

    def test_no_retry_in_same_candle_after_temporary_rejection(self):
        self.assertEqual(self.decide(grid="F1").status, c.CONFIRMED)
        self.s.set_attempt_outcome("F1", "REJECTED_TEMP")        # borsa geçici ret
        self.s.release_hold("F1")
        d = self.decide(grid="F2")
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.ENTRY_LOCKED))
        self.assertEqual(self.s.attempt(ACC, "BTCUSDT", "15m", 1000)["outcome"], "REJECTED_TEMP")
        self.assertEqual(self.decide(cand("LONG", ms=2000), grid="F3").status, c.CONFIRMED)   # sonraki mum serbest

    def test_margin_cap(self):
        self.assertEqual(self.decide(grid="F1", cap=100).status, c.CONFIRMED)
        self.assertEqual(self.decide(cand("LONG", symbol="ETHUSDT"), grid="F2", cap=100).status, c.CONFIRMED)
        d = self.decide(cand("LONG", symbol="SOLUSDT"), grid="F3", cap=100)
        self.assertEqual((d.status, d.reason), (c.REJECTED, c.MARGIN_CAP))

    def test_confirmation_runs_before_risk_so_reason_is_strategy(self):
        d = self.decide(cand("LONG", analysis={"momentum": "Negatif"}), risk=c.RiskView(occupied=("x",)))
        self.assertEqual(d.reason, c.MOMENTUM_MISMATCH)


if __name__ == "__main__":
    unittest.main()

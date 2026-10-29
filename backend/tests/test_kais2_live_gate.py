"""Kais 2 canlı giriş kapısı: yalnız LONG ve güven >= 84 (yalnız daraltıcı)."""
import os
import unittest
from unittest import mock

from app.strategies.contracts import QualityCheck, StrategyProvenance, StrategyResult, StrategySignal
from app.strategies import original_v2_demo as demo
from app.strategies.original_v2_demo import (
    KAIS2_CONFIDENCE_REJECT_REASON, KAIS2_DIRECTION_REJECT_REASON, KAIS2_GATE_FLAG, apply_live_kais2_gate,
)


def make(direction: str, confidence, eligible: bool = True) -> StrategyResult:
    decision = ("BUY" if direction == "LONG" else "SELL") if eligible else "WAIT"
    signal = StrategySignal(
        decision=decision, strategy_eligible=eligible, symbol="BTCUSDT", decision_time=1, signal_open_time=0,
        latest_closed_timestamps={}, provenance=StrategyProvenance("s", "v", "h"), market_type=None, exit_policy_id=None,
        direction=direction, entry=100.0, stop=95.0, tp1=105.0, tp2=110.0, tp3=115.0, atr=1.0, invalidation=None,
        reason="READY" if eligible else "QUALITY_OR_MTF_GATE", reasons=(), quality_checks=(QualityCheck("confidence", True, 85, 80),),
        diagnostics={}, overlay=None,
    )
    legacy = {"analysis": {"direction": direction, "confidence": confidence}, "entry_eligible": eligible,
              "decision": decision, "reason": signal.reason, "reasons": []}
    return StrategyResult(signal=signal, legacy=legacy)


def check(result: StrategyResult, key: str) -> QualityCheck:
    return next(c for c in result.signal.quality_checks if c.key == key)


class Kais2LiveGateTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {KAIS2_GATE_FLAG: "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_long_at_or_above_threshold_keeps_entry(self):
        for confidence in (84, 84.0, 90, 100):
            result = apply_live_kais2_gate(make("LONG", confidence))
            self.assertTrue(result.signal.strategy_eligible, confidence)
            self.assertTrue(result.legacy["entry_eligible"])
            self.assertTrue(check(result, "kais2_long_only").passed)
            self.assertTrue(check(result, "kais2_confidence").passed)

    def test_long_below_threshold_is_blocked(self):
        for confidence in (80, 83, 83.99):
            result = apply_live_kais2_gate(make("LONG", confidence))
            self.assertFalse(result.signal.strategy_eligible, confidence)
            self.assertEqual(result.signal.decision, "WAIT")
            self.assertEqual(result.signal.reason, KAIS2_CONFIDENCE_REJECT_REASON)
            self.assertFalse(result.legacy["entry_eligible"])
            self.assertEqual(result.legacy["decision"], "WAIT")
            self.assertTrue(check(result, "kais2_long_only").passed)
            self.assertFalse(check(result, "kais2_confidence").passed)

    def test_short_is_always_blocked(self):
        for confidence in (80, 84, 99):
            result = apply_live_kais2_gate(make("SHORT", confidence))
            self.assertFalse(result.signal.strategy_eligible, confidence)
            self.assertEqual(result.signal.reason, KAIS2_DIRECTION_REJECT_REASON)
            self.assertFalse(check(result, "kais2_long_only").passed)

    def test_short_below_threshold_lists_both_reasons(self):
        result = apply_live_kais2_gate(make("SHORT", 80))
        self.assertEqual(result.signal.reasons, (KAIS2_DIRECTION_REJECT_REASON, KAIS2_CONFIDENCE_REJECT_REASON))
        self.assertEqual(result.legacy["reasons"], [KAIS2_DIRECTION_REJECT_REASON, KAIS2_CONFIDENCE_REJECT_REASON])

    def test_missing_or_invalid_confidence_fails_closed(self):
        for confidence in (None, "90", True, float("nan"), float("inf")):
            result = apply_live_kais2_gate(make("LONG", confidence))
            self.assertFalse(result.signal.strategy_eligible, confidence)
            self.assertEqual(result.signal.reason, KAIS2_CONFIDENCE_REJECT_REASON)
            self.assertFalse(check(result, "kais2_confidence").passed)

    def test_never_opens_a_rejected_signal(self):
        result = apply_live_kais2_gate(make("LONG", 90, eligible=False))
        self.assertFalse(result.signal.strategy_eligible)
        self.assertEqual(result.signal.reason, "QUALITY_OR_MTF_GATE")
        self.assertEqual(result.legacy["decision"], "WAIT")

    def test_already_rejected_signal_keeps_its_reason(self):
        result = apply_live_kais2_gate(make("SHORT", 80, eligible=False))
        self.assertEqual(result.signal.reason, "QUALITY_OR_MTF_GATE")
        self.assertFalse(check(result, "kais2_long_only").passed)

    def test_analysis_is_untouched_for_profit_guard(self):
        original = make("SHORT", 90)
        result = apply_live_kais2_gate(original)
        self.assertEqual(result.legacy["analysis"], original.legacy["analysis"])

    def test_no_direction_or_analysis_untouched(self):
        waiting = make("BEKLE", 90, eligible=False)
        self.assertIs(apply_live_kais2_gate(waiting), waiting)
        bare = StrategyResult(signal=make("LONG", 90).signal, legacy={})
        self.assertIs(apply_live_kais2_gate(bare), bare)

    def test_flag_off_leaves_result_unchanged(self):
        for value in ("false", "0", "off", "no"):
            with mock.patch.dict(os.environ, {KAIS2_GATE_FLAG: value}):
                original = make("SHORT", 80)
                self.assertIs(apply_live_kais2_gate(original), original)

    def test_flag_defaults_to_on_and_rejects_garbage(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop(KAIS2_GATE_FLAG, None)
            self.assertTrue(demo.kais2_gate_enabled())
        with mock.patch.dict(os.environ, {KAIS2_GATE_FLAG: "maybe"}):
            with self.assertRaises(demo.DemoDecisionError):
                demo.kais2_gate_enabled()

    def test_evaluate_live_universe_default_does_not_apply_gate(self):
        import inspect
        self.assertFalse(inspect.signature(demo.evaluate_live_universe).parameters["kais2_gate"].default)


if __name__ == "__main__":
    unittest.main()

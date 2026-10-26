"""Canlı Original V2: 15 dakikalık momentum onayı (yalnız daraltıcı)."""
import unittest

from app.strategies.contracts import QualityCheck, StrategyProvenance, StrategyResult, StrategySignal
from app.strategies.original_v2_demo import MOMENTUM_REJECT_REASON, apply_live_momentum_gate


def make(direction: str, momentum: str, eligible: bool = True) -> StrategyResult:
    decision = ("BUY" if direction == "LONG" else "SELL") if eligible else "WAIT"
    signal = StrategySignal(
        decision=decision, strategy_eligible=eligible, symbol="BTCUSDT", decision_time=1, signal_open_time=0,
        latest_closed_timestamps={}, provenance=StrategyProvenance("s", "v", "h"), market_type=None, exit_policy_id=None,
        direction=direction, entry=100.0, stop=95.0, tp1=105.0, tp2=110.0, tp3=115.0, atr=1.0, invalidation=None,
        reason="READY" if eligible else "QUALITY_OR_MTF_GATE", reasons=(), quality_checks=(QualityCheck("confidence", True, 85, 80),),
        diagnostics={}, overlay=None,
    )
    legacy = {"analysis": {"direction": direction, "momentum": momentum}, "entry_eligible": eligible,
              "decision": decision, "reason": signal.reason, "reasons": []}
    return StrategyResult(signal=signal, legacy=legacy)


class LiveMomentumGateTest(unittest.TestCase):
    def test_aligned_momentum_keeps_entry(self):
        for direction, momentum in (("LONG", "Pozitif"), ("SHORT", "Negatif")):
            result = apply_live_momentum_gate(make(direction, momentum))
            self.assertTrue(result.signal.strategy_eligible)
            self.assertTrue(result.legacy["entry_eligible"])
            self.assertTrue(next(c for c in result.signal.quality_checks if c.key == "momentum_15m").passed)

    def test_neutral_or_opposite_momentum_blocks_entry(self):
        for direction, momentum in (("LONG", "Nötr"), ("LONG", "Negatif"), ("SHORT", "Nötr"), ("SHORT", "Pozitif"), ("LONG", None)):
            result = apply_live_momentum_gate(make(direction, momentum))
            self.assertFalse(result.signal.strategy_eligible)
            self.assertEqual(result.signal.decision, "WAIT")
            self.assertEqual(result.signal.reason, MOMENTUM_REJECT_REASON)
            self.assertFalse(result.legacy["entry_eligible"])
            self.assertEqual(result.legacy["decision"], "WAIT")

    def test_never_opens_a_rejected_signal(self):
        result = apply_live_momentum_gate(make("LONG", "Pozitif", eligible=False))
        self.assertFalse(result.signal.strategy_eligible)
        self.assertEqual(result.signal.reason, "QUALITY_OR_MTF_GATE")

    def test_rejected_signal_reason_unchanged_when_momentum_fails(self):
        result = apply_live_momentum_gate(make("SHORT", "Nötr", eligible=False))
        self.assertEqual(result.signal.reason, "QUALITY_OR_MTF_GATE")
        self.assertFalse(next(c for c in result.signal.quality_checks if c.key == "momentum_15m").passed)

    def test_no_direction_untouched(self):
        original = make("BEKLE", "Nötr", eligible=False)
        self.assertIs(apply_live_momentum_gate(original), original)


if __name__ == "__main__":
    unittest.main()

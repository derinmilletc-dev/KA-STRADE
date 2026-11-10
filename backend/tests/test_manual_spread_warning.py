"""Manuel emirde yüksek spread yalnız uyarıdır (engel değil); otomatik girişte kapı aynen engeller."""
import unittest

from app import execution_core as core


def _gates(**kwargs):
    return core.evaluate_entry_gates(
        symbol="BTCUSDT", signal={"direction": "LONG", "confidence": 100, "radar": {"trap_score": 0}},
        snapshot={"positions": [], "open_orders": [], "available_balance": 100}, policy={}, daily={},
        spread_bps=13.56, armed=True, **kwargs)


class ManualSpreadWarningTests(unittest.TestCase):
    def _spread(self, result):
        return next(item for item in result["gates"] if item["key"] == "spread")

    def test_automatic_entries_are_still_blocked_by_wide_spread(self):
        self.assertFalse(self._spread(_gates())["passed"])

    def test_manual_entries_only_get_a_warning(self):
        gate = self._spread(_gates(enforce_spread=False))
        self.assertTrue(gate["passed"])
        self.assertIn("uyarı", gate["detail"])


if __name__ == "__main__":
    unittest.main()

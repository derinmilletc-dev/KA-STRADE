"""Manuel emirde toplam büyüklük sınırı yalnız uyarıdır (engel değil); otomatik girişte kapı aynen engeller."""
import unittest

from app import execution_core as core


def _gates(positions=None, **kwargs):
    return core.evaluate_entry_gates(
        symbol="BTCUSDT", signal={"direction": "LONG", "confidence": 100, "radar": {"trap_score": 0}},
        snapshot={"positions": positions or [], "open_orders": [], "available_balance": 500}, policy={}, daily={},
        spread_bps=0.0, armed=True, candidate_notional_usdt=1000.0, **kwargs)


class ManualExposureWarningTests(unittest.TestCase):
    def _exposure(self, result):
        return next(item for item in result["gates"] if item["key"] == "exposure")

    def test_automatic_entries_are_still_blocked_above_the_cap(self):
        self.assertFalse(self._exposure(_gates())["passed"])

    def test_manual_entries_only_get_a_warning(self):
        gate = self._exposure(_gates(enforce_exposure=False))
        self.assertTrue(gate["passed"])
        self.assertIn("uyarı", gate["detail"])

    def test_unreadable_positions_still_block_manual_entries(self):
        gate = self._exposure(_gates(positions=[{"symbol": "ETHUSDT"}], enforce_exposure=False))
        self.assertFalse(gate["passed"])


if __name__ == "__main__":
    unittest.main()

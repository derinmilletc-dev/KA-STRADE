import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from app import manual_trail as mt  # noqa: E402


class TrailMathTests(unittest.TestCase):
    def test_pct_bounds(self):
        for good in ("0.3", 1, "10", 2.5):
            self.assertIsNotNone(mt.valid_trail_pct(good), good)
        for bad in ("0.29", 0, -1, "10.01", "abc", None, "NaN", "Infinity"):
            self.assertIsNone(mt.valid_trail_pct(bad), bad)

    def test_long_follows_price_up_and_never_down(self):
        # mark 110, %2 -> 107.8; mevcut stop 100 -> yeni 107.8
        self.assertEqual(mt.trail_target("LONG", D("110"), D("100"), D("2"), D("0.1")), D("107.8"))
        # fiyat düşse stop gevşemez: mark 104 -> 101.92 > 100 ise yine yukarı, 99'da asla aşağı değil
        self.assertIsNone(mt.trail_target("LONG", D("101"), D("100"), D("2"), D("0.1")))
        self.assertIsNone(mt.trail_target("LONG", D("90"), D("100"), D("2"), D("0.1")))

    def test_short_follows_price_down(self):
        self.assertEqual(mt.trail_target("SHORT", D("90"), D("100"), D("2"), D("0.1")), D("91.8"))
        self.assertIsNone(mt.trail_target("SHORT", D("99"), D("100"), D("2"), D("0.1")))
        self.assertIsNone(mt.trail_target("SHORT", D("120"), D("100"), D("2"), D("0.1")))

    def test_small_moves_are_ignored(self):
        # mesafe 2.2; eşik 0.55: 0.3'lük iyileşme taşınmaz, 0.6'lık taşınır
        self.assertIsNone(mt.trail_target("LONG", D("110"), D("107.5"), D("2"), D("0.1")))
        self.assertEqual(mt.trail_target("LONG", D("110"), D("107.2"), D("2"), D("0.1")), D("107.8"))

    def test_tick_rounding_never_gets_closer_than_the_distance(self):
        long_target = mt.trail_target("LONG", D("110.07"), D("100"), D("2"), D("0.5"))
        self.assertEqual(long_target, D("107.5"))  # 107.8686 -> aşağı
        self.assertLessEqual(long_target, D("110.07") * D("0.98"))
        short_target = mt.trail_target("SHORT", D("90.07"), D("100"), D("2"), D("0.5"))
        self.assertEqual(short_target, D("92.0"))  # 91.8714 -> yukarı
        self.assertGreaterEqual(short_target, D("90.07") * D("1.02"))

    def test_garbage_inputs_are_rejected(self):
        self.assertIsNone(mt.trail_target("FLAT", D("1"), D("1"), D("1")))
        self.assertIsNone(mt.trail_target("LONG", D("0"), D("1"), D("1")))
        self.assertIsNone(mt.trail_target("LONG", D("NaN"), D("1"), D("1")))
        self.assertIsNone(mt.trail_target("LONG", D("110"), D("100"), D("2"), D("0")))

    def test_interval_and_failure_counter(self):
        trail = mt.trail_config(D("1"), "now")
        self.assertTrue(mt.trail_due(trail, 1000.0))
        trail["last_move_epoch"] = 990.0
        self.assertFalse(mt.trail_due(trail, 1000.0))
        self.assertTrue(mt.trail_due(trail, 1021.0))
        self.assertFalse(mt.record_trail_failure(trail, "x"))
        self.assertFalse(mt.record_trail_failure(trail, "x"))
        self.assertTrue(mt.record_trail_failure(trail, "x"))
        mt.record_trail_move(trail)
        self.assertEqual(trail["failures"], 0)


if __name__ == "__main__":
    unittest.main()

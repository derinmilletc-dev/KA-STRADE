import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.dca_engine import plan as dca  # noqa: E402

RULES = {"tick": 0.01, "step": 0.001, "min_qty": 0.001, "min_notional": 5}
POLICY = {"max_leverage": 5, "max_loss_per_trade": 10, "daily_loss_limit": 20, "max_total_exposure_usdt": 500, "max_positions": 5, "max_stop_distance_pct": 5}
BASE = dict(direction="LONG", price=100.0, base_margin_usdt=10.0, safety_orders=3, step_pct=1.5, volume_multiplier=1.5,
            take_profit_pct=1.0, stop_gap_pct=1.5, leverage=3, rules=RULES, policy=POLICY)


def make(**override):
    return dca.prepare_dca_plan(**{**BASE, **override})


class DcaPlanMathTests(unittest.TestCase):
    def test_long_levels_average_and_worst_loss(self):
        plan = make()
        stages = plan["stages"]
        self.assertEqual([s["entry"] for s in stages], [100.0, 98.5, 97.0, 95.5])
        self.assertEqual([s["qty"] for s in stages], [0.3, 0.45, 0.675, 1.012])
        self.assertAlmostEqual(stages[3]["avg_entry"], 236.446 / 2.437, places=6)
        self.assertEqual(stages[0]["tp_price"], 101.0)
        self.assertEqual(plan["stop_price"], 94.06)
        self.assertAlmostEqual(plan["total_notional_usdt"], 236.446, places=4)
        self.assertAlmostEqual(plan["total_margin_usdt"], 236.446 / 3, places=4)
        raw = 0.3 * 5.94 + 0.45 * 4.44 + 0.675 * 2.94 + 1.012 * 1.44
        fees = 236.446 * dca.TAKER_FEE + 2.437 * 94.06 * dca.TAKER_FEE
        self.assertAlmostEqual(plan["worst_loss_usdt"], raw + fees, places=4)

    def test_tp_moves_down_as_average_drops_and_profit_is_positive(self):
        stages = make()["stages"]
        self.assertTrue(all(a["tp_price"] > b["tp_price"] for a, b in zip(stages, stages[1:])))
        self.assertTrue(all(s["tp_net_profit_usdt"] > 0 for s in stages))

    def test_short_is_mirror_image(self):
        plan = make(direction="SHORT")
        stages = plan["stages"]
        self.assertEqual([s["entry"] for s in stages], [100.0, 101.5, 103.0, 104.5])
        self.assertGreater(plan["stop_price"], 104.5)
        self.assertLess(stages[0]["tp_price"], stages[0]["entry"])
        self.assertGreater(plan["liquidation_estimate"], plan["stop_price"])
        self.assertGreater(plan["liquidation_gap_pct"], 0)

    def test_multiplier_one_gives_equal_quantities(self):
        plan = make(volume_multiplier=1.0)
        self.assertEqual(len({s["qty"] for s in plan["stages"]}), 1)

    def test_liquidation_is_beyond_stop_for_long(self):
        plan = make()
        self.assertLess(plan["liquidation_estimate"], plan["stop_price"])


class DcaPlanRejectionTests(unittest.TestCase):
    def reject(self, text=None, **override):
        with self.assertRaises(dca.DcaPlanError) as ctx:
            make(**override)
        if text:
            self.assertIn(text, str(ctx.exception))
        return ctx.exception

    def test_input_limits(self):
        self.reject("LONG veya SHORT", direction="BUY")
        self.reject("sıfırdan büyük", price=0)
        self.reject("sıfırdan büyük", base_margin_usdt=float("nan"))
        self.reject("sıfırdan büyük", step_pct=-1)
        self.reject("tam sayı", safety_orders=2.5)
        self.reject("1 ile 5", safety_orders=6)
        self.reject("1 ile 5", safety_orders=0)
        self.reject("çarpan", volume_multiplier=2.5)
        self.reject("çarpan", volume_multiplier=0.9)
        self.reject("aralık", step_pct=0.1)
        self.reject("Kâr hedefi", take_profit_pct=0.05)
        self.reject("Stop mesafesi", stop_gap_pct=0.1)
        self.reject("Kaldıraç en fazla 5x", leverage=6)
        self.reject("Kaldıraç en fazla 2x", policy={**POLICY, "max_leverage": 2})

    def test_exchange_rules_missing_is_503(self):
        error = self.reject("okunamadı", rules={})
        self.assertEqual(error.status, 503)

    def test_order_below_min_notional(self):
        self.reject("en düşük emir değerinin altında", base_margin_usdt=1.0, leverage=2)

    def test_take_profit_must_cover_fees(self):
        with patch.object(dca, "MIN_TP_PCT", 0.01):     # alt sınır normalde bunu zaten engeller; ücret kontrolü ayrıca çalışmalı
            self.reject("işlem ücretini karşılamıyor", take_profit_pct=0.05)

    def test_risk_policy_limits(self):
        self.reject("tek işlem sınırı", policy={**POLICY, "max_loss_per_trade": 1})
        self.reject("kalan zarar hakkı", realized_pnl_today=-14.0)
        self.reject("kalan zarar hakkı", reserved_worst_loss=15.0)
        self.reject("maruziyet", existing_exposure=400.0)
        self.reject("sınırı dolu", open_positions=5)
        self.reject("sınır %", policy={**POLICY, "max_stop_distance_pct": 1.0})

    def test_span_and_liquidation_guards(self):
        self.reject("uzaklık", safety_orders=5, step_pct=6.0, stop_gap_pct=3.0, policy={**POLICY, "max_loss_per_trade": 1000, "daily_loss_limit": 1000})
        self.assertGreater(make()["liquidation_gap_pct"], dca.LIQ_BUFFER_PCT)
        loose = {**POLICY, "max_leverage": 20, "max_loss_per_trade": 1000, "daily_loss_limit": 1000, "max_total_exposure_usdt": 100000}
        with patch.object(dca, "MAX_LEVERAGE", 20), patch.object(dca, "MAX_SPAN_PCT", 60.0):   # sınırlar gevşetilince likidasyon tamponu tek başına reddeder
            self.reject("likidasyona", leverage=20, step_pct=3.0, safety_orders=5, stop_gap_pct=3.0, policy=loose)


if __name__ == "__main__":
    unittest.main()

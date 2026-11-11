import re
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from app import manual_preflight as mp  # noqa: E402

POLICY = {"max_positions": 2, "max_total_exposure_usdt": 350, "max_loss_per_trade": 1.0, "daily_loss_limit": 50,
          "max_spread_bps": 20, "allow_long": True, "allow_short": True}


def position(symbol="ETHUSDT", direction="LONG", notional=100.0):
    return {"symbol": symbol, "direction": direction, "quantity": notional, "mark_price": 1.0}


def report(**over):
    base = dict(symbol="BTCUSDT", direction="LONG", margin_usdt=20.0, leverage=5, estimated_loss_usdt=0.8,
                snapshot={"positions": [], "open_orders": [], "available_balance": 500.0, "hedge_mode": False, "multi_assets_mode": False},
                policy=POLICY, daily={"entries": 0, "realized_pnl": 0, "unverified_closures": 0, "consecutive_losses": 0},
                spread_bps=2.0, active_plans=[], readiness_gates=[], budget_left=None, committed=0.0,
                symbol_occupied=False, symbol_occupancy_detail="", grid_busy=False, isolated_error=None)
    base.update(over)
    return mp.build_report(**base)


def failed(result):
    return {item["key"]: item for item in result["checks"] if not item["passed"]}


class FitMarginTests(unittest.TestCase):
    def test_loss_scales_with_margin(self):
        self.assertEqual(mp.fit_margin(30.0, 3.0, 1.0), 9.8)
        self.assertIsNone(mp.fit_margin(30.0, 3.0, 0.1))      # 5 USDT altına düşerse öneri yok
        self.assertIsNone(mp.fit_margin(10.0, 0.5, 1.0))      # zaten sığıyorsa öneri yok
        self.assertIsNone(mp.fit_margin(10.0, 0.0, 1.0))


class PreflightReportTests(unittest.TestCase):
    def test_clean_account_passes(self):
        result = report()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["first_blocker"])

    def test_max_loss_exceeded_offers_smaller_margin(self):
        result = report(margin_usdt=30.0, estimated_loss_usdt=3.0)
        item = failed(result)["max_loss"]
        self.assertEqual(item["fix"], {"type": "reduce_margin", "margin_usdt": 9.8})
        self.assertFalse(result["ok"])

    def test_position_limit_full_points_to_positions(self):
        snap = {"positions": [position("ETHUSDT"), position("SOLUSDT", "SHORT")], "open_orders": [], "available_balance": 500.0,
                "hedge_mode": False, "multi_assets_mode": False}
        item = failed(report(snapshot=snap))["gate_positions"]
        self.assertEqual(item["fix"], {"type": "positions"})

    def test_same_coin_open_points_to_other_coin(self):
        snap = {"positions": [position("BTCUSDT")], "open_orders": [], "available_balance": 500.0, "hedge_mode": False, "multi_assets_mode": False}
        self.assertEqual(failed(report(snapshot=snap))["gate_duplicate"]["fix"], {"type": "other_coin"})
        self.assertEqual(failed(report(symbol_occupied=True, symbol_occupancy_detail="x"))["symbol_occupancy"]["fix"], {"type": "other_coin"})

    def test_low_balance_offers_margin_that_fits(self):
        snap = {"positions": [], "open_orders": [], "available_balance": 12.0, "hedge_mode": False, "multi_assets_mode": False}
        item = failed(report(snapshot=snap, margin_usdt=20.0, estimated_loss_usdt=0.5))["collateral"]
        self.assertEqual(item["fix"], {"type": "reduce_margin", "margin_usdt": 11.7})

    def test_daily_budget_shortfall_offers_fit_or_positions(self):
        item = failed(report(margin_usdt=20.0, estimated_loss_usdt=0.8, budget_left=0.5))["risk_budget"]
        self.assertEqual(item["fix"], {"type": "reduce_margin", "margin_usdt": 12.2})
        none_left = failed(report(budget_left=0.0))["risk_budget"]
        self.assertEqual(none_left["fix"], {"type": "positions"})
        self.assertTrue(report(budget_left=5.0)["ok"])

    def test_exposure_overflow_is_only_a_warning_for_manual_orders(self):
        snap = {"positions": [position("ETHUSDT", notional=300.0)], "open_orders": [], "available_balance": 500.0,
                "hedge_mode": False, "multi_assets_mode": False}
        result = report(snapshot=snap, margin_usdt=20.0, leverage=5)
        self.assertNotIn("gate_exposure", failed(result))
        item = next(check for check in result["checks"] if check["key"] == "gate_exposure")
        self.assertTrue(item["passed"])
        self.assertIn("uyarı", item["detail"])

    def test_account_mode_and_readiness_are_reported(self):
        snap = {"positions": [], "open_orders": [], "available_balance": 500.0, "hedge_mode": True, "multi_assets_mode": True}
        result = report(snapshot=snap, readiness_gates=[{"key": "consent", "passed": False, "detail": "İzin yok"}], isolated_error="Marjin türü")
        bad = failed(result)
        self.assertIn("one_way", bad)
        self.assertIn("multi_assets", bad)
        self.assertIn("isolated", bad)
        self.assertEqual(bad["readiness"]["fix"], {"type": "permission"})
        self.assertEqual(result["first_blocker"]["key"], "readiness")

    def test_unknown_loss_skips_loss_checks(self):
        self.assertNotIn("max_loss", {item["key"] for item in report(estimated_loss_usdt=None)["checks"]})


class PreflightRouteTests(unittest.TestCase):
    def test_route_requires_owner_and_never_creates_orders(self):
        source = (BACKEND / "app" / "v25_execution.py").read_text(encoding="utf-8")
        match = re.search(r'@router\.post\("/order/preflight"\)', source)
        self.assertIsNotNone(match)
        block = source[match.start():source.find("class GridPlanRequest", match.start())]
        self.assertIn("execution_owner(request)", block)
        self.assertNotIn("submit_entry", block)
        self.assertNotIn("account_reservations.reserve", block)
        self.assertIn('"created_order": False', block)

    def test_route_reads_account_from_cache_so_it_cannot_queue_the_real_order(self):
        # Ön kontrol her ekran değişikliğinde çağrılır; taze (önbelleksiz) okuma gerçek emri hesap kilidinde 30 sn bekletiyordu.
        source = (BACKEND / "app" / "v25_execution.py").read_text(encoding="utf-8")
        match = re.search(r'@router\.post\("/order/preflight"\)', source)
        block = source[match.start():source.find("class GridPlanRequest", match.start())]
        self.assertIn('account_snapshot(client, cached=True, source="manual_preflight")', block)


if __name__ == "__main__":
    unittest.main()

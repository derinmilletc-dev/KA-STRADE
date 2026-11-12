"""Ön kontrol: saf hesap. Kayıp/kaldıraç/stop kuralları ve Türkçe sebepler."""
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.coinbase.limits import HARD_MAX_LOSS_USD, current_limits  # noqa: E402
from app.coinbase.preflight import run_preflight, worst_case_loss  # noqa: E402

PID = "ETP-20DEC30-CDE"
PRODUCT = {"product_id": PID, "contract_size": D("0.01"), "base_increment": D("1"), "price_increment": D("0.5"), "base_min_size": D("1"), "status": "online"}
ACCOUNT = {"balance": {
    "worst_margin_level": "MARGIN_LEVEL_TYPE_BASE", "worst_margin_window": "intraday",
    "futures_buying_power": {"value": "500"}, "available_margin": {"value": "500"},
    "intraday_window": {"futures_buying_power": "500"}, "overnight_window": {"futures_buying_power": "400"},
}}
REQ = {"product_id": PID, "side": "BUY", "stop_price": "2940", "margin_usd": "60"}


def go(request=None, **over):
    args = dict(request={**REQ, **(request or {})}, product=PRODUCT, bid=D("3000"), ask=D("3000.5"), account=ACCOUNT,
                positions=[], open_orders=[], daily_loss_usd=D("0"), limits=current_limits())
    args.update(over)
    return run_preflight(**args)


class PreflightTests(unittest.TestCase):
    def test_ok_plan_respects_all_rules(self):
        r = go()
        self.assertTrue(r["ok"], r["reasons"])
        plan = r["plan"]
        self.assertLessEqual(D(plan["worst_case_loss_usd"]), HARD_MAX_LOSS_USD)
        self.assertTrue(D("3") <= D(plan["leverage"]) <= D("5"))
        self.assertLess(D(plan["stop_price"]), D("3000"))
        self.assertLess(D(plan["stop_limit_price"]), D(plan["stop_price"]))  # long: limit stop'un da altında

    def test_short_limit_above_stop(self):
        r = go({"side": "SELL", "stop_price": "3060", "margin_usd": "55"})
        self.assertTrue(r["ok"], r["reasons"])
        self.assertGreater(D(r["plan"]["stop_limit_price"]), D(r["plan"]["stop_price"]))

    def test_worst_loss_uses_limit_price_and_fees(self):
        loss = worst_case_loss(side="BUY", entry=D("3000"), limit_price=D("2900"), contracts=10, contract_size=D("0.01"), fee_rate=D("0.001"))
        self.assertEqual(loss, D("10") + D("0.5900"))

    def test_no_stop_is_rejected(self):
        for bad in ("", None, "0", "-5", "abc", "NaN"):
            with self.subTest(bad=bad):
                r = go({"stop_price": bad})
                self.assertFalse(r["ok"])

    def test_stop_wrong_side(self):
        self.assertFalse(go({"stop_price": "3100"})["ok"])
        self.assertFalse(go({"side": "SELL", "stop_price": "2900"})["ok"])

    def test_stop_distance_limits(self):
        from dataclasses import replace
        wide_min = replace(current_limits(), min_stop_distance_pct=D("0.01"))
        r = go({"stop_price": "2990"}, limits=wide_min)           # %0,4 uzak ama alt sınır %1
        self.assertFalse(r["ok"])
        self.assertIn("çok yakın", " ".join(r["reasons"]))
        self.assertFalse(go({"stop_price": "2000"})["ok"])       # çok uzak

    def test_explicit_contracts_over_loss_rule(self):
        r = go({"contracts": 50})
        self.assertFalse(r["ok"])
        self.assertTrue(any("5 USD" in x for x in r["reasons"]))

    def test_one_contract_too_big(self):
        product = {**PRODUCT, "contract_size": D("1")}
        r = go(product=product)
        self.assertFalse(r["ok"])
        self.assertIn("sığmıyor", " ".join(r["reasons"]))

    def test_leverage_below_min_and_above_max(self):
        self.assertFalse(go({"margin_usd": "500"})["ok"])   # kaldıraç çok düşük
        self.assertFalse(go({"margin_usd": "10", "contracts": 6})["ok"])  # çok yüksek

    def test_missing_product_info_rejected(self):
        self.assertFalse(go(product={"product_id": PID})["ok"])

    def test_wide_spread_rejected(self):
        self.assertFalse(go(bid=D("2900"), ask=D("3000"))["ok"])

    def test_crossed_or_zero_prices_rejected(self):
        self.assertFalse(go(bid=D("3001"), ask=D("3000"))["ok"])
        self.assertFalse(go(bid=D("0"), ask=D("3000"))["ok"])

    def test_buying_power_uses_worst_window(self):
        acct = {"balance": {**ACCOUNT["balance"], "overnight_window": {"futures_buying_power": "20"}}}
        r = go(account=acct)
        self.assertFalse(r["ok"])
        self.assertIn("Alım gücü", " ".join(r["reasons"]))

    def test_missing_buying_power_rejected(self):
        acct = {"balance": {**ACCOUNT["balance"], "overnight_window": {}}}
        self.assertFalse(go(account=acct)["ok"])
        self.assertFalse(go(account={})["ok"])

    def test_unknown_margin_level_is_unhealthy(self):
        acct = {"balance": {**ACCOUNT["balance"], "worst_margin_level": "SOMETHING_NEW"}}
        self.assertFalse(go(account=acct)["ok"])

    def test_counts(self):
        pos = [{"product_id": f"P{i}"} for i in range(3)]
        self.assertFalse(go(positions=pos)["ok"])
        self.assertFalse(go(positions=[{"product_id": PID}])["ok"])
        self.assertFalse(go(open_orders=[{}] * 5)["ok"])
        self.assertTrue(go(open_orders=[{}] * 4)["ok"])

    def test_daily_loss(self):
        self.assertFalse(go(daily_loss_usd=D("9.5"))["ok"])
        self.assertTrue(go(daily_loss_usd=D("2"))["ok"])

    def test_all_reasons_are_listed_together(self):
        r = go({"contracts": 50}, daily_loss_usd=D("20"), positions=[{"product_id": PID}])
        self.assertGreaterEqual(len(r["reasons"]), 3)

    def test_env_can_only_tighten(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"COINBASE_MAX_LOSS_USD": "50", "COINBASE_LEVERAGE_MAX": "20", "COINBASE_MAX_POSITIONS": "9"}):
            lim = current_limits()
        self.assertEqual(lim.max_loss_usd, D("5"))
        self.assertEqual(lim.leverage_max, D("5"))
        self.assertEqual(lim.max_positions, 3)
        with patch.dict(os.environ, {"COINBASE_MAX_LOSS_USD": "2", "COINBASE_LEVERAGE_MAX": "4"}):
            lim = current_limits()
        self.assertEqual(lim.max_loss_usd, D("2"))
        self.assertEqual(lim.leverage_max, D("4"))


if __name__ == "__main__":
    unittest.main()

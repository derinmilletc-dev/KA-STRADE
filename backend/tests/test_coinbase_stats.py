"""Coinbase istatistikleri: komisyon dahil net, kısmi kapanış, kaynak bağlama (saf hesap)."""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.coinbase import activity, stats  # noqa: E402

P = "AVP-20DEC30-CDE"
SIZES = {P: Decimal(10)}


def fill(side, size, price, fee, day, oid):
    return {"order_id": oid, "product_id": P, "side": side, "price": Decimal(price), "size": Decimal(size),
            "commission": Decimal(fee), "time": f"2026-11-2{day}T10:00:00+00:00"}


def feed_of(fills, roles):
    return activity.build_feed(activity.group_fills(fills), roles, SIZES)


class StatsTests(unittest.TestCase):
    def test_stop_and_partial_closes_are_net_of_fees(self):
        fills = [fill("BUY", 3, "6.76", "0.5", 1, "a"), fill("SELL", 3, "6.55", "0.5", 2, "b"),
                 fill("BUY", 2, "7", "0.4", 3, "c"), fill("SELL", 1, "7.5", "0.2", 4, "d"), fill("SELL", 1, "7.2", "0.2", 5, "e")]
        feed = feed_of(fills, {"a": "entry", "c": "entry", "d": "close", "e": "close"})
        trades, unknown = stats.build_trades(feed)
        self.assertEqual(unknown, 0)
        self.assertEqual([(t["kind"], str(t["net"])) for t in trades], [("stop", "-7.30"), ("manual_close", "4.60"), ("manual_close", "1.60")])
        # kısmi kapanış: giriş komisyonu kapatılan oranda paylaşıldı (0.4 → 0.2 + 0.2)
        self.assertEqual(sum(t["fees"] for t in trades), Decimal("1.0") + Decimal("0.4") + Decimal("0.4"))

    def test_summary_and_sources(self):
        fills = [fill("BUY", 3, "6.76", "0.5", 1, "a"), fill("SELL", 3, "6.55", "0.5", 2, "b"),
                 fill("BUY", 2, "7", "0.4", 3, "c"), fill("SELL", 2, "7.5", "0.4", 4, "d")]
        trades, unknown = stats.build_trades(feed_of(fills, {"a": "entry", "c": "entry", "d": "close"}))
        stats.attach_sources(trades, [{"time": "2026-11-21T09:59:00+00:00", "product_id": P, "source": "kais"},
                                      {"time": "2026-11-23T09:59:00+00:00", "product_id": P, "source": "manual"}])
        summary = stats.summarize(trades, unknown)
        self.assertEqual((summary["trades"], summary["wins"], summary["losses"]), (2, 1, 1))
        self.assertEqual(summary["win_rate"], 50.0)
        self.assertEqual(summary["net"], "1.90")          # -7.30 (stop) + 9.20 (10.00 brüt - 0.40 giriş - 0.40 çıkış komisyonu)
        self.assertEqual(summary["fees"], "1.80")
        self.assertEqual(summary["profit_factor"], 1.26)  # 9.20 / 7.30
        self.assertEqual(summary["by_source"]["kais"], {"trades": 1, "wins": 0, "net": "-7.30"})
        self.assertEqual(summary["by_source"]["manual"], {"trades": 1, "wins": 1, "net": "9.20"})
        self.assertEqual(summary["by_exit"], {"target": 0, "stop": 1, "manual_close": 1})
        self.assertEqual(summary["by_coin"][0]["coin"], "AVP")

    def test_unknown_close_is_counted_not_guessed(self):
        trades, unknown = stats.build_trades(feed_of([fill("SELL", 1, "7", "0.2", 5, "z")], {"z": "close"}))
        self.assertEqual((trades, unknown), ([], 1))
        empty = stats.summarize([], 1)
        self.assertEqual((empty["trades"], empty["win_rate"], empty["net"], empty["profit_factor"]), (0, None, "0.00", None))


class NetBalanceTests(unittest.TestCase):
    def _trades(self):
        return [
            {"time": "2026-11-22T08:00:00+00:00", "gross": Decimal("-1.00"), "fees": Decimal("0.30"), "net": Decimal("-1.30")},
            {"time": "2026-11-23T08:00:00+00:00", "gross": Decimal("2.00"), "fees": Decimal("0.40"), "net": Decimal("1.60")},
            {"time": "2026-11-23T09:00:00+00:00", "gross": Decimal("3.70"), "fees": Decimal("0.67"), "net": Decimal("3.03")},
        ]

    def test_fees_of_todays_trades_are_deducted(self):
        out = stats.net_balance(Decimal("100.01"), Decimal("5.70"), self._trades(), "2026-11-23")
        self.assertEqual((out["fees"], out["net_today"], out["net_balance"], out["exact"]), ("1.07", "4.63", "104.64", True))

    def test_falls_back_to_utc_day_when_gross_does_not_match(self):
        out = stats.net_balance(Decimal("100"), Decimal("9"), self._trades(), "2026-11-23")
        self.assertEqual((out["fees"], out["exact"]), ("1.07", False))

    def test_no_realized_pnl_means_no_fee_deduction(self):
        out = stats.net_balance(Decimal("100.01"), Decimal("0"), self._trades(), "2026-11-23")
        self.assertEqual((out["fees"], out["net_balance"]), ("0.00", "100.01"))


if __name__ == "__main__":
    unittest.main()

"""Master Trade işlem detayı: kayıttaki alanların listeye güvenle aktarılması (yalnız gösterim)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as live  # noqa: E402


def closed_plan(**extra):
    plan = {
        "id": "p1", "symbol": "SOLUSDT", "direction": "SHORT", "status": "KAPANDI",
        "pnl_verified": True, "realized_pnl": -0.19, "exit_price": 7.97, "quantity": 2,
        "created_at": "2026-10-25T09:00:00+00:00", "opened_at": "2026-10-25T09:00:02+00:00",
        "closed_at": "2026-10-25T10:13:18+00:00", "close_reason": "MANUAL", "initial_risk_usdt": 1,
    }
    plan.update(extra)
    return plan


class LiveJournalDisplayFieldTests(unittest.TestCase):
    def row(self, **extra):
        rows = live.verified_live_journal({"plans": {"p1": closed_plan(**extra)}})
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_stored_fields_are_passed_through(self):
        row = self.row(
            entry_price="8.10", stop_loss="8.20", initial_stop_loss="8.30", targets=["7.9", 7.8, {"price": "7.7"}, 7.6],
            leverage=5, applied_leverage=3, margin_usdt="5.4", notional_usdt=16.2, breakeven_at="2026-10-25T09:30:00+00:00",
            original_tp1_complete=True, commission_usdt=0.01, gross_realized_pnl=-0.18, funding_included=False,
        )
        self.assertEqual(row["planned_entry_price"], 8.10)
        self.assertEqual(row["stop_loss"], 8.30)  # ilk stop tercih edilir
        self.assertEqual(row["targets"], [7.9, 7.8, 7.7])
        self.assertEqual(row["leverage"], 3)
        self.assertEqual(row["margin_usdt"], 5.4)
        self.assertEqual(row["notional_usdt"], 16.2)
        self.assertEqual(row["order_submitted_at"], "2026-10-25T09:00:00+00:00")
        self.assertEqual(row["opened_at"], "2026-10-25T09:00:02+00:00")
        self.assertEqual(row["created_at"], "2026-10-25T10:13:18+00:00")
        self.assertEqual(row["breakeven_at"], "2026-10-25T09:30:00+00:00")
        self.assertTrue(row["tp1_done"])
        self.assertEqual(row["commission_usdt"], 0.01)
        self.assertFalse(row["funding_included"])

    def test_missing_or_bad_values_become_none_not_invented(self):
        row = self.row(entry_price="abc", stop_loss=float("nan"), targets="7.9", leverage=True, margin_usdt=None, created_at=123)
        for key in ("planned_entry_price", "stop_loss", "leverage", "margin_usdt", "notional_usdt",
                    "order_submitted_at", "breakeven_at", "commission_usdt", "gross_realized_pnl"):
            self.assertIsNone(row[key], key)
        self.assertEqual(row["targets"], [])
        self.assertFalse(row["tp1_done"])

    def test_existing_fields_and_filters_unchanged(self):
        row = self.row()
        self.assertEqual(row["kind"], "LIVE_POSITION_CLOSED")
        self.assertEqual(row["r_multiple"], -0.19)
        self.assertEqual(row["price"], 7.97)
        self.assertEqual(live.verified_live_journal({"plans": {"p1": closed_plan(pnl_verified=False)}}), [])


if __name__ == "__main__":
    unittest.main()

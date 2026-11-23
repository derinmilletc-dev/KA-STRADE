"""Coinbase izleyici: kârı koru bildirimi ve günlük zarar uyarısı (saf karar + tek seferlik gönderim)."""
import asyncio
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.coinbase import journal, watcher  # noqa: E402

P = "AVP-20DEC30-CDE"


def position(current, contracts="3", side="FUTURES_POSITION_SIDE_LONG", entry="100"):
    return {"product_id": P, "side": side, "number_of_contracts": contracts, "avg_entry_price": entry, "current_price": current}


def item(target="110", stop="98"):
    return {"product_id": P, "state": "protected", "stop_price": stop, "take_profit_price": target}


class ProfitAlertTests(unittest.TestCase):
    def test_threshold_and_advice(self):
        self.assertIsNone(watcher.profit_alert(position("106.9"), item()))            # %69: henüz değil
        key, title, body = watcher.profit_alert(position("107"), item())
        self.assertIn("%70", title); self.assertIn("Yarısını", body); self.assertEqual(key, f"{P}:100:110")
        self.assertIsNone(watcher.profit_alert(position("110"), item()))              # hedefe ulaştı: emir zaten çalışır
        self.assertIsNone(watcher.profit_alert(position("95"), item()))               # zararda

    def test_single_contract_and_short(self):
        _, _, body = watcher.profit_alert(position("107", contracts="1"), item())
        self.assertIn("Stopu girişe", body)
        self.assertIsNone(watcher.profit_alert(position("107", contracts="1"), item(stop="100.5")))   # stop zaten kârda
        short = position("93", side="FUTURES_POSITION_SIDE_SHORT")
        self.assertIsNotNone(watcher.profit_alert(short, item(target="90", stop="102")))
        self.assertIsNone(watcher.profit_alert(position("107"), {**item(), "take_profit_price": None}))   # hedef yok


class DailyAlertTests(unittest.TestCase):
    def test_levels_and_unlimited(self):
        d = Decimal
        self.assertIsNone(watcher.daily_alert(d("30"), d("50")))
        self.assertEqual(watcher.daily_alert(d("40"), d("50"))[0], "daily_warn")
        self.assertEqual(watcher.daily_alert(d("50"), d("50"))[0], "daily_hit")
        self.assertIsNone(watcher.daily_alert(d("500"), d("1000000")))               # limit koymamış
        self.assertIsNone(watcher.daily_alert(d("0"), d("50")))


class SendOnceTests(unittest.TestCase):
    def test_profit_alert_sent_once(self):
        send = AsyncMock()
        report = {"positions": [item()]}
        with patch.object(journal, "has_event", AsyncMock(side_effect=[False, True])), patch.object(journal, "record", AsyncMock()) as record:
            first = asyncio.run(watcher.check_profit_alerts(None, "u1", [position("107")], report, send=send))
            second = asyncio.run(watcher.check_profit_alerts(None, "u1", [position("107")], report, send=send))
        self.assertEqual((len(first), len(second)), (1, 0))
        self.assertEqual(send.await_count, 1)
        self.assertEqual(record.await_args.args[3], "profit_hint")

    def test_daily_alert_once_per_level_and_day(self):
        from app.coinbase import flow, service, user_limits
        limits = type("L", (), {"daily_loss_limit_usd": Decimal("50")})()
        account = {"balance": {"daily_realized_pnl": {"value": "-45"}}}
        send = AsyncMock()
        now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        with patch.object(user_limits, "effective", AsyncMock(return_value=limits)), patch.object(service, "fetch_account", AsyncMock(return_value=account)), \
                patch.object(journal, "has_event", AsyncMock(side_effect=[False, True])), patch.object(journal, "record", AsyncMock()) as record:
            first = asyncio.run(watcher.check_daily_loss(None, "u1", object(), send=send, now=now))
            second = asyncio.run(watcher.check_daily_loss(None, "u1", object(), send=send, now=now))
        self.assertEqual((first, second), ("daily_warn", None))
        self.assertEqual(send.await_count, 1)
        self.assertEqual(record.await_args.kwargs["detail"]["key"], "2026-11-23")
        self.assertTrue(flow.daily_loss_from_account(account) == Decimal(45))


class CombineTests(unittest.TestCase):
    def test_single_passes_through_and_many_merge(self):
        one = ("close", "A", "x")
        self.assertEqual(watcher.combine([one]), one)
        self.assertIsNone(watcher.combine([]))
        cat, title, body = watcher.combine([("info", "A", "a"), ("close", "B", "b"), ("info", "C", "")])
        self.assertEqual((cat, title), ("close", "Coinbase: 3 yeni gelişme"))
        self.assertIn("A: a", body); self.assertIn("\nC", body)
        _, _, long_body = watcher.combine([("info", f"T{i}", "") for i in range(8)])
        self.assertIn("3 bildirim daha", long_body)


if __name__ == "__main__":
    unittest.main()

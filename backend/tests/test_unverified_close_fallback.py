import asyncio
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as live  # noqa: E402


def now_ms() -> int:
    return int(time.time() * 1000)


class Client:
    def __init__(self, rows):
        self.rows = rows

    async def signed(self, method, path, params=None):
        return self.rows if path == "/fapi/v1/userTrades" else []


def plan(created_ms):
    return {"id": "p1", "symbol": "PUMPUSDT", "direction": "SHORT", "status": "PNL DOĞRULANIYOR", "pnl_verified": False,
            "created_at": datetime.fromtimestamp(created_ms / 1000, timezone.utc).isoformat(),
            "entry_order_id": 1, "exchange_order_ids": [], "close_client_order_ids": []}


class UnverifiedCloseFallbackTests(unittest.TestCase):
    def setUp(self):
        persist = patch.object(live, "persist_state")
        persist.start()
        self.addCleanup(persist.stop)
        review = patch.object(live, "write_trade_review", return_value=None)
        review.start()
        self.addCleanup(review.stop)

    def rows(self, base):
        return [
            {"orderId": 1, "side": "SELL", "price": "1.0", "qty": "10", "time": base + 1000, "realizedPnl": "0", "commission": "0.005", "commissionAsset": "USDT"},
            {"orderId": 99, "side": "BUY", "price": "0.9", "qty": "10", "time": base + 60000, "realizedPnl": "1.0", "commission": "0.005", "commissionAsset": "USDT"},
        ]

    def test_close_by_unknown_order_id_is_strict_without_fallback(self):
        base = now_ms() - 600_000
        with self.assertRaises(live.LiveExchangeError):
            asyncio.run(live.verified_plan_pnl(Client(self.rows(base)), plan(base)))

    def test_symbol_window_fallback_verifies_external_close(self):
        base = now_ms() - 600_000
        result = asyncio.run(live.verified_plan_pnl(Client(self.rows(base)), plan(base), symbol_fallback=True))
        self.assertEqual(result["attribution"], "SYMBOL_WINDOW")
        self.assertAlmostEqual(result["realized_pnl"], 0.99, places=6)

    def test_settle_retries_with_fallback_and_stops_zeroing_arm(self):
        base = now_ms() - 600_000
        state = live.initial_state()
        p = plan(base)
        state["plans"] = {p["id"]: p}
        state["armed_until"] = time.time() + 1000
        asyncio.run(live.settle_closed_plan(Client(self.rows(base)), state, p))
        self.assertEqual(p["status"], "KAPANDI")
        self.assertTrue(p["pnl_verified"])
        self.assertGreater(state["armed_until"], time.time())

    def test_fallback_not_used_when_another_plan_shares_the_symbol(self):
        base = now_ms() - 600_000
        state = live.initial_state()
        p = plan(base)
        other = {**plan(base), "id": "p2", "status": "AKTİF", "pnl_verified": None}
        state["plans"] = {p["id"]: p, other["id"]: other}
        asyncio.run(live.settle_closed_plan(Client(self.rows(base)), state, p))
        self.assertEqual(p["status"], "PNL DOĞRULANIYOR")


if __name__ == "__main__":
    unittest.main()

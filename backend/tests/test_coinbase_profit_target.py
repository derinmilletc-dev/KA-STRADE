"""Coinbase kâr hedefi: saf karar + izleyici kapatma/temizleme akışı."""
import asyncio
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.coinbase import profit_target as pt  # noqa: E402

P = "AVP-20DEC30-CDE"


def pos(pnl, entry="100"):
    return {"product_id": P, "unrealized_pnl": pnl, "avg_entry_price": entry}


class PureTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(pt.parse_usd("5"), Decimal("5.00"))
        for bad in ("0", "-1", "abc", "nan", "inf", "99999999"):
            self.assertIsNone(pt.parse_usd(bad))

    def test_net_includes_fee(self):
        self.assertEqual(pt.net_pnl(pos("10"), "1.5"), Decimal("8.5"))
        self.assertEqual(pt.net_pnl(pos("10"), None), Decimal("10"))
        self.assertIsNone(pt.net_pnl(pos(None), "1"))
        self.assertFalse(pt.reached(pos("10"), "1.5", Decimal("9")))
        self.assertTrue(pt.reached(pos("10"), "1.5", Decimal("8.5")))


class CheckTests(unittest.TestCase):
    def run_check(self, position, saved, close=None):
        send = AsyncMock()
        close = close or AsyncMock(return_value={})
        clear = AsyncMock()
        with patch.object(pt, "list_targets", AsyncMock(return_value=saved)), patch.object(pt, "clear_target", clear), \
             patch.object(pt.activity, "fetch_fills", AsyncMock(return_value=[])), patch.object(pt.activity, "position_fees", return_value={P: "1"}), \
             patch.object(pt.journal, "record", AsyncMock()), patch.object(pt.journal, "has_event", AsyncMock(return_value=False)), \
             patch("app.coinbase.flow.close_flow", close):
            done = asyncio.run(pt.check_targets(object(), "u", object(), [position] if position else [], send=send))
        return done, send, close, clear

    def test_closes_when_reached(self):
        done, send, close, clear = self.run_check(pos("11"), {P: {"target_usd": "10", "entry_price": "100"}})
        self.assertEqual(done, [P]); close.assert_awaited_once(); clear.assert_awaited_once(); self.assertIn("doldu", send.await_args.args[3])

    def test_waits_below_target(self):
        done, _, close, _ = self.run_check(pos("10.5"), {P: {"target_usd": "10", "entry_price": "100"}})
        self.assertEqual(done, []); close.assert_not_awaited()

    def test_stale_target_is_dropped(self):
        for position in (None, pos("50", entry="101")):
            done, _, close, clear = self.run_check(position, {P: {"target_usd": "10", "entry_price": "100"}})
            self.assertEqual(done, []); close.assert_not_awaited(); clear.assert_awaited_once()

    def test_close_failure_keeps_target_and_warns(self):
        done, send, _, clear = self.run_check(pos("11"), {P: {"target_usd": "10", "entry_price": "100"}}, close=AsyncMock(side_effect=RuntimeError("x")))
        self.assertEqual(done, []); clear.assert_not_awaited(); self.assertIn("kapanamadı", send.await_args.args[3])


if __name__ == "__main__":
    unittest.main()

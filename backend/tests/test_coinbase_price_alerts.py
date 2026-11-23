"""Coinbase fiyat alarmları: yön, geçiş, tek seferlik tetikleme."""
import asyncio
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.coinbase import price_alerts as pa  # noqa: E402

P = "AVP-20DEC30-CDE"


class PureTests(unittest.TestCase):
    def test_parse_and_direction(self):
        self.assertEqual(pa.parse_level("1,5"), D("1.5"))
        for bad in ("0", "-1", "x", "nan", "inf", ""):
            self.assertIsNone(pa.parse_level(bad))
        self.assertEqual(pa.direction_for(D("11"), D("10")), "above")
        self.assertEqual(pa.direction_for(D("9"), D("10")), "below")
        self.assertIsNone(pa.direction_for(D("10"), D("10")))

    def test_crossed_and_message(self):
        self.assertTrue(pa.crossed("above", D("10"), D("10")))
        self.assertFalse(pa.crossed("above", D("10"), D("9.99")))
        self.assertTrue(pa.crossed("below", D("10"), D("9")))
        title, body = pa.message(P, "below", D("9.5"), D("9.4"))
        self.assertIn("AVP", title); self.assertIn("altına", body)


class CheckTests(unittest.TestCase):
    def run_check(self, alerts, quotes, claim=True):
        send = AsyncMock()
        with patch.object(pa, "_claim", AsyncMock(return_value=claim)):
            fired = asyncio.run(pa.check_user(object(), "u", alerts, quotes, send=send))
        return fired, send

    def alert(self, direction="above", level="10", id=1):
        return {"id": id, "product_id": P, "level": level, "direction": direction, "ref_price": "9"}

    def test_fires_once_when_crossed(self):
        fired, send = self.run_check([self.alert()], {P: {"mid": "10.2"}})
        self.assertEqual(fired, [1]); send.assert_awaited_once()

    def test_waits_when_not_crossed_or_no_price(self):
        for quotes in ({P: {"mid": "9.9"}}, {P: {"mid": None}}, {}):
            fired, send = self.run_check([self.alert()], quotes)
            self.assertEqual(fired, []); send.assert_not_awaited()

    def test_already_claimed_is_skipped(self):
        fired, send = self.run_check([self.alert()], {P: {"mid": "11"}}, claim=False)
        self.assertEqual(fired, []); send.assert_not_awaited()

    def test_send_failure_does_not_break(self):
        send = AsyncMock(side_effect=RuntimeError("x"))
        with patch.object(pa, "_claim", AsyncMock(return_value=True)):
            fired = asyncio.run(pa.check_user(object(), "u", [self.alert(), self.alert("below", "5", 2)], {P: {"mid": "11"}}, send=send))
        self.assertEqual(fired, [1])


class BinanceRunTests(unittest.TestCase):
    def test_binance_alerts_use_public_prices_and_fire(self):
        class Pool:
            async def fetch(self, sql, *args):
                return [{"id": 7, "user_id": "u", "product_id": "BTCUSDT", "level": "100", "direction": "above", "ref_price": "90", "exchange": "binance"}]
            async def execute(self, *a, **k): return None
            async def fetchrow(self, *a, **k): return {"id": 7}

        class App:
            class state:
                db_pool = Pool()

        pa.reset_schema_cache()
        sent = AsyncMock()
        with patch.object(pa, "binance_prices", AsyncMock(return_value={"BTCUSDT": {"mid": "101"}})), patch.object(pa, "_push", sent):
            total = asyncio.run(pa.run_once(App))
        self.assertEqual(total, 1); sent.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

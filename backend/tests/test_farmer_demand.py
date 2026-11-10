"""KAIS FARMER C4 — akış talebi birleşimi (saf; fastapi/websockets gerekmez)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import demand as d  # noqa: E402


def syms(n, prefix="S"):
    return [f"{prefix}{i}USDT" for i in range(n)]


class TestMerge(unittest.TestCase):
    def test_farmer_added_after_original_order_preserved(self):
        r = d.merge_demand(["AUSDT", "BUSDT"], ["CUSDT", "AUSDT", "DUSDT"])
        self.assertEqual(r.symbols, ["AUSDT", "BUSDT", "CUSDT", "DUSDT"])
        self.assertEqual(r.farmer_included, ["CUSDT", "AUSDT", "DUSDT"])
        self.assertEqual(r.dropped, {})

    def test_never_truncates_original(self):
        base = syms(100)
        r = d.merge_demand(base, ["ZUSDT"])
        self.assertEqual(r.symbols, base)
        self.assertEqual(r.dropped, {"ZUSDT": d.NO_ROOM})            # sessiz kırpma yok: açıkça bildirilir
        self.assertNotIn("ZUSDT", r.farmer_included)

    def test_partial_room_reports_overflow(self):
        r = d.merge_demand(syms(98), ["AUSDT", "BUSDT", "CUSDT"])
        self.assertEqual(len(r.symbols), 100)
        self.assertEqual(r.dropped, {"CUSDT": d.NO_ROOM})

    def test_invalid_symbols_reported(self):
        r = d.merge_demand([], ["btcusdt", "BTC-USDT", "BTCUSD", 5, "ETHUSDT"])      # type: ignore[list-item]
        self.assertEqual(r.symbols, ["ETHUSDT"])
        self.assertEqual(set(r.dropped.values()), {d.INVALID})
        self.assertEqual(len(r.dropped), 4)

    def test_overlap_costs_no_room(self):
        base = syms(100)
        r = d.merge_demand(base, base[:5])
        self.assertEqual(r.symbols, base)
        self.assertEqual(r.dropped, {})

    def test_result_is_valid_for_stream_configure(self):
        r = d.merge_demand(syms(60), syms(60, "F"))
        self.assertLessEqual(len(r.symbols), d.STREAM_SYMBOL_LIMIT)
        self.assertEqual(len(set(r.symbols)), len(r.symbols))
        self.assertTrue(all(d.valid_symbol(s) for s in r.symbols))


class TestWithFarmerDemand(unittest.TestCase):
    def app(self, demand=None, has_attr=True):
        state = SimpleNamespace(**({d.STATE_ATTR: demand} if has_attr else {}))
        return SimpleNamespace(state=state)

    def test_farmer_off_returns_same_object(self):
        base = ["AUSDT", "BUSDT"]
        for app in (self.app([]), self.app(None), self.app(has_attr=False), SimpleNamespace(), SimpleNamespace(state=mock.MagicMock())):
            self.assertIs(d.with_farmer_demand(app, base), base)

    def test_farmer_on_adds_symbols(self):
        base = ["AUSDT"]
        out = d.with_farmer_demand(self.app(["BUSDT"]), base)
        self.assertEqual(out, ["AUSDT", "BUSDT"])
        self.assertEqual(base, ["AUSDT"])                                  # girdi değişmez

    def test_original_empty_farmer_only(self):
        self.assertEqual(d.with_farmer_demand(self.app(["BUSDT"]), []), ["BUSDT"])


if __name__ == "__main__":
    unittest.main()

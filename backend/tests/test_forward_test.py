"""İleriye dönük test: sinyal üretimi, kötümser sonuç çözümü, tekrar önleme, veritabanı ve uçtan uca tarama (ağ yok, emir yok)."""
import asyncio
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import forward_test as ft

T0 = 1_700_000_000


def trend(n, drift, base=100.0, start=T0, volume=100.0):
    out, p = [], base
    for i in range(n):
        o = p
        p = p * (1 + drift + 0.002 * math.sin(i / 3))
        out.append({"time": start + i * 900, "open": o, "high": max(o, p) * 1.001, "low": min(o, p) * 0.999, "close": p, "volume": volume})
    return out


def bar(i, o, h, l, c):
    return {"time": T0 + i * 900, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


LONG = {"symbol": "X", "direction": "LONG", "signal_time": T0, "stop": 98.0, "tp1": 104.0, "confidence": 90, "trap": 10}
SHORT = {"symbol": "X", "direction": "SHORT", "signal_time": T0, "stop": 102.0, "tp1": 96.0, "confidence": 90, "trap": 10}
FAR = T0 + 10 * 86400


class ResolveTests(unittest.TestCase):
    def test_tp1_with_costs(self):
        out = ft.resolve(LONG, [bar(0, 100, 101, 99, 100.5), bar(1, 100.5, 104.5, 100, 104)], FAR)
        self.assertEqual(out["hypothetical_result"], "TP1")
        self.assertEqual(out["hypothetical_r_gross"], 2.0)
        self.assertAlmostEqual(out["hypothetical_r_costs"], 2.0 - 0.0015 * 100 / 2, places=3)

    def test_stop_and_same_bar_is_pessimistic(self):
        stop = ft.resolve(LONG, [bar(0, 100, 101, 97.9, 99)], FAR)
        self.assertEqual((stop["hypothetical_result"], stop["hypothetical_r_gross"]), ("STOP", -1.0))
        self.assertLess(stop["hypothetical_r_costs"], -1.0)
        both = ft.resolve(LONG, [bar(0, 100, 105, 97, 100)], FAR)
        self.assertEqual(both["hypothetical_result"], "STOP")

    def test_short_mirrors_long(self):
        self.assertEqual(ft.resolve(SHORT, [bar(0, 100, 100.5, 95.5, 96)], FAR)["hypothetical_result"], "TP1")
        self.assertEqual(ft.resolve(SHORT, [bar(0, 100, 102.1, 99, 101)], FAR)["hypothetical_result"], "STOP")

    def test_pending_then_timeout(self):
        flat = [bar(i, 100, 100.5, 99.5, 100.2) for i in range(10)]
        self.assertIsNone(ft.resolve(LONG, flat, FAR))                      # henüz belli değil, bekler
        full = [bar(i, 100, 100.5, 99.5, 100.5) for i in range(ft.HORIZON_BARS)]
        out = ft.resolve(LONG, full, FAR)
        self.assertEqual(out["hypothetical_result"], "TIMEOUT")
        self.assertAlmostEqual(out["hypothetical_r_gross"], 0.25)

    def test_unclosed_bar_is_ignored_and_invalid_fill(self):
        live_bar = [bar(0, 100, 105, 97, 100)]
        self.assertIsNone(ft.resolve(LONG, live_bar, T0 + 100))             # mum henüz kapanmadı: sonucu görmeyiz
        self.assertEqual(ft.resolve(LONG, [bar(0, 97.0, 98, 96, 97)], FAR)["hypothetical_result"], "INVALID")
        self.assertIsNone(ft.resolve(LONG, [], FAR))

    def test_mfe_before_stop_is_recorded(self):
        out = ft.resolve(LONG, [bar(0, 100, 102.5, 99.5, 102), bar(1, 102, 102.5, 97.5, 98)], FAR)
        self.assertEqual(out["hypothetical_result"], "STOP")
        self.assertGreaterEqual(out["mfe_r"], 1.2)


class SignalTests(unittest.TestCase):
    def test_signal_directions_and_unclosed_candle(self):
        up, down, flat = trend(262, 0.002), trend(262, -0.002), trend(262, 0.0)
        now = up[-1]["time"] + 910
        self.assertEqual(ft.signal_record("X", up, now)["direction"], "LONG")
        self.assertEqual(ft.signal_record("X", down, now)["direction"], "SHORT")
        self.assertIsNone(ft.signal_record("X", flat, now))
        record = ft.signal_record("X", up, now)
        self.assertEqual(record["signal_time"], up[-1]["time"] + 900)
        # son mum henüz kapanmadıysa o mum kullanılmaz
        early = ft.signal_record("X", up, up[-1]["time"] + 100)
        self.assertEqual(early["signal_time"], up[-2]["time"] + 900)
        self.assertIsNone(ft.signal_record("X", up[:100], now))              # yetersiz geçmiş

    def test_universe_filter(self):
        tickers = [{"symbol": "BTCUSDT", "quoteVolume": "900"}, {"symbol": "ETHUSDT", "quoteVolume": "500"}, {"symbol": "BTCUSDT_241227", "quoteVolume": "9000"},
                   {"symbol": "BTCUSD", "quoteVolume": "9000"}, {"symbol": "XUSDT", "quoteVolume": "0"}, {"symbol": "SOLUSDT", "quoteVolume": "700"}]
        self.assertEqual(ft.pick_universe(tickers, 2), ["BTCUSDT", "SOLUSDT"])
        self.assertEqual(ft.pick_universe("bozuk"), [])


class StoreAndScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ft.Store(Path(self.tmp.name) / "f.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_dedupe_cooldown_and_records(self):
        self.assertTrue(self.store.add(dict(LONG)))
        self.assertFalse(self.store.add(dict(LONG)))                          # aynı sinyal mumu iki kez yazılmaz
        self.assertTrue(self.store.recent("X", "LONG", T0 - 1))
        self.assertFalse(self.store.recent("X", "SHORT", T0 - 1))
        self.assertFalse(self.store.recent("X", "LONG", T0))
        row_id, _ = self.store.pending()[0]
        self.store.save_outcome(row_id, {"hypothetical_result": "TP1", "hypothetical_r_costs": 1.9, "hypothetical_r_gross": 2.0})
        (rec,) = self.store.records()
        self.assertEqual((rec["decision"], rec["hypothetical_r_costs"]), ("ACCEPTED", 1.9))
        self.assertEqual(self.store.counts()["resolved"], 1)
        self.store.add({**LONG, "symbol": "Y", "confidence": 79})
        self.store.add({**LONG, "symbol": "Z", "trap": 50})
        decisions = {r["symbol"]: r["decision"] for r in self.store.records()}
        self.assertEqual((decisions["Y"], decisions["Z"]), ("REJECTED", "REJECTED"))   # canlı kapı: güven>=80 ve tuzak<=35

    def test_end_to_end_scan_then_settle(self):
        full = trend(262 + 130, 0.002)
        state = {"now": full[261]["time"] + 905}

        async def get(path, params):
            if path.endswith("ticker/24hr"):
                return [{"symbol": "AAAUSDT", "quoteVolume": "5"}, {"symbol": "BBBUSDT_X", "quoteVolume": "9"}]
            rows = [c for c in full if "startTime" not in params or c["time"] * 1000 >= params["startTime"]]
            rows = [c for c in rows if c["time"] <= state["now"]][-params["limit"]:]
            return [[c["time"] * 1000, c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in rows]

        first = asyncio.run(ft.scan_once(get, self.store, now=state["now"]))
        self.assertEqual(first["new"], 1)
        self.assertEqual(self.store.counts()["pending"], 1)
        again = asyncio.run(ft.scan_once(get, self.store, now=state["now"] + 900))
        self.assertEqual(again.get("new", 0), 0)                              # 4 saat bekleme: aynı yönde tekrar sayılmaz
        state["now"] = full[-1]["time"] + 905
        later = asyncio.run(ft.scan_once(get, self.store, now=state["now"]))
        self.assertEqual(later["resolved"], 1)
        records = sorted(self.store.records(), key=lambda r: r["signal_time"])
        self.assertEqual(len(records), 2)                                     # 32 saat sonra bekleme süresi dolmuş: yeni sinyal de kaydedilir
        first_rec = records[0]
        self.assertIn(first_rec["hypothetical_result"], {"TP1", "STOP", "TIMEOUT"})
        self.assertIsNotNone(first_rec["hypothetical_r_costs"])
        self.assertNotIn("hypothetical_result", records[1])                   # yenisi henüz bekliyor

    def test_one_failing_symbol_does_not_stop_scan(self):
        full = trend(262, 0.002)

        async def get(path, params):
            if path.endswith("ticker/24hr"):
                return [{"symbol": "AAAUSDT", "quoteVolume": "9"}, {"symbol": "BADUSDT", "quoteVolume": "5"}]
            if params.get("symbol") == "BADUSDT":
                raise RuntimeError("ağ hatası")
            return [[c["time"] * 1000, c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in full]

        out = asyncio.run(ft.scan_once(get, self.store, now=full[-1]["time"] + 910))
        self.assertEqual((out["new"], out["errors"]), (1, 1))


if __name__ == "__main__":
    unittest.main()

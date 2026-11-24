"""Coin yorum motoru: trend, kırılım/tuzak, formasyon, seviye, futures, geçmiş doğruluk, pozisyon ilişkisi."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import coin_read as cr  # noqa: E402


def candle(i, close, spread=0.2, volume=100.0, open_=None):
    return {"time": 1000 + i * 60, "open": close if open_ is None else open_, "high": close + spread, "low": close - spread, "close": close, "volume": volume}


def series(closes, volume=100.0):
    return [candle(i, c, 0.2, volume) for i, c in enumerate(closes)]


def flat(n, base=100.0):
    return [base + (0.3 if i % 2 else -0.3) for i in range(n)]


class EventTests(unittest.TestCase):
    def test_too_few_candles(self):
        self.assertIsNone(cr.read_timeframe(series(flat(10)), "15m"))
        self.assertIsNone(cr.detect_event(series(flat(10))))
        self.assertIsNone(cr.read_timeframe([{"open": "x"}], "15m"))

    def test_trend_up_and_down(self):
        up = cr.read_timeframe(series([100 + i * 0.3 for i in range(60)]), "1h")
        down = cr.read_timeframe(series([120 - i * 0.3 for i in range(60)]), "1h")
        self.assertEqual(up["trend"], "up")
        self.assertEqual(down["trend"], "down")
        self.assertGreater(up["score"], 0.75)
        self.assertLess(down["score"], -0.75)

    def test_choppy_is_flat(self):
        read = cr.read_timeframe(series(flat(60)), "15m")
        self.assertEqual(read["trend"], "flat")
        self.assertEqual(read["tone"], "flat")

    def test_bull_trap(self):
        rows = series([*flat(56), 100.1, 101.5, 100.0, 99.9])
        rows[58]["high"] = 102.2
        event = cr.detect_event(rows)
        self.assertEqual(event["kind"], "bull_trap")
        read = cr.read_timeframe(rows, "15m")
        self.assertEqual(read["tone"], "warn")
        self.assertIn("tuzağı", read["head"])

    def test_bear_trap(self):
        rows = series([*flat(56), 99.9, 98.5, 100.0, 100.1])
        rows[58]["low"] = 97.8
        self.assertEqual(cr.detect_event(rows)["kind"], "bear_trap")

    def test_breakout_with_volume(self):
        rows = series([*flat(57), 101.5, 102.5, 103])
        for i in (57, 58, 59):
            rows[i]["volume"] = 400
        event = cr.detect_event(rows)
        self.assertEqual(event["kind"], "breakout_up")
        self.assertTrue(event["spike"])
        self.assertIn("Hacim de destekliyor", cr.read_timeframe(rows, "15m")["text"])

    def test_breakdown_without_volume(self):
        read = cr.read_timeframe(series([*flat(57), 98.5, 97.5, 97]), "15m")
        self.assertEqual(read["event"], "breakout_down")
        self.assertIn("Hacim güçlü değil", read["text"])

    def test_pullback_inside_uptrend(self):
        closes = [*[100 + i * 0.4 for i in range(54)], 120.5, 120.0, 119.6, 119.2, 118.8, 118.4]
        read = cr.read_timeframe(series(closes), "1h")
        self.assertEqual(read["pullback"], "up")
        self.assertEqual(read["head"], "Yükselişte geri çekilme")


class IndicatorTests(unittest.TestCase):
    def test_rsi_states(self):
        up = cr.read_timeframe(series([100 + i for i in range(60)]), "15m")
        down = cr.read_timeframe(series([200 - i for i in range(60)]), "15m")
        self.assertEqual(up["rsi_state"], "overbought")
        self.assertEqual(down["rsi_state"], "oversold")

    def test_divergence_rule(self):
        closes = [100.0] * 45
        closes[30], closes[42] = 110.0, 112.0      # ikinci tepe daha yüksek
        rsis = [50.0] * 45
        rsis[30], rsis[42] = 75.0, 65.0            # ama RSI daha düşük
        self.assertEqual(cr.divergence(closes, rsis), "bearish")
        rsis[42] = 80.0                            # RSI de yükselirse uyumsuzluk yok
        self.assertIsNone(cr.divergence(closes, rsis))
        lows = [100.0] * 45
        lows[30], lows[42] = 90.0, 88.0            # ikinci dip daha düşük
        low_rsi = [50.0] * 45
        low_rsi[30], low_rsi[42] = 25.0, 35.0      # RSI daha yüksek
        self.assertEqual(cr.divergence(lows, low_rsi), "bullish")
        self.assertIsNone(cr.divergence(closes[:30], rsis[:30]))

    def test_ema_state(self):
        read = cr.read_timeframe(series([100 + i * 0.3 for i in range(80)]), "1h")
        self.assertEqual(read["ema"], "up")

    def test_patterns(self):
        rows = series(flat(30))
        prev = rows[-2]; prev.update(open=100.6, close=99.9, high=100.7, low=99.8)
        last = rows[-1]; last.update(open=99.8, close=101.0, high=101.1, low=99.7)
        names = [p["name"] for p in cr.candle_patterns(rows)]
        self.assertIn("Yutan boğa mumu", names)
        doji = series(flat(30)); doji[-1].update(open=100.0, close=100.01, high=100.9, low=99.1)
        self.assertEqual(cr.candle_patterns(doji)[0]["name"], "Doji")


class LevelTests(unittest.TestCase):
    def test_levels_cluster_around_repeated_highs(self):
        closes = []
        for i in range(120):  # 110 ile 100 arasında gidip gelen, her seferinde ~110'a değen dalga
            closes.append(100 + (i % 20) * 0.5 if (i % 20) <= 10 else 110 - ((i % 20) - 10) * 0.5)
        rows = series(closes)
        levels = cr.key_levels({"15m": rows, "1h": rows, "4h": rows}, 105.0)
        self.assertTrue(levels["resistance"])
        self.assertTrue(levels["support"])
        self.assertGreater(levels["resistance"][0]["price"], 105)
        self.assertLess(levels["support"][0]["price"], 105)
        self.assertGreater(levels["resistance"][0]["dist_pct"], 0)

    def test_levels_empty_without_data(self):
        self.assertEqual(cr.key_levels({}, 100.0), {"support": [], "resistance": []})


class FuturesTests(unittest.TestCase):
    def test_none_without_data(self):
        self.assertIsNone(cr.read_futures(None, None, None, None))

    def test_funding_and_oi(self):
        crowded = cr.read_futures(0.0008, 3.0, 1.0, 1.2)
        self.assertLess(crowded["bias"] + 0.5, 1.5)
        texts = " ".join(n["text"] for n in crowded["notes"])
        self.assertIn("Fonlama yüksek", texts)
        self.assertIn("yeni para giriyor", texts)
        self.assertIn("alımları baskın", texts)
        weak = cr.read_futures(0.0001, -3.0, 1.0, 0.8)
        joined = " ".join(n["text"] for n in weak["notes"])
        self.assertIn("short kapanışlarından", joined)
        self.assertIn("satımları baskın", joined)


class HistoryTests(unittest.TestCase):
    def test_backtest_counts_and_text(self):
        # Tekrarlayan: 30 mum yatay, sonra kırılım ve devam. 600 mumda çok sayıda olay.
        closes = []
        base = 100.0
        for block in range(30):
            closes += [base + (0.3 if i % 2 else -0.3) for i in range(26)]
            closes += [base + 1.5, base + 2.5, base + 3.0, base + 3.5]
            base += 3.5
        hist = cr.backtest(series(closes))
        up = hist["kinds"]["breakout_up"]
        self.assertGreaterEqual(up["n"], cr.HISTORY_MIN_SAMPLES)
        self.assertEqual(up["hits"], up["n"])
        text = cr.history_text(hist, "breakout_up", "15 dk")
        self.assertIn("%100", text)
        self.assertIn("yukarıda kaldı", text)

    def test_few_samples_is_honest(self):
        text = cr.history_text(cr.backtest(series(flat(80))), "bull_trap", "15 dk")
        self.assertIn("yeterli geçmiş örnek yok", text)
        self.assertIsNone(cr.history_text(None, "bull_trap", "15 dk"))
        self.assertIsNone(cr.history_text(cr.backtest(series(flat(80))), None, "15 dk"))


def trend_candles(step):
    return [candle(i, 100 + i * step, 0.2) for i in range(80)]


class BuildTests(unittest.TestCase):
    def all_tf(self, step_by_tf):
        return {tf: trend_candles(step_by_tf[tf]) for tf in cr.TFS}

    def test_full_agreement_is_high_confidence(self):
        out = cr.build(self.all_tf({tf: 0.3 for tf in cr.TFS}))
        summary = out["summary"]
        self.assertEqual(summary["lean"], "LONG")
        self.assertEqual(summary["confidence"], "Yüksek")
        self.assertEqual(summary["alignment"]["up"], 6)
        self.assertEqual(len(out["timeframes"]), 6)
        self.assertGreater(out["price"], 100)

    def test_conflict_between_short_and_long(self):
        steps = {"1m": 0.3, "5m": 0.3, "15m": 0.3, "1h": -0.3, "4h": -0.3, "1d": -0.3}
        summary = cr.build(self.all_tf(steps))["summary"]
        self.assertIn("ters yönlü", summary["conflict"])
        self.assertEqual(summary["confidence"], "Düşük")

    def test_missing_data_is_graceful(self):
        out = cr.build({})
        self.assertEqual(out["summary"]["title"], "Veri yetersiz")
        self.assertEqual(out["timeframes"], [])
        partial = cr.build({"15m": trend_candles(0.3)})
        self.assertEqual(len(partial["timeframes"]), 1)

    def test_history_attached_to_requested_timeframe(self):
        candles = self.all_tf({tf: 0.3 for tf in cr.TFS})
        hist = cr.backtest(candles["15m"])
        out = cr.build(candles, history=hist, history_tf="15m")
        self.assertEqual(out["history"]["tf"], "15m")

    def test_open_candle_is_dropped(self):
        candles = {"15m": series(flat(60))}
        candles["15m"][-1]["close"] = 150.0   # oluşan mum: kırılım gibi görünür ama kapanmış sayılmaz
        read = cr.build(candles)["timeframes"][0]
        self.assertIsNone(read["event"])


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.up = cr.build({tf: trend_candles(0.3) for tf in cr.TFS})
        self.down = cr.build({tf: [candle(i, 130 - i * 0.3, 0.2) for i in range(80)] for tf in cr.TFS})

    def test_fit(self):
        price = self.up["price"]
        long_fit = cr.position_fit(self.up["summary"], self.up["levels"], price, "LONG", price * 0.98)
        short_fit = cr.position_fit(self.up["summary"], self.up["levels"], price, "SHORT", price * 0.98)
        self.assertEqual(long_fit["fit"], "uyumlu")
        self.assertEqual(short_fit["fit"], "ters")
        self.assertGreater(long_fit["pnl_pct"], 0)
        self.assertLess(short_fit["pnl_pct"], 0)

    def test_against_events_for_long_when_market_turns_down(self):
        reads = {r["tf"]: r for r in self.down["timeframes"]}
        events = cr.position_events(reads, self.down["summary"], "LONG")
        self.assertIn("mtf:against", events)
        self.assertEqual(events["mtf:against"]["tone"], "danger")
        none = cr.position_events({r["tf"]: r for r in self.up["timeframes"]}, self.up["summary"], "LONG")
        self.assertNotIn("mtf:against", none)

    def test_trap_and_mode_all(self):
        rows = series([*flat(56), 100.1, 101.5, 100.0, 99.9])
        rows[58]["high"] = 102.2
        reads = {"15m": cr.read_timeframe(rows, "15m")}
        summary = {"bias": 0.0}
        long_events = cr.position_events(reads, summary, "LONG")
        self.assertEqual(long_events["15m:bull_trap"]["tone"], "danger")
        self.assertEqual(cr.position_events(reads, summary, "SHORT"), {})
        self.assertEqual(cr.position_events(reads, summary, "SHORT", mode="all")["15m:bull_trap"]["tone"], "good")

    def test_min_tf_filters_short_frames(self):
        rows = series([*flat(56), 100.1, 101.5, 100.0, 99.9])
        rows[58]["high"] = 102.2
        reads = {"5m": cr.read_timeframe(rows, "5m")}
        self.assertEqual(cr.position_events(reads, {"bias": 0.0}, "LONG", min_tf="15m"), {})
        self.assertIn("5m:bull_trap", cr.position_events(reads, {"bias": 0.0}, "LONG", min_tf="5m"))


class FormatTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(cr.signed_pct(1.234), "+%1,23")
        self.assertEqual(cr.signed_pct(-0.5), "−%0,50")
        self.assertEqual(cr.price_text(0.452), "0,452")
        self.assertEqual(cr.price_text(1234.5), "1.234,5")


if __name__ == "__main__":
    unittest.main()

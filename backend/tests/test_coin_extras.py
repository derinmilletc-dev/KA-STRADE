"""Yorum ek katmanları: piyasa bağlamı, stop/hedef planı, emir defteri, likidasyon, takvim, doğruluk, giriş fırsatı, kişisel istatistik."""
import asyncio
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import binance_insights as bi, coin_extras as ex, entry_watch as ew, market_events as me, personal_stats as ps, read_log as rl  # noqa: E402
from app.liq_stream import LiquidationStream  # noqa: E402

UTC = timezone.utc
LEVELS = {"support": [{"price": 98.0, "dist_pct": -2.0, "strength": 3, "touches": 2}], "resistance": [{"price": 106.0, "dist_pct": 6.0, "strength": 3, "touches": 2}, {"price": 110.0, "dist_pct": 10.0, "strength": 3, "touches": 2}]}


class PlanTests(unittest.TestCase):
    def test_long_plan_uses_support_and_resistance(self):
        p = ex.trade_plan("LONG", 100.0, LEVELS, 1.0, "Orta")
        self.assertLess(p["stop"], 98.0)
        self.assertAlmostEqual(p["stop"], 98.0 - 0.25, places=6)
        self.assertEqual(p["targets"][0]["price"], 106.0)
        self.assertAlmostEqual(p["rr"], round(6.0 / 2.25, 2))
        self.assertEqual(p["tone"], "good")

    def test_short_plan_mirrors(self):
        levels = {"support": [{"price": 94.0, "dist_pct": -6.0}], "resistance": [{"price": 102.0, "dist_pct": 2.0}]}
        p = ex.trade_plan("SHORT", 100.0, levels, 1.0, "Orta")
        self.assertGreater(p["stop"], 102.0)
        self.assertEqual(p["targets"][0]["price"], 94.0)

    def test_weak_rr_is_called_weak(self):
        levels = {"support": [{"price": 95.0, "dist_pct": -5.0}], "resistance": [{"price": 101.0, "dist_pct": 1.0}]}
        p = ex.trade_plan("LONG", 100.0, levels, 1.0, "Düşük")
        self.assertLess(p["rr"], 1.5)
        self.assertEqual(p["tone"], "warn")
        self.assertIn("zayıf", p["verdict"])

    def test_no_levels_uses_atr_and_two_r(self):
        p = ex.trade_plan("LONG", 100.0, {"support": [], "resistance": []}, 2.0, "Orta")
        self.assertEqual(p["stop_basis"], "atr")
        self.assertAlmostEqual(p["stop"], 97.0)
        self.assertEqual(p["targets"][0]["basis"], "2R")
        self.assertAlmostEqual(p["targets"][0]["price"], 106.0)

    def test_neutral_or_invalid_gives_none(self):
        self.assertIsNone(ex.trade_plan("NEUTRAL", 100.0, LEVELS, 1.0, "Orta"))
        self.assertIsNone(ex.trade_plan("LONG", 100.0, {"support": [], "resistance": []}, None, "Orta"))
        self.assertIsNone(ex.trade_plan("LONG", 100.0, {"support": [{"price": 60.0, "dist_pct": -40}], "resistance": []}, 1.0, "Orta"))   # stop çok uzak


class BookTests(unittest.TestCase):
    def depth(self, bid_q, ask_q, wall=None):
        bids = [[100 - 0.05 * i, bid_q] for i in range(1, 30)]
        asks = [[100 + 0.05 * i, ask_q] for i in range(1, 30)]
        if wall:
            asks[10] = [asks[10][0], wall]
        return {"bids": [[str(p), str(q)] for p, q in bids], "asks": [[str(p), str(q)] for p, q in asks]}

    def test_imbalance_and_walls(self):
        r = ex.book_read(self.depth(30, 10, wall=100), 100.0)
        self.assertGreater(r["imbalance"], 0.2)
        self.assertEqual(len(r["ask_walls"]), 1)
        self.assertTrue(any("satış duvarı" in n for n in r["notes"]))

    def test_balanced(self):
        r = ex.book_read(self.depth(10, 10), 100.0)
        self.assertAlmostEqual(r["imbalance"], 0.0, places=1)
        self.assertEqual(r["bid_walls"], [])

    def test_bad_input(self):
        self.assertIsNone(ex.book_read(None, 100.0))
        self.assertIsNone(ex.book_read({"bids": [["x", "y"]], "asks": []}, 100.0))


class LiquidationTests(unittest.TestCase):
    def test_zones_and_lead(self):
        events = [{"price": 98.5, "usd": 200_000, "side": "SELL"}, {"price": 98.6, "usd": 100_000, "side": "SELL"}, {"price": 101.0, "usd": 20_000, "side": "BUY"}]
        z = ex.liquidation_zones(events, 100.0)
        self.assertEqual(z["long_usd"], 300_000)
        self.assertIn("long", z["text"])
        self.assertLess(z["zones"][0]["price"], 100.0)

    def test_small_total_is_ignored(self):
        self.assertIsNone(ex.liquidation_zones([{"price": 100.0, "usd": 1000, "side": "BUY"}], 100.0))
        self.assertIsNone(ex.liquidation_zones([], 100.0))

    def test_stream_ingest_and_window(self):
        t = [1000.0]
        s = LiquidationStream(clock=lambda: t[0])
        payload = {"e": "forceOrder", "o": {"s": "ETHUSDT", "S": "SELL", "q": "2", "p": "100", "ap": "99", "z": "2"}}
        self.assertEqual(s.ingest(payload), 1)
        self.assertEqual(s.ingest({"stream": "x", "data": payload}), 1)
        self.assertEqual(s.ingest({"e": "other"}), 0)
        self.assertEqual(s.ingest({"e": "forceOrder", "o": {"s": "ETHUSDT", "S": "HOLD", "q": "1", "p": "1"}}), 0)
        self.assertEqual(s.recent("ethusdt")[0]["usd"], 198.0)
        t[0] += 1000
        self.assertEqual(s.recent("ETHUSDT"), [])
        self.assertFalse(s.healthy)


class ContextTests(unittest.TestCase):
    def read(self, lean, eff=0.4, bias=1.0):
        return {"summary": {"lean": lean, "bias": bias}, "timeframes": [{"tf": "1h", "efficiency": eff}]}

    def closes(self, scale, n=60):
        out, p = [], 100.0
        for i in range(n):
            p *= 1 + scale * (0.01 if i % 2 else -0.006)
            out.append({"time": i, "open": p, "high": p, "low": p, "close": p, "volume": 1})
        return out

    def test_correlated_opposite_direction_warns(self):
        own, btc = self.read("LONG"), self.read("SHORT", bias=-1.0)
        r = ex.market_context("ETHUSDT", own, btc, self.closes(1.0), self.closes(1.0))
        self.assertGreaterEqual(r["corr"], 0.9)
        self.assertEqual(r["relation"], "bagli")
        self.assertTrue(any("BTC ters yönde" in n for n in r["notes"]))

    def test_btc_itself_has_only_regime(self):
        r = ex.market_context("BTCUSDT", self.read("LONG"), None, [], [])
        self.assertEqual(r["regime"], "trend")
        self.assertIsNone(r["btc"])

    def test_range_regime(self):
        r = ex.market_context("BTCUSDT", self.read("NEUTRAL", eff=0.05), None, [], [])
        self.assertEqual(r["regime"], "yatay")

    def test_independent(self):
        import random
        random.seed(3)
        a = [{"time": i, "open": 1, "high": 1, "low": 1, "close": 100 + random.random() * 5, "volume": 1} for i in range(60)]
        b = [{"time": i, "open": 1, "high": 1, "low": 1, "close": 100 + random.random() * 5, "volume": 1} for i in range(60)]
        r = ex.market_context("SOLUSDT", self.read("LONG"), self.read("LONG"), a, b)
        self.assertIsNotNone(r["corr"])
        self.assertLess(abs(r["corr"]), 0.6)


class EventsTests(unittest.TestCase):
    def test_fomc_decision_is_14_et_and_flagged(self):
        now = datetime(2026, 12, 9, 15, 30, tzinfo=UTC)   # Aralık: ET = UTC-5 → karar 19:00 UTC
        r = me.upcoming(now)
        fomc = next(i for i in r["items"] if i["kind"] == "fomc")
        self.assertEqual(fomc["at"], "2026-12-09T19:00:00+00:00")
        self.assertEqual(fomc["level"], "warn")
        self.assertIn("22:00", fomc["text"])

    def test_two_hours_before_is_danger_and_after_is_warn(self):
        self.assertEqual(next(i for i in me.upcoming(datetime(2026, 12, 9, 17, 30, tzinfo=UTC) + timedelta(minutes=30))["items"] if i["kind"] == "fomc")["level"], "danger")
        after = me.upcoming(datetime(2026, 12, 9, 19, 10, tzinfo=UTC))
        self.assertTrue(any("önce açıklandı" in i["text"] for i in after["items"]))

    def test_summer_time_changes_utc(self):
        r = me.upcoming(datetime(2027, 6, 10, 10, 0, tzinfo=UTC))
        cpi = next(i for i in r["items"] if i["kind"] == "cpi")
        self.assertEqual(cpi["at"], "2027-06-10T12:30:00+00:00")   # 08:30 EDT

    def test_funding_window(self):
        r = me.upcoming(datetime(2026, 11, 25, 15, 45, tzinfo=UTC), funding_pct=0.0123)
        f = next(i for i in r["items"] if i["kind"] == "funding")
        self.assertEqual(f["minutes"], 15)
        self.assertIn("+0.0123%", f["text"])
        self.assertFalse(any(i["kind"] == "funding" for i in me.upcoming(datetime(2026, 11, 25, 12, 0, tzinfo=UTC))["items"]))

    def test_calendar_end_is_declared(self):
        self.assertTrue(me.upcoming(datetime(2027, 12, 1, tzinfo=UTC))["covered"])
        self.assertFalse(me.upcoming(datetime(2028, 1, 5, tzinfo=UTC))["covered"])


class GradeTests(unittest.TestCase):
    def test_grade(self):
        self.assertEqual(rl.grade("LONG", 100, 100.5), "hit")
        self.assertEqual(rl.grade("LONG", 100, 99.5), "miss")
        self.assertEqual(rl.grade("SHORT", 100, 99.5), "hit")
        self.assertEqual(rl.grade("SHORT", 100, 100.05), "flat")
        self.assertIsNone(rl.grade("NEUTRAL", 100, 101))

    def test_price_at_handles_seconds_and_ms(self):
        base = datetime(2026, 11, 24, 10, 0, tzinfo=UTC).timestamp()
        rows = [{"time": base + i * 900, "open": 1, "high": 1, "low": 1, "close": 100 + i, "volume": 1} for i in range(10)]
        self.assertEqual(rl.price_at(rows, datetime.fromtimestamp(base + 3600 + 100, UTC)), 104)
        ms = [{**r, "time": r["time"] * 1000} for r in rows]
        self.assertEqual(rl.price_at(ms, datetime.fromtimestamp(base + 3600 + 100, UTC)), 104)
        self.assertIsNone(rl.price_at(rows, datetime.fromtimestamp(base + 10 * 900 + 5000, UTC)))   # veri hedefe yetişmemiş

    def test_tally_hides_rate_for_small_samples(self):
        small = rl.tally([{"result": "hit"}] * 5)
        self.assertEqual((small["n"], small["rate"]), (5, None))
        big = rl.tally([{"result": "hit"}] * 6 + [{"result": "miss"}] * 3 + [{"result": "flat"}] * 3)
        self.assertEqual((big["n"], big["rate"]), (12, 67))   # yatay olanlar oranı bozmaz: 6 / (6+3)


class FakePool:
    def __init__(self, rows): self.rows, self.updates = rows, []
    async def execute(self, sql, *args): self.updates.append((sql, args)); return "UPDATE 1"
    async def fetch(self, sql, *args): return self.rows


class App:
    def __init__(self, pool): self.state = type("S", (), {"db_pool": pool})()


class EvaluateTests(unittest.TestCase):
    def test_evaluate_grades_and_gives_up(self):
        rl.reset_state()
        now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
        logged = now - timedelta(hours=3)
        rows = [{"id": 1, "symbol": "BTCUSDT", "logged_at": logged, "lean": "LONG", "price": 100.0},
                {"id": 2, "symbol": "BTCUSDT", "logged_at": now - timedelta(hours=50), "lean": "LONG", "price": 100.0}]
        pool = FakePool(rows)
        base = logged.timestamp()
        candles = [{"time": base + i * 900, "open": 1, "high": 1, "low": 1, "close": 100 + i * 0.1, "volume": 1} for i in range(-2, 40)]

        async def fetch(symbol, tf, limit): return candles
        done = asyncio.run(rl.evaluate_once(App(pool), now=now, fetch=fetch))
        self.assertEqual(done, 1)
        sqls = [u[0] for u in pool.updates if "UPDATE" in u[0]]
        self.assertTrue(any("'skip'" in s for s in sqls))
        graded = next(u for u in pool.updates if "SET later_price" in u[0])
        self.assertEqual(graded[1][2], "hit")


class CalibrationTests(unittest.TestCase):
    def rows(self, bias, hits, misses, **extra):
        return [{"result": "hit", "bias": bias, "confidence": "Orta", **extra}] * hits + [{"result": "miss", "bias": bias, "confidence": "Orta", **extra}] * misses

    def test_bias_buckets_and_ordering(self):
        rows = self.rows(0.6, 6, 6) + self.rows(0.9, 8, 4) + self.rows(1.4, 10, 2)
        out = rl.calibration(rows)
        self.assertEqual([out["by_bias"][k]["rate"] for k in ("zayıf", "orta", "güçlü")], [50, 67, 83])
        self.assertTrue(out["bias_ordered"])
        flipped = rl.calibration(self.rows(0.6, 10, 2) + self.rows(1.4, 6, 6) + self.rows(0.9, 8, 4))
        self.assertFalse(flipped["bias_ordered"])

    def test_not_enough_data_is_honest(self):
        out = rl.calibration(self.rows(0.6, 3, 2))
        self.assertIsNone(out["bias_ordered"])
        self.assertIsNone(out["by_bias"]["zayıf"]["rate"])

    def test_regime_volatility_and_long_horizon(self):
        rows = self.rows(1.0, 8, 4, regime="trend", vol_state="expanding", result_4h="hit") + self.rows(1.0, 2, 10, regime="range", vol_state="quiet", result_4h="miss")
        out = rl.calibration(rows)
        self.assertEqual(out["by_regime"]["trend"]["rate"], 67)
        self.assertEqual(out["by_regime"]["range"]["rate"], 17)
        self.assertEqual(out["by_volatility"]["expanding"]["n"], 12)
        self.assertEqual(out["horizon_4h"]["n"], 24)


class EvaluateLongTests(unittest.TestCase):
    def test_four_hour_grade_and_give_up(self):
        rl.reset_state()
        now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
        logged = now - timedelta(hours=6)
        rows = [{"id": 1, "symbol": "BTCUSDT", "logged_at": logged, "lean": "SHORT", "price": 100.0},
                {"id": 2, "symbol": "BTCUSDT", "logged_at": now - timedelta(hours=50), "lean": "LONG", "price": 100.0}]
        pool = FakePool(rows)
        base = logged.timestamp()
        candles = [{"time": base + i * 900, "open": 1, "high": 1, "low": 1, "close": 100 - i * 0.1, "volume": 1} for i in range(-2, 40)]

        async def fetch(symbol, tf, limit): return candles
        done = asyncio.run(rl.evaluate_long_once(App(pool), now=now, fetch=fetch))
        self.assertEqual(done, 1)
        graded = next(u for u in pool.updates if "SET later_price_4h" in u[0])
        self.assertEqual(graded[1][2], "hit")
        self.assertTrue(any("result_4h = 'skip'" in u[0] for u in pool.updates))


def data(lean="LONG", up=3, down=0, conflict=None, tf_event=("15m", "breakout_up"), spike=True):
    reads = [{"tf": "15m", "label": "15 dk", "event": tf_event[1] if tf_event[0] == "15m" else None, "volume_spike": spike},
             {"tf": "1h", "label": "1 saat", "event": tf_event[1] if tf_event[0] == "1h" else None, "volume_spike": spike}]
    return {"price": 100.0, "atr": 1.0, "levels": LEVELS, "timeframes": reads,
            "summary": {"lean": lean, "confidence": "Orta", "conflict": conflict, "alignment": {"up": up, "down": down, "flat": 5 - up - down, "total": 5}}}


class EntryWatchTests(unittest.TestCase):
    def test_all_conditions_met(self):
        found = ew.detect(data())
        self.assertEqual(list(found), ["15m:breakout_up:LONG"])
        self.assertIn("hacimli yukarı kırılım", found["15m:breakout_up:LONG"]["text"])
        self.assertIsNotNone(found["15m:breakout_up:LONG"]["plan"])

    def test_each_missing_condition_blocks(self):
        self.assertEqual(ew.detect(data(spike=False)), {})
        self.assertEqual(ew.detect(data(up=2)), {})
        self.assertEqual(ew.detect(data(conflict="x")), {})
        self.assertEqual(ew.detect(data(lean="NEUTRAL")), {})
        self.assertEqual(ew.detect(data(tf_event=("15m", "breakout_down"))), {})   # olay yönle uyumsuz
        self.assertEqual(ew.detect(data(), direction="short"), {})

    def test_short_setup_and_trap(self):
        found = ew.detect(data(lean="SHORT", up=0, down=4, tf_event=("1h", "bull_trap")))
        self.assertEqual(list(found), ["1h:bull_trap:SHORT"])

    def test_push_once_with_cooldown(self):
        now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
        found = {"a": {}}
        self.assertEqual(ew.plan_push([], found, None, now), (["a"], ["a"]))
        self.assertEqual(ew.plan_push(["a"], found, now, now + timedelta(minutes=5)), ([], ["a"]))
        self.assertEqual(ew.plan_push([], found, now, now + timedelta(minutes=5)), ([], []))          # soğumada: aktif yazılmaz
        self.assertEqual(ew.plan_push([], found, now, now + timedelta(minutes=31)), (["a"], ["a"]))
        self.assertEqual(ew.plan_push(["a"], {}, now, now + timedelta(hours=1)), ([], []))             # olay geçti, anahtar silinir

    def test_normalize(self):
        self.assertEqual(ew.normalize("btcusdt", "long"), {"symbol": "BTCUSDT", "direction": "long"})
        self.assertEqual(ew.normalize("btcusdt", "zzz")["direction"], "both")
        self.assertIsNone(ew.normalize("../x", "both"))


def trade(coin, side, net, hour, day=1, minutes_after_prev=None):
    opened = datetime(2026, 11, day, hour, 0, tzinfo=timezone(timedelta(hours=3)))
    return {"product_id": f"{coin}-PERP-INTX", "side": side, "net": net, "opened": opened.isoformat(), "time": (opened + timedelta(minutes=30)).isoformat()}


class PersonalTests(unittest.TestCase):
    def test_small_sample_is_honest(self):
        r = ps.analyze([trade("BTC", "LONG", 1, 10)] * 3)
        self.assertFalse(r["enough"])
        self.assertIn("en az 8", r["insights"][0]["text"])

    def test_bad_hour_block_detected(self):
        trades = [trade("BTC", "LONG", -2, 2, day=d) for d in range(1, 6)] + [trade("BTC", "LONG", 3, 14, day=d) for d in range(1, 6)]
        r = ps.analyze(trades)
        self.assertEqual(r["bad_blocks"][0]["from"], 0)
        self.assertEqual(r["bad_blocks"][0]["win_rate"], 0)
        self.assertEqual(r["good_blocks"][0]["from"], 12)
        self.assertTrue(any("00-04" in i["text"] for i in r["insights"]))

    def test_payoff_warning_and_coin(self):
        trades = [trade("DOGE", "LONG", -5, 10 + i, day=1 + i) for i in range(3)] + [trade("BTC", "LONG", 1, 10, day=10 + i) for i in range(3)] + [trade("ETH", "SHORT", 1, 11, day=20 + i) for i in range(3)]
        text = " ".join(i["text"] for i in ps.analyze(trades)["insights"])
        self.assertIn("ortalama kaybın", text.lower())
        self.assertIn("DOGE", text)

    def test_bad_rows_are_skipped(self):
        r = ps.analyze([{"net": "x", "time": "bad"}, {"time": "zzz", "net": 1}] + [trade("BTC", "LONG", 1, 10, day=d) for d in range(1, 9)])
        self.assertEqual(r["n"], 8)


def inc(symbol, ms, amount, tran, kind="REALIZED_PNL"):
    return {"symbol": symbol, "incomeType": kind, "income": str(amount), "time": ms, "asset": "USDT", "tranId": tran}


class BinanceInsightsTests(unittest.TestCase):
    def test_partial_closes_merge_and_other_rows_skip(self):
        base = 1_700_000_000_000
        rows = [inc("BTCUSDT", base, 1.0, 1), inc("BTCUSDT", base + 30_000, 2.0, 2), inc("BTCUSDT", base + 600_000, -1.5, 3),
                inc("ETHUSDT", base + 10_000, 0.5, 4), inc("BTCUSDT", base, 9, 5, kind="COMMISSION"), inc("BTCUSDT", base, 1.0, 1),
                {"symbol": "X", "incomeType": "REALIZED_PNL", "income": "bad", "time": 1}]
        trades = bi.to_trades(rows)
        by = {(t["product_id"], round(t["net"], 2)) for t in trades}
        self.assertEqual(by, {("BTCUSDT", 3.0), ("BTCUSDT", -1.5), ("ETHUSDT", 0.5)})   # kısmi kapanışlar tek işlem, komisyon ve çift kayıt yok

    def test_load_caches_and_reports(self):
        bi.reset()
        calls = []
        base = 1_700_000_000_000
        class Client:
            async def signed(self, method, path, params):
                calls.append(params)
                return [inc("BTCUSDT", base + i * 3_600_000 * 5, -2.0 if i % 3 == 0 else 1.0, i) for i in range(10)]
        out = asyncio.run(bi.load(Client(), "u1", now=1_700_100_000))
        self.assertEqual(out["n"], 10)
        self.assertIn("komisyon", out["caveat"])
        asyncio.run(bi.load(Client(), "u1", now=1_700_100_100))
        self.assertEqual(len(calls), 1)                                  # 10 dk içinde önbellekten
        asyncio.run(bi.load(Client(), "u1", now=1_700_100_000 + 700))
        self.assertEqual(len(calls), 2)

    def test_closed_basis_wording(self):
        trades = [trade("BTC", "", -2, 2, day=d) for d in range(1, 6)] + [trade("BTC", "", 3, 14, day=d) for d in range(1, 6)]
        for t in trades: t["opened"] = None
        r = ps.analyze(trades, basis="closed")
        self.assertTrue(any("kapattığın" in i["text"] for i in r["insights"]))


if __name__ == "__main__":
    unittest.main()

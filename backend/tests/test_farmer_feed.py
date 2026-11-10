"""KAIS FARMER C4 — FarmerFeed / CandidateCache testleri (ağ yok; fastapi/websockets gerekmez)."""
from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.farmer_engine import confirm as c  # noqa: E402
from app.farmer_engine import feed as f  # noqa: E402
from app.farmer_engine.engine import FarmerEngine, farmer_spec  # noqa: E402
from app.farmer_engine.store import FarmerStore  # noqa: E402
from app.grid_engine.fake_exchange import FakeExchange  # noqa: E402
from app.grid_engine.ports import LONG  # noqa: E402

STEP = f.INTERVAL_MS
BOUNDARY = 1_800_000_000_000 // STEP["4h"] * STEP["4h"]       # 15m/1h/4h sınırlarının hepsiyle hizalı
NOW = BOUNDARY + 5 * 60_000                                    # 4h'lik mumun 5. dakikası → son kapanmış 15m = BOUNDARY-15dk


def make_rows(interval: str, now_ms: int, count: int = 262, price: float = 100.0):
    """Gerçekçi akış satırları: kapanmışlar + SON SATIR oluşmakta olan mum (T gelecekte)."""
    step = STEP[interval]
    forming_open = now_ms // step * step
    rows = []
    for k in range(count - 1, -1, -1):
        t = forming_open - k * step
        p = price + (count - 1 - k) * 0.001
        rows.append([t, str(p), str(p * 1.001), str(p * 0.999), str(p), "1000", t + step - 1, "100000", 10, "0", "0", "0"])
    return rows


class FakeStream:
    def __init__(self, symbols, now_ms=NOW, count=262):
        self.now_ms = now_ms
        self.connected = True
        self.subscribed = {(s, iv) for s in symbols for iv in ("15m", "30m", "1h", "4h")}
        self.rows = {(s, iv): make_rows(iv, now_ms, count) for s in symbols for iv in f.INTERVALS}
        self.candle_reads = 0
        self.rest_calls = 0
        self.get_candles_calls = []

    def is_subscribed(self, symbol, interval):
        return (symbol, interval) in self.subscribed

    def candles(self, symbol, interval):
        self.candle_reads += 1
        return [r.copy() for r in self.rows.get((symbol, interval), [])]

    async def get_candles(self, symbol, interval, limit, rest_fetch):
        self.get_candles_calls.append((symbol, interval))
        self.rest_calls += 1
        self.rows[(symbol, interval)] = make_rows(interval, self.now_ms, limit + 2)
        return self.rows[(symbol, interval)][-limit:]

    def advance(self, now_ms):
        """15 dk ilerle: yeni oluşmakta olan mumlar."""
        self.now_ms = now_ms
        for key in list(self.rows):
            self.rows[key] = make_rows(key[1], now_ms)


class Evaluator:
    def __init__(self, table=None):
        self.calls = []
        self.table = table or {}

    def __call__(self, symbol, at, frames, allowed):
        self.calls.append((symbol, at, {iv: (rows[0]["time"], rows[-1]["time"], len(rows)) for iv, rows in frames.items()}, list(allowed)))
        analysis = {"direction": "LONG", "confidence": 94, "momentum": "Pozitif", "entry": 100.0, "atr": 1.0,
                    "radar": {"trap_score": 20, "breakout_quality": 70}, "series": {"huge": list(range(1000))}, "explanation": "x"}
        analysis.update(self.table.get(symbol, {}))
        mtf = {"direction": analysis["direction"], "entry_permission": True, "alignment": 80, "verdict": "GÜÇLÜ ONAY"}
        if analysis["direction"] == "SHORT":
            mtf["short_filter_v2"] = {"accepted": True, "reason": None}
        return SimpleNamespace(legacy={"analysis": analysis, "mtf": mtf, "decision": "WAIT"})


SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def mk(symbols=SYMS, subscribed=None, table=None, **kw):
    stream = FakeStream(subscribed if subscribed is not None else symbols)
    ev = Evaluator(table)
    feed = f.FarmerFeed(stream, evaluator=ev, clock=lambda: 1234.5, **kw)
    asyncio.run(feed.set_demand(symbols))
    return feed, stream, ev


class TestClosedCandle(unittest.TestCase):
    def test_candidates_come_only_from_last_closed_candle(self):
        feed, stream, ev = mk()
        res = feed.scan(NOW)
        closed_ms = BOUNDARY - STEP["15m"] * 1 if NOW // STEP["15m"] * STEP["15m"] == BOUNDARY else NOW // STEP["15m"] * STEP["15m"] - STEP["15m"]
        self.assertEqual(res.candle_open_ms, closed_ms)
        for symbol, at, spans, _ in ev.calls:
            self.assertEqual(at, (closed_ms + STEP["15m"]) // 1000)                 # karar anı = kapanan mumun bitişi
            for iv, (first, last, n) in spans.items():
                self.assertEqual(n, 259)
                self.assertLessEqual(last * 1000 + STEP[iv], at * 1000)             # hiçbir çerçevede oluşmakta olan mum yok
            self.assertEqual(spans["15m"][1] * 1000, closed_ms)
        self.assertTrue(all(cand.candle_open_ms == closed_ms for cand in feed.cache.all()))

    def test_forming_candle_never_in_frames_even_if_last_row_is_newer(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        forming_open_s = (NOW // STEP["15m"] * STEP["15m"]) // 1000
        self.assertTrue(all(spans["15m"][1] < forming_open_s for _, _, spans, _ in ev.calls))

    def test_same_candle_no_duplicate_candidate(self):
        feed, stream, ev = mk()
        first = feed.scan(NOW)
        self.assertEqual((first.scanned, first.reused, first.new_candle), (3, 0, True))
        before = {cand.symbol: cand for cand in feed.cache.all()}
        for later in (NOW + 1_000, NOW + 60_000, NOW + 8 * 60_000):
            again = feed.scan(later)
            self.assertEqual((again.scanned, again.reused, again.new_candle), (0, 3, False))
        self.assertEqual(len(ev.calls), 3)
        self.assertEqual(feed.evaluations, 3)
        self.assertTrue(all(feed.cache.get(s) is before[s] for s in SYMS))          # aynı nesneler

    def test_new_candle_refreshes_cache(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        old = {cand.symbol: cand for cand in feed.cache.all()}
        later = NOW + STEP["15m"]
        stream.advance(later)
        res = feed.scan(later)
        self.assertTrue(res.new_candle)
        self.assertEqual(res.scanned, 3)
        self.assertEqual(len(ev.calls), 6)
        for cand in feed.cache.all():
            self.assertEqual(cand.candle_open_ms, old[cand.symbol].candle_open_ms + STEP["15m"])
            self.assertIsNot(cand, old[cand.symbol])
        self.assertIsNone(feed.cache.get("AAAUSDT", old["AAAUSDT"].candle_open_ms))   # eski mum adayı kalmadı

    def test_clock_going_backwards_does_not_rewind_cache(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        res = feed.scan(NOW - 3 * STEP["15m"])
        self.assertFalse(res.new_candle)
        self.assertEqual(len(ev.calls), 3)

    def test_history_gap_is_data_unavailable_not_candidate(self):
        feed, stream, ev = mk()
        rows = stream.rows[("BBBUSDT", "1h")]
        del rows[100]                                                               # bir mum eksik
        feed.scan(NOW)
        self.assertIn("BBBUSDT", feed.cache.unavailable())
        self.assertEqual(feed.cache.unavailable()["BBBUSDT"], f.DATA_GAP)
        self.assertIsNone(feed.cache.get("BBBUSDT"))
        self.assertEqual({s for s, *_ in ev.calls}, {"AAAUSDT", "CCCUSDT"})

    def test_short_history_is_warmup_and_retried_in_same_candle(self):
        feed, stream, ev = mk()
        stream.rows[("AAAUSDT", "4h")] = stream.rows[("AAAUSDT", "4h")][-50:]
        feed.scan(NOW)
        self.assertEqual(feed.cache.unavailable()["AAAUSDT"], f.WARMUP)
        stream.rows[("AAAUSDT", "4h")] = make_rows("4h", NOW)                       # ortak indirme tamamladı
        res = feed.scan(NOW + 1000)
        self.assertEqual((res.scanned, res.reused), (1, 2))                         # yalnız eksik olan işlendi, full rescan yok
        self.assertIsNotNone(feed.cache.get("AAAUSDT"))
        self.assertEqual(len(ev.calls), 3)


class TestSubscription(unittest.TestCase):
    def test_unsubscribed_symbol_gets_no_candidate_and_no_entry_permission(self):
        feed, stream, ev = mk(symbols=SYMS + ["DDDUSDT"], subscribed=SYMS)
        feed.scan(NOW)
        self.assertIsNone(feed.cache.get("DDDUSDT"))
        self.assertEqual(feed.cache.unavailable()["DDDUSDT"], f.UNSUBSCRIBED)
        self.assertNotIn("DDDUSDT", {s for s, *_ in ev.calls})
        self.assertEqual(feed.entry_data_ok("DDDUSDT", NOW), (False, f.UNSUBSCRIBED))
        self.assertEqual(feed.entry_data_ok("AAAUSDT", NOW), (True, ""))
        status = feed.status()
        self.assertEqual(status["unsubscribed"], {"DDDUSDT": f.UNSUBSCRIBED})
        self.assertNotIn("DDDUSDT", status["subscribed"])

    def test_partially_subscribed_intervals_count_as_unsubscribed(self):
        feed, stream, ev = mk()
        stream.subscribed.discard(("AAAUSDT", "4h"))
        feed.scan(NOW)
        self.assertEqual(feed.cache.unavailable()["AAAUSDT"], f.UNSUBSCRIBED)

    def test_subscription_lost_after_scan_revokes_entry_data(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        self.assertTrue(feed.entry_data_ok("AAAUSDT", NOW)[0])
        stream.subscribed.discard(("AAAUSDT", "15m"))
        self.assertEqual(feed.entry_data_ok("AAAUSDT", NOW), (False, f.UNSUBSCRIBED))

    def test_stream_overflow_symbols_reported_not_silently_dropped(self):
        feed, stream, ev = mk(symbols=SYMS, subscribed=SYMS)
        asyncio.run(feed.set_demand(SYMS + ["btcusdt"]))
        feed.scan(NOW)
        self.assertEqual(feed.status()["unsubscribed"], {"btcusdt": "INVALID_SYMBOL"})

    def test_symbols_that_did_not_fit_in_stream_are_labeled_stream_limit(self):
        async def apply(symbols):
            return [x for x in symbols if x != "CCCUSDT"]          # birleşik listeye sığmadı

        stream = FakeStream(["AAAUSDT", "BBBUSDT"])
        feed = f.FarmerFeed(stream, apply_demand=apply, evaluator=Evaluator())
        asyncio.run(feed.set_demand(SYMS))
        feed.scan(NOW)
        self.assertEqual(feed.status()["unsubscribed"], {"CCCUSDT": "STREAM_LIMIT"})
        self.assertEqual(feed.entry_data_ok("CCCUSDT", NOW), (False, "STREAM_LIMIT"))
        self.assertIsNone(feed.cache.get("CCCUSDT"))

    def test_no_stream_means_nothing_available(self):
        feed = f.FarmerFeed(None, evaluator=Evaluator())
        asyncio.run(feed.set_demand(SYMS))
        feed.scan(NOW)
        self.assertEqual(feed.cache.all(), [])
        self.assertFalse(feed.entry_data_ok("AAAUSDT", NOW)[0])
        self.assertFalse(feed.status()["stream_connected"])

    def test_stream_disconnected_blocks_scan_and_entry(self):
        feed, stream, ev = mk()
        stream.connected = False
        feed.scan(NOW)
        self.assertEqual(ev.calls, [])
        self.assertEqual(set(feed.cache.unavailable().values()), {f.STREAM_DOWN})
        stream.connected = True
        self.assertEqual(feed.scan(NOW + 1000).scanned, 3)                          # geçici neden: yeniden denendi

    def test_set_demand_hands_symbols_to_shared_stream_hook(self):
        seen = []

        async def apply(symbols):
            seen.append(list(symbols))
            return symbols

        stream = FakeStream(SYMS)
        feed = f.FarmerFeed(stream, apply_demand=apply, evaluator=Evaluator())
        asyncio.run(feed.set_demand(SYMS + ["AAAUSDT"]))                            # yineleme tekilleştirilir
        self.assertEqual(seen, [SYMS])
        asyncio.run(feed.set_demand([]))
        self.assertEqual(seen[-1], [])                                              # Farmer kapanınca talep kalkar


class TestCandidateFields(unittest.TestCase):
    def test_fields(self):
        feed, stream, ev = mk(table={"BBBUSDT": {"direction": "SHORT", "momentum": "Negatif", "confidence": 91}})
        feed.scan(NOW)
        b = feed.cache.get("BBBUSDT")
        self.assertEqual((b.symbol, b.interval, b.direction, b.confidence, b.momentum), ("BBBUSDT", "15m", "SHORT", 91.0, "Negatif"))
        self.assertEqual((b.trap_score, b.breakout_quality, b.atr, b.entry_price), (20.0, 70.0, 1.0, 100.0))
        self.assertEqual(b.candle_open_ms, NOW // STEP["15m"] * STEP["15m"] - STEP["15m"])
        self.assertEqual(b.generated_at, 1234.5)
        self.assertEqual(b.mtf["direction"], "SHORT")
        self.assertEqual(b.short_filter, {"accepted": True, "reason": None})
        self.assertNotIn("series", b.snapshot)                                      # büyük seri önbelleğe girmez
        self.assertEqual(b.snapshot["explanation"], "x")
        a = feed.cache.get("AAAUSDT")
        self.assertIsNone(a.short_filter)

    def test_missing_fields_are_none_not_invented(self):
        feed, stream, ev = mk()
        feed._evaluate = lambda *a: SimpleNamespace(legacy={"analysis": {"direction": "LONG"}, "mtf": {}})
        feed.scan(NOW)
        a = feed.cache.get("AAAUSDT")
        self.assertEqual((a.confidence, a.momentum, a.trap_score, a.breakout_quality, a.atr, a.entry_price), (None,) * 6)

    def test_snapshot_is_independent_copy(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        a = feed.cache.get("AAAUSDT")
        a.snapshot["radar"]["trap_score"] = 99
        self.assertEqual(feed.cache.get("AAAUSDT").trap_score, 20.0)                # bu bir alan kopyasıdır
        fc = a.to_farmer_candidate()
        fc.analysis["radar"]["trap_score"] = 1                                      # type: ignore[index]
        self.assertEqual(a.snapshot["radar"]["trap_score"], 99)

    def test_entry_candidates_sorted_and_exclude_bekle(self):
        feed, stream, ev = mk(symbols=SYMS + ["DDDUSDT"], table={
            "AAAUSDT": {"confidence": 90}, "BBBUSDT": {"confidence": 95}, "CCCUSDT": {"direction": "BEKLE", "confidence": 99},
            "DDDUSDT": {"confidence": 90}})
        feed.scan(NOW)
        self.assertEqual([x.symbol for x in feed.cache.entry_candidates()], ["BBBUSDT", "AAAUSDT", "DDDUSDT"])   # eşitlikte sembol sırası
        self.assertEqual(len(feed.cache.all()), 4)

    def test_real_strategy_shape_is_compatible_with_confirmation(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        p = c.ConfirmParams(variant=c.STRATEGY_QUALITY, min_score=92, margin_usdt=50, leverage=3, target_usdt=1.0, max_loss_usdt=1.5)
        r = c.confirm(feed.cache.get("AAAUSDT").to_farmer_candidate(), p)
        self.assertTrue(r.confirmed, r.reasons)


class TestDeadCheckInput(unittest.TestCase):
    def test_input_available_and_consumed_by_engine_api(self):
        feed, stream, ev = mk(table={"AAAUSDT": {"direction": "SHORT", "momentum": "Negatif", "confidence": 60}})
        feed.scan(NOW)
        inp = feed.dead_check_input("AAAUSDT", NOW)
        self.assertEqual((inp.symbol, inp.analysis["direction"], inp.analysis["momentum"], inp.analysis["confidence"]), ("AAAUSDT", "SHORT", "Negatif", 60.0))
        self.assertEqual(inp.candle_open_ms, feed.cache.candle_open_ms)
        self.assertEqual(inp.price_source, "KLINE_LAST")
        self.assertAlmostEqual(inp.price, float(stream.rows[("AAAUSDT", "15m")][-1][4]))
        ex = FakeExchange(tick=0.01, step=0.001)
        ex.prices["AAAUSDT"] = 100.0
        store = FarmerStore()
        eng = FarmerEngine(ex, store, farmer_spec("F1", "AAAUSDT", LONG, 100.0, margin=50, leverage=3, target_usdt=1.0, max_loss_usdt=1.5,
                                                  tick=0.01, step=0.001))
        eng.start()
        self.assertTrue(eng.is_adverse(inp.analysis, 75))                           # girdi motorun ölü tanımına doğrudan uyar
        out = eng.dead_check(inp.analysis, inp.candle_open_ms, 100.0, dead_score=75, safety_margin_usdt=0.05)
        self.assertEqual(out, "DEAD_WAIT")                                          # karar motorda; feed kapatmadı
        self.assertEqual(ex.positions["AAAUSDT"], 1.5)

    def test_bekle_included_for_dead_input(self):
        feed, stream, ev = mk(table={"AAAUSDT": {"direction": "BEKLE", "momentum": "Nötr", "confidence": 55}})
        feed.scan(NOW)
        inp = feed.dead_check_input("AAAUSDT", NOW)
        self.assertEqual(inp.analysis["direction"], "BEKLE")
        self.assertNotIn("AAAUSDT", [x.symbol for x in feed.cache.entry_candidates()])

    def test_none_when_not_scanned_or_stale(self):
        feed, stream, ev = mk()
        self.assertIsNone(feed.dead_check_input("AAAUSDT", NOW))
        feed.scan(NOW)
        self.assertIsNone(feed.dead_check_input("AAAUSDT", NOW + STEP["15m"]))      # bir sonraki mum için analiz henüz yok
        self.assertIsNone(feed.dead_check_input("ZZZUSDT", NOW))

    def test_feed_makes_no_dead_close_decision(self):
        self.assertFalse(any(hasattr(f.FarmerFeed, n) for n in ("dead_check", "reduce_if_dead_profit", "close", "emergency_stop")))


class TestSlotFreedReusesCache(unittest.TestCase):
    def test_slot_freed_in_same_candle_only_reruns_confirmation(self):
        feed, stream, ev = mk(table={"AAAUSDT": {"confidence": 95}, "BBBUSDT": {"confidence": 93}, "CCCUSDT": {"confidence": 91}})
        feed.scan(NOW)
        store = FarmerStore()
        params = c.ConfirmParams(variant=c.CONFIDENCE, min_score=92, margin_usdt=25, leverage=3, target_usdt=0.5, max_loss_usdt=1.0)
        first = c.decide_entry(store, "u:live", feed.cache.entry_candidates()[0].to_farmer_candidate(), params, c.RiskView(), grid_id="F1", cap_usdt=100)
        self.assertEqual(first.status, c.CONFIRMED)
        evaluations = feed.evaluations
        feed.scan(NOW + 4 * 60_000)                                                 # slot boşaldı: tarama TEKRARLANMAZ
        self.assertEqual(feed.evaluations, evaluations)
        second_pick = [x for x in feed.cache.entry_candidates() if x.symbol != "AAAUSDT"][0]
        second = c.decide_entry(store, "u:live", second_pick.to_farmer_candidate(), params, c.RiskView(), grid_id="F2", cap_usdt=100)
        self.assertEqual((second_pick.symbol, second.status), ("BBBUSDT", c.CONFIRMED))
        # aynı sembol/mum için gerçek giriş denemesi kilidi C5/C6'da kalır
        again = c.decide_entry(store, "u:live", feed.cache.get("AAAUSDT").to_farmer_candidate(), params, c.RiskView(), grid_id="F3", cap_usdt=100)
        self.assertEqual((again.status, again.reason), (c.REJECTED, c.ENTRY_LOCKED))
        self.assertLessEqual(len(ev.calls), 3)

    def test_new_candle_creates_fresh_cache_and_new_lock_key(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        old = feed.cache.get("AAAUSDT")
        later = NOW + STEP["15m"]
        stream.advance(later)
        feed.scan(later)
        fresh = feed.cache.get("AAAUSDT")
        self.assertNotEqual(old.candle_open_ms, fresh.candle_open_ms)
        self.assertEqual(fresh.candle_open_ms - old.candle_open_ms, STEP["15m"])


class TestNoIndependentPolling(unittest.TestCase):
    def test_five_engines_share_one_reader_and_no_rest(self):
        feed, stream, ev = mk()
        engines = []
        for i in range(5):
            ex = FakeExchange(tick=0.01, step=0.001)
            ex.prices[f"S{i}USDT"] = 100.0
            engines.append(FarmerEngine(ex, FarmerStore(), farmer_spec(f"F{i}", f"S{i}USDT", LONG, 100.0, margin=20, leverage=3, target_usdt=0.5,
                                                                      max_loss_usdt=1.0, tick=0.01, step=0.001)))
        feed.scan(NOW)
        for _ in range(3):
            feed.scan(NOW + 1000)
            for e in engines:
                e.tick()
        self.assertEqual(stream.rest_calls, 0)
        self.assertEqual(stream.candle_reads, len(SYMS) * len(f.INTERVALS))          # sembol×aralık başına tek okuma; motor sayısından bağımsız
        self.assertTrue(all(not hasattr(e, "stream") and not hasattr(e, "feed") for e in engines))

    def test_engine_module_has_no_kline_access(self):
        src = (Path(__file__).parents[1] / "app" / "farmer_engine" / "engine.py").read_text(encoding="utf-8").lower()
        self.assertIsNone(re.search(r"kline|klines|public_get|candles\(", src))

    def test_warm_up_only_for_short_history_via_shared_download(self):
        feed, stream, ev = mk()
        stream.rows[("AAAUSDT", "1h")] = stream.rows[("AAAUSDT", "1h")][-10:]
        done = asyncio.run(feed.warm_up(lambda params: None))
        self.assertEqual(stream.get_candles_calls, [("AAAUSDT", "1h")])
        self.assertEqual(done, 1)
        self.assertEqual(asyncio.run(feed.warm_up(lambda params: None)), 0)           # yeterliyse tekrar indirme yok

    def test_scan_never_calls_get_candles(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        self.assertEqual(stream.get_candles_calls, [])


class TestIsolation(unittest.TestCase):
    def test_evaluator_error_isolated_per_symbol(self):
        feed, stream, ev = mk()
        inner = feed._evaluate

        def flaky(symbol, at, frames, allowed):
            if symbol == "BBBUSDT":
                raise RuntimeError("boom")
            return inner(symbol, at, frames, allowed)

        feed._evaluate = flaky
        feed.scan(NOW)
        self.assertTrue(feed.cache.unavailable()["BBBUSDT"].startswith(f.EVAL_ERROR))
        self.assertIsNotNone(feed.cache.get("AAAUSDT"))
        self.assertIsNotNone(feed.cache.get("CCCUSDT"))
        evaluations = feed.evaluations
        feed.scan(NOW + 1000)
        self.assertEqual(feed.evaluations, evaluations)                              # hatalı sembol aynı mumda sürekli yeniden denenmez

    def test_allowed_universe_passed_to_strategy_is_the_scan_target_list(self):
        feed, stream, ev = mk()
        feed.scan(NOW)
        self.assertTrue(all(allowed == SYMS for *_, allowed in ev.calls))


if __name__ == "__main__":
    unittest.main()

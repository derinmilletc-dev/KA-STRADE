import ast
import copy
import json
import re
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path

from app import original_shadow as sh

COSTS = {"fee_bps": 5.0, "slip_bps": 3.0}
T0 = 1_700_000_100  # 900'ün katı değil olabilir; mum zamanları için hizalıyoruz
T0 = T0 // 900 * 900


def analysis(direction="SHORT", price=100.0, stop=102.0, tp1=97.0, tp3=94.0):
    return {
        "direction": direction, "confidence": 85, "entry": price, "stop_loss": stop, "tp1": tp1, "tp2": 95.5, "tp3": tp3,
        "rsi": 40.0, "macd": -0.5, "adx": 25.0, "atr": 1.0, "volume_ratio": 1.2, "support": 98.0, "resistance": 103.0,
        "bollinger": {"upper": 103.0, "middle": 101.0, "lower": 99.0},
        "ema": {"ema20": 101.0, "ema50": 102.0, "ema200": 104.0},
        "radar": {"trap_score": 10, "breakout_quality": 70}, "trend": "DOWN", "momentum": "DOWN",
    }


def item(symbol="AAAUSDT", signal_time=T0, group_blocked=True, **kw):
    a = analysis(**kw)
    return {
        "symbol": symbol, "signal_time": signal_time, "decision_time": signal_time + 900, "analysis": a,
        "mtf": {"alignment": 85.0, "blocked_by_short_filter": group_blocked, "verdict": "x"},
        "checks": {"direction": True, "confidence": True, "trap": True, "breakout": True, "mtf": not group_blocked},
        "ranked": True, "eligible": False, "profile_cap_pass": True,
        "recent": [{"time": T0 - 900 * i, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1} for i in range(8)],
    }


def candle(time_, high, low, close=None, open_=None):
    return {"time": time_, "open": open_ if open_ is not None else (high + low) / 2, "high": high, "low": low,
            "close": close if close is not None else (high + low) / 2, "volume": 1.0}


def short_trade():
    rec = sh.signal_record(item(), sh.GROUP_BLOCKED, 1.0, COSTS)
    trade, reason = sh.open_virtual(rec, COSTS)
    assert trade is not None, reason
    return trade


class ClassifyTests(unittest.TestCase):
    def test_blocked_short_needs_all_other_gates(self):
        self.assertEqual(sh.classify(item()), sh.GROUP_BLOCKED)
        broken = item()
        broken["checks"]["trap"] = False
        self.assertIsNone(sh.classify(broken))
        unranked = item()
        unranked["ranked"] = False
        self.assertIsNone(sh.classify(unranked))
        long_ = item(direction="LONG", stop=98.0, tp1=103.0, tp3=106.0)
        self.assertIsNone(sh.classify(long_))

    def test_eligible_is_live_group(self):
        live = item(group_blocked=False)
        live["eligible"] = True
        self.assertEqual(sh.classify(live), sh.GROUP_LIVE)


class RecordTests(unittest.TestCase):
    def test_record_has_required_fields_and_is_strict_json(self):
        rec = sh.signal_record(item(), sh.GROUP_BLOCKED, 1.0, COSTS)
        for key in ("scores", "indicators", "price_position", "htf", "radar", "levels", "wicks", "quality_checks"):
            self.assertIn(key, rec)
        for key in ("rsi", "macd_hist", "ema20", "ema50", "ema200"):
            self.assertIn(key, rec["indicators"])
        self.assertIn("trap_score", rec["radar"])
        self.assertIn("breakout_quality", rec["radar"])
        self.assertEqual(rec["htf"]["alignment"], 85.0)
        json.dumps(rec, allow_nan=False)

    def test_score_reconstruction(self):
        scores = sh.reconstruct_scores(analysis())
        # SHORT: 25 + price<ema20(10) + ema20<ema50(12) + ema50<ema200(14) + macd<0(10) + rsi 28-48(9)
        #        + price<bb mid(6) + adx>=20 & ema down(8) + volume>=1.05(6) = 100
        self.assertEqual(scores["short_score"], 100)
        self.assertEqual(scores["long_score"], 31)  # yalnız hacim puanı
        self.assertTrue(all(scores["short_parts"].values()))

    def test_reconstruction_matches_real_analyze_on_random_candles(self):
        import random
        from app import analysis as real

        rng, checked = random.Random(7), 0
        for _ in range(120):
            price, rows, drift = 100.0, [], rng.uniform(-0.3, 0.3)
            for i in range(300):
                o = price
                c = o + drift * rng.uniform(0, 1) + rng.gauss(0, 0.6)
                rows.append({"time": i * 900, "open": o, "high": max(o, c) + abs(rng.gauss(0, .4)),
                             "low": min(o, c) - abs(rng.gauss(0, .4)), "close": c, "volume": rng.uniform(50, 150)})
                price = c
            observed = {}
            result = real.analyze(rows, observation_scores=observed)
            scores = sh.reconstruct_scores(result)
            self.assertEqual((scores["long_score"], scores["short_score"]), (observed["long_score"], observed["short_score"]))
            checked += 1
        self.assertEqual(checked, 120)

    def test_nonfinite_becomes_none(self):
        self.assertIsNone(sh.sanitize({"a": float("nan")})["a"])


class VirtualTradeTests(unittest.TestCase):
    def test_levels_follow_live_rules(self):
        trade = short_trade()
        self.assertAlmostEqual(trade["tp1"], 100 - 3 * 0.8)  # sinyal TP1 mesafesinin %80'i
        self.assertEqual(trade["tp1_fraction"], 0.70)
        self.assertEqual(trade["tp3"], 94.0)
        self.assertEqual(trade["stop"], 102.0)

    def test_invalid_levels_rejected(self):
        rec = sh.signal_record(item(stop=99.0), sh.GROUP_BLOCKED, 1.0, COSTS)
        self.assertEqual(sh.open_virtual(rec, COSTS), (None, "invalid_levels"))
        rec = sh.signal_record(item(), sh.GROUP_BLOCKED, 1.0, COSTS)
        self.assertEqual(sh.open_virtual(rec, {"fee_bps": None, "slip_bps": 3.0})[1], "costs_unavailable")

    def test_stop_loss_is_about_minus_one_r_plus_costs(self):
        trade = short_trade()
        sh.advance(trade, [candle(T0 + 900, 102.5, 99.5)])
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["close_reason"], "STOP")
        self.assertLess(trade["net_r"], -0.99)
        self.assertGreater(trade["net_r"], -1.4)
        self.assertEqual(trade["exits"][0]["reason"], "STOP")

    def test_tp1_then_tp3_is_profit(self):
        trade = short_trade()
        sh.advance(trade, [candle(T0 + 900, 100.2, 97.4)])
        self.assertTrue(trade["tp1_done"])
        self.assertAlmostEqual(trade["remaining"], 0.30)
        self.assertEqual(trade["status"], "OPEN")
        sh.advance(trade, [candle(T0 + 1800, 98.0, 93.5)])
        self.assertEqual(trade["status"], "CLOSED")
        self.assertEqual(trade["close_reason"], "TP1+TP3")
        self.assertGreater(trade["net_r"], 1.0)
        self.assertEqual(len(trade["exits"]), 2)

    def test_tp1_then_stop_keeps_initial_stop(self):
        trade = short_trade()
        sh.advance(trade, [candle(T0 + 900, 100.2, 97.4)])
        sh.advance(trade, [candle(T0 + 1800, 102.1, 99.0)])
        self.assertEqual(trade["close_reason"], "TP1+STOP")
        self.assertEqual(trade["exits"][-1]["expected_price"], 102.0)  # stop TP1'den sonra taşınmaz

    def test_ambiguous_bar_stop_first(self):
        trade = short_trade()
        sh.advance(trade, [candle(T0 + 900, 102.5, 93.0)])
        self.assertEqual(trade["close_reason"], "STOP")
        self.assertEqual(trade["ambiguous_bars"], 1)

    def test_same_candle_not_processed_twice(self):
        trade = short_trade()
        bar = candle(T0 + 900, 100.5, 99.5)
        sh.advance(trade, [bar, bar])
        self.assertEqual(trade["last_time"], T0 + 900)
        before = copy.deepcopy(trade)
        sh.advance(trade, [bar])
        self.assertEqual(trade, before)

    def test_gap_is_unresolved_not_a_result(self):
        trade = short_trade()
        sh.advance(trade, [candle(T0 + 900 * 3, 100.5, 99.5)])
        self.assertEqual(trade["close_reason"], "DATA_GAP")
        self.assertFalse(trade["resolved"])

    def test_long_symmetry(self):
        rec = sh.signal_record(item(direction="LONG", stop=98.0, tp1=103.0, tp3=106.0), sh.GROUP_LIVE, 1.0, COSTS)
        trade, _ = sh.open_virtual(rec, COSTS)
        sh.advance(trade, [candle(T0 + 900, 102.6, 99.8)])
        self.assertTrue(trade["tp1_done"])
        sh.advance(trade, [candle(T0 + 1800, 106.5, 101.0)])
        self.assertEqual(trade["close_reason"], "TP1+TP3")
        self.assertGreater(trade["net_r"], 1.0)

    def test_matches_live_constants(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "original_v2_live.py").read_text(encoding="utf-8")
        ratio = re.search(r'^TP1_PRICE_RATIO = Decimal\("([0-9.]+)"\)', source, re.M).group(1)
        fraction = re.search(r'^TP1_FRACTION = Decimal\("([0-9.]+)"\)', source, re.M).group(1)
        self.assertEqual(Decimal(str(sh.TP1_PRICE_RATIO)), Decimal(ratio))
        self.assertEqual(Decimal(str(sh.TP1_FRACTION)), Decimal(fraction))

    def test_costs_match_fixed_policy(self):
        policy = json.loads((Path(__file__).resolve().parents[1] / "app" / "strategies" / "original_v2_demo_policy.json")
                            .read_text(encoding="utf-8"))["execution_policy"]
        self.assertEqual(policy["fee_bps_per_side"], 5.0)
        self.assertEqual(policy["slippage_bps_per_side"], 3.0)


def scan(at, items, candles=None):
    symbols = {}
    for it in items:
        symbols[it["symbol"]] = {"item": it, "candles": candles or []}
    return {"at": at, "symbols": symbols, "costs": COSTS}


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def rows(self, name):
        path = self.folder / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_same_candle_counted_once_even_after_restart(self):
        rec = sh.Recorder(self.folder)
        rec.process(scan(T0 + 900, [item()]))
        rec.process(scan(T0 + 900, [item()]))
        rec2 = sh.Recorder(self.folder)  # yeniden başlatma
        rec2.process(scan(T0 + 1800, [item()]))
        self.assertEqual(len(self.rows("signals.jsonl")), 1)
        self.assertEqual(rec2.state["counters"]["virtual_opened"], 1)

    def test_new_candle_is_new_signal_but_one_open_trade_per_symbol(self):
        rec = sh.Recorder(self.folder)
        rec.process(scan(T0 + 900, [item(signal_time=T0)]))
        rec.process(scan(T0 + 1800, [item(signal_time=T0 + 900)]))
        rows = self.rows("signals.jsonl")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["virtual"], {"opened": False, "skip_reason": "virtual_position_open"})

    def test_trade_lifecycle_written_with_reason_and_r(self):
        rec = sh.Recorder(self.folder)
        rec.process(scan(T0 + 900, [item()]))
        rec.process(scan(T0 + 1800, [], None) | {"symbols": {"AAAUSDT": {"item": None, "candles": [candle(T0 + 900, 102.5, 99.5)]}}})
        trades = self.rows("trades.jsonl")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["close_reason"], "STOP")
        self.assertIn("net_r", trades[0])
        self.assertIn("fees", trades[0])
        self.assertEqual(trades[0]["group"], sh.GROUP_BLOCKED)
        self.assertEqual(json.loads((self.folder / "status.json").read_text())["open_virtual"], 0)

    def test_both_groups_tracked_independently(self):
        live = item(group_blocked=False)
        live["eligible"] = True
        rec = sh.Recorder(self.folder)
        rec.process(scan(T0 + 900, [item(), item(symbol="BBBUSDT", group_blocked=False) | {"eligible": True}]))
        groups = sorted(r["group"] for r in self.rows("signals.jsonl"))
        self.assertEqual(groups, [sh.GROUP_BLOCKED, sh.GROUP_LIVE])

    def test_profile_cap_blocked_group_not_traded(self):
        capped = item()
        capped["profile_cap_pass"] = False
        rec = sh.Recorder(self.folder)
        rec.process(scan(T0 + 900, [capped]))
        self.assertEqual(self.rows("signals.jsonl")[0]["virtual"]["skip_reason"], "profile_cap")

    def test_corrupt_state_is_quarantined(self):
        (self.folder / "state.json").write_text("{not json", encoding="utf-8")
        rec = sh.Recorder(self.folder)
        self.assertEqual(rec.state["open"], {})
        self.assertTrue(list(self.folder.glob("state.corrupt-*.json")))

    def test_seen_is_bounded(self):
        old = sh.SEEN_LIMIT
        sh.SEEN_LIMIT = 5
        self.addCleanup(setattr, sh, "SEEN_LIMIT", old)
        rec = sh.Recorder(self.folder)
        for i in range(10):
            rec.state["seen"][f"x{i}"] = i
        rec.process(scan(T0 + 900, []))
        self.assertLessEqual(len(rec.state["seen"]), 5)


class BuildScanTests(unittest.TestCase):
    def test_build_scan_reads_result_without_mutating_it(self):
        from types import SimpleNamespace as NS

        a = analysis()
        a["series"] = ["big"]
        legacy = {"analysis": a, "signal_timestamp": T0, "mtf": {"alignment": 85.0, "blocked_by_short_filter": True}}
        checks = [NS(key=k, passed=True) for k in ("direction", "confidence", "trap", "breakout")] + [NS(key="mtf", passed=False)]
        result = NS(legacy=legacy, signal=NS(quality_checks=checks))
        candles = [candle(T0 - 900 * i, 101, 99) for i in range(10)]
        before = copy.deepcopy(legacy)
        scan_ = sh.build_scan(T0 + 900, {"AAAUSDT": {"candles": candles, "result": result},
                                         "BBBUSDT": {"candles": candles, "result": NS(legacy={}, signal=None)}},
                              {"AAAUSDT"}, set(), COSTS, lambda e, s: True)
        self.assertEqual(legacy, before)
        built = scan_["symbols"]["AAAUSDT"]["item"]
        self.assertNotIn("series", built["analysis"])
        self.assertEqual(sh.classify(built), sh.GROUP_BLOCKED)
        self.assertIsNone(scan_["symbols"]["BBBUSDT"]["item"])
        json.dumps(sh.signal_record(built, sh.GROUP_BLOCKED, 1.0, COSTS), allow_nan=False)


class IsolationTests(unittest.TestCase):
    def test_observe_never_raises(self):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        sh.observe_scan(T0, {"AAAUSDT": {"candles": Boom(), "result": Boom()}}, {"AAAUSDT"}, set())
        sh.observe_scan(T0, None, None, None)

    def test_worker_survives_recorder_failure_and_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = sh.ShadowWorker(Path(tmp) / "f")
            bad = {"at": T0, "symbols": 5, "costs": COSTS}  # process() içinde hata verir
            started = time.time()
            self.assertTrue(worker.publish(bad))
            self.assertLess(time.time() - started, 1.0)
            worker.queue.join()
            self.assertEqual(worker.failures, 1)
            self.assertTrue(worker.publish(scan(T0 + 900, [item()])))
            worker.queue.join()
            self.assertEqual(worker.failures, 1)
            self.assertTrue((Path(tmp) / "f" / "signals.jsonl").exists())

    def test_full_queue_drops_instead_of_blocking(self):
        worker = sh.ShadowWorker(capacity=1)
        worker.thread = type("T", (), {"is_alive": lambda self: True})()  # tüketici yok
        self.assertTrue(worker.publish({"at": 1}))
        started = time.time()
        self.assertFalse(worker.publish({"at": 2}))
        self.assertLess(time.time() - started, 0.5)
        self.assertEqual(worker.dropped, 1)

    def test_disabled_by_env(self):
        import os
        os.environ[sh.ENV_ENABLED] = "0"
        try:
            self.assertFalse(sh.enabled())
        finally:
            del os.environ[sh.ENV_ENABLED]
        self.assertTrue(sh.enabled())

    def test_module_has_no_exchange_or_secret_access(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "original_shadow.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("httpx", "requests", "aiohttp", "urllib", "socket", "binance", "live", "original_v2_live",
                          "original_v2_demo_execution", "live_access", "execution_core"):
            self.assertNotIn(forbidden, imported)
        for word in ("api_key", "secret", "signature", "/fapi/v1/order", "public_get", "private"):
            self.assertNotIn(word, source.lower())


class LiveHookTests(unittest.TestCase):
    """Canlı dosyalardaki fark yalnız gözlemci tesisatı: gövdeler kaynak düzeyinde doğrulanır."""

    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "app"
        self.exec_src = (base / "original_v2_demo_execution.py").read_text(encoding="utf-8")
        self.live_src = (base / "original_v2_live.py").read_text(encoding="utf-8")

    def test_observer_is_optional_and_default_off(self):
        self.assertEqual(self.exec_src.count("observer: Callable[..., None] | None = None"), 2)

    def test_observer_call_is_guarded_and_after_all_decisions(self):
        tail = self.exec_src.split("diagnostics[\"near_misses\"]")[1]
        call = tail.index("observer(at, observed")
        self.assertLess(tail.index("[:3]"), call)
        self.assertLess(call, tail.index("return eligible, results"))
        self.assertIn("except Exception", tail[call:tail.index("return eligible, results")])

    def test_cycle_passes_observer(self):
        self.assertIn("observer=original_shadow.observe_scan", self.live_src)


if __name__ == "__main__":
    unittest.main()

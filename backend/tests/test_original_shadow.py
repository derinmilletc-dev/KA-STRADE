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
        "direction": direction, "confidence": 85, "entry": price, "stop_loss": stop, "tp1": tp1, "tp2": (tp1 + tp3) / 2, "tp3": tp3,
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


class EntryModelTests(unittest.TestCase):
    def with_scan(self, scan_price, tick=None, size=None, scan_time=None, **kw):
        it = item(**kw)
        it.update(scan_price=scan_price, scan_time=it["decision_time"] if scan_time is None else scan_time, tick=tick, size=size)
        return it

    def test_scan_price_is_the_entry_and_tp1_follows_it(self):
        rec = sh.signal_record(self.with_scan(100.04), sh.GROUP_BLOCKED, 1.0, COSTS)
        self.assertEqual(rec["entry_model"]["source"], sh.ENTRY_SCAN)
        self.assertFalse(rec["entry_model"]["approximate_entry"])
        trade, _ = sh.open_virtual(rec, COSTS)
        self.assertAlmostEqual(trade["entry_reference"], 100.04)
        self.assertAlmostEqual(trade["tp1"], 100.04 + (97.0 - 100.04) * 0.8)  # canlı: entry + (tp1 - entry) * 0.80
        self.assertEqual(trade["stop"], 102.0)  # stop ve TP3 mutlak kalır
        self.assertEqual(trade["tp3"], 94.0)
        self.assertFalse(trade["approximate_entry"])

    def test_missing_or_implausible_scan_price_marks_approximate_entry(self):
        for scan_price, scan_time in ((None, None), (110.0, None), (100.0, 123)):
            it = self.with_scan(scan_price, scan_time=scan_time)
            rec = sh.signal_record(it, sh.GROUP_BLOCKED, 1.0, COSTS)
            self.assertEqual(rec["entry_model"]["source"], sh.ENTRY_SIGNAL_CLOSE, (scan_price, scan_time))
            self.assertTrue(rec["entry_model"]["approximate_entry"])
            trade, _ = sh.open_virtual(rec, COSTS)
            self.assertTrue(trade["approximate_entry"])
            self.assertEqual(trade["entry_reference"], 100.0)

    def test_tick_rounding_matches_live_round_half_up(self):
        self.assertEqual(sh.round_tick(100.005, 0.01), 100.01)  # ROUND_HALF_UP
        self.assertEqual(sh.round_tick(100.004, 0.01), 100.0)
        self.assertEqual(sh.round_tick(7.0, None), 7.0)
        rec = sh.signal_record(self.with_scan(100.0449, tick=0.01), sh.GROUP_BLOCKED, 1.0, COSTS)
        trade, _ = sh.open_virtual(rec, COSTS)
        self.assertEqual(trade["entry_reference"], 100.04)
        self.assertIsNotNone(trade["entry_tick_rounding_bps"])
        self.assertIsNotNone(trade["tp1_tick_rounding_bps"])

    def test_levels_live_would_reject_are_not_opened(self):
        rec = sh.signal_record(self.with_scan(98.9, tp1=99.0, tp3=96.0), sh.GROUP_BLOCKED, 1.0, COSTS)  # fiyat TP1'i geçmiş
        self.assertEqual(sh.open_virtual(rec, COSTS), (None, "invalid_levels"))

    def test_collected_and_uncollected_differences_are_explicit(self):
        size = {"passes": False, "total_quantity": "1"}
        rec = sh.signal_record(self.with_scan(100.1, size=size), sh.GROUP_BLOCKED, 1.0, COSTS)
        model = rec["entry_model"]
        self.assertAlmostEqual(model["scan_vs_signal_close_bps"], 10.0)
        self.assertIsNone(model["mark_vs_candle_bps"])
        self.assertIn("realized_fill", model["not_collected"])
        self.assertIs(model["live_openable"], False)

    def test_unopenable_trades_are_flagged_and_reported_separately(self):
        rows = []
        for passes, r in ((True, 1.0), (False, -1.0), (None, 0.5)):
            rec = sh.signal_record(self.with_scan(100.0, size=None if passes is None else {"passes": passes}), sh.GROUP_BLOCKED, 1.0, COSTS)
            trade, _ = sh.open_virtual(rec, COSTS)
            trade.update(status="CLOSED", resolved=True, net_r=r)
            rows.append(trade)
        summary = sh.summarize(rows)[sh.GROUP_BLOCKED]
        self.assertEqual((summary["N"], summary["live_unopenable"], summary["live_openable_unknown"], summary["N_live_openable"]), (3, 1, 1, 2))
        self.assertAlmostEqual(summary["mean_net_r_live_openable"], 0.75)


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


def v2_item(accepted=False, reasons=("SHORT_EXTENDED_BELOW_EMA20",), gates=True, legacy_block=True, **kw):
    it = item(group_blocked=False, **kw)
    it["mtf"] = {"alignment": 85.0, "blocked_by_short_filter": False, "verdict": "x",
                 "legacy_short_alignment_would_block": legacy_block,
                 "short_filter_v2": {"version": "short-exhaustion-v1", "accepted": accepted, "gates_before_passed": gates,
                                     "reason": reasons[0] if reasons else None, "reasons": list(reasons),
                                     "extension_atr": 2.4, "support_distance_atr": 1.3, "rsi": 40.0,
                                     "missing_fields": [], "thresholds": {"ema20_atr": 2.0}}}
    return it


class ShortFilterV2CompatTests(unittest.TestCase):
    def test_v2_rejection_is_blocked_group_for_every_reason(self):
        for reason in ("SHORT_EXTENDED_BELOW_EMA20", "SHORT_RSI_OVERSOLD", "SHORT_SUPPORT_TOO_CLOSE", "SHORT_SUPPORT_UNAVAILABLE"):
            self.assertEqual(sh.classify(v2_item(reasons=(reason,))), sh.GROUP_BLOCKED, reason)

    def test_v2_accepted_or_failed_other_gates_not_recorded_as_blocked(self):
        self.assertIsNone(sh.classify(v2_item(accepted=True, reasons=())))
        self.assertIsNone(sh.classify(v2_item(gates=False)))
        broken = v2_item()
        broken["checks"]["trap"] = False
        self.assertIsNone(sh.classify(broken))

    def test_v2_never_blocked_when_legacy_flag_false_only(self):
        # eski bayrak False olsa da v2 reddi sınıflandırmayı belirler
        it = v2_item()
        self.assertFalse(it["mtf"]["blocked_by_short_filter"])
        self.assertEqual(sh.classify(it), sh.GROUP_BLOCKED)

    def test_record_carries_regime_reason_and_legacy_flag(self):
        rec = sh.signal_record(v2_item(legacy_block=False), sh.GROUP_BLOCKED, 1.0, COSTS)
        f = rec["short_filter"]
        self.assertEqual((f["regime"], f["rejected"], f["reason"], f["legacy_would_block"]),
                         (sh.REGIME_V2, True, "SHORT_EXTENDED_BELOW_EMA20", False))
        legacy = sh.signal_record(item(), sh.GROUP_BLOCKED, 1.0, COSTS)["short_filter"]
        self.assertEqual((legacy["regime"], legacy["rejected"], legacy["legacy_would_block"]), (sh.REGIME_LEGACY, True, True))

    def test_long_has_no_short_filter_record(self):
        long_ = item(direction="LONG", stop=98.0, tp1=103.0, tp3=106.0)
        self.assertIsNone(sh.signal_record(long_, sh.GROUP_LIVE, 1.0, COSTS)["short_filter"])

    def test_summary_counts_reasons_and_legacy(self):
        rec = sh.signal_record(v2_item(reasons=("SHORT_SUPPORT_UNAVAILABLE",), legacy_block=False), sh.GROUP_BLOCKED, 1.0, COSTS)
        trade, _ = sh.open_virtual(rec, COSTS)
        self.assertEqual(trade["short_filter_reason"], "SHORT_SUPPORT_UNAVAILABLE")
        self.assertIs(trade["legacy_would_block"], False)


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

    def test_default_off_and_explicit_on(self):
        import os
        saved = os.environ.pop(sh.ENV_ENABLED, None)
        try:
            self.assertFalse(sh.enabled())  # varsayılan kapalı
            for value in ("0", "false", "off", "", "garbage"):
                os.environ[sh.ENV_ENABLED] = value
                self.assertFalse(sh.enabled(), value)
            for value in ("1", "true", "ON"):
                os.environ[sh.ENV_ENABLED] = value
                self.assertTrue(sh.enabled(), value)
        finally:
            os.environ.pop(sh.ENV_ENABLED, None)
            if saved is not None:
                os.environ[sh.ENV_ENABLED] = saved

    def test_observe_scan_writes_nothing_when_off(self):
        import os
        os.environ.pop(sh.ENV_ENABLED, None)
        before = sh._worker
        sh.observe_scan(T0, {}, set(), set())
        self.assertIs(sh._worker, before)

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

"""Yeni SHORT giriş filtresi (canlı Original V2): eşikler, canlıya özgü kapsam ve eski %80 kuralının kapanışı."""

import logging
import random
import unittest
from copy import deepcopy

from app import main
from app.strategies import original_v2_demo as strategy
from app.strategies.contracts import StrategyInput
from app.strategies.kais_original import evaluate as evaluate_original

AT = 1_700_000_000 // 900 * 900
RULES = dict(strategy.LIVE_SHORT_FILTER_RULES)
POLICY = {"confidence_threshold": 80, "breakout_quality_threshold": 50, "mtf_allow_either_timeframe": 0}
INTERVALS = ("15m", "1h", "4h")
SYMBOL = sorted(strategy.fixed_profile()["execution_policy"]["allowed_symbols"])[0]  # Demo yalnız sabit evrendeki sembolleri kabul eder


def frames(seed, drift):
    out = {}
    for interval, step in (("15m", 900), ("1h", 3600), ("4h", 14400)):
        rng = random.Random(f"{seed}|{interval}")
        last, price, rows = AT // step * step - step, 100.0, []
        for i in range(259):
            opened, o = last - (258 - i) * step, price
            c = o + drift * rng.random() + rng.gauss(0, 0.25)
            rows.append({"time": opened, "open": o, "high": max(o, c) + abs(rng.gauss(0, .15)),
                         "low": min(o, c) - abs(rng.gauss(0, .15)), "close": c, "volume": rng.uniform(50, 150)})
            price = c
        out[interval] = rows
    return out


def decide(seed, rules=None):
    return main.canonical_historical_decision(
        "XUSDT", frames(seed, -0.35 if seed % 2 == 0 else 0.35), AT, required_intervals=INTERVALS,
        historical_policy_override=POLICY, short_filter_rules=rules)


def analysis(price=100.0, ema20=101.0, atr=1.0, rsi=40.0, support=95.0):
    return {"entry": price, "atr": atr, "rsi": rsi, "support": support, "ema": {"ema20": ema20}}


MTF = {"alignment": 91, "timeframes": {"15m": {"direction": "SHORT"}, "1h": {"direction": "SHORT"}, "4h": {"direction": "SHORT"}}}


class ThresholdTests(unittest.TestCase):
    def verdict(self, **kw):
        return main.evaluate_short_entry_filter(analysis(**kw), MTF, RULES)

    def test_rules_are_the_requested_thresholds(self):
        self.assertEqual(RULES, {"extension_atr_min": 2.0, "rsi_min": 25.0, "support_atr_max": 0.5})

    def test_strong_alignment_alone_is_not_a_rejection(self):
        result = self.verdict()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["alignment"], 91)
        self.assertEqual(result["directions"], {"15m": "SHORT", "1h": "SHORT", "4h": "SHORT"})

    def test_extension_boundary_two_atr_blocks(self):
        self.assertEqual(self.verdict(price=99.0, ema20=101.0)["reason"], "SHORT_EXTENDED_BELOW_EMA20")  # tam 2 ATR
        self.assertEqual(self.verdict(price=98.9, ema20=101.0)["reason"], "SHORT_EXTENDED_BELOW_EMA20")
        self.assertTrue(self.verdict(price=99.01, ema20=101.0)["accepted"])  # 2 ATR'den az
        self.assertTrue(self.verdict(price=105.0, ema20=101.0)["accepted"])  # EMA20 üstü: uzama sayılmaz

    def test_rsi_boundary_below_25_blocks(self):
        self.assertEqual(self.verdict(rsi=24.99)["reason"], "SHORT_RSI_OVERSOLD")
        self.assertTrue(self.verdict(rsi=25.0)["accepted"])
        self.assertTrue(self.verdict(rsi=60.0)["accepted"])

    def test_support_boundary_half_atr_blocks(self):
        self.assertEqual(self.verdict(price=100.0, support=99.5)["reason"], "SHORT_SUPPORT_TOO_CLOSE")  # tam 0,5 ATR
        self.assertEqual(self.verdict(price=100.0, support=100.0)["reason"], "SHORT_SUPPORT_TOO_CLOSE")
        self.assertTrue(self.verdict(price=100.0, support=99.49)["accepted"])

    def test_all_failing_reasons_are_listed_first_one_is_reason(self):
        result = self.verdict(price=99.0, ema20=101.0, rsi=20.0, support=98.9)
        self.assertEqual(result["reasons"], ["SHORT_EXTENDED_BELOW_EMA20", "SHORT_RSI_OVERSOLD", "SHORT_SUPPORT_TOO_CLOSE"])
        self.assertEqual(result["reason"], "SHORT_EXTENDED_BELOW_EMA20")
        self.assertEqual(result["decision"], "REJECT")

    def test_extension_formula_is_exactly_ema20_minus_price_over_atr(self):
        result = self.verdict(price=97.5, ema20=101.0, atr=1.75)
        self.assertEqual(result["extension_atr"], (101.0 - 97.5) / 1.75)  # == 2.0
        self.assertIn("SHORT_EXTENDED_BELOW_EMA20", result["reasons"])
        self.assertEqual(self.verdict(price=105.0, ema20=101.0)["extension_atr"], -4.0)  # EMA20 üstünde negatif
        self.assertEqual(self.verdict(price=100.0, ema20=101.0, atr=0.5)["extension_atr"], 2.0)

    def test_boundaries_hold_with_float_noise(self):
        # (EMA20 - fiyat) / ATR matematiksel olarak tam 2,0 ama kayan noktada 1,9999999999999 olabilen girişler yine engellenir
        for ema20, price, atr in ((100.2, 100.0, 0.1), (0.3, 0.1, 0.1), (1.1, 0.7, 0.2), (123.456, 123.256, 0.1)):
            result = self.verdict(price=price, ema20=ema20, atr=atr, support=price - 50 * atr)
            self.assertIn("SHORT_EXTENDED_BELOW_EMA20", result["reasons"], (ema20, price, atr, result["extension_atr"]))
        for price, support, atr in ((100.3, 100.0, 0.6), (10.05, 10.0, 0.1), (0.35, 0.3, 0.1)):  # mesafe tam 0,5 ATR
            result = self.verdict(price=price, support=support, atr=atr, ema20=price + 0.5 * atr)
            self.assertIn("SHORT_SUPPORT_TOO_CLOSE", result["reasons"], (price, support, atr))
        # sınırın gerçekten altı/üstü etkilenmez
        self.assertTrue(self.verdict(price=99.0 + 1e-6, ema20=101.0, support=90.0)["accepted"])
        self.assertTrue(self.verdict(price=100.0, support=99.5 - 1e-6)["accepted"])

    def test_missing_support_is_reported_separately_from_support_too_close(self):
        for support in (None, float("nan"), float("inf")):
            result = self.verdict(support=support)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["reasons"], ["SHORT_SUPPORT_UNAVAILABLE"])
            self.assertEqual(result["missing_fields"], ["support"])
            self.assertIsNone(result["support_distance_atr"])  # destek uydurulmaz
            self.assertIsNotNone(result["extension_atr"])  # diğer kurallar yine hesaplanır
        close = self.verdict(price=100.0, support=99.8)
        self.assertEqual(close["reasons"], ["SHORT_SUPPORT_TOO_CLOSE"])
        self.assertEqual(close["missing_fields"], [])
        absent = main.evaluate_short_entry_filter({"entry": 100.0, "atr": 1.0, "rsi": 40.0, "ema": {"ema20": 101.0}}, MTF, RULES)
        self.assertEqual(absent["reasons"], ["SHORT_SUPPORT_UNAVAILABLE"])  # destek anahtarı hiç yok

    def test_other_missing_data_is_rejected_with_its_own_reason(self):
        for bad, field in (({"atr": 0.0}, "atr"), ({"atr": None}, "atr"), ({"rsi": None}, "rsi"),
                           ({"ema20": None}, "ema20"), ({"price": 0.0}, "price"), ({"price": float("nan")}, "price")):
            result = self.verdict(**bad)
            self.assertFalse(result["accepted"], bad)
            self.assertEqual(result["reason"], "SHORT_FILTER_DATA_UNAVAILABLE", bad)
            self.assertIn(field, result["missing_fields"])
            self.assertNotIn("SHORT_SUPPORT_TOO_CLOSE", result["reasons"])
        both = self.verdict(atr=None, support=None)
        self.assertEqual(both["reasons"], ["SHORT_FILTER_DATA_UNAVAILABLE", "SHORT_SUPPORT_UNAVAILABLE"])


class CanonicalIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [(seed, decide(seed), decide(seed, RULES)) for seed in range(60)]

    def test_default_behaviour_is_untouched(self):
        for seed, legacy, _ in self.cases:
            again = main.canonical_historical_decision(
                "XUSDT", frames(seed, -0.35 if seed % 2 == 0 else 0.35), AT, required_intervals=INTERVALS,
                historical_policy_override=POLICY)
            self.assertEqual(again, legacy)
            self.assertNotIn("short_filter_v2", legacy["mtf"])

    def test_long_and_wait_decisions_are_identical_with_new_rules(self):
        checked = 0
        for _seed, legacy, new in self.cases:
            if (legacy.get("analysis") or {}).get("direction") != "SHORT":
                self.assertEqual(new, legacy)
                checked += 1
        self.assertGreater(checked, 10)

    def test_old_80_alignment_rejection_is_gone_when_rules_are_on(self):
        found = 0
        for _seed, legacy, new in self.cases:
            if legacy["mtf"].get("blocked_by_short_filter"):
                self.assertFalse(new["mtf"]["blocked_by_short_filter"])
                self.assertTrue(new["mtf"]["legacy_short_alignment_would_block"])
                self.assertNotEqual(new["mtf"]["verdict"], "SHORT_ALIGNMENT_FILTRE")
                if new["entry_eligible"]:
                    found += 1
                    self.assertEqual(new["decision"], "SELL")
        self.assertGreater(found, 0, "eski kuralın reddettiği ama yeni kuralla uygun çıkan en az bir SHORT bulunmalı")

    def test_short_decisions_equal_filter_off_plus_new_rules(self):
        from app.regime_study import short_gate_disabled

        compared = 0
        for seed, _legacy, new in self.cases:
            if (new.get("analysis") or {}).get("direction") != "SHORT":
                continue
            with short_gate_disabled():
                off = decide(seed)
            v2 = new["mtf"]["short_filter_v2"]
            self.assertEqual(new["entry_eligible"], bool(off["entry_eligible"] and v2["accepted"]), seed)
            self.assertEqual(new["analysis"], off["analysis"])  # puan/analiz hesabı değişmez
            compared += 1
        self.assertGreater(compared, 20)

    def test_rejection_reason_is_explicit_and_logged_in_result(self):
        reasons = {new["reason"] for _s, _l, new in self.cases if new["mtf"].get("short_filter_v2") and not new["entry_eligible"]
                   and new["mtf"]["short_filter_v2"]["gates_before_passed"]}
        self.assertTrue(reasons <= {"SHORT_EXTENDED_BELOW_EMA20", "SHORT_RSI_OVERSOLD", "SHORT_SUPPORT_TOO_CLOSE",
                                    "SHORT_SUPPORT_UNAVAILABLE", "SHORT_FILTER_DATA_UNAVAILABLE"}, reasons)


class LiveLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = next(seed for seed in range(80) if decide(seed)["mtf"].get("blocked_by_short_filter")
                        and decide(seed, RULES)["entry_eligible"])
        cls.frames = frames(cls.seed, -0.35 if cls.seed % 2 == 0 else 0.35)

    def request(self):
        return StrategyInput(SYMBOL, AT, deepcopy(self.frames))

    def test_live_uses_new_filter_but_demo_keeps_the_old_one(self):
        live = strategy.evaluate_live_universe(self.request(), allowed_symbols=[SYMBOL], enabled=True)
        demo = strategy.evaluate(self.request(), enabled=True)
        self.assertTrue(live.signal.strategy_eligible)
        self.assertFalse(demo.signal.strategy_eligible)  # Demo: eski %80 kuralı aynen
        self.assertTrue(demo.legacy["mtf"]["blocked_by_short_filter"])
        self.assertFalse(live.legacy["mtf"]["blocked_by_short_filter"])
        self.assertIn("short_filter", {check.key for check in live.signal.quality_checks})
        self.assertNotIn("short_filter", {check.key for check in demo.signal.quality_checks})

    def test_analysis_and_confidence_are_identical_live_vs_demo(self):
        live = strategy.evaluate_live_universe(self.request(), allowed_symbols=[SYMBOL], enabled=True)
        demo = strategy.evaluate(self.request(), enabled=True)
        self.assertEqual(live.legacy["analysis"], demo.legacy["analysis"])  # Akıllı Kâr Koruma puanı etkilenmez
        for key in ("entry", "stop", "tp1", "tp2", "tp3"):
            self.assertEqual(getattr(live.signal, key), getattr(demo.signal, key))
        self.assertEqual(live.signal.provenance, demo.signal.provenance)

    def test_caller_cannot_inject_rules(self):
        from dataclasses import replace

        with self.assertRaises(strategy.DemoDecisionError):
            strategy.evaluate_live_universe(replace(self.request(), short_filter_rules={"extension_atr_min": 99, "rsi_min": 0,
                                                                                      "support_atr_max": 0}),
                                            allowed_symbols=[SYMBOL], enabled=True)
        with self.assertRaises(strategy.DemoDecisionError):
            strategy.evaluate(replace(self.request(), short_filter_rules=dict(RULES)), enabled=True)

    def test_filter_decision_is_logged_with_all_required_fields(self):
        live = strategy.evaluate_live_universe(self.request(), allowed_symbols=[SYMBOL], enabled=True)
        with self.assertLogs("app.strategies.original_v2_demo", level="INFO") as logs:
            strategy.log_short_filter(SYMBOL, live.legacy)
        line = next(row for row in logs.output if "ORIGINAL_V2_SHORT_FILTER " in row)
        for field in ("decision=ACCEPT", "alignment=", "dir15m=SHORT", "dir1h=SHORT", "dir4h=SHORT", "rsi=",
                      "extension_atr=", "support_distance_atr=", "legacy_alignment_would_block=True", "reason="):
            self.assertIn(field, line)

    def test_evaluating_a_symbol_never_logs_so_position_monitoring_stays_silent(self):
        logger = logging.getLogger("app.strategies.original_v2_demo")
        with self.assertNoLogs(logger, level="INFO"):
            strategy.evaluate_live_universe(self.request(), allowed_symbols=[SYMBOL], enabled=True)  # Akıllı Kâr Koruma yolu
            strategy.evaluate(self.request(), enabled=True)

    def test_missing_support_gets_its_own_warning_and_counter(self):
        live = strategy.evaluate_live_universe(self.request(), allowed_symbols=[SYMBOL], enabled=True)
        legacy = deepcopy(live.legacy)
        v2 = main.evaluate_short_entry_filter({**legacy["analysis"], "support": None}, legacy["mtf"], RULES)
        v2["gates_before_passed"] = True
        legacy["mtf"]["short_filter_v2"] = v2
        with self.assertLogs("app.strategies.original_v2_demo", level="INFO") as logs:
            strategy.log_short_filter(SYMBOL, legacy)
        self.assertTrue(any("ORIGINAL_V2_SHORT_FILTER_DATA_MISSING" in row and "missing=support" in row for row in logs.output))
        self.assertTrue(any("reason=SHORT_SUPPORT_UNAVAILABLE" in row for row in logs.output))
        close = deepcopy(legacy)
        close["mtf"]["short_filter_v2"] = {**v2, "reasons": ["SHORT_SUPPORT_TOO_CLOSE"], "reason": "SHORT_SUPPORT_TOO_CLOSE",
                                           "missing_fields": [], "accepted": False, "decision": "REJECT"}
        stub = type("R", (), {"legacy": legacy})(), type("R", (), {"legacy": close})()
        counts = strategy.short_filter_scan_counts({"A": stub[0], "B": stub[1]})
        self.assertEqual((counts["evaluated"], counts["rejected"], counts["support_unavailable"], counts["support_too_close"]),
                         (2, 2, 1, 1))
        self.assertEqual(counts["by_reason"], {"SHORT_SUPPORT_UNAVAILABLE": 1, "SHORT_SUPPORT_TOO_CLOSE": 1})

    def test_only_the_live_entry_point_passes_rules(self):
        import inspect

        self.assertIn("LIVE_SHORT_FILTER_RULES", inspect.getsource(strategy.evaluate_live_universe))
        self.assertNotIn("LIVE_SHORT_FILTER_RULES", inspect.getsource(strategy.evaluate))
        self.assertNotIn("short_filter_rules", inspect.getsource(strategy.evaluate))

    def test_adapter_default_has_no_extra_quality_check(self):
        result = evaluate_original(StrategyInput(SYMBOL, AT, deepcopy(self.frames), required_intervals=INTERVALS,
                                                 historical_policy_override=dict(POLICY)))
        self.assertEqual([check.key for check in result.signal.quality_checks], ["direction", "confidence", "trap", "breakout", "mtf"])


class FakeKlineClient:
    """Deterministik sahte borsa: ağ yok. 15m/1s/4s mumları, oluşmakta olan mum dahil 260 satır."""

    def __init__(self, seed, drift, symbols):
        self.seed, self.drift, self.symbols = seed, drift, symbols

    async def public_get(self, path, params=None):
        if path == "/fapi/v1/exchangeInfo":
            filters = [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                       {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                       {"filterType": "MIN_NOTIONAL", "notional": "5"}]
            return {"symbols": [{"symbol": s, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT",
                                 "filters": filters} for s in self.symbols]}
        symbol, interval, limit = params["symbol"], params["interval"], params["limit"]
        step = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
        rng = random.Random(f"{self.seed}|{symbol}|{interval}")
        drift = self.drift * (0.5 + rng.random())
        last, price, rows = AT // step * step, 100.0, []
        for i in range(limit):
            opened, o = last - (limit - 1 - i) * step, price
            c = o + drift * rng.random() + rng.gauss(0, 0.5)
            rows.append([opened * 1000, str(o), str(max(o, c) + abs(rng.gauss(0, 0.3))), str(min(o, c) - abs(rng.gauss(0, 0.3))),
                         str(c), str(rng.uniform(1e3, 2e3)), (opened + step) * 1000 - 1, str(rng.uniform(1e6, 2e6)), 10, "0", "0", "0"])
            price = c
        return rows


class ScanAndSideEffectTests(unittest.TestCase):
    SYMBOLS = sorted(strategy.fixed_profile()["execution_policy"]["allowed_symbols"])[:8]

    def scan(self, seed, drift=-0.3, **kw):
        import asyncio

        from app import original_v2_demo_execution as ex

        diagnostics = {}
        eligible, results = asyncio.run(ex.live_candidates(
            FakeKlineClient(seed, drift, self.SYMBOLS), AT, set(), self.SYMBOLS, require_complete=True,
            diagnostics=diagnostics, **kw))
        return eligible, results, diagnostics

    def test_scan_reports_short_filter_counts_and_logs_the_summary(self):
        for seed in range(12):
            with self.assertLogs("app.original_v2_demo_execution", level="INFO") as logs:
                logging.getLogger("app.original_v2_demo_execution").info("probe")
                _eligible, _results, diagnostics = self.scan(seed)
            counts = diagnostics["short_filter_counts"]
            if counts["evaluated"]:
                self.assertEqual(counts["accepted"] + counts["rejected"], counts["evaluated"])
                self.assertTrue(any("ORIGINAL_V2_SHORT_FILTER_SCAN" in row for row in logs.output))
                self.assertEqual(sum(counts["by_reason"].values()) >= counts["rejected"], True)
                return
        self.fail("hiçbir senaryoda filtrenin karar verdiği SHORT bulunamadı")

    def test_demo_scan_path_has_no_short_filter_reporting_and_keeps_old_filter(self):
        import asyncio

        from app import original_v2_demo_execution as ex

        diagnostics = {}
        _eligible, results = asyncio.run(ex._candidates(
            FakeKlineClient(0, -0.3, self.SYMBOLS), AT, set(), self.SYMBOLS, require_complete=True, diagnostics=diagnostics))
        self.assertNotIn("short_filter_counts", diagnostics)
        for result in results.values():
            self.assertNotIn("short_filter_v2", (result.legacy.get("mtf") or {}))

    def test_open_position_exclusion_still_works(self):
        import asyncio

        from app import original_v2_demo_execution as ex

        eligible, _results, _diagnostics = self.scan(0)
        occupied = {candidate["symbol"] for candidate, _ in eligible}
        after, _ = asyncio.run(ex.live_candidates(FakeKlineClient(0, -0.3, self.SYMBOLS), AT, occupied, self.SYMBOLS,
                                                  require_complete=True))
        self.assertTrue(occupied.isdisjoint({candidate["symbol"] for candidate, _ in after}))  # dolu sembol hâlâ dışarıda

    def test_profit_guard_score_is_identical_and_silent(self):
        import asyncio

        from app import original_profit_guard as guard

        for seed in range(4):
            client = FakeKlineClient(seed, -0.3, self.SYMBOLS)
            symbol = self.SYMBOLS[0]
            with self.assertNoLogs("app.strategies.original_v2_demo", level="INFO"):
                analysis, status = asyncio.run(guard.fetch_analysis(client, symbol, AT, None))
            self.assertEqual(status, "ok")
            frames = {}
            from app.backtest_data import normalize_candles

            for interval in strategy.INTERVALS:
                raw = asyncio.run(client.public_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval,
                                                                       "limit": strategy.CANDLE_FETCH_LIMIT}))
                rows = [{"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5],
                         "close_time": r[6], "quote_volume": r[7]} for r in raw]
                frames[interval] = normalize_candles(rows, interval)[0].closed(AT)
            demo = strategy.evaluate(StrategyInput(symbol, AT, frames), enabled=True)
            self.assertEqual(analysis, demo.legacy["analysis"])  # Akıllı Kâr Koruma puanı eski kurala göre değişmez


if __name__ == "__main__":
    unittest.main()

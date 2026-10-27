"""Original V2 yeni giriş modeli (pullback_v1): kurallar, LONG/SHORT simetrisi, işaret ve kapıların sürmesi.

Ağ yok. Stop/TP/risk/emir kodu bu dosyada yalnız "değişmedi" yönünden sınanır.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.analysis import analyze, entry_levels  # noqa: E402
from app.execution_core import evaluate_entry_gates, sanitize_execution_policy  # noqa: E402
from app.strategies import original_v2_demo as demo  # noqa: E402
from app.strategies import original_v2_pullback as pb  # noqa: E402
from app.strategies.contracts import StrategyInput  # noqa: E402

AT = 1_700_000_000 // 900 * 900
SYMBOL = "BTCUSDT"
MIRROR = 200.0


def candle(time, open_, high, low, close, volume=100.0):
    return {"time": time, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def trend(count, drift, noise, *, start_price=100.0, start_time=0, seed=3):
    rng, price, rows, t = random.Random(seed), start_price, [], start_time
    for _ in range(count):
        o = price
        c = o + drift + rng.gauss(0, noise)
        rows.append(candle(t, o, max(o, c) + abs(rng.gauss(0, noise * 0.6)), min(o, c) - abs(rng.gauss(0, noise * 0.6)),
                           c, 100 + rng.random() * 20))
        price, t = c, t + 900
    return rows


def flat(count=259):
    """Hiçbir yöne gitmeyen piyasa: EMA20-EMA50 aralığı sıfıra çok yakın."""
    rows, t = [], AT - count * 900
    for i in range(count):
        close = 100.0 + (0.1 if i % 2 else -0.1)
        rows.append(candle(t, 100.0 - (0.1 if i % 2 else -0.1), 100.4, 99.6, close, 100.0))
        t += 900
    return rows


def pullback_series(*, up=0.06, pull_n=6, pull=-0.12, trigger=0.5, count=259):
    """Yükseliş, `pull_n` mumluk EMA20 geri çekilmesi ve son mumda yeniden başlama. Son mum AT'de kapanır."""
    rows = trend(count - pull_n - 1, up, 0.12, start_time=AT - count * 900)
    price, t = rows[-1]["close"], rows[-1]["time"] + 900
    for _ in range(pull_n):
        o, c = price, price + pull
        rows.append(candle(t, o, o + 0.05, c - 0.05, c, 90))
        price, t = c, t + 900
    rows.append(candle(t, price, price + trigger + 0.02, price - 0.12, price + trigger, 160))
    return rows


def higher(step, drift, count=259, seed=5):
    rng, price, rows, t = random.Random(seed), 100.0, [], AT // step * step - count * step
    for _ in range(count):
        o = price
        c = o + drift + rng.gauss(0, 0.1)
        rows.append(candle(t, o, max(o, c) + 0.05, min(o, c) - 0.05, c, 100 + rng.random() * 10))
        price, t = c, t + step
    return rows


def mirror(rows):
    return [candle(r["time"], 2 * MIRROR - r["open"], 2 * MIRROR - r["low"], 2 * MIRROR - r["high"],
                   2 * MIRROR - r["close"], r["volume"]) for r in rows]


def request(fifteen, hourly, four_hourly):
    return StrategyInput(SYMBOL, AT, {"15m": fifteen, "1h": hourly, "4h": four_hourly}, required_intervals=("15m", "1h", "4h"))


def decide(fifteen=None, hourly=None, four_hourly=None):
    fifteen = fifteen or pullback_series()
    return pb.evaluate_live_pullback(
        request(fifteen, hourly or higher(3600, 0.15), four_hourly or higher(14400, 0.3)),
        allowed_symbols=[SYMBOL], enabled=True)


def checks(result):
    return {check.key: check.passed for check in result.signal.quality_checks}


class RuleTests(unittest.TestCase):
    def test_reference_scenario_is_eligible_long_with_marker(self):
        result = decide()
        self.assertTrue(result.signal.strategy_eligible, checks(result))
        self.assertEqual((result.signal.decision, result.signal.direction), ("BUY", "LONG"))
        self.assertTrue(all(checks(result).values()))
        self.assertTrue(pb.is_pullback_result(result))

    def test_only_the_new_rules_are_reported(self):
        result = decide()
        self.assertEqual(tuple(checks(result)), pb.CHECK_KEYS)
        for retired in ("confidence", "trap", "breakout", "short_filter", "momentum_15m"):
            self.assertNotIn(retired, checks(result))
        self.assertNotIn("short_filter_v2", result.legacy["mtf"])

    def test_flat_market_fails_structure(self):
        decision = pb.evaluate_rules(flat(), "LONG", "LONG")
        self.assertFalse(decision["checks"]["pb_structure"])
        self.assertIsNone(decision["direction"])
        self.assertFalse(decision["eligible"])

    def test_higher_timeframes_must_agree(self):
        rows = pullback_series()
        for hourly, four in (("BEKLE", "LONG"), ("LONG", "SHORT"), ("SHORT", "SHORT")):
            decision = pb.evaluate_rules(rows, hourly, four)
            self.assertFalse(decision["checks"]["pb_trend"], (hourly, four))
            self.assertFalse(decision["eligible"])
        self.assertTrue(pb.evaluate_rules(rows, "LONG", "LONG")["checks"]["pb_trend"])

    def test_no_pullback_fails_pullback(self):
        rows = pullback_series(pull_n=0, trigger=0.4)
        decision = pb.evaluate_rules(rows, "LONG", "LONG")
        self.assertFalse(decision["checks"]["pb_pullback"])
        self.assertFalse(decision["eligible"])

    def test_pullback_window_is_six_candles(self):
        self.assertEqual(pb.PARAMETERS["pullback_window"], 6)
        self.assertTrue(pb.evaluate_rules(pullback_series(), "LONG", "LONG")["checks"]["pb_pullback"])
        # Geri çekilme 7+ mum önce bitip fiyat güçlü biçimde uzaklaştıysa, 6 mumluk pencere onu görmez.
        rows = pullback_series(trigger=0.2)
        price, t = rows[-1]["close"], rows[-1]["time"] + 900
        for _ in range(8):
            rows.append(candle(t, price, price + 0.32, price - 0.02, price + 0.3, 120))
            price, t = price + 0.3, t + 900
        rows = rows[-259:]
        self.assertFalse(pb.evaluate_rules(rows, "LONG", "LONG")["checks"]["pb_pullback"])
        with patch.dict(pb.PARAMETERS, {"pullback_window": 14}):
            self.assertTrue(pb.evaluate_rules(rows, "LONG", "LONG")["checks"]["pb_pullback"])

    def test_trigger_requires_all_four_conditions(self):
        rows = pullback_series()
        self.assertTrue(pb.evaluate_rules(rows, "LONG", "LONG")["checks"]["pb_trigger"])
        previous_high = rows[-2]["high"]
        # (2) Kapanış önceki mumun tepesini geçmiyor.
        weak = deepcopy(rows)
        last = weak[-1]
        weak[-1] = candle(last["time"], last["open"], previous_high + 0.01, last["low"], previous_high - 0.01, last["volume"])
        self.assertFalse(pb.evaluate_rules(weak, "LONG", "LONG")["checks"]["pb_trigger"])
        # (3) Kapanış mumun üst %40'lık diliminde değil.
        wick = deepcopy(rows)
        last = wick[-1]
        close = last["close"]
        wick[-1] = candle(last["time"], last["open"], close + 3.0, last["low"], close, last["volume"])
        decision = pb.evaluate_rules(wick, "LONG", "LONG")
        self.assertLess(decision["metrics"]["close_position"], pb.PARAMETERS["close_position"])
        self.assertFalse(decision["checks"]["pb_trigger"])
        # (1) Kapanış EMA20'nin trend tarafında değil.
        below = deepcopy(rows)
        last = below[-1]
        below[-1] = candle(last["time"], last["open"], last["high"], last["low"] - 5, last["low"] - 5 + 0.0001, last["volume"])
        self.assertFalse(pb.evaluate_rules(below, "LONG", "LONG")["checks"]["pb_trigger"])

    def test_extension_cap_blocks_both_directions(self):
        rows = pullback_series(pull_n=5, pull=-0.12, trigger=0.6)
        decision = pb.evaluate_rules(rows, "LONG", "LONG")
        self.assertGreater(decision["metrics"]["extension_atr"], pb.PARAMETERS["extension_max_atr"])
        self.assertFalse(decision["checks"]["pb_extension"])
        short = pb.evaluate_rules(mirror(rows), "SHORT", "SHORT")
        self.assertEqual(short["direction"], "SHORT")
        self.assertFalse(short["checks"]["pb_extension"])
        self.assertAlmostEqual(short["metrics"]["extension_atr"], decision["metrics"]["extension_atr"], places=9)

    def test_room_uses_prior_80_candles_and_excludes_the_signal_candle(self):
        rows = pullback_series()
        decision = pb.evaluate_rules(rows, "LONG", "LONG")
        atr = decision["metrics"]["atr"]
        expected = (max(r["high"] for r in rows[-81:-1]) - rows[-1]["close"]) / atr
        self.assertAlmostEqual(decision["metrics"]["room_atr"], expected, places=9)
        # Sinyal mumu önceki tepeyi aşarsa: dahil eden hesap pozitif "yer" gösterirdi, doğru hesap negatif çıkar.
        broken = deepcopy(rows)
        ceiling = max(r["high"] for r in broken[-81:-1])
        last = broken[-1]
        broken[-1] = candle(last["time"], last["open"], ceiling + 1.0, last["low"], ceiling + 0.2, last["volume"])
        result = pb.evaluate_rules(broken, "LONG", "LONG")
        atr = result["metrics"]["atr"]
        self.assertAlmostEqual(result["metrics"]["room_atr"], (ceiling - (ceiling + 0.2)) / atr, places=9)
        including_signal = (max(r["high"] for r in broken[-80:]) - broken[-1]["close"]) / atr
        self.assertGreater(including_signal, 0)
        self.assertLess(result["metrics"]["room_atr"], 0)

    def test_close_above_prior_extreme_is_rejected(self):
        rows = pullback_series()
        broken = deepcopy(rows)
        ceiling = max(r["high"] for r in broken[-81:-1])
        last = broken[-1]
        broken[-1] = candle(last["time"], last["open"], ceiling + 1.0, last["low"], ceiling + 0.9, last["volume"])
        decision = pb.evaluate_rules(broken, "LONG", "LONG")
        self.assertLess(decision["metrics"]["room_atr"], 0)
        self.assertFalse(decision["checks"]["pb_room"])

    def test_short_is_the_exact_mirror_of_long(self):
        rows = pullback_series()
        long_decision = pb.evaluate_rules(rows, "LONG", "LONG")
        short_decision = pb.evaluate_rules(mirror(rows), "SHORT", "SHORT")
        self.assertEqual((long_decision["direction"], short_decision["direction"]), ("LONG", "SHORT"))
        self.assertEqual(long_decision["checks"], short_decision["checks"])
        self.assertTrue(short_decision["eligible"])
        for key in ("ema_gap_atr", "pullback_depth_atr", "extension_atr", "room_atr", "atr", "hist_change"):
            sign = -1 if key in {"ema_gap_atr", "hist_change"} else 1
            self.assertAlmostEqual(long_decision["metrics"][key], sign * short_decision["metrics"][key], places=9, msg=key)
        self.assertAlmostEqual(long_decision["metrics"]["close_position"], 1 - short_decision["metrics"]["close_position"], places=9)

    def test_full_short_decision_mirrors_long_levels(self):
        long_result = decide()
        short_result = decide(mirror(pullback_series()), mirror(higher(3600, 0.15)), mirror(higher(14400, 0.3)))
        self.assertTrue(short_result.signal.strategy_eligible, checks(short_result))
        self.assertEqual((short_result.signal.decision, short_result.signal.direction), ("SELL", "SHORT"))
        self.assertEqual(checks(long_result), checks(short_result))
        long_risk = long_result.signal.entry - long_result.signal.stop
        short_risk = short_result.signal.stop - short_result.signal.entry
        self.assertAlmostEqual(long_risk, short_risk, places=9)
        self.assertAlmostEqual(long_result.signal.tp1 - long_result.signal.entry, short_result.signal.entry - short_result.signal.tp1, places=9)

    def test_ineligible_result_is_not_marked(self):
        result = decide(flat())
        self.assertFalse(result.signal.strategy_eligible)
        self.assertFalse(pb.is_pullback_result(result))
        self.assertNotIn("entry_model", result.legacy)

    def test_invalid_and_short_histories_are_rejected_without_a_decision(self):
        rows = pullback_series()
        broken = deepcopy(rows)
        broken[10]["high"] = broken[10]["low"] - 1
        invalid = decide(broken)
        self.assertFalse(invalid.signal.strategy_eligible)
        self.assertEqual(invalid.signal.reason, "INVALID_CANDLE_DATA")
        short = decide(rows[-100:])
        self.assertFalse(short.signal.strategy_eligible)
        self.assertEqual(short.signal.reason, "INSUFFICIENT_CLOSED_CANDLES")

    def test_unclosed_candle_is_ignored(self):
        rows = pullback_series()
        extra = candle(rows[-1]["time"] + 900, rows[-1]["close"], rows[-1]["close"] + 9, rows[-1]["close"] - 9, rows[-1]["close"] - 8)
        with_open = decide(rows[1:] + [extra])
        baseline = decide(rows)
        self.assertEqual(with_open.signal.signal_open_time, baseline.signal.signal_open_time)
        self.assertEqual(checks(with_open), checks(baseline))


class UnchangedExitTests(unittest.TestCase):
    def test_analyze_levels_equal_the_original_inline_formulas(self):
        for seed, drift in ((1, 0.12), (2, -0.12), (3, 0.0), (4, 0.05), (5, -0.05)):
            rows = trend(259, drift, 0.3, start_time=AT - 259 * 900, seed=seed)
            result = analyze(rows)
            price, atr, support, resistance = result["entry"], result["atr"], result["support"], result["resistance"]
            if result["direction"] == "LONG":
                stop = min(price - 1.5 * atr, support - 0.15 * atr)
                risk = max(price - stop, atr)
                targets = [price + risk * ratio for ratio in (1, 2, 3)]
            elif result["direction"] == "SHORT":
                stop = max(price + 1.5 * atr, resistance + 0.15 * atr)
                risk = max(stop - price, atr)
                targets = [price - risk * ratio for ratio in (1, 2, 3)]
            else:
                stop, risk = price - atr, atr
                targets = [price + atr * ratio for ratio in (1, 2, 3)]
            self.assertEqual((result["stop_loss"], result["tp1"], result["tp2"], result["tp3"]), (stop, *targets), (seed, drift))
            self.assertEqual(entry_levels(result["direction"], price, atr, support, resistance), (stop, risk, targets))

    def test_new_model_stop_and_targets_are_identical_to_analyze(self):
        rows = pullback_series()
        reference = analyze(rows)
        result = decide(rows)
        self.assertEqual(reference["direction"], "LONG")  # eski puan sistemi de aynı yönde: sayılar doğrudan karşılaştırılabilir
        analysis = result.legacy["analysis"]
        self.assertEqual((analysis["stop_loss"], analysis["tp1"], analysis["tp2"], analysis["tp3"]),
                         (reference["stop_loss"], reference["tp1"], reference["tp2"], reference["tp3"]))
        self.assertEqual((result.signal.stop, result.signal.tp1, result.signal.tp2, result.signal.tp3),
                         (reference["stop_loss"], reference["tp1"], reference["tp2"], reference["tp3"]))
        self.assertEqual(result.signal.entry, reference["entry"])

    def test_short_levels_use_the_same_formula(self):
        rows = mirror(pullback_series())
        reference = analyze(rows)
        result = decide(rows, mirror(higher(3600, 0.15)), mirror(higher(14400, 0.3)))
        stop, _risk, targets = entry_levels("SHORT", reference["entry"], reference["atr"], reference["support"], reference["resistance"])
        self.assertEqual((result.signal.stop, result.signal.tp1, result.signal.tp2, result.signal.tp3), (stop, *targets))


class MarkerAndGateTests(unittest.TestCase):
    def policy(self, **extra):
        return sanitize_execution_policy({"allowed_symbols": [SYMBOL, "ETHUSDT", "SOLUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"], **extra})

    def gates(self, result, *, snapshot=None, policy=None, armed=True, spread=1.0):
        return evaluate_entry_gates(
            symbol=SYMBOL, signal=pb.gate_signal(result), snapshot=snapshot or {"positions": [], "open_orders": []},
            policy=policy or self.policy(), daily={"entries": 0, "realized_pnl": 0, "unverified_closures": 0},
            spread_bps=spread, armed=armed, candidate_notional_usdt=10)

    @staticmethod
    def failed(outcome):
        return {gate["key"] for gate in outcome["gates"] if not gate["passed"]}

    def test_marker_neutralises_only_the_old_score_comparisons(self):
        result = decide()
        signal = pb.gate_signal(result)
        self.assertEqual((signal["confidence"], signal["radar"]["trap_score"]), (100, 0))
        outcome = self.gates(result)
        self.assertTrue(outcome["passed"], outcome["reason"])
        # Orijinal analiz sözlüğü değişmez; yalnız geçit kopyası nötrleşir.
        self.assertNotEqual(result.legacy["analysis"]["radar"]["trap_score"], None)
        self.assertEqual(result.legacy["analysis"]["confidence"], analyze(pullback_series())["confidence"])

    def test_forged_or_missing_marker_gets_no_bypass(self):
        result = decide()
        original_confidence = result.legacy["analysis"]["confidence"]
        for marker in (None, {"name": pb.MODEL_PULLBACK}, {"name": pb.MODEL_PULLBACK, "token": "forged"},
                       {"name": "other", "token": pb._TOKEN}, "pullback_v1"):
            forged = SimpleNamespace(signal=result.signal, legacy={**deepcopy(result.legacy), "entry_model": marker})
            if marker is None:
                forged.legacy.pop("entry_model")
            self.assertFalse(pb.is_pullback_result(forged), marker)
            self.assertEqual(pb.gate_signal(forged)["confidence"], original_confidence)
        ineligible = decide(flat())
        ineligible.legacy["entry_model"] = {"name": pb.MODEL_PULLBACK, "token": pb._TOKEN}
        self.assertFalse(pb.is_pullback_result(ineligible))

    def test_marker_survives_a_deep_copy(self):
        self.assertTrue(pb.is_pullback_result(deepcopy(decide())))

    def test_every_other_gate_still_rejects_a_marked_result(self):
        result = decide()
        full = [{"symbol": f"{name}USDT", "direction": "SHORT", "notional_usdt": 10} for name in ("EEE", "FFF", "GGG", "HHH", "III")]
        cases = {
            "arm": dict(armed=False),
            "spread": dict(spread=50.0),
            "long": dict(policy=self.policy(allow_long=False)),
            "symbol": dict(policy=sanitize_execution_policy({"allowed_symbols": ["ETHUSDT"]})),
            "positions": dict(snapshot={"positions": full, "open_orders": []}),
            "duplicate": dict(snapshot={"positions": [{"symbol": SYMBOL, "direction": "LONG", "notional_usdt": 10}], "open_orders": []}),
            "same_direction_positions": dict(snapshot={"positions": [
                {"symbol": "AAAUSDT", "direction": "LONG", "notional_usdt": 10},
                {"symbol": "BBBUSDT", "direction": "LONG", "notional_usdt": 10}], "open_orders": []}),
            "open_loss": dict(snapshot={"positions": [], "open_orders": [], "unrealized_pnl": -1e9}),
        }
        for key, kwargs in cases.items():
            outcome = self.gates(result, **kwargs)
            self.assertFalse(outcome["passed"], key)
            self.assertIn(key, self.failed(outcome), (key, self.failed(outcome)))

    def test_unmarked_legacy_style_signal_still_hits_the_confidence_and_trap_gates(self):
        weak = SimpleNamespace(signal=SimpleNamespace(strategy_eligible=True), legacy={
            "analysis": {"direction": "LONG", "confidence": 40, "radar": {"trap_score": 90}}})
        outcome = self.gates(weak)
        self.assertTrue({"confidence", "trap"} <= self.failed(outcome))


class FlagTests(unittest.TestCase):
    def test_default_is_legacy(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(pb.MODEL_ENV, None)
            self.assertEqual(pb.configured_model(), pb.MODEL_LEGACY)
            self.assertFalse(pb.pullback_enabled())

    def test_explicit_values(self):
        for value, expected in (("legacy", False), ("", False), ("pullback_v1", True), (" PULLBACK_V1 ", True)):
            with patch.dict(os.environ, {pb.MODEL_ENV: value}):
                self.assertEqual(pb.pullback_enabled(), expected, value)

    def test_invalid_value_fails_closed(self):
        for value in ("pullback", "true", "1", "v2"):
            with patch.dict(os.environ, {pb.MODEL_ENV: value}):
                with self.assertRaises(demo.DemoDecisionError):
                    pb.pullback_enabled()


class FillDeviationAndRankTests(unittest.TestCase):
    def marked(self, direction="LONG", entry=100.0, atr=2.0):
        analysis = {"direction": direction, "entry": entry, "atr": atr}
        return SimpleNamespace(legacy={"analysis": analysis})

    def test_adverse_move_limit_is_0_4_atr_for_both_directions(self):
        long_result, short_result = self.marked("LONG"), self.marked("SHORT")
        self.assertTrue(pb.fill_deviation_check(long_result, 100.0 + 0.39 * 2)[0])
        self.assertFalse(pb.fill_deviation_check(long_result, 100.0 + 0.41 * 2)[0])
        self.assertTrue(pb.fill_deviation_check(short_result, 100.0 - 0.39 * 2)[0])
        self.assertFalse(pb.fill_deviation_check(short_result, 100.0 - 0.41 * 2)[0])

    def test_favourable_move_is_not_blocked(self):
        self.assertTrue(pb.fill_deviation_check(self.marked("LONG"), 90.0)[0])
        self.assertTrue(pb.fill_deviation_check(self.marked("SHORT"), 110.0)[0])

    def test_missing_or_bad_price_blocks(self):
        for bad in (None, "x", float("nan"), 0, -1):
            self.assertFalse(pb.fill_deviation_check(self.marked(), bad)[0], bad)

    def rank(self, extension, volume, opportunity=1.0):
        result = SimpleNamespace(legacy={"analysis": {"pullback": {"extension_atr": extension}, "volume_ratio": volume}})
        return pb.rank_key(result, opportunity)

    def test_reasonable_distance_beats_hugging_the_ema(self):
        self.assertGreater(self.rank(0.6, 1.0), self.rank(0.02, 5.0))
        self.assertGreater(self.rank(0.6, 1.0), self.rank(1.2, 5.0))

    def test_same_group_orders_by_volume_then_opportunity(self):
        self.assertGreater(self.rank(0.4, 2.0), self.rank(0.9, 1.0))
        self.assertGreater(self.rank(0.4, 1.0, 9.0), self.rank(0.9, 1.0, 1.0))


class MoveFilterTests(unittest.TestCase):
    """24 saatlik minimum hareket süzgeci yalnız yeni modelde kalkar; hacim süzgeci ve eski davranış korunur."""

    INFO = {"symbols": [{"symbol": name, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"}
                        for name in ("FLATUSDT", "MOVEDUSDT", "THINUSDT")]}

    @staticmethod
    def tickers():
        return [
            {"symbol": "FLATUSDT", "quoteVolume": "5000000", "priceChangePercent": "0.05", "lastPrice": "10"},
            {"symbol": "MOVEDUSDT", "quoteVolume": "5000000", "priceChangePercent": "3.0", "lastPrice": "10"},
            {"symbol": "THINUSDT", "quoteVolume": "10000", "priceChangePercent": "5.0", "lastPrice": "10"},
        ]

    def rank(self, **kwargs):
        from app.v25_execution import rank_market_tickers

        diagnostics = {}
        ranked = rank_market_tickers(self.INFO, self.tickers(), diagnostics=diagnostics, **kwargs)
        return sorted(item["symbol"] for item in ranked), diagnostics["rejection_by_symbol"]

    def test_default_keeps_the_old_move_and_volume_filters(self):
        symbols, rejected = self.rank()
        self.assertEqual(symbols, ["MOVEDUSDT"])
        self.assertEqual(rejected, {"FLATUSDT": "market_move", "THINUSDT": "market_volume"})

    def test_zero_minimum_move_admits_quiet_coins_but_not_illiquid_ones(self):
        symbols, rejected = self.rank(min_move_pct=0.0)
        self.assertEqual(symbols, ["FLATUSDT", "MOVEDUSDT"])
        self.assertEqual(rejected, {"THINUSDT": "market_volume"})

    def test_scan_passes_zero_only_for_the_new_model(self):
        from app import original_v2_demo_execution as ex
        from app import v25_execution

        seen = []
        real = v25_execution.rank_market_tickers

        def spy(*args, **kwargs):
            seen.append(kwargs.get("min_move_pct", "absent"))
            return real(*args, **kwargs)

        for value, expected in ((pb.MODEL_PULLBACK, 0.0), (pb.MODEL_LEGACY, None)):
            seen.clear()
            with patch.object(v25_execution, "rank_market_tickers", spy), patch.dict(os.environ, {pb.MODEL_ENV: value}):
                asyncio.run(ex.live_candidates(ScanIntegrationTests.Client(0, 0.3, ScanIntegrationTests.SYMBOLS), AT, set(),
                                               ScanIntegrationTests.SYMBOLS, require_complete=True, diagnostics={}))
            self.assertEqual(seen, [expected], value)


class ScanIntegrationTests(unittest.TestCase):
    SYMBOLS = sorted(demo.fixed_profile()["execution_policy"]["allowed_symbols"])[:6]

    class Client:
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

    def scan(self, seed=0, drift=0.3):
        from app import original_v2_demo_execution as ex

        diagnostics = {}
        eligible, results = asyncio.run(ex.live_candidates(
            self.Client(seed, drift, self.SYMBOLS), AT, set(), self.SYMBOLS, require_complete=True, diagnostics=diagnostics))
        return eligible, results, diagnostics

    def test_default_scan_uses_the_legacy_model(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(pb.MODEL_ENV, None)
            _eligible, results, diagnostics = self.scan()
        self.assertTrue(results)
        for result in results.values():
            self.assertNotIn("entry_model", result.legacy["analysis"])
            self.assertNotIn("entry_model", result.legacy)
        self.assertNotIn("pb_trigger", diagnostics.get("check_failure_counts", {}))

    def test_flagged_scan_uses_the_new_model_and_marks_only_eligible_results(self):
        with patch.dict(os.environ, {pb.MODEL_ENV: pb.MODEL_PULLBACK}):
            eligible, results, diagnostics = self.scan()
        self.assertEqual(set(results), set(self.SYMBOLS))
        for result in results.values():
            self.assertEqual(result.legacy["analysis"]["entry_model"], pb.MODEL_VERSION)
            self.assertEqual(pb.is_pullback_result(result), result.signal.strategy_eligible)
        for _candidate, result in eligible:
            self.assertTrue(pb.is_pullback_result(result))
        keys = [pb.rank_key(result, candidate["opportunity_score"]) for candidate, result in eligible]
        self.assertEqual(keys, sorted(keys, reverse=True))
        self.assertTrue(set(diagnostics.get("check_failure_counts", {})) <= set(pb.CHECK_KEYS))

    class CraftedClient(Client):
        """İlk sembol: EMA20 geri çekilmesi + yeniden başlama mumu olan yükseliş. Diğerleri rastgele."""

        async def public_get(self, path, params=None):
            if path == "/fapi/v1/exchangeInfo" or params["symbol"] != self.symbols[0]:
                return await super().public_get(path, params)
            interval, limit = params["interval"], params["limit"]
            step = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
            if interval == "15m":
                rows = pullback_series(count=limit - 1)
                last = rows[-1]["close"]
                rows.append(candle(AT, last, last + 0.1, last - 0.1, last))  # oluşmakta olan mum
            else:
                rows = higher(step, 0.15 if interval == "1h" else 0.3, count=limit - 1)
                last = rows[-1]["close"]
                rows.append(candle(rows[-1]["time"] + step, last, last, last, last))
            return [[r["time"] * 1000, str(r["open"]), str(r["high"]), str(r["low"]), str(r["close"]), str(r["volume"]),
                     (r["time"] + step) * 1000 - 1, str(5e4), 10, "0", "0", "0"] for r in rows]

    def test_crafted_pullback_symbol_flows_through_the_whole_scan(self):
        from app import original_v2_demo_execution as ex

        diagnostics = {}
        with patch.dict(os.environ, {pb.MODEL_ENV: pb.MODEL_PULLBACK}):
            eligible, results = asyncio.run(ex.live_candidates(
                self.CraftedClient(0, 0.3, self.SYMBOLS), AT, set(), self.SYMBOLS, require_complete=True, diagnostics=diagnostics))
        self.assertEqual([candidate["symbol"] for candidate, _ in eligible], [self.SYMBOLS[0]])
        result = results[self.SYMBOLS[0]]
        self.assertTrue(pb.is_pullback_result(result))
        self.assertEqual(result.signal.direction, "LONG")
        self.assertEqual(diagnostics["candidate_count"], 1)
        self.assertEqual(diagnostics["rejection_counts"]["stop_too_wide"], 0)  # mevcut %6 stop kapısı yine uygulanır
        for symbol in self.SYMBOLS[1:]:
            self.assertFalse(results[symbol].signal.strategy_eligible)

    def test_invalid_flag_stops_the_scan(self):
        with patch.dict(os.environ, {pb.MODEL_ENV: "bogus"}):
            with self.assertRaises(demo.DemoDecisionError):
                self.scan()


if __name__ == "__main__":
    unittest.main()

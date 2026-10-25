"""Gölge kayıt: canlı karar eşitliği, hata izolasyonu, performans ve canlı kurallarla sanal hesabın tutarlılığı.

Canlı tarama yolu sentetik (ama deterministik) mum verisiyle ve gerçek strateji koduyla çalıştırılır; ağ yoktur."""

import asyncio
import json
import os
import random
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from app import original_shadow as sh
from app import original_v2_demo_execution as ex
from app.strategies import original_v2_demo as strategy

SYMBOLS = sorted(strategy.fixed_profile()["execution_policy"]["allowed_symbols"])[:8]
AT = 1_700_000_000 // 900 * 900
SCENARIOS = [(seed, drift) for seed in range(8) for drift in (-0.3, 0.3)]


class FakeClient:
    def __init__(self, seed, drift):
        self.seed, self.drift = seed, drift

    async def public_get(self, path, params=None):
        if path == "/fapi/v1/exchangeInfo":
            filters = [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                       {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                       {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                       {"filterType": "MIN_NOTIONAL", "notional": "5"}]
            return {"symbols": [{"symbol": s, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT",
                                 "filters": filters} for s in SYMBOLS]}
        symbol, interval, limit = params["symbol"], params["interval"], params["limit"]
        step = {"15m": 900, "1h": 3600, "4h": 14400}[interval]
        rng = random.Random(f"{self.seed}|{symbol}|{interval}")
        drift = self.drift * (0.5 + rng.random())
        last, price, rows = AT // step * step, 100.0, []
        for i in range(limit):
            opened = last - (limit - 1 - i) * step
            o = price
            c = o + drift * rng.random() + rng.gauss(0, 0.5)
            h, l = max(o, c) + abs(rng.gauss(0, 0.3)), min(o, c) - abs(rng.gauss(0, 0.3))
            rows.append([opened * 1000, str(o), str(h), str(l), str(c), str(rng.uniform(1e3, 2e3)),
                         (opened + step) * 1000 - 1, str(rng.uniform(1e6, 2e6)), 10, "0", "0", "0"])
            price = c
        return rows


SIZING = {"max_loss_per_trade": 3, "max_margin_per_trade": 25, "fee_bps_per_side": 5.0, "slippage_bps_per_side": 3.0,
          "tp1_fraction": "0.70", "split_steps": 10}


def scan(seed, drift, observer):
    diagnostics = {}
    eligible, results = asyncio.run(ex.live_candidates(
        FakeClient(seed, drift), AT, set(), SYMBOLS, require_complete=True, diagnostics=diagnostics,
        sizing_context=dict(SIZING), observer=observer))
    return {"eligible": [(candidate, repr(result)) for candidate, result in eligible],
            "results": {symbol: repr(result) for symbol, result in results.items()},
            "diagnostics": json.dumps(diagnostics, sort_keys=True, default=repr)}


class LiveDecisionParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {sh.ENV_DIR: self.tmp.name, sh.ENV_ENABLED: "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        sh._worker = None
        self.addCleanup(setattr, sh, "_worker", None)

    def test_observer_off_on_and_failing_give_identical_decisions(self):
        def raising(*_args):
            raise RuntimeError("gözlemci bozuk")

        def mutating(at, observed, ranked, eligible):
            for data in observed.values():  # gözlemci kendi kopyasını bozsa bile karar değişmemeli
                data["candles"].clear()
            ranked.clear()
            eligible.clear()

        total_eligible = 0
        for seed, drift in SCENARIOS:
            baseline = scan(seed, drift, None)
            total_eligible += len(baseline["eligible"])
            self.assertEqual(scan(seed, drift, sh.observe_scan), baseline, (seed, drift))
            sh._get_worker().queue.join()
            self.assertEqual(scan(seed, drift, raising), baseline, (seed, drift))
            self.assertEqual(scan(seed, drift, mutating), baseline, (seed, drift))
        self.assertGreater(total_eligible, 0, "senaryolar en az bir uygun aday üretmeli (test anlamlı olsun)")
        self.assertEqual(sh._get_worker().failures, 0, sh._get_worker().last_error)

    def test_recorded_groups_match_live_classification(self):
        """Kaydedilen LIVE_ELIGIBLE sinyalleri tam olarak canlının uygun bulduğu adaylardır."""
        live_ids, recorded = set(), set()
        for seed, drift in SCENARIOS:
            result = scan(seed, drift, sh.observe_scan)
            live_ids |= {f"{sh.GROUP_LIVE}|{candidate['symbol']}" for candidate, _ in result["eligible"]}
            sh._get_worker().queue.join()
        path = Path(self.tmp.name) / "signals.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        recorded = {f"{r['group']}|{r['symbol']}" for r in rows if r["group"] == sh.GROUP_LIVE}
        self.assertEqual(recorded, live_ids)
        print(f"\n[kayıt] LIVE_ELIGIBLE={sum(r['group'] == sh.GROUP_LIVE for r in rows)} "
              f"FILTER_BLOCKED_SHORT={sum(r['group'] == sh.GROUP_BLOCKED for r in rows)}")
        for row in rows:
            if row["group"] == sh.GROUP_BLOCKED:
                self.assertEqual(row["direction"], "SHORT")
                self.assertTrue(row["htf"]["blocked_by_short_filter"])
                self.assertGreaterEqual(row["htf"]["alignment"], 80)


    def test_observer_receives_tick_scan_price_and_live_size_check(self):
        captured = []
        baseline = scan(0, -0.3, None)
        self.assertEqual(scan(0, -0.3, lambda at, observed, ranked, eligible: captured.append(observed)), baseline)
        observed = captured[0]
        self.assertTrue(observed)
        for symbol, data in observed.items():
            self.assertEqual(data["tick"], "0.01")
            self.assertEqual(data["scan_row"]["time"], AT)  # oluşmakta olan mum: kline getirildiği an
            analysis = data["result"].legacy.get("analysis") or {}
            if analysis.get("direction") in {"LONG", "SHORT"}:
                self.assertIn(data["size"]["passes"], (True, False), data["size"])  # geçerli kurallarda bilinmeyen olmamalı
            else:
                self.assertIsNone(data["size"])

    def test_size_check_uses_the_live_function_and_scan_price(self):
        captured = []
        scan(0, -0.3, lambda at, observed, ranked, eligible: captured.append(observed))
        info = asyncio.run(FakeClient(0, 0).public_get("/fapi/v1/exchangeInfo"))["symbols"][0]
        checked = 0
        for symbol, data in captured[0].items():
            analysis = (data["result"].legacy.get("analysis") or {})
            if analysis.get("direction") not in {"LONG", "SHORT"}:
                continue
            signal = {k: v for k, v in analysis.items() if k != "series"} | {"entry": data["scan_row"]["close"]}
            try:
                expected = ex.live_minimum_order_assessment(signal, info, dict(SIZING))["passes"]
            except Exception as exc:  # stop tavanı: canlı girmezdi (False); başka hata: bilinmiyor
                expected = False if str(exc) == "profile_cap" else None
            self.assertEqual(data["size"]["passes"], expected)
            checked += 1
        self.assertGreater(checked, 0)


class FailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {sh.ENV_DIR: self.tmp.name, sh.ENV_ENABLED: "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        sh._worker = None
        self.addCleanup(setattr, sh, "_worker", None)

    def test_unwritable_folder_live_scan_continues(self):
        baseline = scan(0, -0.3, None)
        blocker = Path(self.tmp.name) / "dosya"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {sh.ENV_DIR: str(blocker / "alt")}):  # klasör dosyanın altında: oluşturulamaz
            sh._worker = None
            self.assertEqual(scan(0, -0.3, sh.observe_scan), baseline)
            sh._get_worker().queue.join()
            self.assertGreaterEqual(sh._get_worker().failures, 1)

    def test_disk_full_live_scan_continues_and_recovers(self):
        baseline = scan(1, -0.3, None)
        real_open = Path.open

        def full(self, mode="r", *a, **k):
            if "a" in mode or "w" in mode:
                raise OSError(28, "No space left on device")
            return real_open(self, mode, *a, **k)

        with mock.patch.object(Path, "open", full), mock.patch.object(Path, "write_text", side_effect=OSError(28, "disk full")):
            sh._worker = None
            self.assertEqual(scan(1, -0.3, sh.observe_scan), baseline)
            sh._get_worker().queue.join()
            self.assertGreaterEqual(sh._get_worker().failures, 1)
        # disk geri geldi: aynı işçi sonraki taramada normal yazar
        failures = sh._get_worker().failures
        self.assertEqual(scan(2, -0.3, sh.observe_scan), scan(2, -0.3, None))
        sh._get_worker().queue.join()
        self.assertEqual(sh._get_worker().failures, failures)
        self.assertTrue((Path(self.tmp.name) / "status.json").exists())

    def test_file_cap_stops_writing_but_does_not_raise(self):
        with mock.patch.object(sh, "MAX_FILE_BYTES", 10):
            recorder = sh.Recorder(Path(self.tmp.name) / "cap")
            (Path(self.tmp.name) / "cap" / "signals.jsonl").write_text("x" * 100, encoding="utf-8")
            self.assertFalse(recorder._append("signals.jsonl", {"a": 1}))
            self.assertTrue(recorder.cap_reached)


class LiveLevelParityTests(unittest.TestCase):
    def test_virtual_levels_equal_live_levels_exactly_for_random_prices_and_ticks(self):
        from app import original_v2_live as live_module
        from tests.test_original_shadow import COSTS, item

        rng = random.Random(11)
        checked = 0
        for _ in range(300):
            tick = rng.choice(["0.0001", "0.001", "0.01", "0.1", "0.5"])
            close = round(rng.uniform(20, 400), 6)
            scan_price = close * (1 + rng.uniform(-0.002, 0.002))
            direction = rng.choice(["LONG", "SHORT"])
            sign = 1 if direction == "LONG" else -1
            stop = close - sign * close * rng.uniform(0.008, 0.03)
            tps = [close + sign * close * rng.uniform(0.005, 0.01) * k for k in (1, 2, 3)]
            it = item(direction=direction, price=close, stop=stop, tp1=tps[0], tp3=tps[2])
            it["analysis"]["tp2"] = tps[1]
            it.update(scan_price=scan_price, scan_time=it["decision_time"], tick=tick)
            record = sh.signal_record(it, sh.GROUP_LIVE, 1.0, COSTS)
            trade, reason = sh.open_virtual(record, COSTS)
            # canlı: build_spec -> _build_spec ve scaled_targets
            t = Decimal(tick)
            entry = ex.demo.round_tick(Decimal(str(scan_price)), t)
            spec = {"entry_price": ex.demo.decimal_text(entry), "direction": direction,
                    "targets": [ex.demo.decimal_text(ex.demo.round_tick(Decimal(str(v)), t)) for v in tps]}
            stop_live = ex.demo.round_tick(Decimal(str(stop)), t)
            try:
                ex.demo.validate_levels(direction, entry, stop_live, [Decimal(v) for v in spec["targets"]])
                live_tp1 = Decimal(live_module.scaled_targets(spec, t)[0])
                live_ok = True
            except Exception:
                live_ok = False
            self.assertEqual(trade is not None, live_ok, (tick, close, scan_price, direction, reason))
            if live_ok:
                self.assertEqual(Decimal(str(trade["entry_reference"])), entry)
                self.assertEqual(Decimal(str(trade["stop"])), stop_live)
                self.assertEqual(Decimal(str(trade["tp3"])), Decimal(spec["targets"][2]))
                self.assertEqual(Decimal(str(trade["tp1"])), live_tp1)
                checked += 1
        self.assertGreater(checked, 150)


class PersistenceTests(unittest.TestCase):
    def test_open_virtual_trades_survive_restart_and_close_later(self):
        from tests.test_original_shadow import COSTS, T0, candle, item  # aynı yardımcılar

        with tempfile.TemporaryDirectory() as tmp:
            first = sh.Recorder(Path(tmp))
            first.process({"at": T0 + 900, "symbols": {"AAAUSDT": {"item": item(), "candles": []}}, "costs": COSTS})
            self.assertEqual(len(first.state["open"]), 1)
            second = sh.Recorder(Path(tmp))  # süreç yeniden başladı
            self.assertEqual(len(second.state["open"]), 1)
            self.assertEqual(second.state["open"], first.state["open"])
            second.process({"at": T0 + 1800, "costs": COSTS,
                            "symbols": {"AAAUSDT": {"item": item(), "candles": [candle(T0 + 900, 100.2, 97.4)]}}})
            third = sh.Recorder(Path(tmp))
            third.process({"at": T0 + 2700, "costs": COSTS,
                           "symbols": {"AAAUSDT": {"item": None, "candles": [candle(T0 + 1800, 98.0, 93.5)]}}})
            trades = [json.loads(line) for line in (Path(tmp) / "trades.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([t["close_reason"] for t in trades], ["TP1+TP3"])
            signals = (Path(tmp) / "signals.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(signals), 1)  # üç yeniden başlatmada aynı sinyal bir kez


class PerformanceTests(unittest.TestCase):
    def test_observer_cost_is_small_and_never_waits_for_a_slow_recorder(self):
        from tests.test_original_shadow import T0, analysis, candle

        from types import SimpleNamespace as NS
        candles = [candle(T0 - 900 * i, 101, 99) for i in range(200)][::-1]
        legacy = {"analysis": analysis() | {"series": list(range(500))}, "signal_timestamp": T0,
                  "mtf": {"alignment": 85.0, "blocked_by_short_filter": True}}
        checks = [NS(key=k, passed=True) for k in ("direction", "confidence", "trap", "breakout")] + [NS(key="mtf", passed=False)]
        observed = {f"S{i}USDT": {"candles": candles, "result": NS(legacy=legacy, signal=NS(quality_checks=checks))} for i in range(100)}
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {sh.ENV_DIR: tmp, sh.ENV_ENABLED: "1"}):
            sh._worker = sh.ShadowWorker(Path(tmp), capacity=2)
            with mock.patch.object(sh.Recorder, "process", lambda self, scan_: gate.wait(5)):
                durations = []
                for _ in range(6):  # işçi tıkalı: kuyruk dolar, çağrı yine bekletmez
                    started = time.perf_counter()
                    sh.observe_scan(T0, observed, set(observed), set())
                    durations.append(time.perf_counter() - started)
                gate.set()
            sh._worker.queue.join()
            print(f"\n[perf] 100 sembol, 200 mum: en yavaş gözlem çağrısı {max(durations) * 1000:.1f} ms; düşen={sh._worker.dropped}")
            self.assertLess(max(durations), 0.5)
            self.assertGreaterEqual(sh._worker.dropped, 1)
            sh._worker = None


class LiveRuleConsistencyTests(unittest.TestCase):
    """Miktar yuvarlama ve dolum kurallarının sanal hesapla ilişkisi."""

    def test_live_split_is_exactly_seventy_thirty_for_any_step(self):
        from app import original_v2_live as live_module

        for step in ("0.001", "0.01", "1", "0.1", "10"):
            for raw in ("123.456789", "999.999", "57.3", "1000.5"):
                step_d = Decimal(step)
                quantity = ex.demo.floor_step(Decimal(raw), step_d * live_module.SPLIT_STEPS)
                if quantity <= 0:
                    continue
                parts = ex.minimum_order_parts(quantity, Decimal("1"), step_d, Decimal("0"), Decimal("0"),
                                               live_module.TP1_FRACTION)
                self.assertEqual(Decimal(parts["tp1_quantity"]) / quantity, Decimal("0.7"), (step, raw))

    def test_virtual_r_equals_live_dollar_pnl_over_risk_when_fills_match(self):
        """Miktar ne olursa olsun (yuvarlama dahil) R = dolar K/Z / (miktar × risk birimi): R miktardan bağımsızdır."""
        from tests.test_original_shadow import COSTS, T0, candle, item

        record = sh.signal_record(item(), sh.GROUP_BLOCKED, 1.0, COSTS)
        trade, _ = sh.open_virtual(record, COSTS)
        sh.advance(trade, [candle(T0 + 900, 100.2, 97.4), candle(T0 + 1800, 98.0, 93.5)])
        per_unit = trade["net_per_unit"]
        for quantity in (Decimal("10"), Decimal("37.7"), Decimal("1234.5")):
            dollars = Decimal(str(per_unit)) * quantity
            risk_dollars = Decimal(str(trade["risk_unit"])) * quantity
            self.assertAlmostEqual(float(dollars / risk_dollars), trade["net_r"], places=9)

    def test_live_tp1_matches_virtual_up_to_one_tick(self):
        from app import original_v2_live as live_module

        spec = {"entry_price": "100", "direction": "SHORT", "targets": ["97", "95.5", "94"]}
        tick = Decimal("0.01")
        live_tp1 = Decimal(live_module.scaled_targets(spec, tick)[0])
        record = sh.signal_record(item_for_tp(), sh.GROUP_BLOCKED, 1.0, {"fee_bps": 5.0, "slip_bps": 3.0})
        virtual, _ = sh.open_virtual(record, {"fee_bps": 5.0, "slip_bps": 3.0})
        self.assertLessEqual(abs(Decimal(str(virtual["tp1"])) - live_tp1), tick)

    def test_sensitivity_to_live_entry_price_and_tick_is_small(self):
        """Canlı giriş, emir anındaki anlık fiyattır (sinyal mumu kapanışı değil). Etkisini ölçer ve sınırlar."""
        from tests.test_original_shadow import COSTS, T0, candle, item

        def run(entry):
            rec = sh.signal_record(item(price=entry, stop=102.0, tp1=97.0, tp3=94.0), sh.GROUP_BLOCKED, 1.0, COSTS)
            trade, reason = sh.open_virtual(rec, COSTS)
            self.assertIsNotNone(trade, reason)
            sh.advance(trade, [candle(T0 + 900, 100.2, 97.4), candle(T0 + 1800, 98.0, 93.5)])
            return trade["net_r"]

        base = run(100.0)
        for offset_bps in (-5, 5):  # mum kapanışı ile emir anı arasında ±5 bps kayma
            moved = run(100.0 * (1 + offset_bps / 10_000))
            print(f"\n[duyarlılık] giriş {offset_bps:+d} bps -> R farkı {moved - base:+.4f}")
            self.assertLess(abs(moved - base), 0.10)  # 2% stop'ta 5 bps ≈ 0,05R; iki grupta da aynı yönsüz gürültü


def item_for_tp():
    from tests.test_original_shadow import item
    return item()


if __name__ == "__main__":
    unittest.main()

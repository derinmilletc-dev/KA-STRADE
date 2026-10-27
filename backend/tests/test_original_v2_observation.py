"""Giriş kalitesi gözlem katmanı: saf hesap, fiyat izi, emir akışına dokunmama, hata yalıtımı."""
import ast
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import original_v2_record as rec  # noqa: E402
from app import signal_journal as journal_module  # noqa: E402

PUBLISHED = []
TRADE_FIELDS = (
    "status", "stop_loss", "initial_stop_loss", "targets", "quantity", "remaining_quantity", "entry_price",
    "protection_ids", "protection_state", "protection_status", "tp1_fraction", "direction", "symbol", "leverage",
    "margin_usdt", "notional_usdt", "initial_quantity", "tp_order_ids", "exchange_order_ids",
)


def setUpModule():
    global _CAPTURE
    _CAPTURE = patch.object(journal_module, "emit", lambda *a, **k: PUBLISHED.append((a, k)))
    _CAPTURE.start()


def tearDownModule():
    _CAPTURE.stop()


def candles(count=259, step=0.1, base=100.0):
    return [{"time": i * 900, "open": base + i * step, "high": base + i * step + 0.5,
             "low": base + i * step - 0.5, "close": base + i * step, "volume": 1.0} for i in range(count)]


def make_plan(direction="LONG", fill=100.0, stop=98.0, tp1=102.0, with_context=True):
    plan = {"id": "p1", "intent_id": "i1", "symbol": "ABCUSDT", "direction": direction, "entry_price": fill,
            "initial_stop_loss": stop, "stop_loss": stop, "targets": [tp1, tp1 + 2, tp1 + 4], "quantity": 1.0,
            "status": "KORUMA AKTİF", "created_at": "2026-01-01T00:00:00+00:00", "tp1_fraction": 0.7}
    if with_context:
        plan["original_record"] = {"schema": 1, "context": {"direction": direction},
                                   "fill": {"avg_price": fill, "time_s": 1000.0,
                                           "time_source": "order_result", "price_source": "order_result"}}
    return plan


def snap(symbol="ABCUSDT", direction="LONG", mark=100.0, entry=100.0):
    return {"positions": [{"symbol": symbol, "direction": direction, "quantity": 1.0,
                           "entry_price": entry, "mark_price": mark}]}


class ContextTests(unittest.TestCase):
    def test_long_context_matches_hand_calculation(self):
        c = rec.compute_context(candles(), "LONG", atr=2.0, signal_entry=125.8, signal_stop=120.8,
                                signal_timestamp=1, decision_time=2)
        move = c["prior_move"]["8h"]
        self.assertAlmostEqual(move["pct"], 3.2 / 122.6 * 100, places=6)
        self.assertAlmostEqual(move["atr"], 1.6, places=6)
        self.assertAlmostEqual(move["r"], 0.64, places=6)
        rng = c["range_20h"]
        self.assertAlmostEqual(rng["position"], (125.8 - 117.4) / 8.9, places=6)
        self.assertAlmostEqual(rng["extreme_distance_atr"], 0.25, places=6)
        self.assertGreater(c["ema_distance"]["ema20"]["atr"], 0)
        self.assertGreater(c["ema_distance"]["ema200"]["pct"], c["ema_distance"]["ema20"]["pct"])
        age = c["alignment_age"]["all_aligned"]
        self.assertEqual(age["candles"], 258)
        self.assertEqual(age["minutes"], 258 * 15)
        self.assertTrue(age["capped"])

    def test_short_signs_are_flipped_and_range_is_from_the_low(self):
        c = rec.compute_context(candles(), "SHORT", atr=2.0, signal_entry=125.8, signal_stop=130.8)
        self.assertAlmostEqual(c["prior_move"]["8h"]["pct"], -3.2 / 122.6 * 100, places=6)
        self.assertLess(c["ema_distance"]["ema20"]["atr"], 0)
        self.assertEqual(c["alignment_age"]["all_aligned"]["candles"], 0)
        self.assertFalse(c["alignment_age"]["all_aligned"]["capped"])
        self.assertAlmostEqual(c["range_20h"]["position"], (126.3 - 125.8) / 8.9, places=6)

    def test_short_history_gives_none_not_guess(self):
        c = rec.compute_context(candles(20), "LONG", atr=2.0, signal_entry=102.0, signal_stop=100.0)
        self.assertIsNone(c["prior_move"]["8h"])
        self.assertIsNone(c["prior_move"]["24h"])

    def test_age_stops_at_last_break(self):
        self.assertEqual(rec._run_age([True, False, True, True])["candles"], 2)
        self.assertFalse(rec._run_age([True, False, True, True])["capped"])
        self.assertTrue(rec._run_age([True] * 5)["capped"])

    def test_bad_input_returns_none(self):
        self.assertIsNone(rec.compute_context([], "LONG", atr=1, signal_entry=1, signal_stop=0))
        self.assertIsNone(rec.compute_context(candles(), "BEKLE", atr=1, signal_entry=1, signal_stop=0))
        bad = candles(); bad[5]["close"] = float("nan")
        self.assertIsNone(rec.compute_context(bad, "LONG", atr=1, signal_entry=1, signal_stop=0))

    def test_fill_slippage_latency_and_fallback(self):
        ctx = {"direction": "LONG", "risk_basis": {"r": 2.0}}
        fill = rec.build_fill({"quantity": 1.0, "entry_price": 100.0}, {"avgPrice": "100.5", "executedQty": "1",
                              "updateTime": 1_900_030, "status": "FILLED"}, ctx, 100.0, 1_900)
        self.assertAlmostEqual(fill["slippage_vs_signal"]["bp"], 50.0)
        self.assertAlmostEqual(fill["slippage_vs_signal"]["r"], 0.25)
        self.assertAlmostEqual(fill["latency_s"], 1_900.03 - 1_900)
        empty = rec.build_fill({"quantity": 1.0}, {"avgPrice": "0"}, ctx, 100.0, 1_900)
        self.assertIsNone(empty["avg_price"])
        self.assertIsNone(empty["time_s"])


class PathTests(unittest.TestCase):
    def setUp(self):
        rec.reset_breaker()

    def run_samples(self, plan, samples, direction="LONG"):
        state = {"plans": {"p1": plan}}
        for now, mark in samples:
            rec.observe_positions(state, snap(direction=direction, mark=mark), now=now)
        return state["plans"]["p1"]["original_record"]["path"]

    def test_long_path_extremes_thresholds_and_gaps(self):
        path = self.run_samples(make_plan(), [(1010, 101.0), (1020, 99.0), (1100, 102.0)])
        self.assertEqual(path["samples"], 3)
        self.assertAlmostEqual(path["mfe"]["r"], 1.0)
        self.assertAlmostEqual(path["mae"]["r"], -0.5)
        self.assertEqual(path["mae"]["t_s"], 20)
        self.assertEqual(path["first_profit_s"], 10)
        self.assertEqual(path["first_r025_s"], 10)
        self.assertEqual(path["first_r05_s"], 10)
        self.assertEqual(path["first_r10_s"], 100)
        self.assertEqual(path["tp1_touch_s"], 100)
        self.assertEqual(path["max_gap_s"], 80)
        self.assertFalse(path["partial"])

    def test_short_sign(self):
        path = self.run_samples(make_plan("SHORT", 100.0, 102.0, 98.0), [(1010, 99.0), (1020, 101.0)], "SHORT")
        self.assertAlmostEqual(path["mfe"]["r"], 0.5)
        self.assertAlmostEqual(path["mae"]["r"], -0.5)
        self.assertIsNone(path["tp1_touch_s"])

    def test_checkpoints_use_first_sample_after_the_minute(self):
        path = self.run_samples(make_plan(), [(1010, 100.0), (1000 + 905, 101.0), (1000 + 1805, 100.0)])
        self.assertEqual(sorted(path["checkpoints"]), ["15", "30"])
        self.assertAlmostEqual(path["checkpoints"]["15"]["sample_age_s"], 905)

    def test_late_start_is_marked_partial_only_when_fill_time_is_real(self):
        path = self.run_samples(make_plan(), [(1300, 100.0)])  # dolum zamanı borsa cevabından (gerçek)
        self.assertTrue(path["partial"])
        self.assertTrue(path["partial_known"])

    def test_missing_position_does_not_end_the_path_and_sampling_resumes(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        for now in (1020, 1030, 1040, 1050):  # pozisyon görünmüyor ama plan kapanmadı: iz AÇIK kalır
            rec.observe_positions(state, {"positions": []}, now=now)
        path = plan["original_record"]["path"]
        self.assertIsNone(path["ended_reason"])
        self.assertEqual(path["missing_snapshots"], 4)
        self.assertEqual(path["samples"], 1)
        rec.observe_positions(state, snap(mark=102.0), now=1060)  # pozisyon geri geldi
        self.assertEqual(path["samples"], 2)
        self.assertEqual(path["missing_snapshots"], 0)
        self.assertIsNone(path["ended_reason"])

    def test_invalid_snapshots_are_ignored_not_counted_as_missing(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        for bad in (None, {}, {"positions": None}, {"positions": "x"}, "x"):
            rec.observe_positions(state, bad, now=1020)
        self.assertEqual(plan["original_record"]["path"]["missing_snapshots"], 0)
        self.assertIsNone(plan["original_record"]["path"]["ended_reason"])

    def test_emergency_stopped_plan_ends_only_after_position_is_also_gone(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        plan["status"] = "ACİL DURDURULDU"
        rec.observe_positions(state, snap(mark=101.0), now=1020)  # pozisyon hâlâ açık: bitmez
        self.assertIsNone(plan["original_record"]["path"]["ended_reason"])
        rec.observe_positions(state, {"positions": []}, now=1030)
        self.assertIsNone(plan["original_record"]["path"]["ended_reason"])
        rec.observe_positions(state, {"positions": []}, now=1040)
        self.assertEqual(plan["original_record"]["path"]["ended_reason"], "plan_closed")

    def test_plan_closed_ends_the_path(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        plan["status"] = "KAPANDI"
        rec.observe_positions(state, snap(mark=101.0), now=1020)
        self.assertEqual(plan["original_record"]["path"]["ended_reason"], "plan_closed")

    def test_survives_restart_through_plan_persistence(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        reloaded = json.loads(json.dumps(state))
        rec.observe_positions(reloaded, snap(mark=102.0), now=1500)
        path = reloaded["plans"]["p1"]["original_record"]["path"]
        self.assertEqual(path["samples"], 2)
        self.assertEqual(path["max_gap_s"], 490)

    def test_plans_without_context_are_skipped(self):
        plan = make_plan(with_context=False)
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        self.assertNotIn("original_record", plan)

    def test_fill_falls_back_to_snapshot_with_source(self):
        plan = make_plan()
        plan["original_record"]["fill"] = {}
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0, entry=100.2), now=1010)
        fill = plan["original_record"]["fill"]
        self.assertEqual(fill["price_source"], "snapshot")
        self.assertEqual(fill["time_source"], "first_snapshot")

    def test_journal_publishing_is_throttled(self):
        PUBLISHED.clear()
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        for now in (1010, 1020, 1030, 1040):  # r sabit 0 -> eşik yok
            rec.observe_positions(state, snap(mark=100.0), now=now)
        self.assertEqual(len([p for p in PUBLISHED if p[1].get("section") == "path"]), 1)
        rec.observe_positions(state, snap(mark=100.0), now=1400)
        self.assertEqual(len([p for p in PUBLISHED if p[1].get("section") == "path"]), 2)

    def test_exact_fill_from_rows(self):
        exact = rec.exact_fill_from_rows([{"qty": "1", "price": "100", "time": 2000}, {"qty": "3", "price": "104", "time": 3000}])
        self.assertAlmostEqual(exact["vwap"], 103.0)
        self.assertEqual(exact["first_time_s"], 2.0)
        self.assertIsNone(rec.exact_fill_from_rows([{"qty": "0", "price": "1"}]))


class FlowSafetyTests(unittest.TestCase):
    def setUp(self):
        rec.reset_breaker()
        rec._CONTEXTS.clear()

    def strategy(self):
        legacy = {"signal_timestamp": 900, "decision_time": 1800,
                  "analysis": {"direction": "LONG", "confidence": 90, "entry": 125.8, "stop_loss": 120.8, "atr": 2.0}}
        return SimpleNamespace(legacy=legacy, signal=SimpleNamespace(quality_checks=()))

    def test_trade_fields_are_identical_before_and_after_hooks(self):
        result = self.strategy()
        rec.note_candidate("ABCUSDT", result, candles())
        plan = make_plan(with_context=False)
        before = {k: copy.deepcopy(plan.get(k)) for k in TRADE_FIELDS}
        rec.attach_entry(plan, result, {"symbol": "ABCUSDT"}, spread_bp=1, snapshot=snap(), policy={}, config={},
                         recorded_at="x", order_result={"avgPrice": "100", "executedQty": "1", "updateTime": 1_800_500,
                                                        "status": "FILLED"})
        state = {"plans": {"p1": plan}}
        snapshot = snap(mark=101.0)
        snapshot_before = copy.deepcopy(snapshot)
        rec.observe_positions(state, snapshot, now=1_801)
        self.assertEqual({k: plan.get(k) for k in TRADE_FIELDS}, before)
        self.assertEqual(snapshot, snapshot_before)
        self.assertIn("context", plan["original_record"])
        self.assertIn("path", plan["original_record"])
        self.assertEqual(plan["original_record"]["fill"]["price_source"], "order_result")

    def test_error_injection_never_raises_and_plan_is_untouched(self):
        result = self.strategy()
        for bad in (None, "x", {"positions": "x"}, {"positions": [1, None]}, {"positions": [{"symbol": "ABCUSDT", "direction": "LONG", "mark_price": float("nan")}]}):
            rec.reset_breaker()
            plan = make_plan()
            before = copy.deepcopy(plan)
            rec.observe_positions({"plans": {"p1": plan, "bad": "x", "n": None}}, bad, now=1010)
            if isinstance(bad, dict) and isinstance(bad.get("positions"), list):
                # yapısı geçerli snapshot: yalnız gözlem kaydı (original_record) değişebilir, işlem alanları asla
                self.assertEqual({k: v for k, v in plan.items() if k != "original_record"},
                                 {k: v for k, v in before.items() if k != "original_record"})
                self.assertNotIn("path", plan["original_record"])
            else:
                self.assertEqual(plan, before)  # geçersiz snapshot: hiçbir şey değişmez
        for order in (None, "x", [], {"avgPrice": object()}, {"avgPrice": "nan", "updateTime": "abc"}):
            rec.reset_breaker()
            rec.note_candidate("ABCUSDT", result, candles())
            plan = make_plan(with_context=False)
            rec.attach_observation(plan, result, order)
        rec.observe_positions({"plans": "bad"}, snap(), now=1)
        rec.note_candidate("X", object(), "bad")
        rec.attach_observation({}, None, None)

    def test_circuit_breaker_opens_after_five_consecutive_errors_and_success_resets(self):
        def boom(): raise ValueError("x")
        for _ in range(4):
            rec._guarded("t", boom)
        rec._guarded("t", lambda: 1)  # sayaç sıfırlanır
        for _ in range(4):
            rec._guarded("t", boom)
        self.assertTrue(rec.observation_enabled())
        rec._guarded("t", boom)
        self.assertFalse(rec.observation_enabled())
        calls = []
        rec._guarded("t", lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_kill_switch_does_nothing(self):
        for value in ("0", "false", "off"):
            with patch.dict(os.environ, {rec.KILL_SWITCH_ENV: value}):
                plan = make_plan()
                before = copy.deepcopy(plan)
                rec.observe_positions({"plans": {"p1": plan}}, snap(mark=101.0), now=1010)
                rec.note_candidate("ABCUSDT", self.strategy(), candles())
                self.assertEqual(plan, before)
                self.assertEqual(rec._CONTEXTS, {})

    def test_context_store_is_bounded(self):
        for i in range(rec.CONTEXT_STORE_LIMIT + 10):
            rec._CONTEXTS[(f"S{i}", 1)] = {}
            while len(rec._CONTEXTS) > rec.CONTEXT_STORE_LIMIT:
                rec._CONTEXTS.pop(next(iter(rec._CONTEXTS)))
        self.assertEqual(len(rec._CONTEXTS), rec.CONTEXT_STORE_LIMIT)

    def test_new_functions_have_no_await_network_or_file_access(self):
        tree = ast.parse(Path(rec.__file__).read_text(encoding="utf-8"))
        banned_names = {"open", "client", "requests", "httpx", "socket", "urlopen", "subprocess", "aiohttp"}
        found = set()
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef):
                self.fail(f"async function {node.name}")
            if isinstance(node, ast.FunctionDef) and node.name in rec.OBSERVATION_FUNCTIONS:
                found.add(node.name)
                for sub in ast.walk(node):
                    self.assertNotIsInstance(sub, (ast.Await, ast.AsyncFor, ast.AsyncWith), node.name)
                    if isinstance(sub, ast.Name):
                        self.assertNotIn(sub.id, banned_names, node.name)
                    if isinstance(sub, ast.Attribute):
                        self.assertNotIn(sub.attr, {"read_text", "write_text", "urlopen", "request"}, node.name)
        self.assertEqual(found, set(rec.OBSERVATION_FUNCTIONS))

    def test_hooks_in_reconcile_come_after_smart_guard_in_their_own_try(self):
        source = (Path(rec.__file__).parent / "v25_execution.py").read_text(encoding="utf-8")
        guard = source.index("SMART_GUARD_HOOK_FAILED")
        hook = source.index("observe_positions(state, snapshot)")
        clear = source.index("clear_clean_reconciliation_state(state, snapshot)", hook)
        self.assertLess(guard, hook)
        self.assertLess(hook, clear)


class FillCompletionTests(unittest.TestCase):
    def setUp(self):
        rec.reset_breaker()

    def plan_with_empty_fill(self):
        plan = make_plan()
        plan["original_record"]["context"] = {"direction": "LONG", "decision_time": 900,
                                              "risk_basis": {"signal_entry": 100.0, "r": 2.0}}
        plan["original_record"]["fill"] = {"avg_price": None, "time_s": None, "decision_time": 900,
                                           "slippage_vs_signal": {"bp": None, "r": None},
                                           "slippage_vs_planned": {"bp": None, "r": None}, "latency_s": None}
        return plan

    def test_missing_fill_fields_are_completed_from_the_real_position(self):
        plan = self.plan_with_empty_fill()
        rec.observe_positions({"plans": {"p1": plan}}, snap(mark=101.0, entry=100.4), now=1000)
        fill = plan["original_record"]["fill"]
        self.assertEqual(fill["avg_price"], 100.4)
        self.assertEqual(fill["price_source"], "snapshot")
        self.assertEqual(fill["time_source"], "first_snapshot")
        self.assertAlmostEqual(fill["slippage_vs_signal"]["bp"], 40.0)
        self.assertAlmostEqual(fill["slippage_vs_signal"]["r"], 0.2)
        self.assertAlmostEqual(fill["latency_s"], 100.0)

    def test_real_fill_is_never_overwritten_by_snapshot_or_plan_values(self):
        plan = make_plan()
        plan["original_record"]["fill"] = {"avg_price": 100.7, "price_source": "order_result", "time_s": 990.0,
                                           "time_source": "order_result",
                                           "slippage_vs_signal": {"bp": 70.0, "r": 0.35}, "latency_s": 90.0}
        before = copy.deepcopy(plan["original_record"]["fill"])
        rec.observe_positions({"plans": {"p1": plan}}, snap(mark=101.0, entry=100.0), now=1010)
        fill = plan["original_record"]["fill"]
        for key, value in before.items():
            self.assertEqual(fill[key], value, key)

    def test_completed_fill_is_republished_to_the_journal(self):
        calls = []
        plan = self.plan_with_empty_fill()
        plan["original_record"]["journal_sent"] = {"context": True, "fill": True}
        with patch.object(journal_module, "emit", lambda *a, **k: calls.append(k) or True):
            rec.observe_positions({"plans": {"p1": plan}}, snap(mark=101.0, entry=100.4), now=1000)
        fills = [c for c in calls if c.get("section") == "fill"]
        self.assertTrue(fills)
        self.assertEqual(fills[0]["data"]["avg_price"], 100.4)


class RealStack:
    """Gerçek Observer + gerçek SQLite (geçici klasör). Üretim yolu: record -> signal_journal.emit -> Observer -> Journal."""
    def __init__(self, capacity=256):
        import tempfile
        self.folder = tempfile.TemporaryDirectory()
        self.journal = journal_module.Journal(Path(self.folder.name) / "j.sqlite3")
        self.observer = journal_module.Observer(self.journal, capacity=capacity)
        self.patch = patch.object(journal_module, "emit", lambda kind, **d: self.observer.publish(kind, None, d))

    def __enter__(self):
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        self.observer.stopping.set()
        if self.observer.thread:
            self.observer.thread.join(3)
        self.folder.cleanup()

    def disk(self, intent="i1"):
        import sqlite3
        db = sqlite3.connect(self.journal.path)
        try:
            row = db.execute("SELECT payload FROM trades WHERE intent_id=?", (intent,)).fetchone()
        except sqlite3.OperationalError:  # henüz hiçbir şey yazılmadı: tablo da yok
            row = None
        finally:
            db.close()
        return json.loads(row[0]).get("original_v2", {}) if row else {}


def wait_for(predicate, timeout=6.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class JournalAckTests(unittest.TestCase):
    def setUp(self):
        rec.reset_breaker()

    def observe(self, stack, plan, now, mark=100.0):
        rec.observe_positions({"plans": {"p1": plan}}, snap(mark=mark), now=now)

    def test_normal_write_is_persisted_only_after_the_sqlite_readback(self):
        import threading
        with RealStack() as stack:
            gate, real = threading.Event(), stack.journal.original_v2
            stack.journal.original_v2 = lambda *a, **k: (gate.wait(5), real(*a, **k))[1]
            plan = make_plan()
            self.observe(stack, plan, 1010)
            token = plan["original_record"]["path"]["journal"]["token"]
            self.assertEqual(plan["original_record"]["path"]["journal"]["state"], "queued")  # kuyruğa alındı
            self.assertEqual(journal_module.receipt_state(token), "queued")                    # henüz diskte değil
            self.assertEqual(stack.disk(), {})
            gate.set()
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(token) == "persisted"))
            self.assertEqual(sorted(stack.disk()), ["context", "fill", "path", "schema"])
            self.observe(stack, plan, 1020)  # sonraki turda durum 'persisted' olarak plana işlenir
            self.assertEqual(plan["original_record"]["journal_sent"]["context"]["state"], "persisted")

    def test_persisted_records_are_not_republished_needlessly(self):
        calls = []
        with RealStack() as stack:
            plan = make_plan()
            self.observe(stack, plan, 1010)
            self.assertTrue(wait_for(lambda: stack.disk().get("path")))
            self.observe(stack, plan, 1011)
            with patch.object(journal_module, "emit", lambda kind, **d: calls.append(d["section"]) or True):
                self.observe(stack, plan, 1020)
                self.observe(stack, plan, 1030)
        self.assertEqual(calls, [])

    def test_sqlite_error_is_retried_and_the_record_is_not_lost(self):
        import sqlite3
        with RealStack() as stack:
            real, state = stack.journal.original_v2, {"n": 0}
            def flaky(intent_id, section, data):
                if section == "path" and state["n"] < 2:
                    state["n"] += 1
                    raise sqlite3.OperationalError("database is locked")
                return real(intent_id, section, data)
            stack.journal.original_v2 = flaky
            plan = make_plan()
            self.observe(stack, plan, 1010)
            token = plan["original_record"]["path"]["journal"]["token"]
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(token) == "retrying", 3))
            self.assertNotIn("path", stack.disk())
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(token) == "persisted", 8))
            self.assertEqual(stack.disk()["path"]["samples"], 1)
            self.assertGreaterEqual(stack.observer.failures, 2)

    def test_exhausted_retries_become_failed_and_the_observation_layer_republishes(self):
        import sqlite3
        with patch.object(journal_module, "MAX_WRITE_ATTEMPTS", 2), RealStack() as stack:
            real, broken = stack.journal.original_v2, {"on": True}
            def maybe(intent_id, section, data):
                if broken["on"] and section == "context":
                    raise sqlite3.OperationalError("disk I/O error")
                return real(intent_id, section, data)
            stack.journal.original_v2 = maybe
            plan = make_plan()
            self.observe(stack, plan, 1010)
            token = plan["original_record"]["journal_sent"]["context"]["token"]
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(token) == "failed", 6))
            self.assertNotIn("context", stack.disk())
            broken["on"] = False
            self.observe(stack, plan, 1020)  # 'failed' görüldü -> yeniden yayım
            new_token = plan["original_record"]["journal_sent"]["context"]["token"]
            self.assertNotEqual(new_token, token)
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(new_token) == "persisted"))
            self.assertIn("context", stack.disk())

    def test_dead_writer_thread_never_looks_like_a_successful_write(self):
        with RealStack() as stack:
            plan = make_plan()
            self.observe(stack, plan, 1005)
            self.assertTrue(wait_for(lambda: "path" in stack.disk()))
            real = stack.journal.original_v2
            def die(*a, **k):
                raise SystemExit("writer crashed")
            stack.journal.original_v2 = die
            self.observe(stack, plan, 1010, mark=103.0)  # eşik geçişi -> yayım -> iş parçacığı ölür
            self.assertTrue(wait_for(lambda: not stack.observer.thread.is_alive(), 3))
            token = plan["original_record"]["path"]["journal"]["token"]
            self.assertNotEqual(journal_module.receipt_state(token), "persisted")
            self.assertNotEqual(plan["original_record"]["path"]["journal"]["state"], "persisted")
            stack.journal.original_v2 = real
            self.observe(stack, plan, 1020, mark=103.0)          # henüz bayat değil: bekler
            self.assertEqual(plan["original_record"]["path"]["journal"]["token"], token)
            self.observe(stack, plan, 1010 + rec.STALE_QUEUED_SECONDS + 5, mark=103.0)   # bayat -> yeniden yayım, iş parçacığı yenilenir
            new_token = plan["original_record"]["path"]["journal"]["token"]
            self.assertNotEqual(new_token, token)
            self.assertTrue(stack.observer.thread.is_alive())
            self.assertTrue(wait_for(lambda: journal_module.receipt_state(new_token) == "persisted"))
            self.assertEqual(stack.disk()["path"]["samples"], plan["original_record"]["path"]["samples"])

    def test_full_queue_is_reported_as_dropped_and_retried_later(self):
        import threading
        with RealStack(capacity=1) as stack:
            gate, real = threading.Event(), stack.journal.original_v2
            stack.journal.original_v2 = lambda *a, **k: (gate.wait(5), real(*a, **k))[1]
            states = [rec.publish_tracked("i1", "context", {"a": i}) for i in range(4)]
            self.assertIn("dropped", [s["state"] for s in states])
            dropped = next(s for s in states if s["state"] == "dropped")
            self.assertEqual(journal_module.receipt_state(dropped["token"]), "dropped")
            gate.set()
            stack.observer.queue.join()
            plan = make_plan()
            plan["original_record"]["journal_sent"] = {"context": dropped}
            for tick in range(12):  # kuyruk 1 kayıtlık: bazı yayımlar yine atılabilir; her tur eksik olanı yeniden dener
                self.observe(stack, plan, 1010 + tick)
                stack.observer.queue.join()
                if plan["original_record"]["journal_sent"]["context"].get("state") == "persisted":
                    break
            self.assertNotEqual(plan["original_record"]["journal_sent"]["context"]["token"], dropped["token"])
            self.assertEqual(plan["original_record"]["journal_sent"]["context"]["state"], "persisted")
            self.assertIn("context", stack.disk())

    def test_shutdown_is_bounded_and_unwritten_records_are_marked_abandoned(self):
        import time
        with RealStack() as stack:
            real = stack.journal.original_v2
            stack.journal.original_v2 = lambda *a, **k: (time.sleep(0.4), real(*a, **k))[1]
            tokens = [rec.publish_tracked("i1", "context", {"a": 1})["token"] for _ in range(6)]
            started = time.monotonic()
            stack.observer.close(timeout=0.3)
            self.assertLess(time.monotonic() - started, 3.0)  # sınırsız bekleme yok
            states = [journal_module.receipt_state(t) for t in tokens]
            self.assertIn("abandoned", states)
            for state in states:
                self.assertIn(state, ("persisted", "abandoned"))  # 'queued' takılı kalmaz, yalan 'persisted' yok
            # yeniden başlatma: eski süreçten kalan 'queued' kayıt bu süreçte doğrulanmış sayılmaz
            old = {"state": "queued", "token": tokens[0], "boot": "eski-surec", "at": 0.0}
            self.assertTrue(rec._needs_publish(old, 1.0))

    def test_shutdown_drains_everything_when_the_writer_is_fast(self):
        with RealStack() as stack:
            tokens = [rec.publish_tracked("i1", "context", {"a": 1})["token"] for _ in range(20)]
            stack.observer.close(timeout=3)
            self.assertEqual({journal_module.receipt_state(t) for t in tokens}, {"persisted"})

    def test_253_sections_keep_their_old_behaviour(self):
        with RealStack() as stack:
            real, seen = stack.journal.original_v2, []
            def failing(intent_id, section, data):
                seen.append(section)
                raise RuntimeError("write failed")
            stack.journal.original_v2 = failing
            for section in ("entry", "tp1", "legs"):
                rec.publish("i1", section, {"x": 1} if section != "legs" else {"legs": {}, "summary": {}})
            self.assertTrue(wait_for(lambda: len(seen) == 3))
            self.assertTrue(wait_for(lambda: stack.observer.failures == 3))
            self.assertEqual(stack.observer.retry, [])          # eski davranış: yeniden deneme/makbuz yok
            stack.journal.original_v2 = real
            rec.publish("i1", "entry", {"x": 1})
            self.assertTrue(wait_for(lambda: stack.disk().get("entry") == {"x": 1}))

    def test_stale_retry_never_overwrites_a_newer_or_ended_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            journal = journal_module.Journal(Path(folder) / "j.sqlite3")
            journal.original_v2("i1", "path", {"samples": 5, "ended_reason": None})
            journal.original_v2("i1", "path", {"samples": 3, "ended_reason": None})
            journal.original_v2("i1", "path", {"samples": 6, "ended_reason": "plan_closed"})
            journal.original_v2("i1", "path", {"samples": 7, "ended_reason": None})
            self.assertEqual(journal.original_v2_trades()[0]["original_v2"]["path"], {"samples": 6, "ended_reason": "plan_closed"})

    def test_tracked_publishing_adds_no_meaningful_cost_to_reconcile(self):
        import time
        with RealStack() as stack:
            plans = {f"p{i}": dict(make_plan(), id=f"p{i}", intent_id=f"i{i}", symbol=f"S{i}USDT") for i in range(5)}
            for i in range(245):
                plans[f"old{i}"] = {"id": f"old{i}", "status": "KAPANDI", "symbol": "X"}  # eski, bağlamsız planlar
            state = {"plans": plans}
            snapshot = {"positions": [{"symbol": f"S{i}USDT", "direction": "LONG", "entry_price": 100.0, "mark_price": 100.5}
                                      for i in range(5)]}
            started = time.perf_counter()
            for tick in range(300):
                rec.observe_positions(state, snapshot, now=1000 + tick * 10)
            per_tick_ms = (time.perf_counter() - started) / 300 * 1000
        self.assertLess(per_tick_ms, 10.0, f"{per_tick_ms:.3f} ms/tur")


class TimeAccuracyTests(unittest.TestCase):
    def setUp(self):
        rec.reset_breaker()

    def snapshot_fill_plan(self):
        plan = make_plan()
        plan["original_record"]["context"] = {"direction": "LONG", "decision_time": 900,
                                              "risk_basis": {"signal_entry": 100.0, "r": 2.0}}
        plan["original_record"]["fill"] = {"avg_price": 100.0, "price_source": "order_result", "time_s": None,
                                           "decision_time": 900, "latency_s": None}
        return plan

    def run_estimated(self):
        plan = self.snapshot_fill_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=100.0), now=1000)   # ilk snapshot: zaman TAHMİNİ
        rec.observe_positions(state, snap(mark=101.0), now=1010)   # r=0.5
        rec.observe_positions(state, snap(mark=102.0), now=1100)   # r=1.0, TP1
        return plan

    def test_snapshot_time_is_marked_estimated_everywhere(self):
        record = self.run_estimated()["original_record"]
        fill, path = record["fill"], record["path"]
        self.assertEqual(fill["time_source"], "first_snapshot")
        self.assertTrue(fill["time_estimated"])
        self.assertEqual(fill["latency_source"], "first_snapshot")
        self.assertTrue(fill["latency_estimated"])
        self.assertTrue(path["time_basis"]["estimated"])
        self.assertEqual(path["time_basis"]["source"], "first_snapshot")
        self.assertEqual(path["time_basis"]["price_source"], "order_result")
        for field in rec.TIME_FIELDS:
            self.assertEqual(path["time_sources"][field], "first_snapshot", field)
            self.assertIn(field, path["time_basis"]["applies_to"])

    def test_estimated_times_never_claim_partial_false(self):
        path = self.run_estimated()["original_record"]["path"]
        self.assertIsNone(path["partial"])
        self.assertFalse(path["partial_known"])

    def test_exact_fill_never_arriving_leaves_everything_estimated(self):
        plan = self.run_estimated()
        rec.publish_close(plan, [], [], [], [], lambda kind, intent: f"{kind}{intent}")  # giriş bacağı yok
        record = plan["original_record"]
        self.assertTrue(record["path"]["time_basis"]["estimated"])
        self.assertIsNone(record["path"]["partial"])
        self.assertNotIn("time_exact_s", record["fill"])
        self.assertNotIn("timing_estimated", record["path"])

    def test_exact_fill_recomputes_event_times_and_keeps_both_versions(self):
        plan = self.run_estimated()
        rows = [{"qty": "1", "price": "100", "time": 930_000}]   # gerçek dolum 930 sn; tahmin 1000 sn
        rec.publish_close(plan, rows, [], [], [], lambda kind, intent: f"{kind}{intent}")
        record = plan["original_record"]
        path, fill = record["path"], record["fill"]
        self.assertFalse(path["time_basis"]["estimated"])
        self.assertEqual(path["time_basis"]["source"], "exact_legs")
        self.assertTrue(path["time_basis"]["recomputed_from_exact"])
        self.assertEqual(path["fill_time_s"], 930.0)
        self.assertEqual(path["first_r05_s"], 10 + 70)       # önceki 10 sn + 70 sn
        self.assertEqual(path["first_r10_s"], 100 + 70)
        self.assertEqual(path["tp1_touch_s"], 100 + 70)
        self.assertEqual(path["mfe"]["t_s"], 100 + 70)
        self.assertTrue(path["partial_known"])
        self.assertTrue(path["partial"])                    # ilk örnek gerçek dolumdan 70 sn sonra
        for field in rec.TIME_FIELDS:
            self.assertEqual(path["time_sources"][field], "exact_legs")
        old = path["timing_estimated"]                      # tahmini sürümler ayrı korunur
        self.assertEqual(old["fill_time_s"], 1000)
        self.assertEqual(old["first_r05_s"], 10)
        self.assertIsNone(old["partial"])
        self.assertFalse(old["partial_known"])
        self.assertEqual(fill["time_source"], "first_snapshot")   # ilk tahmin ezilmez
        self.assertTrue(fill["time_estimated"])
        self.assertEqual(fill["time_exact_s"], 930.0)
        self.assertEqual(fill["latency_exact_s"], 30.0)
        self.assertEqual(fill["latency_s"], 100.0)
        self.assertTrue(fill["latency_estimated"])

    def test_exact_time_later_than_estimate_is_rejected_and_estimate_stays_estimate(self):
        plan = self.run_estimated()
        rec.publish_close(plan, [{"qty": "1", "price": "100", "time": 1_500_000}], [], [], [], lambda k, i: f"{k}{i}")
        path = plan["original_record"]["path"]
        self.assertEqual(path["time_basis"]["exact_rejected"], "exact_after_estimate")
        self.assertTrue(path["time_basis"]["estimated"])
        self.assertEqual(path["fill_time_s"], 1000)

    def test_exchange_time_is_not_labeled_estimated_and_exact_is_only_a_cross_check(self):
        plan = make_plan()
        state = {"plans": {"p1": plan}}
        rec.observe_positions(state, snap(mark=101.0), now=1010)
        path = plan["original_record"]["path"]
        self.assertFalse(path["time_basis"]["estimated"])
        self.assertEqual(path["time_basis"]["source"], "order_result")
        rec.publish_close(plan, [{"qty": "1", "price": "100", "time": 998_000}], [], [], [], lambda k, i: f"{k}{i}")
        self.assertEqual(plan["original_record"]["path"]["time_basis"]["exact_check_s"], 2.0)
        self.assertEqual(plan["original_record"]["path"]["fill_time_s"], 1000.0)

    def test_missing_time_is_never_presented_as_a_real_fill_time(self):
        fill = rec.build_fill({"quantity": 1.0}, {"avgPrice": "100"}, {"direction": "LONG", "risk_basis": {"r": 2.0}}, 100.0, 900)
        self.assertIsNone(fill["time_s"])
        self.assertIsNone(fill["time_source"])
        self.assertIsNone(fill["latency_s"])
        self.assertIsNone(fill["latency_estimated"])
        basis = rec._time_basis(None)
        self.assertTrue(basis["estimated"])
        self.assertEqual(basis["source"], "unknown")

    def test_journal_keeps_estimated_time_and_adds_exact_fields_later(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            journal = journal_module.Journal(Path(folder) / "j.sqlite3")
            journal.original_v2("i1", "fill", {"time_s": 1000.0, "time_source": "first_snapshot", "time_estimated": True})
            journal.original_v2("i1", "fill", {"time_s": 5.0, "time_source": "x", "time_exact_s": 930.0, "latency_exact_s": 30.0})
            fill = journal.original_v2_trades()[0]["original_v2"]["fill"]
            self.assertEqual((fill["time_s"], fill["time_source"], fill["time_estimated"]), (1000.0, "first_snapshot", True))
            self.assertEqual((fill["time_exact_s"], fill["latency_exact_s"]), (930.0, 30.0))


class JournalTests(unittest.TestCase):
    def test_new_sections_merge_correctly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            journal = journal_module.Journal(Path(folder) / "j.sqlite3")
            journal.original_v2("i1", "context", {"a": 1})
            journal.original_v2("i1", "context", {"a": 2})
            journal.original_v2("i1", "path", {"samples": 1})
            journal.original_v2("i1", "path", {"samples": 5})
            journal.original_v2("i1", "fill", {"avg_price": 1.0})
            journal.original_v2("i1", "fill", {"avg_price": 9.0, "exact": {"vwap": 1.1}})
            record = journal.original_v2_trades()[0]["original_v2"]
            self.assertEqual(record["context"], {"a": 1})
            self.assertEqual(record["path"], {"samples": 5})
            self.assertEqual(record["fill"], {"avg_price": 1.0, "exact": {"vwap": 1.1}})
            with self.assertRaises(ValueError):
                journal.original_v2("i1", "bogus", {})

    def test_empty_fill_fields_are_completed_later_but_real_values_are_kept(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            journal = journal_module.Journal(Path(folder) / "j.sqlite3")
            journal.original_v2("i2", "fill", {"avg_price": None, "time_s": 5.0, "slippage_vs_signal": {"bp": None, "r": None}})
            journal.original_v2("i2", "fill", {"avg_price": 100.4, "time_s": 9.0, "slippage_vs_signal": {"bp": 40.0, "r": 0.2}})
            journal.original_v2("i2", "fill", {"avg_price": 1.0, "slippage_vs_signal": {"bp": 1.0, "r": 1.0}})
            fill = journal.original_v2_trades()[0]["original_v2"]["fill"]
            self.assertEqual(fill["avg_price"], 100.4)
            self.assertEqual(fill["time_s"], 5.0)
            self.assertEqual(fill["slippage_vs_signal"], {"bp": 40.0, "r": 0.2})


if __name__ == "__main__":
    unittest.main()

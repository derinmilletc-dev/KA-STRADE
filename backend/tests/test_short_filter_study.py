"""SHORT-filtre karşılaştırma yardımcıları. Bağımlılık yok: yalnız stdlib (unittest/pytest ikisiyle de çalışır)."""

from __future__ import annotations

import ast
import itertools
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import short_filter_study as study  # noqa: E402


def decision(side="SELL", alignment=85.0, eligible=True, fomo="DÜŞÜK", rsi=45.0):
    return {"decision": side, "signal_timestamp": 1000, "entry_eligible": eligible,
            "mtf": {"alignment": alignment}, "analysis": {"rsi": rsi, "radar": {"fomo_risk": fomo}}}


def real_shared_mtf_decision():
    """Canlı koddaki shared_mtf_decision fonksiyonunu, fastapi'yi içe aktarmadan, olduğu gibi çalıştırır."""
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "shared_mtf_decision")
    namespace = {"SHORT_MTF_ALIGNMENT_MAX": 80.0}
    exec("from __future__ import annotations\n" + ast.get_source_segment(source, node), namespace)  # noqa: S102
    return namespace["shared_mtf_decision"]


class CandidateRules(unittest.TestCase):
    def test_current_filter_blocks_short_at_and_above_80_only(self):
        self.assertTrue(study.blocks("CUR_80", decision("SELL", 80)))
        self.assertTrue(study.blocks("CUR_80", decision("SELL", 92)))
        self.assertFalse(study.blocks("CUR_80", decision("SELL", 79.9)))

    def test_current_filter_never_touches_long_or_ineligible(self):
        self.assertFalse(study.blocks("CUR_80", decision("BUY", 99)))
        self.assertFalse(study.blocks("CUR_80", decision("SELL", 99, eligible=False)))

    def test_off_blocks_nothing(self):
        for side, alignment in itertools.product(("BUY", "SELL"), (10, 80, 99)):
            self.assertFalse(study.blocks("OFF", decision(side, alignment)))

    def test_threshold_variants(self):
        self.assertTrue(study.blocks("THR_75", decision("SELL", 76)))
        self.assertFalse(study.blocks("THR_85", decision("SELL", 84.9)))
        self.assertTrue(study.blocks("THR_85", decision("SELL", 85)))
        self.assertFalse(study.blocks("THR_90", decision("SELL", 89)))
        self.assertTrue(study.blocks("THR_90", decision("SELL", 90)))

    def test_extension_candidate_needs_both_conditions(self):
        self.assertFalse(study.blocks("EXT_ONLY_80", decision("SELL", 90)))                     # yüksek ama uzamamış
        self.assertTrue(study.blocks("EXT_ONLY_80", decision("SELL", 90, fomo="YÜKSEK")))
        self.assertTrue(study.blocks("EXT_ONLY_80", decision("SELL", 90, rsi=29.9)))
        self.assertFalse(study.blocks("EXT_ONLY_80", decision("SELL", 79, fomo="YÜKSEK")))      # uzamış ama alignment düşük
        self.assertFalse(study.blocks("EXT_ONLY_80", decision("SELL", 90, rsi=30)))             # sınır dahil değil

    def test_symmetric_candidate_blocks_both_sides(self):
        self.assertTrue(study.blocks("SYM_80", decision("BUY", 80)))
        self.assertTrue(study.blocks("SYM_80", decision("SELL", 80)))
        self.assertFalse(study.blocks("SYM_80", decision("BUY", 79)))

    def test_missing_alignment_is_never_blocked(self):
        self.assertFalse(study.blocks("CUR_80", {"decision": "SELL", "entry_eligible": True, "mtf": {}}))
        self.assertFalse(study.blocks("CUR_80", {"decision": "SELL", "entry_eligible": True}))

    def test_candidates_only_remove_entries_never_add(self):
        waiting = decision("SELL", 99, eligible=False)
        for name in study.CANDIDATES:
            self.assertIs(study.apply_candidate(name, waiting), waiting)
            kept = study.apply_candidate(name, decision("SELL", 50))
            self.assertTrue(kept["entry_eligible"])
        blocked = study.apply_candidate("CUR_80", decision("SELL", 90))
        self.assertFalse(blocked["entry_eligible"])
        self.assertEqual(blocked["decision"], "WAIT")
        self.assertEqual(blocked, {"decision": "WAIT", "signal_timestamp": 1000, "entry_eligible": False,
                                   "reason": "QUALITY_OR_MTF_GATE", "reasons": ["QUALITY_OR_MTF_GATE"]})

    def test_protocol_hash_is_stable_and_detects_edits(self):
        first = study.protocol_sha256()
        self.assertEqual(first, study.protocol_sha256())
        changed = {**study.CANDIDATES, "THR_85": {**study.CANDIDATES["THR_85"], "short_block_at": 86}}
        with mock.patch.object(study, "CANDIDATES", changed):
            self.assertNotEqual(first, study.protocol_sha256())


class ControlMatchesLiveFilter(unittest.TestCase):
    """CUR_80, canlı shared_mtf_decision ile aynı giriş kararını vermeli (gerçek fonksiyonla)."""

    def test_cur_80_reproduces_real_filter_on_a_confidence_grid(self):
        shared = real_shared_mtf_decision()
        grid = range(40, 96, 5)
        checked = blocked = 0
        for direction, c15, c1h, c4h in itertools.product(("LONG", "SHORT"), grid, grid, grid):
            frames = {"1h": {"direction": direction, "confidence": c1h}, "4h": {"direction": direction, "confidence": c4h}}
            live = shared("X", direction, c15, frames)                       # filtre AÇIK (canlı)
            off = shared("X", direction, c15, frames, short_filter=False)    # filtre KAPALI (araştırma önbelleği)
            side = "BUY" if direction == "LONG" else "SELL"
            on_decision = {"decision": side if live["entry_permission"] else "WAIT",
                           "entry_eligible": live["entry_permission"]}
            off_decision = {"decision": side, "entry_eligible": off["entry_permission"], "mtf": off,
                            "signal_timestamp": 1}
            self.assertTrue(off["entry_permission"])  # üç dilim aynı yönde: filtre kapalıyken hep izinli
            self.assertEqual(study.parity_mismatches([("k", on_decision, off_decision)]), [])
            checked += 1
            blocked += not live["entry_permission"]
        self.assertGreater(checked, 1000)
        self.assertGreater(blocked, 0)  # test gerçekten engellenen vakalar içeriyor

    def test_parity_detects_a_wrong_control(self):
        on = {"decision": "SELL", "entry_eligible": True}
        off = decision("SELL", 90)  # CUR_80 bunu engellerdi, canlı ise izin vermiş: uyumsuz
        self.assertEqual(study.parity_mismatches([("k", on, off)]), ["k"])


class Statistics(unittest.TestCase):
    def test_month_returns_and_labels(self):
        rows = [{"time": 0, "open": 100, "close": 101}, {"time": 1, "open": 101, "close": 110},
                {"time": 100, "open": 110, "close": 100}]
        month = {0: "A", 1: "A", 100: "B"}
        returns = study.month_returns(rows, lambda t: month[t])
        self.assertAlmostEqual(returns["A"], 10.0)
        self.assertAlmostEqual(returns["B"], (100 / 110 - 1) * 100)
        self.assertEqual(study.market_label(5.0), "YUKARI")
        self.assertEqual(study.market_label(-5.0), "ASAGI")
        self.assertEqual(study.market_label(4.99), "YATAY")
        self.assertEqual(study.market_label(None), "BILINMIYOR")

    def test_breakdown_groups_by_label_and_direction(self):
        rows = [{"direction": "SHORT", "m": "ASAGI"}, {"direction": "SHORT", "m": "ASAGI"},
                {"direction": "LONG", "m": "ASAGI"}, {"direction": "SHORT", "m": "YATAY"}]
        result = study.breakdown(rows, lambda r: r["m"], lambda items: {"n": len(items)})
        self.assertEqual(result, {"ASAGI": {"LONG": {"n": 1}, "SHORT": {"n": 2}}, "YATAY": {"SHORT": {"n": 1}}})

    def test_signal_changes_lists_added_and_removed_separately(self):
        base = [{"signal_id": "a", "opened_at": "2026-01-01"}, {"signal_id": "b", "opened_at": "2026-01-02"}]
        variant = [{"signal_id": "b", "opened_at": "2026-01-02"}, {"signal_id": "c", "opened_at": "2026-01-03", "direction": "SHORT"}]
        changes = study.signal_changes(variant, base)
        self.assertEqual([(c["degisim"], c["signal_id"]) for c in changes], [("ELENDI", "a"), ("EKLENDI", "c")])

    def test_compact_off_matches_dataset_compaction_rules(self):
        waiting = {"decision": "WAIT", "signal_timestamp": 5, "entry_eligible": False, "reason": "R",
                   "reasons": ["x"], "analysis": {"big": 1}, "mtf": {"a": 1}}
        self.assertEqual(study.compact_off(waiting),
                         {"decision": "WAIT", "signal_timestamp": 5, "entry_eligible": False, "reason": "R", "reasons": ["x"]})
        eligible = {"decision": "SELL", "entry_eligible": True, "analysis": {"series": [1, 2], "rsi": 40}}
        kept = study.compact_off(eligible)
        self.assertEqual(kept["analysis"], {"rsi": 40})
        self.assertIn("series", eligible["analysis"])  # girdi bozulmaz

    def test_report_never_declares_a_winner_and_marks_skipped_checks(self):
        text = study.render_report({"anchors": {}, "phases": {}, "limitations": ["x"]})
        self.assertIn("ATLANDI", text)
        self.assertIn("kazanan", text)
        self.assertIn("`ATLANDI` başarılı sayılmaz", text)

    def test_report_renders_phase_tables_with_all_sections(self):
        metrics = {"N": 70, "mean_net_r": -0.05, "usdt_pf": 0.9, "win_rate": 0.4, "max_drawdown_usdt": 20.0,
                   "total_net_pnl_usdt": -5.0}
        item = {"metrics": metrics, "by_direction": {"SHORT": {"N": 10, "mean_net_r": -0.1, "usdt_pf": 0.8}},
                "market_regime": {"ASAGI": {"SHORT": {"N": 10, "mean_net_r": -0.1}}}, "mean_r_95": [-0.2, 0.1]}
        changed = {**item, "changes_vs_control": {"added": {"N": 3, "mean_net_r": 0.1}, "removed": {"N": 1, "mean_net_r": None}}}
        text = study.render_report({"anchors": {"count_parity": "BAŞARILI", "prescreen_parity": "BAŞARISIZ", "notes": ["n"]},
                                    "phases": {"TRAIN": {"CUR_80": item, "THR_85": changed}}, "limitations": []})
        for expected in ("Dönem: TRAIN", "| THR_85 |", "Piyasa dönemine göre", "BAŞARISIZ", "[-0.200; 0.100]", "ASAGI"):
            self.assertIn(expected, text)


class FakeMain:
    def __init__(self):
        self.calls = []
        self.gate_open = False
        self.canonical_historical_decision = self.real

    def real(self, symbol, candles, decision_time, **kwargs):
        self.calls.append((symbol, decision_time, self.gate_open, kwargs.get("policy")))
        return {"decision": "SELL", "signal_timestamp": decision_time, "entry_eligible": True,
                "mtf": {"alignment": 85.0}, "analysis": {"rsi": 45.0, "series": [1], "radar": {"fomo_risk": "DÜŞÜK"}}}


class VariantGateBehavior(unittest.TestCase):
    def setUp(self):
        self.main = FakeMain()
        main = self.main

        class Gate:
            def __enter__(self_inner):
                main.gate_open = True

            def __exit__(self_inner, *_):
                main.gate_open = False

        self.make = lambda: study.VariantGate(main, Gate)
        self.real = main.canonical_historical_decision

    def test_off_decision_is_computed_once_and_each_variant_filters_it(self):
        with self.make() as gate:
            gate.variant = "CUR_80"
            blocked = self.main.canonical_historical_decision("X", {}, 100, historical_policy_override={"a": 1})
            gate.variant = "THR_90"
            kept = self.main.canonical_historical_decision("X", {}, 100, historical_policy_override={"a": 1})
            gate.variant = "OFF"
            off = self.main.canonical_historical_decision("X", {}, 100, historical_policy_override={"a": 1})
        self.assertFalse(blocked["entry_eligible"])      # alignment 85 >= 80
        self.assertTrue(kept["entry_eligible"])          # 85 < 90
        self.assertNotIn("series", kept["analysis"])     # önbellek küçültülmüş
        self.assertTrue(off["entry_eligible"])
        self.assertEqual(len(self.main.calls), 1)        # gerçek fonksiyon yalnız bir kez çalıştı
        self.assertTrue(self.main.calls[0][2])           # ve filtre-kapalı bağlamı içinde

    def test_different_time_or_policy_override_is_a_different_cache_entry(self):
        with self.make() as gate:
            gate.variant = "OFF"
            self.main.canonical_historical_decision("X", {}, 100, historical_policy_override={"a": 1})
            self.main.canonical_historical_decision("X", {}, 101, historical_policy_override={"a": 1})
            self.main.canonical_historical_decision("X", {}, 100, historical_policy_override={"a": 2})
        self.assertEqual(len(self.main.calls), 3)

    def test_without_variant_or_with_account_context_the_real_function_runs_directly(self):
        with self.make() as gate:
            direct = self.main.canonical_historical_decision("X", {}, 100)            # variant None
            gate.variant = "CUR_80"
            with_policy = self.main.canonical_historical_decision("X", {}, 100, policy={"p": 1})
        self.assertIn("series", direct["analysis"])      # dokunulmadı
        self.assertIn("series", with_policy["analysis"])
        self.assertEqual([call[2] for call in self.main.calls], [False, False])  # filtre-kapalı bağlamı yok
        self.assertEqual(gate.memo, {})

    def test_original_function_is_restored_on_exit_even_after_error(self):
        with self.assertRaises(RuntimeError):
            with self.make():
                self.assertIsNot(self.main.canonical_historical_decision, self.real)
                raise RuntimeError("boom")
        self.assertEqual(self.main.canonical_historical_decision, self.real)


if __name__ == "__main__":
    unittest.main()

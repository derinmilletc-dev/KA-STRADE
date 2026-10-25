"""SHORT-filtre karşılaştırma yardımcıları. Bağımlılık yok: yalnız stdlib (unittest/pytest ikisiyle de çalışır)."""

from __future__ import annotations

import ast
import itertools
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import analysis as live_analysis  # noqa: E402
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


def flagged(side="SELL", alignment=85.0, *, trap=10, rsi=45.0, entry=100.0, support=90.0, atr=2.0, volume=1.0, fomo="DÜŞÜK"):
    """Tükenme bayrakları için gerçek alanlarla (analysis.py ile aynı adlar) bir karar."""
    return {"decision": side, "signal_timestamp": 1000, "entry_eligible": True, "mtf": {"alignment": alignment},
            "analysis": {"rsi": rsi, "entry": entry, "support": support, "atr": atr, "volume_ratio": volume,
                         "radar": {"fomo_risk": fomo, "trap_score": trap}}}


class ExhaustionFlags(unittest.TestCase):
    def test_each_flag_from_direct_fields(self):
        flags = study.short_flags(flagged(trap=10 + 18, fomo="YÜKSEK"))
        self.assertTrue(flags["extension"])
        self.assertFalse(any(flags[n] for n in ("rsi_low", "near_support", "lower_wick", "low_volume")))
        self.assertTrue(study.short_flags(flagged(trap=10 + 10, rsi=27.9))["rsi_low"])
        self.assertFalse(study.short_flags(flagged(trap=10, rsi=28.0))["rsi_low"])          # sınır dahil değil
        self.assertTrue(study.short_flags(flagged(trap=10 + 15, entry=101.9, support=100.0, atr=2.0))["near_support"])
        self.assertFalse(study.short_flags(flagged(trap=10, entry=102.0, support=100.0, atr=2.0))["near_support"])  # tam 1 ATR
        self.assertTrue(study.short_flags(flagged(trap=10 + 24, volume=0.89))["low_volume"])

    def test_lower_wick_is_recovered_from_the_trap_remainder(self):
        self.assertTrue(study.short_flags(flagged(trap=10 + 20))["lower_wick"])
        self.assertFalse(study.short_flags(flagged(trap=10))["lower_wick"])
        # destek yakın (15) + alt fitil (20) = 45 > 35 olsa da bileşenler doğru ayrışır
        both = study.short_flags(flagged(trap=10 + 15 + 20, entry=100.5, support=100.0, atr=2.0))
        self.assertTrue(both["near_support"] and both["lower_wick"])

    def test_unknown_when_fields_missing_or_remainder_is_inconsistent(self):
        self.assertIsNone(study.short_flags({"decision": "SELL", "analysis": {"rsi": 40}}))
        self.assertIsNone(study.short_flags({"decision": "SELL"}))
        self.assertIsNone(study.short_flags(flagged(trap=10 + 7)))          # hiçbir bayrak 7 puan değil
        no_fomo = flagged()
        del no_fomo["analysis"]["radar"]["fomo_risk"]
        self.assertIsNone(study.short_flags(no_fomo))

    def test_exhaustion_excludes_low_volume(self):
        self.assertFalse(study.has_exhaustion(flagged(trap=10 + 24, volume=0.8)))
        self.assertTrue(study.has_exhaustion(flagged(trap=10 + 18, fomo="YÜKSEK")))

    def test_flags_match_real_analyze_and_an_independent_wick_calculation(self):
        """Gerçek analysis.analyze çıktısında, trap_score <= 35 olan her SHORT için bayraklar çıkarılabilmeli ve alt fitil
        bayrağı mumlardan bağımsız hesaplananla aynı olmalı. Her bayrak en az bir örnekte görülmeli (boş test değil)."""
        def series(seed, crash):
            rng = random.Random(seed)
            price, rows = 100.0, []
            crash_at = rng.randint(240, 300)
            drift0 = rng.choice([-0.002, -0.001, -0.0004, 0.0003])
            for i in range(320):
                opening = price
                drift, sigma = (-0.0008, 0.006) if crash else (drift0, 0.012)
                if crash and crash_at <= i < crash_at + 4:
                    drift = -0.025
                close = opening * (1 + rng.gauss(drift, sigma))
                spread = sigma * (0.3 if crash else rng.choice([0.2, 0.6, 1.5]))
                high = max(opening, close) * (1 + abs(rng.gauss(0, spread)))
                low = min(opening, close) * (1 - abs(rng.gauss(0, spread)))
                volume = rng.uniform(50, 150) * (rng.choice([0.5, 1, 1, 2]) if not crash else 1)
                if crash and crash_at <= i < crash_at + 4:
                    volume *= 2
                rows.append({"time": i * 900, "open": opening, "high": high, "low": low, "close": close, "volume": volume})
                price = close
            return rows

        seen = {name: 0 for name in ("extension", "rsi_low", "near_support", "lower_wick", "low_volume")}
        checked = 0
        for seed, crash in [(s, False) for s in range(60)] + [(s, True) for s in range(40)]:
            rows = series(seed, crash)
            for end in range(230, 320, 3 if not crash else 1):
                result = live_analysis.analyze(rows[:end])
                if result["direction"] != "SHORT" or result["radar"]["trap_score"] > study.TRAP_GATE_MAX:
                    continue
                flags = study.short_flags({"analysis": {k: v for k, v in result.items() if k != "series"}})
                self.assertIsNotNone(flags, (seed, end))
                recent = rows[:end][-8:]
                body = max(sum(abs(c["close"] - c["open"]) for c in recent), result["atr"] * 0.05, 0.00000001)
                direct_wick = sum(min(c["open"], c["close"]) - c["low"] for c in recent) / body > 1.5
                self.assertEqual(flags["lower_wick"], direct_wick, (seed, end))
                for name, on in flags.items():
                    seen[name] += bool(on)
                checked += 1
        self.assertGreater(checked, 300)
        self.assertTrue(all(count > 0 for count in seen.values()), seen)

    def test_constants_mirror_analysis_source(self):
        source = (ROOT / "app" / "analysis.py").read_text(encoding="utf-8")
        for fragment in ("trap_score += 24 if volume_ratio < 0.9", "trap_score += 20 if lower_wick_ratio > 1.5",
                         "trap_score += 18 if extension_atr > 2", "trap_score += 15 if (price - support) / max(atr_value, 0.00000001) < 1",
                         "trap_score += 10 if rsi_value < 28", "trap_score = 10\n"):
            self.assertIn(fragment, source)
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('analysis["radar"]["trap_score"] <= 35', main_source)
        self.assertEqual(study.TRAP_GATE_MAX, 35)


class TrapGateOverlap(unittest.TestCase):
    def test_pairs_listing_matches_brute_force(self):
        result = study.trap_gate_pairs()
        passing = {tuple(c) for c in result["passes_trap_gate"]}
        expected = {tuple(c) for size in range(6) for c in itertools.combinations(study.TRAP_WEIGHTS, size)
                    if study.TRAP_BASE + sum(study.TRAP_WEIGHTS[n] for n in c) <= 35}
        self.assertEqual(passing, expected)
        self.assertEqual(passing, {(), ("low_volume",), ("lower_wick",), ("extension",), ("near_support",), ("rsi_low",),
                                   ("near_support", "rsi_low")})
        self.assertEqual(len(passing) + len(result["rejected_by_trap_gate"]), 2 ** len(study.TRAP_WEIGHTS))

    def test_flags_are_uniquely_recoverable_below_the_gate(self):
        sums = {}
        for combo in study.trap_gate_pairs()["passes_trap_gate"]:
            sums.setdefault(sum(study.TRAP_WEIGHTS[n] for n in combo), []).append(combo)
        self.assertTrue(all(len(v) == 1 for v in sums.values()), sums)


class NewCandidates(unittest.TestCase):
    def test_original_seven_candidates_are_unchanged(self):
        expected = {"CUR_80": (80, None, False), "OFF": (None, None, False), "THR_75": (75, None, False),
                    "THR_85": (85, None, False), "THR_90": (90, None, False), "EXT_ONLY_80": (80, None, True),
                    "SYM_80": (80, 80, False)}
        for name, (short_at, long_at, extended) in expected.items():
            rule = study.CANDIDATES[name]
            self.assertEqual((rule["short_block_at"], rule["long_block_at"], rule["short_only_if_extended"]),
                             (short_at, long_at, extended), name)
            self.assertFalse(rule.get("short_exhaustion"), name)
        self.assertEqual(study.CONTROL, "CUR_80")

    def test_exhaust_80_needs_high_alignment_and_a_flag(self):
        flagged_extension = flagged(alignment=85, trap=28, fomo="YÜKSEK")
        self.assertTrue(study.blocks("EXHAUST_80", flagged_extension))
        self.assertFalse(study.blocks("EXHAUST_80", flagged(alignment=85, trap=10)))                 # yüksek güven TEK BAŞINA ret değil
        self.assertFalse(study.blocks("EXHAUST_80", flagged(alignment=79, trap=28, fomo="YÜKSEK")))  # alignment düşük
        self.assertTrue(study.blocks("CUR_80", flagged(alignment=85, trap=10)))                      # mevcut filtre ise reddeder
        self.assertFalse(study.blocks("EXHAUST_80", flagged(alignment=95, trap=34, volume=0.8)))     # düşük hacim tükenme sayılmaz

    def test_exhaust_any_ignores_alignment(self):
        self.assertTrue(study.blocks("EXHAUST_ANY", flagged(alignment=40, trap=20, rsi=27)))
        self.assertTrue(study.blocks("EXHAUST_ANY", flagged(alignment=99, trap=20, rsi=27)))
        self.assertFalse(study.blocks("EXHAUST_ANY", flagged(alignment=99, trap=10)))

    def test_unknown_flags_are_never_blocked_and_long_and_ineligible_untouched(self):
        for name in ("EXHAUST_80", "EXHAUST_ANY"):
            self.assertFalse(study.blocks(name, decision("SELL", 90)))                      # alan yok -> bilinmiyor
            self.assertFalse(study.blocks(name, flagged("BUY", 99, trap=28, fomo="YÜKSEK")))
            ineligible = flagged(alignment=99, trap=28, fomo="YÜKSEK")
            ineligible["entry_eligible"] = False
            self.assertFalse(study.blocks(name, ineligible))
            self.assertFalse(study.blocks(name, {**flagged(alignment=99, trap=28, fomo="YÜKSEK"), "mtf": {}}))  # alignment yok

    def test_new_candidates_only_remove_entries(self):
        for name in ("EXHAUST_80", "EXHAUST_ANY"):
            waiting = {"decision": "WAIT", "entry_eligible": False}
            self.assertIs(study.apply_candidate(name, waiting), waiting)
            blocked = study.apply_candidate(name, flagged(alignment=90, trap=28, fomo="YÜKSEK"))
            self.assertEqual(blocked["decision"], "WAIT")
            self.assertEqual(blocked["reasons"], ["QUALITY_OR_MTF_GATE"])

    def test_protocol_hash_covers_exhaustion_constants(self):
        first = study.protocol_sha256()
        with mock.patch.object(study, "RSI_LOW_BELOW", 29.0):
            self.assertNotEqual(first, study.protocol_sha256())
        with mock.patch.object(study, "SUPPORT_ATR_BELOW", 1.5):
            self.assertNotEqual(first, study.protocol_sha256())


class EvidenceRule(unittest.TestCase):
    def test_bootstrap_diff_is_deterministic_and_signed(self):
        better, base = [1.0, 0.5, 0.8, 1.2] * 30, [0.0, -0.2, 0.1, 0.0] * 30
        first = study.bootstrap_diff(better, base)
        self.assertEqual(first, study.bootstrap_diff(better, base))
        self.assertGreater(first["diff"], 0.8)
        self.assertGreater(first["lo"], 0)
        self.assertIsNone(study.bootstrap_diff([], base))
        noisy = study.bootstrap_diff([1.0, -1.0] * 20, [0.9, -1.1] * 20)
        self.assertLess(noisy["lo"], 0)  # küçük fark gürültüden ayırt edilemez

    def test_verdict_requires_both_phases_strictly_positive_interval(self):
        good = {"diff": 0.1, "lo": 0.02, "hi": 0.2}
        weak = {"diff": 0.1, "lo": -0.05, "hi": 0.25}
        self.assertEqual(study.evidence_verdict({"TRAIN": good, "VALIDATION": good})["karar"], "KANITLANDI")
        for case in ({"TRAIN": good, "VALIDATION": weak}, {"TRAIN": weak, "VALIDATION": good},
                     {"TRAIN": good, "VALIDATION": None}, {"TRAIN": good}):
            result = study.evidence_verdict(case)
            self.assertEqual(result["karar"], "KANITLANMADI", case)
            self.assertTrue(result["nedenler"])
        zero = {"diff": 0.0, "lo": 0.0, "hi": 0.1}
        self.assertEqual(study.evidence_verdict({"TRAIN": zero, "VALIDATION": good})["karar"], "KANITLANMADI")  # 0 dahil


class BuildersUsedByCli(unittest.TestCase):
    def test_build_evidence_only_for_exhaustion_candidates_and_available_bases(self):
        better = [1.0, 0.8, 1.2, 0.9] * 40
        base = [0.0, -0.1, 0.1, 0.0] * 40
        net_r = {"CUR_80": {"TRAIN": base, "VALIDATION": base}, "EXHAUST_80": {"TRAIN": better, "VALIDATION": better},
                 "THR_85": {"TRAIN": better, "VALIDATION": better}}
        names = ["CUR_80", "EXHAUST_80", "THR_85"]
        evidence = study.build_evidence(names, net_r, ["TRAIN", "VALIDATION"])
        self.assertEqual(list(evidence), ["EXHAUST_80"])                      # THR_85 tükenme adayı değil
        self.assertEqual(list(evidence["EXHAUST_80"]), ["CUR_80"])            # OFF çalıştırılmadı
        self.assertEqual(evidence["EXHAUST_80"]["CUR_80"]["karar"], "KANITLANDI")
        import json
        json.dumps(evidence, allow_nan=False)                                  # results.json'a yazılabilir
        same = {**net_r, "EXHAUST_80": {"TRAIN": base, "VALIDATION": base}}
        self.assertEqual(study.build_evidence(names, same, ["TRAIN", "VALIDATION"])["EXHAUST_80"]["CUR_80"]["karar"], "KANITLANMADI")

    def test_build_overlap_is_json_serialisable_and_phase_keyed(self):
        import json
        features = OverlapSummary.make_features(None)
        out = study.build_overlap(features, {"TRAIN": [], "VALIDATION": []})
        self.assertEqual(list(out["donemler"]), ["TRAIN", "VALIDATION"])
        json.dumps(out, allow_nan=False)


class OverlapSummary(unittest.TestCase):
    def make_features(self):
        base = {"phase": "TRAIN", "confidence": 85, "trap_score": 20, "breakout_quality": 60}
        flags_yes = {"extension": True, "rsi_low": False, "near_support": False, "lower_wick": False, "low_volume": False}
        flags_no = {k: False for k in flags_yes}
        return {
            ("A", 1): {**base, "side": "SHORT", "alignment": 85, "flags": flags_yes},
            ("A", 2): {**base, "side": "SHORT", "alignment": 90, "flags": flags_no},
            ("A", 3): {**base, "side": "SHORT", "alignment": 60, "flags": flags_yes},
            ("A", 4): {**base, "side": "SHORT", "alignment": 88, "flags": None},
            ("A", 5): {**base, "side": "LONG", "alignment": 90},
            ("B", 6): {**base, "phase": "VALIDATION", "side": "SHORT", "alignment": 95, "flags": flags_yes},
        }

    def trade(self, symbol, at, direction, r):
        return {"symbol": symbol, "opened_at_epoch": at, "direction": direction, "net_r": r, "net_pnl": r * 10}

    def test_signal_level_counts_and_unknowns(self):
        out = study.overlap_summary(self.make_features(), [], "TRAIN")["signal_duzeyi"]
        self.assertEqual(out["uygun_short"], 4)
        self.assertEqual(out["mevcut_filtrenin_reddettigi"], 3)                 # 85, 90, 88 (LONG ve VALIDATION hariç)
        self.assertEqual(out["reddedilenlerde_herhangi_isaret"], {"N": 3, "bilinmeyen": 1, "isaretli": 1, "oran": 0.5})
        self.assertEqual(out["reddedilmeyenlerde_herhangi_isaret"]["oran"], 1.0)
        self.assertEqual(study.overlap_summary(self.make_features(), [], "VALIDATION")["signal_duzeyi"]["uygun_short"], 1)
        self.assertEqual(out["ortalama_kirilim_kalitesi"], {"reddedilen": 60.0, "reddedilmeyen": 60.0})
        self.assertEqual(out["ortalama_guven"]["reddedilen"], 85.0)

    def test_trade_level_groups_and_unmatched_are_reported(self):
        trades = [self.trade("A", 1, "SHORT", 1.0), self.trade("A", 2, "SHORT", -1.0), self.trade("A", 3, "SHORT", 0.5),
                  self.trade("A", 4, "SHORT", 0.2), self.trade("A", 5, "LONG", 0.3), self.trade("Z", 9, "SHORT", 0.1),
                  self.trade("B", 6, "SHORT", 2.0)]  # B/6 VALIDATION'da: TRAIN özetinde eşleşmemiş sayılır
        out = study.overlap_summary(self.make_features(), trades, "TRAIN")
        self.assertEqual((out["baglanan_islem"], out["baglanamayan_islem"]), (5, 2))
        groups = out["islem_duzeyi"]
        self.assertEqual(groups["SHORT|alignment>=80|isaret_var"]["N"], 1)
        self.assertEqual(groups["SHORT|alignment>=80|isaret_yok"]["mean_net_r"], -1.0)
        self.assertEqual(groups["SHORT|alignment>=80|bilinmiyor"]["N"], 1)
        self.assertEqual(groups["SHORT|alignment<80|isaret_var"]["mean_net_r"], 0.5)
        self.assertEqual(groups["LONG|alignment>=80|-"]["N"], 1)

    def test_simple_stats(self):
        stats = study.simple_stats([{"net_r": 1.0, "net_pnl": 10.0}, {"net_r": -0.5, "net_pnl": -5.0}])
        self.assertEqual((stats["N"], stats["win_rate"], stats["usdt_pf"]), (2, 0.5, 2.0))
        self.assertEqual(study.simple_stats([])["N"], 0)
        self.assertIsNone(study.simple_stats([{"net_r": 1.0, "net_pnl": 5.0}])["usdt_pf"])  # kayıp yok -> tanımsız


class GateRecordsFeaturesAndReport(unittest.TestCase):
    def test_gate_records_one_feature_row_per_eligible_decision_with_phase(self):
        class Main:
            def __init__(self):
                self.canonical_historical_decision = self.real

            def real(self, symbol, candles, decision_time, **kwargs):
                if decision_time == 200:
                    return {"decision": "WAIT", "signal_timestamp": 200, "entry_eligible": False, "reason": "R", "reasons": ["R"]}
                result = flagged(alignment=85, trap=28, fomo="YÜKSEK")
                return {**result, "analysis": {**result["analysis"], "confidence": 88, "series": [1]}}

        class Gate:
            def __enter__(self_inner):
                pass

            def __exit__(self_inner, *_):
                pass

        main = Main()
        with study.VariantGate(main, Gate) as gate:
            gate.variant, gate.phase = "CUR_80", "TRAIN"
            main.canonical_historical_decision("X", {}, 100)
            main.canonical_historical_decision("X", {}, 200)
            gate.variant = "OFF"
            main.canonical_historical_decision("X", {}, 100)       # önbellekten: yeniden kaydedilmez
        self.assertEqual(list(gate.features), [("X", 100)])
        row = gate.features[("X", 100)]
        self.assertEqual((row["phase"], row["side"], row["alignment"], row["confidence"]), ("TRAIN", "SHORT", 85.0, 88))
        self.assertTrue(row["flags"]["extension"])

    def test_report_states_evidence_and_overlap_and_never_a_winner(self):
        text = study.render_report({
            "anchors": {"count_parity": "BAŞARILI", "prescreen_parity": "BAŞARILI", "notes": []},
            "evidence": {"EXHAUST_80": {"CUR_80": {"karar": "KANITLANMADI", "nedenler": ["TRAIN: fark +0.010 R"]}}},
            "overlap": {"trap_kapisi": study.trap_gate_pairs(), "donemler": {"TRAIN": {
                "signal_duzeyi": {"uygun_short": 4, "mevcut_filtrenin_reddettigi": 3,
                                  "reddedilenlerde_herhangi_isaret": {"N": 3, "bilinmeyen": 0, "isaretli": 1, "oran": 0.33},
                                  "reddedilmeyenlerde_herhangi_isaret": {"N": 1, "bilinmeyen": 0, "isaretli": 0, "oran": 0.0},
                                  "reddedilenlerde_isaret_bazinda": {"extension": {"isaretli": 1}},
                                  "ortalama_kirilim_kalitesi": {"reddedilen": 60.0, "reddedilmeyen": None},
                                  "ortalama_guven": {"reddedilen": 85.0, "reddedilmeyen": 82.0}},
                "islem_duzeyi": {"SHORT|alignment>=80|isaret_var": {"N": 1, "mean_net_r": 0.5, "usdt_pf": None}},
                "baglanan_islem": 1, "baglanamayan_islem": 0}}},
            "phases": {}, "limitations": ["x"]})
        for expected in ("KANITLANMADI", "ayırt edilemiyor", "Çakışma analizi", "geçer: near_support + rsi_low",
                         "1 bağlandı, 0 bağlanamadı", "kazanan"):
            self.assertIn(expected, text)
        self.assertNotIn("KANITLANDI", text.replace("KANITLANMADI", ""))

    def test_cli_writes_only_to_a_validated_new_folder(self):
        source = (ROOT / "short_filter_v2_compare_cli.py").read_text(encoding="utf-8")
        for fragment in ('re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}"', "DATA_HOME / args.output_name",
                         "RESULTS_ALREADY_EXIST", "exist_ok=False"):
            self.assertIn(fragment, source)
        self.assertNotIn("OUTPUT.mkdir", source)


if __name__ == "__main__":
    unittest.main()

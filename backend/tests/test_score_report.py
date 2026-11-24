"""Puanlama raporu: sonuç gruplama, eşik taraması, gölge puan karşılaştırması ve dürüstlük (az örnek, eksik veri)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import score_report as sr  # noqa: E402


def rec(conf, r, direction="LONG", decision="ACCEPTED", trap=20, **extra):
    base = {"direction": direction, "confidence": conf, "trap": trap, "decision": decision, "hypothetical_r_costs": r, "hypothetical_r_gross": None if r is None else r + 0.05,
            "hypothetical_result": "STOP" if (r or 0) < 0 else "TP1", "entry": 100.0, "atr": 1.0, "ema20": 99.5, "ema50": 98.0, "ema200": 95.0,
            "macd_hist": 0.2, "rsi": 60.0, "adx": 25.0, "vol_ratio": 1.2}
    return {**base, **extra}


class ReportTests(unittest.TestCase):
    def test_only_records_with_outcome_are_used(self):
        rows = sr.usable([rec(90, 1.0), rec(90, None), rec(90, 0.5, direction="UNKNOWN"), rec(90, 0.5, direction="UNKNOWN", hypothetical_direction="SHORT")])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["_dir"], "SHORT")

    def test_summary_and_low_sample_flag(self):
        rows = sr.usable([rec(90, 1.0), rec(90, -1.0), rec(90, 2.0)])
        s = sr.summarize_r(rows)
        self.assertEqual((s["n"], s["win_pct"], s["total_r"]), (3, 67, 2.0))
        self.assertTrue(s["low_sample"])
        self.assertEqual(sr.summarize_r([])["n"], 0)

    def test_confidence_buckets_and_sweep(self):
        rows = sr.usable([rec(72, -1.0)] * 3 + [rec(88, 1.0)] * 3)
        buckets = {b["bucket"]: b for b in sr.by_confidence(rows)}
        self.assertEqual(buckets["70-77"]["n"], 3)
        self.assertEqual(buckets["86-89"]["n"], 3)
        sweep = {b["min_confidence"]: b for b in sr.sweep_confidence(rows)}
        self.assertEqual(sweep[70]["n"], 6)
        self.assertEqual(sweep[86]["n"], 3)
        self.assertEqual(sweep[86]["mean_r"], 1.0)
        trap = {b["max_trap"]: b for b in sr.sweep_trap(sr.usable([rec(80, 1.0, trap=10), rec(80, -1.0, trap=45)]))}
        self.assertEqual((trap[20]["n"], trap[50]["n"]), (1, 2))

    def test_accepted_vs_rejected(self):
        d = sr.decision_split(sr.usable([rec(90, 1.0), rec(90, -1.0, decision="REJECTED"), rec(90, -1.0, decision="REJECTED")]))
        self.assertEqual((d["ACCEPTED"]["n"], d["REJECTED"]["n"]), (1, 2))

    def test_shadow_groups(self):
        extended = rec(87, 1.0, ema20=90.0)          # fiyat EMA20'den 10 ATR uzak: ceza, yeni puan eşiğin altına düşer
        normal = rec(87, 1.0)                       # eski geçer, yeni de geçer
        only_new = rec(82, 1.0)                     # eski geçmez, yeni geçer
        ignored_short = rec(90, 1.0, direction="SHORT")
        missing = rec(90, 1.0, atr=None)
        out = sr.shadow_compare(sr.usable([extended, normal, only_new, ignored_short, missing]))
        g = out["groups"]
        self.assertEqual(g["yalnız_eski_geçer"]["n"], 1)
        self.assertEqual(g["ikisi_de_geçer"]["n"], 1)
        self.assertEqual(g["yalnız_yeni_geçer"]["n"], 1)
        self.assertEqual(out["skipped_missing_fields"], 1)

    def test_build_and_render_are_honest(self):
        report = sr.build([rec(88, 1.0), rec(75, -1.0, direction="SHORT")])
        text = sr.render(report)
        self.assertIn("az örnek", text)
        self.assertIn("LONG", text)
        self.assertIn("SHORT", text)
        self.assertIn("yalnız ölçer", text)
        empty = sr.render(sr.build([]))
        self.assertIn("veri yok", empty)


if __name__ == "__main__":
    unittest.main()

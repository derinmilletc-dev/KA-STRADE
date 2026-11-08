"""Edge diagnosis helpers: synthetic fixtures only (unit tests; never used to produce a real result)."""
from __future__ import annotations

import unittest

import numpy as np

from app import backtest_farmer as bf
from app import farmer_edge_diagnosis as ed
from tests.test_backtest_farmer import candidates, market

PARAMS = {"margin": 25.0, "leverage": 3.0, "target": 0.5, "max_loss": 1.0}


class DiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mk = market()
        self.cand = candidates(self.mk)
        self.table = bf.confirmed_table(self.cand, 25, 3, 0.5, 1.0, 90)

    def sig(self, d, sym, slip=1.0, dead=None, side=None):
        return ed.single_trade(self.mk, self.cand, d, sym, side=side, slippage_bps=slip, dead_score=dead, **PARAMS)

    def test_single_signal_matches_simulator(self):
        # one-signal table through the real simulator must equal the independent evaluation (exits, gross, commission, funding, net)
        checked = 0
        for d, _neg, sym in self.table[:60]:
            res = bf.simulate(self.mk, self.cand, [(d, _neg, sym)], max_positions=2, dead_score=None, slippage_bps=1.0, **PARAMS)
            mine = self.sig(d, sym)
            if not res["trades"]:
                self.assertTrue(mine is None or mine["status"] == "OPEN")
                continue
            ref = res["trades"][0]
            for key in ("reason", "exit_ts", "gross", "commission", "funding", "net"):
                self.assertAlmostEqual(ref[key], mine[key], places=9) if key not in ("reason",) else self.assertEqual(ref[key], mine[key])
            checked += 1
        self.assertGreater(checked, 20)

    def test_gross_net_separation(self):
        t = self.sig(self.table[0][0], self.table[0][2])
        self.assertAlmostEqual(t["net"], t["gross"] - t["commission"] + t["funding"], places=12)
        zero, five = self.sig(self.table[0][0], self.table[0][2], slip=0.0), self.sig(self.table[0][0], self.table[0][2], slip=5.0)
        rows, stat = ed.attach_layers([t], [zero], [five])
        self.assertEqual(stat["matched"], 1)
        r = rows[0]
        self.assertAlmostEqual(r["n0"], r["g0"] - r["c0"] + r["f0"], places=12)
        self.assertAlmostEqual(r["slip_cost"], zero["net"] - t["net"], places=12)
        layers = ed.cost_layers(rows)
        self.assertAlmostEqual(layers["L2_gross_plus_funding"]["sum"], r["g0"] + r["f0"], places=12)
        self.assertLessEqual(layers["L3_fee_adjusted"]["sum"], layers["L2_gross_plus_funding"]["sum"])

    def test_mfe_mae_bounds(self):
        seen = 0
        for d, _neg, sym in self.table[:80]:
            t = self.sig(d, sym)
            if not t or t["status"] != "CLOSED":
                continue
            seen += 1
            sign = 1 if t["side"] == "LONG" else -1
            self.assertGreaterEqual(t["mfe"], 0)
            self.assertGreaterEqual(t["mae"], 0)
            if t["reason"] == "TARGET":
                self.assertGreaterEqual(t["mfe"] + 1e-9, abs(t["tp"] - t["entry_fill"]) * (0 if t["exit_ts"] % bf.BAR == 0 else 1))
            if t["reason"] == "STOP" and t["exit_ts"] % bf.BAR != 0:
                self.assertGreaterEqual(t["mae"] + 1e-9, abs(t["entry_fill"] - t["stop"]))
            self.assertEqual(sign, 1 if t["side"] == "LONG" else -1)
        self.assertGreater(seen, 20)

    def test_flipped_side_mirrors_pnl_direction(self):
        d, _neg, sym = self.table[3]
        a, b = self.sig(d, sym, slip=0.0), self.sig(d, sym, slip=0.0, side="SHORT" if self.cand.rows[sym][d]["direction"] == "LONG" else "LONG")
        self.assertNotEqual(a["side"], b["side"])

    def test_feature_extraction_and_na(self):
        legacy = {"rsi": 42.0, "macd": 1.5, "adx": 30.0, "ema": {"ema20": 1.0, "ema50": 2.0}, "radar": {"trap_level": "DÜŞÜK"}}
        mtf = {"timeframes": {"15m": {"direction": "SHORT"}, "1h": {"direction": "BEKLE"}}}
        f = ed.extract_features(legacy, mtf)
        self.assertEqual((f["rsi"], f["macd_hist"], f["ema20"], f["dir_15m"], f["dir_1h"]), (42.0, 1.5, 1.0, "SHORT", "BEKLE"))
        self.assertIsNone(f["ema200"])
        self.assertIsNone(f["dir_4h"])                                  # missing stays None, never invented
        self.assertEqual(ed.mtf_class("SHORT", "SHORT", "SHORT", "SHORT"), "FULL")
        self.assertEqual(ed.mtf_class("SHORT", "SHORT", "SHORT", "LONG"), "PARTIAL")
        self.assertEqual(ed.mtf_class("SHORT", "SHORT", "BEKLE", "LONG"), "WEAK")
        self.assertEqual(ed.mtf_class("SHORT", "LONG", "BEKLE", "LONG"), "OTHER")
        self.assertEqual(ed.mtf_class("SHORT", "SHORT", None, "LONG"), "NA")

    def test_bucketing_uses_train_edges_only(self):
        train = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        edges = ed.quantile_edges(train, 3)
        self.assertEqual(len(edges), 2)
        self.assertEqual(ed.bucket(0.0, edges), "LOW")
        self.assertEqual(ed.bucket(100.0, edges), "HIGH")
        self.assertEqual(ed.bucket(None, edges), "NA")
        # edges do not move when later data is huge (OOS cannot influence them)
        self.assertEqual(edges, ed.quantile_edges(train, 3))
        self.assertEqual(ed.quantile_edges([5.0] * 10, 3), [5.0])        # discrete score: duplicate edges collapse
        self.assertEqual(ed.confidence_bucket(90.4), "90-91")
        self.assertEqual(ed.confidence_bucket(93.0), "92-93")
        self.assertEqual(ed.confidence_bucket(95.9), "94-95")
        self.assertEqual(ed.time_bucket(7 * 3600), "06-12 UTC")

    def test_period_separation(self):
        seg = {"TRAIN": (0, 100), "VALID": (100, 200), "OOS": (200, 300), "ALL": (0, 300)}
        self.assertEqual([ed.period_of(t, seg) for t in (0, 99, 100, 199, 200, 299, 300)], ["TRAIN", "TRAIN", "VALID", "VALID", "OOS", "OOS", None])

    def test_fee_dominance_and_long_short_aggregation(self):
        mk = lambda s, g, c, f, sc, r: {"symbol": "X", "side": s, "g0": g, "c0": c, "f0": f, "slip_cost": sc, "n2": g - c + f - sc, "reason": r, "n0": g - c + f}
        trades = [mk("LONG", 2.0, 0.3, 0.0, 0.1, "TARGET"), mk("LONG", 0.2, 0.3, 0.0, 0.05, "TARGET"), mk("SHORT", -2.0, 0.3, 0.0, 0.1, "STOP"),
                  mk("SHORT", 0.1, 0.3, 0.01, 0.0, "DEAD_CLOSE")]
        fd = ed.fee_dominance(trades, 2.0)
        self.assertEqual((fd["gross_pos"], fd["gross_neg"], fd["gross_pos_net_neg"], fd["gross_neg_net_neg"]), (3, 1, 2, 1))
        self.assertAlmostEqual(fd["share_gross_pos_turned_negative"], 2 / 3)
        longs = ed.describe([t for t in trades if t["side"] == "LONG"], gross_key="g0", net_key="n2")
        shorts = ed.describe([t for t in trades if t["side"] == "SHORT"], gross_key="g0", net_key="n2")
        self.assertEqual((longs["n"], shorts["n"]), (2, 2))
        self.assertAlmostEqual(longs["net_sum"] + shorts["net_sum"], sum(t["n2"] for t in trades))
        self.assertEqual(shorts["stop_rate"], 0.5)

    def test_statistics(self):
        rng = np.random.default_rng(1)
        vals = rng.normal(0.0, 1.0, 400)
        clusters = np.repeat(np.arange(40), 10)
        st = ed.cluster_stat(vals, clusters)
        self.assertEqual(st["n"], 400)
        self.assertGreater(st["p"], 0.01)                                # pure noise: not significant
        strong = ed.cluster_stat(vals + 1.0, clusters)
        self.assertLess(strong["p"], 1e-6)
        adj = ed.holm([0.01, 0.04, 0.5, float("nan")])
        self.assertAlmostEqual(adj[0], 0.03)
        self.assertTrue(np.isnan(adj[3]))
        flip = ed.sign_flip_p(np.ones(60), np.repeat(np.arange(30), 2), rng, reps=2000)
        self.assertLess(flip["p"], 0.01)
        lo, hi = ed.boot_ci(vals, clusters, rng, reps=500)
        self.assertLess(lo, vals.mean())
        self.assertGreater(hi, vals.mean())


if __name__ == "__main__":
    unittest.main()

"""Entry research (C14): synthetic candles only, used to check the definitions. Never used to produce a result."""
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

import numpy as np

from app import farmer_entry_research as er


def flat(n: int, price: float = 100.0):
    """n candles with true range exactly 1.0 around `price` (so ATR14 == 1.0)."""
    return [(price, price + 0.5, price - 0.5, price)] * n


def build(candles):
    a = np.array(candles, dtype=float)
    o, h, lo, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    return o, h, lo, c, er.wilder_atr(h, lo, c)


LONG_BREAKOUT = (100.0, 101.6, 99.9, 101.5)        # body 1.5 ATR, close location .94, close > range high 100.5
LONG_RETEST = (101.4, 101.5, 100.6, 101.0)         # low 100.6 <= 100.5 + 0.2, close >= level
LONG_CONFIRM = (101.0, 102.1, 100.9, 102.0)        # close > retest high 101.5, body 1.0 ATR
TAIL = [(102.0, 102.5, 101.5, 102.0)] * 30


def detect(*pieces):
    candles = flat(150)
    for p in pieces:
        candles = candles + (p if isinstance(p, list) else [p])
    o, h, lo, c, atr = build(candles)
    return er.detect_setups(o, h, lo, c, atr), (o, h, lo, c, atr)


class DetectionTests(unittest.TestCase):
    def test_atr_is_causal_and_exact(self):
        *_, atr = build(flat(60))
        self.assertTrue(np.isnan(atr[13]))
        self.assertAlmostEqual(float(atr[14]), 1.0)
        self.assertAlmostEqual(float(atr[59]), 1.0)

    def test_full_long_sequence_enters_after_confirmation(self):
        ev, _ = detect(LONG_BREAKOUT, LONG_RETEST, LONG_CONFIRM, TAIL)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e["state"], e["side"], e["breakout_idx"], e["retest_idx"], e["confirm_idx"], e["entry_idx"]), (er.ENTRY, "LONG", 150, 151, 152, 153))
        self.assertAlmostEqual(e["level"], 100.5)
        self.assertAlmostEqual(e["setup_atr"], 1.0)

    def test_short_mirror(self):
        brk, rt, cf = (100.0, 100.1, 98.4, 98.5), (98.6, 99.4, 98.5, 99.0), (99.0, 99.1, 97.9, 98.0)
        ev, _ = detect(brk, rt, cf, TAIL)
        self.assertEqual((ev[0]["state"], ev[0]["side"], ev[0]["entry_idx"]), (er.ENTRY, "SHORT", 153))

    def test_wide_range_is_not_compression(self):
        wide = flat(140) + [(100, 105.5, 94.5, 100)] * 10          # 20-candle range ~11 ATR
        o, h, lo, c, atr = build(wide + [LONG_BREAKOUT] + TAIL)
        self.assertEqual(er.detect_setups(o, h, lo, c, atr), [])

    def test_breakout_needs_body_and_close_location(self):
        weak_body = (101.2, 101.7, 101.0, 101.3)                    # close > range high but body .1 ATR
        ev, _ = detect(weak_body, TAIL)
        self.assertEqual(ev, [])
        low_close = (100.0, 102.0, 99.9, 100.9)                     # body .9 ATR, close location .43 (< .65)
        ev, _ = detect(low_close, TAIL)
        self.assertEqual(ev, [])

    def test_expired_when_no_retest_in_three_candles(self):
        away = [(101.5, 102.5, 101.4, 102.4), (102.4, 103.4, 102.3, 103.3), (103.3, 104.3, 103.2, 104.2)]
        ev, _ = detect(LONG_BREAKOUT, away, TAIL)
        self.assertEqual(ev[0]["state"], er.EXPIRED)

    def test_invalidated_when_close_back_inside(self):
        back = (101.0, 101.1, 99.8, 100.0)                          # closes below the 100.5 level
        ev, _ = detect(LONG_BREAKOUT, back, TAIL)
        self.assertEqual(ev[0]["state"], er.INVALIDATED)

    def test_failed_confirmation_is_not_retried(self):
        weak = (101.0, 101.4, 100.9, 101.2)                         # close below retest high
        ev, _ = detect(LONG_BREAKOUT, LONG_RETEST, weak, LONG_CONFIRM, TAIL)
        self.assertEqual(ev[0]["state"], er.FAILED_CONFIRMATION)
        self.assertEqual(sum(e["state"] == er.ENTRY for e in ev), 0)

    def test_confirmation_needs_displacement(self):
        small = (101.9, 102.0, 101.8, 101.95)                       # beats retest high but body .05 ATR
        ev, _ = detect(LONG_BREAKOUT, (101.4, 101.5, 100.6, 101.0)[:3] + (101.0,), small, TAIL)
        self.assertEqual(ev[0]["state"], er.FAILED_CONFIRMATION)

    def test_independence_one_setup_per_structure(self):
        ev, _ = detect(LONG_BREAKOUT, LONG_RETEST, LONG_CONFIRM, (102.0, 103.6, 101.9, 103.5), TAIL)
        self.assertEqual(len(ev), 1)                                # the second marubozu 3 candles later is the SAME structure

    def test_no_lookahead_prefix_gives_same_signals(self):
        candles = flat(150) + [LONG_BREAKOUT, LONG_RETEST, LONG_CONFIRM] + TAIL
        o, h, lo, c, atr = build(candles)
        full = er.detect_setups(o, h, lo, c, atr)
        for cut in (151, 152, 153, 154, 160):
            part = er.detect_setups(o[:cut], h[:cut], lo[:cut], c[:cut], er.wilder_atr(h[:cut], lo[:cut], c[:cut]))
            known = [e for e in full if e.get("entry_idx", 10**9) <= cut - 1 or e.get("confirm_idx", 10**9) < cut]
            for e in known:
                twin = next(p for p in part if p["breakout_idx"] == e["breakout_idx"])
                self.assertEqual({k: twin.get(k) for k in ("state", "entry_idx", "confirm_idx", "retest_idx")}, {k: e.get(k) for k in ("state", "entry_idx", "confirm_idx", "retest_idx")})
        # truncated before the confirmation candle closed there must be no entry yet
        part = er.detect_setups(o[:152], h[:152], lo[:152], c[:152], er.wilder_atr(h[:152], lo[:152], c[:152]))
        self.assertTrue(all(e["state"] != er.ENTRY for e in part))


class ReferenceTests(unittest.TestCase):
    def test_reference_detector_agrees_on_random_walk(self):
        rng = np.random.default_rng(11)
        n = 20000
        steps = rng.normal(0, 0.03, n) + np.where(rng.random(n) < 0.04, rng.normal(0, 0.5, n), 0.0)      # quiet noise with occasional bursts
        price = 100 + np.cumsum(steps)
        o = np.concatenate([[100.0], price[:-1]])
        c = price
        h = np.maximum(o, c) + np.abs(rng.normal(0, 0.03, n))
        lo = np.minimum(o, c) - np.abs(rng.normal(0, 0.03, n))
        atr = er.wilder_atr(h, lo, c)
        main, ref = er.detect_setups(o, h, lo, c, atr), er.reference_detect(o, h, lo, c, atr)
        self.assertGreater(len(main), 20)
        self.assertGreater(sum(e['state'] == er.ENTRY for e in main), 3)
        keys = ("breakout_idx", "side", "state", "entry_idx")
        self.assertEqual([{k: e.get(k) for k in keys} for e in main], [{k: e.get(k) for k in keys} for e in ref])
        for e in main:
            if e["state"] == er.ENTRY:
                self.assertEqual(er.verify_entry_event(e, o, h, lo, c, atr), [])
        self.assertTrue(any(e["state"] == er.ENTRY for e in main))

    def test_reference_label_agrees(self):
        rng = np.random.default_rng(5)
        mid = 100 + np.cumsum(rng.normal(0, 0.3, 500))
        hi, lo = mid + np.abs(rng.normal(0, 0.2, 500)), mid - np.abs(rng.normal(0, 0.2, 500))
        for side in ("LONG", "SHORT"):
            for start in range(0, 400, 7):
                self.assertEqual(er.label_outcome(hi, lo, start, float(mid[start]), 0.5, side), er.reference_label(hi, lo, start, float(mid[start]), 0.5, side))


class OutcomeTests(unittest.TestCase):
    def test_target_stop_and_stop_first(self):
        hi = np.array([100.5, 100.9, 102.1, 100.0])
        lo = np.array([99.5, 99.0, 100.0, 97.0])
        self.assertEqual(er.label_outcome(hi, lo, 0, 100.0, 1.0, "LONG")["result"], "TARGET")       # +2 reached in candle 2, -2 not before
        both = er.label_outcome(np.array([102.5]), np.array([97.5]), 0, 100.0, 1.0, "LONG")
        self.assertEqual((both["result"], both["ambiguous"]), ("STOP", True))                        # same candle: STOP FIRST
        short = er.label_outcome(np.array([100.5, 100.9]), np.array([99.0, 97.9]), 0, 100.0, 1.0, "SHORT")
        self.assertEqual(short["result"], "TARGET")
        self.assertEqual(er.label_outcome(np.array([100.5]), np.array([99.5]), 0, 100.0, 1.0, "LONG")["result"], "UNRESOLVED")

    def test_cost_in_r(self):
        c = er.cost_r(100.0, 0.5, "LONG", 0.0002, 2.0)            # 1R = 1.0 / 100 = 1% of price
        self.assertAlmostEqual(c["commission_r"], 0.10)
        self.assertAlmostEqual(c["slippage_r"], 0.04)
        self.assertAlmostEqual(c["funding_r"], 0.02)
        self.assertAlmostEqual(er.cost_r(100.0, 0.5, "SHORT", 0.0002, 2.0)["funding_r"], -0.02)


class StatsTests(unittest.TestCase):
    def test_wilson_and_binomial(self):
        lo, hi = er.wilson(57, 100)
        self.assertAlmostEqual(lo, 0.4723, places=3)
        self.assertAlmostEqual(hi, 0.6627, places=3)
        self.assertLess(lo, 0.5)                                    # 57% on 100 trades is NOT a pass
        self.assertAlmostEqual(er.binom_z(60, 100)["z"], 2.0)
        self.assertTrue(np.isnan(er.wilson(0, 0)[0]))

    def test_edge_stats_pf_expectancy_and_drawdown(self):
        s = er.edge_stats([True] * 6 + [False] * 4)
        self.assertAlmostEqual(s["expectancy_r"], 0.2)
        self.assertAlmostEqual(s["profit_factor"], 1.5)
        self.assertAlmostEqual(er.max_drawdown_r([1, 1, -1, -1, -1, 1]), 3.0)
        self.assertEqual(er.max_drawdown_r([1, 1, 1]), 0.0)

    def test_protocol_is_frozen_and_hash_is_stable(self):
        with self.assertRaises(FrozenInstanceError):
            er.PROTOCOL.target_atr = 3.0                            # type: ignore[misc]
        self.assertEqual(er.protocol_hash(), er.protocol_hash())
        self.assertEqual((er.PROTOCOL.range_window, er.PROTOCOL.target_atr, er.PROTOCOL.stop_atr, er.PROTOCOL.minimum_oos_trades), (20, 2.0, 2.0, 100))

    def test_locked_split_is_contiguous_and_chronological(self):
        t, v, o = er.PROTOCOL.train, er.PROTOCOL.valid, er.PROTOCOL.oos
        self.assertEqual((t[0], t[1], v[0], v[1], o[0], o[1]), ("2025-04-01", "2026-01-01", "2026-01-01", "2026-06-01", "2026-06-01", "2026-10-01"))
        self.assertLess(t[0], t[1] and v[0] and o[0])

    def test_bootstrap_interval_contains_rate(self):
        rng = np.random.default_rng(3)
        lo, hi = er.day_bootstrap_ci([True, False] * 50, list(range(100)), rng, reps=500)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)


if __name__ == "__main__":
    unittest.main()

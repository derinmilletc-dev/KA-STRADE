"""Offline Farmer backtest simulator: synthetic market only (no data files, no network)."""
from __future__ import annotations

import itertools
import unittest

import numpy as np
from app import backtest_farmer as bf

T0, N = 900 * 4_000_000, 400
RULES = {"tickSize": 0.01, "stepSize": 0.001, "minQty": 0.001, "minNotional": 5.0}


def market(seed: int = 7, drift: float = 0.0) -> bf.Market:
    rng = np.random.default_rng(seed)
    price = 100 * np.exp(np.cumsum(rng.normal(drift, 0.002, N + 1)))
    opens, closes = price[:-1], price[1:]
    spread = np.abs(rng.normal(0, 0.0015, N)) * opens
    mk = bf.Market(T0, N, ["AAAUSDT", "BBBUSDT"])
    for i, sym in enumerate(mk.symbols):
        o = opens * (1 + 0.01 * i)
        mk.copen[sym], mk.mopen[sym] = o, o.copy()
        mk.mhigh[sym], mk.mlow[sym] = o + spread, o - spread
        mk.mclose[sym] = closes * (1 + 0.01 * i)
        mk.fund_t[sym] = np.array([T0 + 8 * 3600 * k + 0.005 for k in range(1, N * 900 // (8 * 3600))])
        mk.fund_r[sym] = np.full(len(mk.fund_t[sym]), 0.0001)
        mk.rules[sym] = {**RULES, "symbol": sym}
    return mk


def candidates(mk: bf.Market, every: int = 5) -> bf.Candidates:
    rows = {}
    for i, sym in enumerate(mk.symbols):
        table = {}
        for d in range(1, N):
            long = (d + i) % every == 0
            table[d] = {"at": T0 + d * 900, "direction": "LONG" if long else ("SHORT" if (d + i) % every == 1 else "BEKLE"),
                        "confidence": 95.0 if long else 91.0, "momentum": "Pozitif" if long else "Negatif", "atr": float(mk.copen[sym][d] * 0.004),
                        "entry": float(mk.mclose[sym][d - 1]), "trap": 10.0, "breakout": 80.0, "mtf_permission": True,
                        "mtf_direction": "LONG" if long else "SHORT", "mtf_alignment": 85, "sf_accepted": True, "sf_present": True}
        rows[sym] = {"rows": list(table.values())}
    return bf.build_candidates(rows, T0, N, (70, 75, 80, None))


def brute(mk, p, slip):
    sym, sg = p["symbol"], 1 if p["side"] == "LONG" else -1
    for k in range(p["d"], mk.n):
        t = T0 + k * 900
        if sg * (mk.mopen[sym][k] - p["stop"]) <= 0:
            return "STOP", t, mk.copen[sym][k] * (1 - sg * slip)
        if sg * (mk.mopen[sym][k] - p["tp"]) >= 0:
            return "TARGET", t, mk.copen[sym][k] * (1 - sg * slip)
        adverse = mk.mlow[sym][k] if sg == 1 else mk.mhigh[sym][k]
        favorable = mk.mhigh[sym][k] if sg == 1 else mk.mlow[sym][k]
        if sg * (adverse - p["stop"]) <= 0:
            return "STOP", t + 899, p["stop"] * (1 - sg * slip)
        if sg * (favorable - p["tp"]) >= 0:
            return "TARGET", t + 899, p["tp"] * (1 - sg * slip)
    return None


class SimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mk, self.slip = market(), 1.0
        self.cand = candidates(self.mk)
        self.table = bf.confirmed_table(self.cand, 25, 3, 0.5, 1.0, 90)

    def run_sim(self, **kw):
        base = {"margin": 25.0, "leverage": 3.0, "target": 0.5, "max_loss": 1.0, "max_positions": 2, "dead_score": None, "slippage_bps": self.slip}
        base.update(kw)
        return bf.simulate(self.mk, self.cand, self.table, **base)

    def test_exits_match_independent_replay_and_pnl_formula(self):
        res = self.run_sim()
        self.assertGreater(len(res["trades"]), 20)
        for p in res["trades"]:
            kind, ts, px = brute(self.mk, p, self.slip / 10_000)
            self.assertEqual((kind, ts), (p["reason"], p["exit_ts"]))
            self.assertAlmostEqual(px, p["exit_fill"], places=9)
            sg = 1 if p["side"] == "LONG" else -1
            gross = sg * (p["exit_fill"] - p["entry_fill"]) * p["qty"]
            fees = (p["entry_fill"] + p["exit_fill"]) * p["qty"] * 0.0005
            self.assertAlmostEqual(p["net"], gross - fees + p["funding"], places=9)

    def test_capacity_symbol_occupancy_and_cooldown(self):
        res = self.run_sim(max_positions=1)
        events = sorted([(p["entry_ts"], 1) for p in res["trades"]] + [(p["exit_ts"], -1) for p in res["trades"]], key=lambda e: (e[0], e[1]))
        live = peak = 0
        for _t, delta in events:
            live += delta
            peak = max(peak, live)
        self.assertLessEqual(peak, 1)
        ordered = sorted(res["trades"], key=lambda p: p["entry_ts"])
        for left, right in itertools.pairwise(ordered):
            self.assertGreater(right["entry_ts"] - left["exit_ts"], 0)
        self.assertEqual(self.run_sim(margin=150.0)["capacity"], 0)              # 150 > 100 USDT hard cap: Farmer never opens
        self.assertEqual(self.run_sim(margin=150.0)["trades"], [])
        self.assertEqual(self.run_sim(margin=150.0, total_cap=150.0)["capacity"], 1)   # research-only lifted cap

    def test_zero_cooldown_never_reduces_trades(self):
        self.assertGreaterEqual(len(self.run_sim(cooldown_s=0)["trades"]), len(self.run_sim(cooldown_s=900)["trades"]))

    def test_dead_close_never_realises_a_loss_and_costs_are_applied(self):
        for dead in (70, 75, 80):
            for p in self.run_sim(dead_score=dead)["trades"]:
                if p["reason"] == "DEAD_CLOSE":
                    sg = 1 if p["side"] == "LONG" else -1
                    self.assertGreater(sg * (p["exit_fill"] - p["entry_fill"]) * p["qty"], 0)
        zero, five = self.run_sim(slippage_bps=0.0), self.run_sim(slippage_bps=5.0)
        self.assertGreater(sum(p["net"] for p in zero["trades"]), sum(p["net"] for p in five["trades"]))

    def test_funding_sign_follows_position_side(self):
        res = self.run_sim()
        for p in res["trades"]:
            if p["funding"] and p["side"] == "LONG":
                self.assertLessEqual(p["funding"], 0)       # positive rate: longs pay
            if p["funding"] and p["side"] == "SHORT":
                self.assertGreaterEqual(p["funding"], 0)

    def test_entry_fills_at_next_open_after_closed_candle_decision(self):
        row = self.cand.rows["AAAUSDT"][10]
        self.assertEqual(row["at"], T0 + 10 * 900)            # decided at the CLOSE of bar 9; entry fills at the open of bar 10
        res = self.run_sim()
        for p in res["trades"]:
            self.assertEqual(p["entry_ts"], T0 + p["d"] * 900)
            self.assertEqual(p["entry_fill"], self.mk.copen[p["symbol"]][p["d"]] * (1 + (1 if p["side"] == "LONG" else -1) * self.slip / 10_000))

    def test_metrics_split_by_exit_time(self):
        res = self.run_sim()
        mid = T0 + (N // 2) * 900
        segs = {"A": (T0, mid), "B": (mid, T0 + N * 900 + 10**6), "ALL": (T0, T0 + N * 900 + 10**6)}
        m = bf.trade_metrics(res["trades"], segs, [("x", T0, T0 + N * 900 + 10**6)], 1.0)
        self.assertEqual(m["A_n"] + m["B_n"], m["ALL_n"])
        self.assertAlmostEqual(m["A_net"] + m["B_net"], m["ALL_net"], places=9)
        self.assertAlmostEqual(m["M_x"], m["ALL_net"], places=9)


if __name__ == "__main__":
    unittest.main()

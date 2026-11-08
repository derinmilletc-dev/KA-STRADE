"""KAIS FARMER edge diagnosis (C13): research only. Reuses the C12 data, candidate cache, simulator and TRAIN/VALIDATION/OOS split.

  python farmer_edge_diagnosis_cli.py --data D --cache C --output O

One FIXED configuration (the C12 'Best' setting, chosen before this diagnosis) is analysed; no parameter is searched, nothing is selected on OOS.
Populations:  A = the real Farmer flow (2 slots)   B = same entries/exits but no slot limit (more samples)   C = every confirmed signal on its own
(direction-information test).  All segment edges are fixed on TRAIN trades only and applied unchanged to VALIDATION and OOS.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import backtest_farmer_cli as cli
from app import backtest_farmer as bf
from app import farmer_edge_diagnosis as ed

CFG = {"margin": 25.0, "leverage": 3.0, "target": 2.0, "max_loss": 2.0, "min_score": 90, "dead": 70}
PERIODS = ("TRAIN", "VALID", "OOS", "ALL")
SEED = 20260713


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--start", default="2026-03-01")
    p.add_argument("--end", default="2026-10-01")
    p.add_argument("--first", default="2026-04-13T04:00")
    p.add_argument("--valid-start", default="2026-07-13")
    p.add_argument("--oos-start", default="2026-08-22")
    p.add_argument("--no-features", action="store_true", help="skip the (slow) evaluator re-run for entry-time features")
    return p.parse_args()


def trade_kw(slip: float, dead) -> dict:
    return {"margin": CFG["margin"], "leverage": CFG["leverage"], "target": CFG["target"], "max_loss": CFG["max_loss"],
            "slippage_bps": slip, "dead_score": dead}


def build_population(mk, cand, table, max_pos: int, research_cap: bool) -> tuple[list[dict], dict]:
    """Entries come from the real simulator at 2 bps; every layer (0 / 2 / 5 bps) re-evaluates the SAME signals."""
    kw = {"margin": CFG["margin"], "leverage": CFG["leverage"], "target": CFG["target"], "max_loss": CFG["max_loss"], "max_positions": max_pos,
          "dead_score": CFG["dead"], "slippage_bps": 2.0}
    if research_cap:
        kw["total_cap"] = 10_000.0
    res = bf.simulate(mk, cand, table, **kw)
    entries = [(t["d"], t["symbol"]) for t in res["trades"]]
    sims = {(t["d"], t["symbol"]): t for t in res["trades"]}
    runs = {s: [ed.single_trade(mk, cand, d, sym, **trade_kw(s, CFG["dead"])) for d, sym in entries] for s in (0.0, 2.0, 5.0)}
    base = [t for t in runs[2.0] if t and t["status"] == "CLOSED"]
    l0 = [t for t in runs[0.0] if t and t["status"] == "CLOSED"]
    l5 = [t for t in runs[5.0] if t and t["status"] == "CLOSED"]
    mism = max((abs(sims[(t["d"], t["symbol"])]["net"] - t["net"]) for t in base), default=0.0)
    rows, stat = ed.attach_layers(base, l0, l5)
    stat.update(simulator_trades=len(res["trades"]), open_at_end=len(res["open"]), max_abs_net_diff_vs_simulator=mism)
    return rows, stat


def add_counterfactual(mk, cand, trades: list[dict]) -> None:
    """For every trade: the 2 bps outcome had the dead-position close never existed (diagnoses what that exit does; no new rule)."""
    for t in trades:
        alt = ed.single_trade(mk, cand, t["d"], t["symbol"], **trade_kw(2.0, None))
        t["n2_no_dead"] = alt["net"] if alt and alt["status"] == "CLOSED" else None


def add_features(ds, trades: list[dict], cache: dict) -> None:
    from app.farmer_engine.feed import (
        HISTORY_CANDLES,
        default_evaluator,
    )
    universe = sorted(ds.frames)
    for t in trades:
        key = (t["symbol"], t["entry_ts"])
        if key not in cache:
            at = t["entry_ts"]
            frames = {iv: ds.frames[t["symbol"]][iv].closed(at, limit=HISTORY_CANDLES - 1) for iv in ("15m", "1h", "4h")}
            result = default_evaluator(t["symbol"], at, frames, universe)
            legacy = getattr(result, "legacy", result)
            analysis = legacy["analysis"] if isinstance(legacy, dict) and "analysis" in legacy else getattr(legacy, "analysis", {})
            mtf = legacy.get("mtf", {}) if isinstance(legacy, dict) else getattr(legacy, "mtf", {})
            cache[key] = ed.extract_features(analysis, mtf)
        t.update(cache[key])
        t["mtf_cls"] = ed.mtf_class(t["side"], t.get("dir_15m"), t.get("dir_1h"), t.get("dir_4h"))
        t["mom_cls"] = ed.momentum_class(t.get("momentum"))


def label(t: dict, plan, edges: dict) -> None:
    t["period"] = ed.period_of(t["exit_ts"], plan.segments)
    t["day"] = t["entry_ts"] // ed.DAY
    t["conf_bkt"] = ed.confidence_bucket(t["confidence"])
    t["tod"] = ed.time_bucket(t["entry_ts"])
    for name, field in (("atr_pct", "atr_pct"), ("target_atr", "target_over_atr"), ("stop_atr", "stop_over_atr"), ("trap", "trap"), ("breakout", "breakout")):
        t[f"{name}_bkt"] = ed.bucket(t.get(field), edges[name])
    t["side_mom"] = f"{t['side']}+{t.get('mom_cls', 'NA')}"
    t["tp_dist"] = abs(t["tp"] - t["entry_fill"])
    t["stop_dist"] = abs(t["entry_fill"] - t["stop"]) if t["stop"] else None


def cell(trades: list[dict], rng, ci: bool = False) -> dict:
    out = ed.describe(trades)
    if out["n"] >= 2:
        days = np.array([t["day"] for t in trades])
        net = np.array([t["n2"] for t in trades])
        gross = np.array([t["g0"] for t in trades])
        out["net_stat"], out["gross_stat"] = ed.cluster_stat(net, days), ed.cluster_stat(gross, days)
        if ci:
            out["net_ci"], out["gross_ci"] = ed.boot_ci(net, days, rng), ed.boot_ci(gross, days, rng)
    return out


def table(trades: list[dict], key: str, rng, ci: bool = False) -> dict:
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        if t["period"]:
            groups[str(t[key])][t["period"]].append(t)
            groups[str(t[key])]["ALL"].append(t)
    return {k: {p: cell(groups[k].get(p, []), rng, ci) for p in PERIODS} for k in sorted(groups)}


def fmt(c: dict, field: str, nd: int = 2) -> str:
    if c.get("n", 0) == 0:
        return "-"
    v = c.get(field)
    return "NA" if v is None else f"{v:.{nd}f}"


def md_table(title: str, tab: dict) -> str:
    lines = [f"\n#### {title}", "| segment | period | n | gross/trade | net/trade (2bps) | win% | stop% | target% | MFE/ATR | MAE/ATR |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, per in tab.items():
        for p in PERIODS:
            c = per[p]
            flag = " (n<30)" if 0 < c.get("n", 0) < ed.MIN_N else ""
            lines.append(f"| {k} | {p} | {c.get('n', 0)}{flag} | {fmt(c, 'gross_mean', 3)} | {fmt(c, 'net_mean', 3)} | "
                         f"{(c['win'] * 100 if c.get('n') else float('nan')):.0f} | {(c['stop_rate'] * 100 if c.get('n') else float('nan')):.0f} | "
                         f"{(c['target_rate'] * 100 if c.get('n') else float('nan')):.0f} | {fmt(c, 'mfe_atr')} | {fmt(c, 'mae_atr')} |")
    return "\n".join(lines)


def main() -> None:
    a = args()
    ns = cli.parser().parse_args(["report", "--data", str(a.data), "--cache", str(a.cache), "--output", str(a.output), "--start", a.start, "--end", a.end,
                                  "--first", a.first, "--valid-start", a.valid_start, "--oos-start", a.oos_start])
    plan = cli.Plan(ns)
    out_dir = cli.outside_repo(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    ds, mk = cli.load(ns, plan)
    cand = cli.load_candidates(ns, mk, plan)
    rng = np.random.default_rng(SEED)
    conf = bf.confirmed_table(cand, CFG["margin"], CFG["leverage"], CFG["target"], CFG["max_loss"], CFG["min_score"])
    report: dict = {"config": CFG, "segments": {k: list(v) for k, v in plan.segments.items()}, "confirmed_signals": len(conf)}

    pop_a, stat_a = build_population(mk, cand, conf, 2, False)
    pop_b, stat_b = build_population(mk, cand, conf, 99, True)
    report["population_a"], report["population_b"] = stat_a, stat_b
    # D = same flow, entry gates (MTF, momentum, trap, breakout, short filter) OFF, only confidence >= min_score. Diagnostic of the gates, not a strategy.
    ungated = sorted((d, -float(r["confidence"]), sym) for sym, tab in cand.rows.items() for d, r in tab.items()
                     if r["direction"] in ("LONG", "SHORT") and r["confidence"] is not None and r["confidence"] >= CFG["min_score"])
    pop_d, stat_d = build_population(mk, cand, ungated, 99, True)
    gated_keys = {(d, sym) for d, _n, sym in conf}
    for t in pop_d:
        t["gate"] = "CONFIRMED" if (t["d"], t["symbol"]) in gated_keys else "NOT_CONFIRMED"
    stat_d["ungated_signals"] = len(ungated)
    report["population_d"] = stat_d
    print("A", stat_a, "\nB", stat_b, "\nD", stat_d, file=sys.stderr)

    allt = {id(t): t for t in pop_a + pop_b + pop_d}
    feat_cache: dict = {}
    if not a.no_features:
        add_features(ds, list(allt.values()), feat_cache)
    add_counterfactual(mk, cand, list(allt.values()))

    # bucket edges from TRAIN trades of population B (by exit time): fixed before VALIDATION / OOS are looked at
    train_b = [t for t in pop_b if plan.segments["TRAIN"][0] <= t["exit_ts"] < plan.segments["TRAIN"][1]]
    edges = {"atr_pct": ed.quantile_edges([t["atr_pct"] for t in train_b]), "target_atr": ed.quantile_edges([t["target_over_atr"] for t in train_b]),
             "stop_atr": ed.quantile_edges([t["stop_over_atr"] for t in train_b]), "trap": ed.quantile_edges([t["trap"] for t in train_b]),
             "breakout": ed.quantile_edges([t["breakout"] for t in train_b])}
    report["train_edges"] = edges
    for t in pop_a + pop_b:
        label(t, plan, edges)
    train_d = [t for t in pop_d if plan.segments["TRAIN"][0] <= t["exit_ts"] < plan.segments["TRAIN"][1]]
    edges_d = {"atr_pct": ed.quantile_edges([t["atr_pct"] for t in train_d]), "target_atr": ed.quantile_edges([t["target_over_atr"] for t in train_d]),
               "stop_atr": ed.quantile_edges([t["stop_over_atr"] for t in train_d]), "trap": ed.quantile_edges([t["trap"] for t in train_d]),
               "breakout": ed.quantile_edges([t["breakout"] for t in train_d])}
    report["train_edges_D"] = edges_d
    for t in pop_d:                                                                  # D has its own TRAIN-defined edges (its trap/breakout range is not gated)
        label(t, plan, edges_d)

    families_d = {"gate": "gate", "side": "side", "confidence": "conf_bkt", "mtf": "mtf_cls", "momentum_x_side": "side_mom", "trap": "trap_bkt",
                  "breakout": "breakout_bkt", "overall": None}
    families = {"side": "side", "symbol": "symbol", "confidence": "conf_bkt", "mtf": "mtf_cls", "momentum_x_side": "side_mom", "trap": "trap_bkt",
                "breakout": "breakout_bkt", "target_atr": "target_atr_bkt", "stop_atr": "stop_atr_bkt", "atr_pct_regime": "atr_pct_bkt", "exit_reason": "reason",
                "time_of_day": "tod", "overall": None}
    md: list[str] = []
    tabs: dict = {}
    for pop_name, pop in (("A", pop_a), ("B", pop_b)):
        tabs[pop_name] = {}
        for fam, key in families.items():
            if key is None:
                for t in pop:
                    t["_all"] = "ALL TRADES"
            tabs[pop_name][fam] = table(pop, key or "_all", rng, ci=(fam == "overall"))
            if pop_name == "B" or fam in ("overall", "side"):
                md.append(md_table(f"[{pop_name}] {fam}", tabs[pop_name][fam]))
    tabs["D"] = {}
    for fam, key in families_d.items():
        if key is None:
            for t in pop_d:
                t["_all"] = "ALL TRADES"
        tabs["D"][fam] = table(pop_d, key or "_all", rng, ci=(fam == "overall"))
        md.append(md_table(f"[D, gates off] {fam}", tabs["D"][fam]))
    report["tables"] = tabs

    # cost layers (Gross -> Stress) on A and B, by period
    layers = {}
    for pop_name, pop in (("A", pop_a), ("B", pop_b), ("D", pop_d)):
        layers[pop_name] = {p: ed.cost_layers([t for t in pop if t["period"] == p or p == "ALL"]) for p in PERIODS}
        layers[pop_name]["fee_dominance"] = {p: ed.fee_dominance([t for t in pop if t["period"] == p or p == "ALL"], CFG["target"]) for p in PERIODS}
    report["layers"] = layers

    # exit-reason detail incl. dead-close counterfactual
    dead = [t for t in pop_b if t["reason"] == "DEAD_CLOSE" and t["n2_no_dead"] is not None]
    report["dead_close"] = {"n": len(dead), "net_with": float(sum(t["n2"] for t in dead)), "net_without": float(sum(t["n2_no_dead"] for t in dead)),
                            "by_period": {p: {"n": sum(t["period"] == p for t in dead), "value": float(sum(t["n2"] - t["n2_no_dead"] for t in dead if t["period"] == p))}
                                          for p in PERIODS[:3]}}

    # MFE / MAE questions
    def share(trades, pred):
        return float(np.mean([pred(t) for t in trades])) if trades else None
    losers = [t for t in pop_b if t["n2"] < 0]
    winners = [t for t in pop_b if t["n2"] > 0 and t["reason"] == "TARGET"]
    report["mfe_mae"] = {
        "losers_n": len(losers),
        "losers_mfe_ge_50pct_tp_dist": share(losers, lambda t: t["mfe"] >= 0.5 * t["tp_dist"]),
        "losers_mfe_ge_75pct_tp_dist": share(losers, lambda t: t["mfe"] >= 0.75 * t["tp_dist"]),
        "stop_losers_mfe_ge_50pct_tp_dist": share([t for t in losers if t["reason"] == "STOP"], lambda t: t["mfe"] >= 0.5 * t["tp_dist"]),
        "losers_median_mfe_atr": float(np.median([t["mfe_atr"] for t in losers if t["mfe_atr"] is not None])) if losers else None,
        "winners_n": len(winners),
        "winners_median_mae_over_stop_dist": float(np.median([t["mae"] / t["stop_dist"] for t in winners if t["stop_dist"]])) if winners else None,
        "winners_share_mae_ge_50pct_stop": share([t for t in winners if t["stop_dist"]], lambda t: t["mae"] >= 0.5 * t["stop_dist"]),
        "median_target_over_atr": float(np.median([t["target_over_atr"] for t in pop_b if t["target_over_atr"]])),
        "median_stop_over_atr": float(np.median([t["stop_over_atr"] for t in pop_b if t["stop_over_atr"]])),
        "all_median_mfe_atr": float(np.median([t["mfe_atr"] for t in pop_b if t["mfe_atr"] is not None])),
        "all_median_mae_atr": float(np.median([t["mae_atr"] for t in pop_b if t["mae_atr"] is not None])),
        "share_mfe_reached_tp_dist": share(pop_b, lambda t: t["mfe"] >= t["tp_dist"]),
        "share_mae_reached_stop_dist": share([t for t in pop_b if t["stop_dist"]], lambda t: t["mae"] >= t["stop_dist"])}

    # random-walk reference for the SAME barriers: P(target first) = stop_dist / (stop_dist + tp_dist) when price has no drift or signal
    def baseline(pop):
        sub = [t for t in pop if t["stop_dist"] and t["reason"] in ("TARGET", "STOP")]
        p = np.array([t["stop_dist"] / (t["stop_dist"] + t["tp_dist"]) for t in sub])
        obs = np.array([t["reason"] == "TARGET" for t in sub], dtype=float)
        z = float((obs.sum() - p.sum()) / np.sqrt((p * (1 - p)).sum())) if len(sub) else float("nan")
        return {"n": len(sub), "expected_target_rate": float(p.mean()), "observed_target_rate": float(obs.mean()), "z": z, "p_two_sided": ed.norm_sf2(z)}
    report["barrier_baseline"] = {"A": baseline(pop_a), "B": baseline(pop_b), "D": baseline(pop_d),
                                  **{f"B_{q}": baseline([t for t in pop_b if t["period"] == q]) for q in PERIODS[:3]}}

    # direction information test on population C (every confirmed signal, 0 bps, no dead close, actual vs opposite direction)
    sig_rows = []
    for d, _neg, sym in conf:
        act = ed.single_trade(mk, cand, d, sym, **trade_kw(0.0, None))
        opp = ed.single_trade(mk, cand, d, sym, side="SHORT" if cand.rows[sym][d]["direction"] == "LONG" else "LONG", **trade_kw(0.0, None))
        if act and opp and act["status"] == opp["status"] == "CLOSED":
            sig_rows.append({"entry_ts": act["entry_ts"], "side": act["side"], "g_act": act["gross"], "g_opp": opp["gross"], "n_act": act["net"], "n_opp": opp["net"],
                             "day": act["entry_ts"] // ed.DAY, "period": ed.period_of(act["entry_ts"], plan.segments)})
    direction = {}
    for p in PERIODS:
        rows = [r for r in sig_rows if r["period"] == p or (p == "ALL" and r["period"])]
        if len(rows) < 3:
            direction[p] = {"n": len(rows)}
            continue
        days = np.array([r["day"] for r in rows])
        delta = np.array([r["g_act"] - r["g_opp"] for r in rows])
        res = ed.sign_flip_p(delta, days, rng)
        res.update(gross_actual_mean=float(np.mean([r["g_act"] for r in rows])), gross_opposite_mean=float(np.mean([r["g_opp"] for r in rows])),
                   net_actual_mean=float(np.mean([r["n_act"] for r in rows])), net_opposite_mean=float(np.mean([r["n_opp"] for r in rows])))
        direction[p] = res
    report["direction_test"] = direction
    report["signals_C"] = len(sig_rows)

    # multiple testing over every segment cell with ALL n >= MIN_N (population B); Holm on net and on gross separately
    cells = []
    for fam, tab in [(f"B/{f}", t) for f, t in tabs["B"].items()] + [(f"D/{f}", t) for f, t in tabs["D"].items()]:
        if fam.endswith("/exit_reason"):                      # partition defined by the outcome itself: not a predictor, never tested for "edge"
            continue
        for seg, per in tab.items():
            c = per["ALL"]
            if c.get("n", 0) >= ed.MIN_N and "net_stat" in c:
                cells.append({"family": fam, "segment": seg, "n": c["n"], "net_mean": c["net_mean"], "gross_mean": c["gross_mean"],
                              "p_net": c["net_stat"]["p"], "p_gross": c["gross_stat"]["p"],
                              "tvo_net_pos": [per[p].get("n", 0) > 0 and per[p]["net_mean"] > 0 for p in PERIODS[:3]],
                              "tvo_gross_pos": [per[p].get("n", 0) > 0 and per[p]["gross_mean"] > 0 for p in PERIODS[:3]]})
    for key, pk in (("holm_net", "p_net"), ("holm_gross", "p_gross")):
        for c, v in zip(cells, ed.holm([c[pk] for c in cells]), strict=True):
            c[key] = v
    report["multiple_testing"] = {"cells_tested": len(cells), "min_holm_net": min((c["holm_net"] for c in cells if c["holm_net"] == c["holm_net"]), default=None),
                                  "min_holm_gross": min((c["holm_gross"] for c in cells if c["holm_gross"] == c["holm_gross"]), default=None),
                                  "net_pos_in_all_three_periods": sum(all(c["tvo_net_pos"]) for c in cells),
                                  "gross_pos_in_all_three_periods": sum(all(c["tvo_gross_pos"]) for c in cells),
                                  "expected_by_chance_if_no_edge": len(cells) / 8, "cells": cells}

    (out_dir / "edge_report.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    (out_dir / "edge_tables.md").write_text("\n".join(md), encoding="utf-8")
    keys = ["symbol", "side", "entry_ts", "exit_ts", "hold_bars", "period", "reason", "confidence", "momentum", "atr", "atr_pct", "trap", "breakout", "mtf_cls",
            "dir_15m", "dir_1h", "dir_4h", "mtf_alignment", "mtf_permission", "sf_accepted", "rsi", "macd_hist", "adx", "ema20", "ema50", "ema200", "volume_ratio",
            "entry_fill", "exit_fill", "qty", "target_over_atr", "stop_over_atr", "tp_dist", "stop_dist", "g0", "f0", "c0", "n0", "n2", "n5", "slip_cost", "mfe", "mae",
            "mfe_atr", "mae_atr", "n2_no_dead"]
    with (out_dir / "edge_trades_B.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(keys)
        for t in pop_b:
            w.writerow(["NA" if t.get(k) is None else t[k] for k in keys])
    print(json.dumps({k: report[k] for k in ("population_a", "population_b", "population_d", "barrier_baseline", "direction_test", "mfe_mae", "dead_close")}, indent=1, default=float))


if __name__ == "__main__":
    main()

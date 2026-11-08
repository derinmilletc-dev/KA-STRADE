"""KAIS FARMER offline backtest: candidate cache, parameter grid, report. No network, no trading API, no repository output.

  candidates --data D --cache C        evaluate the unmodified Original V2 evaluator on closed candles (parameter independent)
  grid       --data D --cache C --output O   run the parameter grid x slippage and write O/grid.csv
  report     --data D --cache C --output O   TRAIN/VALIDATION/OOS tables, Monte Carlo and overfitting checks -> O/report.json
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import multiprocessing as mp
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from app import backtest_data as bd
from app import backtest_farmer as bf

REPO = Path(__file__).resolve().parents[1]
MARGINS, TARGETS, LOSSES = [25, 50, 75, 100, 150], [0.5, 1, 2], [1, 1.5, 2]
SCORES, DEADS, MAXPOS, SLIPS = [90, 92, 94, 95], [70, 75, 80, None], [1, 2, 4, 5], [0, 1, 2, 5]
KEYS = ["margin", "target", "max_loss", "min_score", "dead", "max_pos"]


def day(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


class Plan:
    """Fixed period and walk-forward split (chronological; OOS is never used for selection)."""
    def __init__(self, a: argparse.Namespace) -> None:
        self.start, self.end = day(a.start), day(a.end)
        self.first = int(datetime.strptime(a.first, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp())
        train_end, valid_end = day(a.valid_start), day(a.oos_start)
        self.segments = {"TRAIN": (self.first, train_end), "VALID": (train_end, valid_end), "OOS": (valid_end, self.end), "ALL": (self.first, self.end)}
        first_day = datetime.fromtimestamp(self.first, timezone.utc)
        edges, year, month = [self.first], first_day.year, first_day.month
        while True:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            edge = day(f"{year:04d}-{month:02d}-01")
            if edge >= self.end:
                break
            edges.append(edge)
        edges.append(self.end)
        self.months = [(datetime.fromtimestamp(lo, timezone.utc).strftime("%Y-%m"), lo, hi) for lo, hi in itertools.pairwise(edges)]
        self.report_months = [label for label, _lo, _hi in self.months[:4]]      # months before the OOS split (no OOS leakage)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["candidates", "grid", "report"])
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--start", default="2026-03-01")
    p.add_argument("--end", default="2026-10-01", help="exclusive")
    p.add_argument("--first", default="2026-04-13T04:00", help="first decision time with full 259-candle history on every interval")
    p.add_argument("--valid-start", default="2026-07-13")
    p.add_argument("--oos-start", default="2026-08-22")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--margins", type=float, nargs="*")
    p.add_argument("--max-positions", type=int, nargs="*")
    p.add_argument("--report-slippage", type=float, default=2.0)
    p.add_argument("--variant", choices=["CONFIDENCE", "STRATEGY_QUALITY"], default="CONFIDENCE", help="Farmer entry confirmation variant")
    return p


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data, cache and results must stay outside the repository")
    return path


def load(a: argparse.Namespace, plan: Plan):
    root = outside_repo(a.data)
    dataset = bd.load_dataset(root, root / "current-metadata.json", plan.start, plan.end)
    return dataset, bf.build_market(dataset, plan.start, plan.end)


def cache_file(a: argparse.Namespace, symbol: str) -> Path:
    return outside_repo(a.cache) / f"candidates_{symbol}.json"


def candidate_job(args):
    a, symbol = args
    from app.farmer_engine.feed import (
        HISTORY_CANDLES,
        SIGNAL_INTERVAL,
        candidate_from_legacy,
        default_evaluator,
    )
    plan = Plan(a)
    dataset, _ = load(a, plan)
    universe, rows, unavailable = sorted(dataset.frames), [], {}
    for at in range(plan.first, plan.end - 900 + 1, 900):
        frames = {iv: dataset.frames[symbol][iv].closed(at, limit=HISTORY_CANDLES - 1) for iv in ("15m", "1h", "4h")}
        if not all(len(frames[iv]) == HISTORY_CANDLES - 1 and dataset.frames[symbol][iv].history_complete(at) for iv in frames):
            unavailable[str(at)] = "DATA_GAP"
            continue
        try:
            result = default_evaluator(symbol, at, frames, universe)
            item = candidate_from_legacy(symbol, SIGNAL_INTERVAL, (at - 900) * 1000, getattr(result, "legacy", result), 0.0)
        except Exception as exc:                                           # noqa: BLE001 - reported, never silently skipped
            unavailable[str(at)] = f"EVAL_ERROR:{exc.__class__.__name__}"
            continue
        mtf = item.mtf
        short_filter = mtf.get("short_filter_v2")
        rows.append({"at": at, "direction": item.direction, "confidence": item.confidence, "momentum": item.momentum, "atr": item.atr,
                     "entry": item.entry_price, "trap": item.trap_score, "breakout": item.breakout_quality,
                     "mtf_permission": mtf.get("entry_permission"), "mtf_direction": mtf.get("direction"), "mtf_alignment": mtf.get("alignment"),
                     "sf_accepted": short_filter.get("accepted") if isinstance(short_filter, dict) else None,
                     "sf_present": isinstance(short_filter, dict)})
    cache_file(a, symbol).parent.mkdir(parents=True, exist_ok=True)
    cache_file(a, symbol).write_text(json.dumps({"symbol": symbol, "rows": rows, "unavailable": unavailable}), encoding="utf-8")
    return symbol, len(rows), len(unavailable)


def load_candidates(a: argparse.Namespace, mk: bf.Market, plan: Plan) -> bf.Candidates:
    cache = {s: json.loads(cache_file(a, s).read_text(encoding="utf-8")) for s in mk.symbols}
    return bf.build_candidates(cache, mk.t0, mk.n, (70, 75, 80, None))


def run_one(mk, cand, table, margin, target, loss, max_pos, dead, slip, research=False):
    kw = {"margin": float(margin), "leverage": 3.0, "target": float(target), "max_loss": float(loss), "max_positions": int(max_pos),
          "dead_score": dead, "slippage_bps": float(slip)}
    if research:
        kw["total_cap"] = 150.0
    return bf.simulate(mk, cand, table, **kw)


def grid_job(args):
    a, margins, research = args
    plan = Plan(a)
    _, mk = load(a, plan)
    cand = load_candidates(a, mk, plan)
    maxpos = a.max_positions or MAXPOS
    rows = []
    for margin, target, loss, score in itertools.product(margins, TARGETS, LOSSES, SCORES):
        table = bf.confirmed_table(cand, margin, 3.0, target, loss, score, a.variant)
        for dead, max_pos, slip in itertools.product(DEADS, maxpos, SLIPS):
            res = run_one(mk, cand, table, margin, target, loss, max_pos, dead, slip, research)
            row = {"margin": margin, "target": target, "max_loss": loss, "min_score": score, "dead": -1 if dead is None else dead,
                   "max_pos": max_pos, "slip": slip, "research": int(research), "cap": res["capacity"], "open_end": len(res["open"]),
                   "rej_slot": res["rejects"]["NO_SLOT"], "rej_occ": res["rejects"]["OCCUPIED"]}
            row.update(bf.trade_metrics(res["trades"], plan.segments, plan.months, float(loss)))
            rows.append(row)
    return rows


def read_grid(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [{k: (float(v) if v not in ("", "nan") else float("nan")) for k, v in r.items()} for r in csv.DictReader(fh)]


def monte_carlo(net, days, rng, n=5000):
    net = np.asarray(net, dtype=float)
    if len(net) < 2:
        return None
    s = net[rng.integers(0, len(net), size=(n, len(net)))]
    curve = np.concatenate([np.zeros((n, 1)), np.cumsum(s, 1)], 1)
    dd = (np.maximum.accumulate(curve, 1) - curve).max(1)
    by_day: dict[int, float] = {}
    for d, v in zip(days, net):
        by_day[d] = by_day.get(d, 0.0) + float(v)
    daily = np.array(list(by_day.values()))
    block = daily[rng.integers(0, len(daily), size=(n, len(daily)))].sum(1)
    total = s.sum(1)
    return {"trades": len(net), "samples": n, "iid_p_net_positive": float((total > 0).mean()), "iid_p5": float(np.percentile(total, 5)),
            "iid_p95": float(np.percentile(total, 95)), "iid_dd_p95": float(np.percentile(dd, 95)),
            "day_block_p_net_positive": float((block > 0).mean()), "day_block_p5": float(np.percentile(block, 5)),
            "t_stat": float(net.mean() / (net.std(ddof=1) / math.sqrt(len(net)))) if net.std() > 0 else None}


def report(a: argparse.Namespace, plan: Plan) -> dict:
    out_dir = outside_repo(a.output)
    rows = read_grid(out_dir / "grid.csv")
    sl = a.report_slippage
    base = [r for r in rows if not r["research"] and r["cap"] > 0 and r["slip"] == sl]
    for r in base:       # selection uses TRAIN + VALID only; OOS is shown, never ranked
        r["TV_net"], r["TV_n"] = r["TRAIN_net"] + r["VALID_net"], r["TRAIN_n"] + r["VALID_n"]
        r["TV_dd"] = max(r["TRAIN_dd"], r["VALID_dd"])
        r["score"] = r["TV_net"] / max(r["TV_dd"], 1.0)
        avgm = (r["TRAIN_avgm"] + r["VALID_avgm"]) / 2
        r["TV_roc"] = r["TV_net"] / avgm if avgm else float("nan")
        months = [r[f"M_{label}"] for label in plan.report_months]
        r["pos_months"], r["worst_month"] = sum(m > 0 for m in months), min(months)
    eligible = [r for r in base if r["TV_n"] >= 100]
    positive = [r for r in eligible if r["TV_net"] > 0]
    picks = {"Top1_BestOverall": max(eligible, key=lambda r: r["score"]),
             "Conservative": min(positive, key=lambda r: (r["TV_dd"], -r["TV_net"])) if positive else None,
             "CapitalEfficient": max(positive, key=lambda r: r["TV_roc"]) if positive else None,
             "Stable": max(positive, key=lambda r: (r["pos_months"], r["worst_month"])) if positive else None}
    slim = lambda r: {k: r[k] for k in KEYS + ["TRAIN_n", "TRAIN_net", "VALID_net", "OOS_net", "OOS_n", "ALL_net", "TV_dd", "score"]}
    result = {"slippage_bps": sl, "configs": len(base), "eligible": len(eligible), "tv_positive": len(positive),
              "top10": [slim(r) for r in sorted(eligible, key=lambda r: -r["score"])[:10]],
              "worst10": [slim(r) for r in sorted(eligible, key=lambda r: r["score"])[:10]], "picks": {}}
    unique = len({(round(r["ALL_net"], 6), r["ALL_n"]) for r in base})
    result["null_max_t_bound"] = math.sqrt(2 * math.log(unique))
    _, mk = load(a, plan)
    cand = load_candidates(a, mk, plan)
    rng = np.random.default_rng(2026)
    for name, r in picks.items():
        if r is None:
            result["picks"][name] = None
            continue
        dead = None if r["dead"] == -1 else r["dead"]
        res = run_one(mk, cand, bf.confirmed_table(cand, r["margin"], 3.0, r["target"], r["max_loss"], int(r["min_score"]), a.variant), r["margin"],
                      r["target"], r["max_loss"], r["max_pos"], dead, sl)
        trades = sorted(res["trades"], key=lambda p: p["exit_ts"])
        def part(*names, rows=trades):
            return [p for p in rows if any(plan.segments[n][0] <= p["exit_ts"] < plan.segments[n][1] for n in names)]

        tv, oos = part("TRAIN", "VALID"), part("OOS")
        result["picks"][name] = {"config": slim(r), "monte_carlo_train_valid": monte_carlo([p["net"] for p in tv], [p["exit_ts"] // 86400 for p in tv], rng),
                                 "monte_carlo_oos": monte_carlo([p["net"] for p in oos], [p["exit_ts"] // 86400 for p in oos], rng),
                                 "slippage_sensitivity": {str(int(g["slip"])): {s: g[f"{s}_net"] for s in ("TRAIN", "VALID", "OOS", "ALL")} for g in rows
                                                          if not g["research"] and all(g[k] == r[k] for k in KEYS)},
                                 "close_reasons": dict(Counter(p["reason"] for p in trades)), "open_at_end": len(res["open"]), "rejects": res["rejects"]}
    survivors = {}
    for g in (0, 1, 2, 5):
        at = [r for r in rows if not r["research"] and r["cap"] > 0 and r["slip"] == g]
        both = [r for r in at if r["TRAIN_net"] > 0 and r["VALID_net"] > 0]
        survivors[str(g)] = {"configs": len(at), "train_valid_positive": len(both), "of_which_oos_positive": sum(r["OOS_net"] > 0 for r in both),
                             "all_positive": sum(r["ALL_net"] > 0 for r in at)}
    result["walk_forward_survivors_by_slippage"] = survivors
    (out_dir / "report.json").write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    return result


def main() -> None:
    a = parser().parse_args()
    plan = Plan(a)
    if a.command == "candidates":
        dataset, _ = load(a, plan)
        with mp.Pool(max(1, a.workers)) as pool:
            for symbol, count, missing in pool.imap_unordered(candidate_job, [(a, s) for s in sorted(dataset.frames)]):
                print(json.dumps({"symbol": symbol, "candidates": count, "unavailable": missing}))
    elif a.command == "grid":
        out = outside_repo(a.output)
        out.mkdir(parents=True, exist_ok=True)
        margins = [m for m in MARGINS if not a.margins or m in a.margins]
        jobs = [(a, [m for m in margins if m != 150], False)] + [(a, [150], research) for research in (False, True) if 150 in margins]
        jobs = [j for j in jobs if j[1]]
        with mp.Pool(max(1, a.workers)) as pool:
            parts = pool.map(grid_job, jobs)
        rows = [r for part in parts for r in part]
        with (out / "grid.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps({"rows": len(rows), "output": str(out / "grid.csv"), "assumptions": bf.ASSUMPTIONS}, indent=1))
    else:
        print(json.dumps(report(a, plan), indent=1, default=float))


if __name__ == "__main__":
    sys.exit(main())

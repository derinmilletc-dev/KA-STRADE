"""KAIS FARMER C15 (analysis + scoring research). Research only; no network, no exchange client, no live gate, output stays outside the repository.

  dataset  --data-2025 D --data-2026 D --output O   TRAIN+VALID feature snapshot with look-ahead-free labels (evaluates the REAL Original V2 evaluator)
  diagnose --dataset DIR --output O                  TRAIN-only feature / redundancy / regime / current-score diagnosis (VALID is not opened)
  models   --dataset DIR --output O                  fit candidates B and C on TRAIN only (VALID is not opened)
  c14link  --dataset DIR --data-2025 D --data-2026 D --output O   descriptive: C14 TRAIN/VALID entries seen through the current score (no OOS)
  valid    --dataset DIR --output O                  ONE comparison of A/B/C on VALID with the frozen models (refuses to run twice)

The 2026 data directory must NOT contain archives from 2026-06 on (OOS is locked); `dataset` refuses a manifest that does.
"""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app import backtest_data as bd
from app import backtest_farmer as bf
from app.research import c15_analysis as ca
from app.research import c15_scoring as cs

REPO = Path(__file__).resolve().parents[1]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
# (data start, exclusive end, first decision with a full 259-candle history on every interval). 2026 ends at the OOS start.
BLOCKS = {"2025": ("2025-03-01", "2025-10-01", "2025-04-13T04:00"), "2026": ("2026-03-01", "2026-06-01", "2026-04-13T04:00")}


def utc(text: str) -> int:
    fmt = "%Y-%m-%dT%H:%M" if "T" in text else "%Y-%m-%d"
    return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data and results must stay outside the repository")
    return path


def guard_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    late = [a["path"] for a in manifest["archives"] if str(a["month"]) >= "2026-06"]
    if late:
        raise SystemExit(f"OOS archives present in the manifest ({len(late)}): remove them before running C15")


def job(arg):
    key, root, symbol, limit = arg
    from app.farmer_engine.feed import HISTORY_CANDLES, default_evaluator
    start, end, first = (utc(x) for x in BLOCKS[key])
    assert end <= cs.OOS_START or key == "2025"
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    mk = bf.build_market(ds, start, end)
    universe, rows, skipped = sorted(ds.frames), [], 0
    mh, ml, mc = mk.mhigh[symbol], mk.mlow[symbol], mk.mclose[symbol]
    for at in range(first, end - bf.BAR + 1, bf.BAR):
        if limit and len(rows) >= limit:
            break
        frames = {iv: ds.frames[symbol][iv].closed(at, limit=HISTORY_CANDLES - 1) for iv in ("15m", "1h", "4h")}
        if not all(len(frames[iv]) == HISTORY_CANDLES - 1 and ds.frames[symbol][iv].history_complete(at) for iv in frames):
            skipped += 1
            continue
        legacy = getattr(default_evaluator(symbol, at, frames, universe), "legacy", None)
        if not legacy or not legacy.get("analysis"):
            skipped += 1
            continue
        row = cs.snapshot_row(symbol, at, legacy)
        d = (at - start) // bf.BAR
        if d >= mk.n or not row["atr"]:
            skipped += 1
            continue
        entry = float(mk.copen[symbol][d])
        row.update(entry_price=entry, **cs.label_row(mh, ml, mc, d, entry, row["atr"], mk.fund_t[symbol], mk.fund_r[symbol], start))
        rows.append(row)
    return key, symbol, rows, skipped


def dataset(a: argparse.Namespace) -> None:
    out_dir = outside_repo(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = {"2025": outside_repo(a.data_2025), "2026": outside_repo(a.data_2026)}
    guard_manifest(roots["2026"])
    jobs = [(k, roots[k], s, a.limit) for k in BLOCKS for s in SYMBOLS]
    with mp.get_context("fork").Pool(a.workers) as pool:
        results = pool.map(job, jobs, chunksize=1)
    rows = [r for _k, _s, rs, _sk in results for r in rs]
    rows.sort(key=lambda r: (r["at"], r["symbol"]))
    if any(r["at"] >= cs.OOS_START for r in rows):
        raise SystemExit("OOS row in dataset: refusing to write")
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    for period in ("TRAIN", "VALID"):
        sub = [r for r in rows if r["period"] == period]
        with (out_dir / f"c15_feature_dataset_{period.lower()}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(sub)
    summary = {"rows": {p: sum(1 for r in rows if r["period"] == p) for p in ("TRAIN", "VALID")},
               "skipped": {f"{k}/{s}": sk for k, s, _r, sk in results}, "max_at": max(r["at"] for r in rows),
               "score_reproduced_all": all(r.get("score_reproduced") for r in rows), "oos_start": cs.OOS_START}
    (out_dir / "c15_dataset_build.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


SEED = 20261108


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=1, default=float, allow_nan=True), encoding="utf-8")


def diagnose(a: argparse.Namespace) -> None:
    """TRAIN only. The VALID file is never opened here."""
    out_dir = outside_repo(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    raw = pd.read_csv(outside_repo(a.dataset) / "c15_feature_dataset_train.csv")
    if (raw["at"] >= cs.VALID_START).any() or (raw["period"] != "TRAIN").any():
        raise SystemExit("TRAIN file contains non-TRAIN rows")
    d = ca.prepare(raw)
    edges = ca.make_edges(d)
    quality = ca.feature_quality(d, rng, edges)
    red = ca.redundancy(d)
    vol_edges = ca.vol_edges_from(d)
    top = sorted(quality, key=lambda k: -abs(quality[k]["auc"] - 0.5))[:8]
    reg = ca.regime_table(ca.add_regimes(d, vol_edges), rng, top)
    write_json(out_dir / "c15_feature_diagnosis.json", {
        "scope": {"period": "TRAIN", "rows_directional_resolved": len(d), "days": int(d["day"].nunique()),
                  "first_at": int(d["at"].min()), "last_at": int(d["at"].max()), "test_family_size": len(ca.FEATURES),
                  "note": "rows overlap in time; p-values are day-cluster bootstrap, Holm-adjusted over the feature family"},
        "feature_quality": quality, "redundancy": red, "regimes": reg, "vol_regime_edges_from_train": vol_edges, "decomposition": ca.decomposition(d)})
    write_json(out_dir / "c15_current_score_diagnosis.json", ca.current_score_diagnosis(d, rng))
    print(json.dumps({"rows": len(d), "features": len(quality)}))


def train_frame(a: argparse.Namespace) -> pd.DataFrame:
    raw = pd.read_csv(outside_repo(a.dataset) / "c15_feature_dataset_train.csv")
    if (raw["at"] >= cs.VALID_START).any() or (raw["period"] != "TRAIN").any():
        raise SystemExit("TRAIN file contains non-TRAIN rows")
    return ca.prepare(raw)


def models(a: argparse.Namespace) -> None:
    """TRAIN only. Candidates: A current confidence (unchanged), B percentile evidence score, C ridge-logistic. Nothing else is tried."""
    out_dir = outside_repo(a.output)
    rng = np.random.default_rng(SEED + 1)
    d = train_frame(a)
    quality = json.loads((out_dir / "c15_feature_diagnosis.json").read_text(encoding="utf-8"))["feature_quality"]
    selected = ca.select_for_b(d, quality)
    mb = ca.fit_b(d, selected)
    feats_c = ca.prune_for_c(d, quality)
    mc = ca.fit_c(d, feats_c)
    n_current = int(d["u_farmer"].sum())
    scores = {"A": d["confidence"].to_numpy(dtype=float), "B": ca.score_b(mb, d), "C": ca.score_c(mc, d)}
    # honest TRAIN estimate for C: out-of-fold predictions over 5 contiguous day blocks with the chosen ridge
    oof = np.zeros(len(d))
    x = d[feats_c].to_numpy(dtype=float)
    x = np.where(np.isfinite(x), x, np.array(mc["median"]))
    for f in np.array_split(np.unique(d["day"].to_numpy()), 5):
        te = np.isin(d["day"].to_numpy(), f)
        mu, sd = x[~te].mean(0), x[~te].std(0) + 1e-12
        w, b = ca._logistic_ridge((x[~te] - mu) / sd, d["y"].to_numpy(dtype=float)[~te], mc["ridge_lambda"])
        oof[te] = 1 / (1 + np.exp(-(((x[te] - mu) / sd) @ w + b)))
    reports = {"A": ca.ranking_report(d, scores["A"], rng, n_current), "B": ca.ranking_report(d, scores["B"], rng, n_current),
               "C": ca.ranking_report(d, oof, rng, n_current)}
    reports_c_in_sample = ca.ranking_report(d, scores["C"], rng, n_current)
    base = float(d["y"].mean())
    write_json(out_dir / "c15_train_models.json", {
        "candidates": ["A current confidence (unchanged)", "B simplified evidence score", "C calibrated logistic score"], "max_candidates": 3,
        "selection_rule_B": f"TRAIN day-cluster p < {ca.SELECT_P} (unadjusted) AND same Spearman sign in both TRAIN halves, one representative per |rho|>={ca.C_PRUNE_RHO} pair",
        "selection_rule_C": f"up to {ca.C_MAX_FEATURES} raw features, pruned so no pair has |rho|>={ca.C_PRUNE_RHO}, ordered by TRAIN |AUC-0.5|; ridge in {ca.RIDGE_GRID} by 5-fold contiguous-day CV",
        "B_selected_evidence": selected, "model_B": mb, "model_C": mc, "n_train_rows": len(d), "n_current_entries_train": n_current,
        "train_base_success_rate": base, "train_ranking": reports, "train_ranking_C_in_sample": reports_c_in_sample,
        "note": "B is in-sample on TRAIN; C is shown out-of-fold (and in-sample separately). VALID was not opened."})
    print(json.dumps({"B_features": [s["feature"] for s in selected], "C_features": feats_c, "lambda": mc["ridge_lambda"]}))


def valid(a: argparse.Namespace) -> None:
    """The ONE VALID comparison. Models are frozen JSON from `models`; nothing is refitted. Refuses to run twice."""
    out_dir = outside_repo(a.output)
    target = out_dir / "c15_valid_comparison.json"
    if target.exists():
        raise SystemExit("VALID comparison already produced: it is a single-shot step and will not be repeated")
    rng = np.random.default_rng(SEED + 2)
    raw = pd.read_csv(outside_repo(a.dataset) / "c15_feature_dataset_valid.csv")
    if (raw["at"] < cs.VALID_START).any() or (raw["at"] >= cs.OOS_START).any():
        raise SystemExit("VALID file contains rows outside VALID")
    v = ca.prepare(raw)
    tm = json.loads((out_dir / "c15_train_models.json").read_text(encoding="utf-8"))
    n_current = int(v["u_farmer"].sum())
    scores = {"A": v["confidence"].to_numpy(dtype=float), "B": ca.score_b(tm["model_B"], v), "C": ca.score_c(tm["model_C"], v)}
    reports = {m: ca.ranking_report(v, s, rng, n_current) for m, s in scores.items()}
    cur = v[v["u_farmer"]]
    current_entries = {"n": len(cur), "overall": cs.summarize_outcomes(cur["y"].to_numpy().astype(int)),
                       "max_drawdown_r": cs.max_drawdown_r(cur["y"].to_numpy().astype(int)[np.argsort(cur["at"].to_numpy(), kind="stable")]),
                       "bands": ca.band_table(cur, rng), "spearman_confidence_y": ca.cluster_spearman(
                           cur["confidence"].to_numpy(dtype=float), cur["y"].to_numpy(dtype=float), cur["day"].to_numpy(), rng)}
    # ranking INSIDE the current entry universe (same signals): top half vs bottom half by each score
    inside = {}
    for m, s in scores.items():
        sc = s[v["u_farmer"].to_numpy()]
        yy = cur["y"].to_numpy().astype(int)
        hi, lo = ca.top_fraction(sc, 0.5), ca.top_fraction(sc, 0.5, bottom=True)
        inside[m] = {"top_half": cs.summarize_outcomes(yy[hi]), "bottom_half": cs.summarize_outcomes(yy[lo]), "spearman": ca.cluster_spearman(sc, yy.astype(float), cur["day"].to_numpy(), rng)}
    pc = scores["C"]
    dec = np.minimum((ca._rank(pc) / len(pc) * 10).astype(int), 9)
    calib = [{"decile": int(b), "n": int((dec == b).sum()), "mean_predicted": float(pc[dec == b].mean()), "observed": float(v["y"].to_numpy()[dec == b].mean())} for b in range(10)]
    brier = float(((pc - v["y"].to_numpy()) ** 2).mean())
    brier_const = float(((tm["train_base_success_rate"] - v["y"].to_numpy()) ** 2).mean())
    tr = {m: tm["train_ranking"][m] for m in ("A", "B", "C")}
    classification = ca.classify(tr, reports)
    ratios = {m: {"valid_over_train_top30_expectancy": (reports[m]["top30"]["expectancy_r"] / tr[m]["top30"]["expectancy_r"]) if tr[m]["top30"]["expectancy_r"] > 0 else None,
                  "valid_over_train_spearman": (reports[m]["spearman_score_y"]["rho"] / tr[m]["spearman_score_y"]["rho"]) if tr[m]["spearman_score_y"]["rho"] > 0 else None}
              for m in reports}
    vol = ca.add_regimes(v, json.loads((out_dir / "c15_feature_diagnosis.json").read_text(encoding="utf-8"))["vol_regime_edges_from_train"] and
                         {k: tuple(x) for k, x in json.loads((out_dir / "c15_feature_diagnosis.json").read_text(encoding="utf-8"))["vol_regime_edges_from_train"].items()})
    by_regime = {f"{c}={val}": {m: ca.cluster_spearman(scores[m][vol[c].to_numpy() == val], vol.loc[vol[c] == val, "y"].to_numpy(dtype=float),
                                                          vol.loc[vol[c] == val, "day"].to_numpy(), rng, reps=500)["rho"] for m in scores}
                 for c in ("vol_regime", "trend_regime") for val in sorted(vol[c].unique())}
    write_json(target, {"scope": {"period": "VALID", "first_at": int(v["at"].min()), "last_at": int(v["at"].max()), "days": int(v["day"].nunique()),
                                  "rows_directional_resolved": len(v), "requested_window": "2026-01-01 -> 2026-05-31",
                                  "effective_window": "2026-04-13 -> 2026-05-31 (archives start 2026-03; 259-candle history on 4h needs ~43 days)"},
                        "base_success_rate": float(v["y"].mean()), "current_entries_on_valid": current_entries, "current_score_valid_bands_all_directional": ca.band_table(v, rng),
                        "ranking": reports, "ranking_inside_current_entry_universe": inside, "calibration_C_deciles": calib,
                        "brier_C": brier, "brier_constant_train_rate": brier_const, "train_vs_valid_ratios": ratios,
                        "spearman_by_regime": by_regime, "classification": classification})
    print(json.dumps(classification, indent=1, default=float))


def c14link(a: argparse.Namespace) -> None:
    """Descriptive only. C14 setups on TRAIN/VALID contract candles are joined to the decision snapshot at their entry candle."""
    import numpy as np

    from app import farmer_entry_research as er
    out_dir = outside_repo(a.output)
    raw = pd.concat([pd.read_csv(outside_repo(a.dataset) / f"c15_feature_dataset_{p}.csv") for p in ("train", "valid")])
    raw = raw.set_index(["symbol", "at"])
    roots = {"2025": outside_repo(a.data_2025), "2026": outside_repo(a.data_2026)}
    guard_manifest(roots["2026"])
    recs = []
    for key, root in roots.items():
        start, end, _first = (utc(x) for x in BLOCKS[key])
        ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
        for sym in SYMBOLS:
            rows = [r for r in ds.frames[sym]["15m"].rows if start <= r["time"] < end]
            o, h, lo, c = (np.array([r[k] for r in rows]) for k in ("open", "high", "low", "close"))
            for e in er.detect_setups(o, h, lo, c, er.wilder_atr(h, lo, c, er.PROTOCOL.atr_period)):
                if e["state"] != er.ENTRY:
                    continue
                at = start + e["entry_idx"] * bf.BAR
                period = cs.period_of(at) if at < cs.OOS_START else None
                if period is None or (sym, at) not in raw.index:
                    continue
                r = raw.loc[(sym, at)]
                side = e["side"]
                y = r["y_long"] if side == "LONG" else r["y_short"]
                recs.append({"period": period, "symbol": sym, "side": side, "current_direction": r["direction"], "confidence": r["confidence"],
                             "farmer_confirmed": bool(r["farmer_confirmed"]), "y_analysis_atr": None if pd.isna(y) else int(y)})
    df = pd.DataFrame(recs)
    out: dict = {"note": "descriptive only; C14 OOS not used; outcomes here use the analysis ATR, not the C14 Wilder ATR"}
    for period, g in df.groupby("period"):
        res = g[g["y_analysis_atr"].notna()]
        out[period] = {"c14_entries_joined": len(g), "current_direction_agrees": int((g["current_direction"] == g["side"]).sum()),
                       "current_direction_opposite": int(((g["current_direction"] != g["side"]) & (g["current_direction"] != "BEKLE")).sum()),
                       "current_direction_BEKLE": int((g["current_direction"] == "BEKLE").sum()), "current_farmer_confirmed": int(g["farmer_confirmed"].sum()),
                       "confidence_ge_90_among_agreeing": int(((g["current_direction"] == g["side"]) & (g["confidence"] >= cs.MIN_FARMER_SCORE)).sum()),
                       "c14_side_outcome_analysis_atr": cs.summarize_outcomes(res["y_analysis_atr"].to_numpy().astype(int)),
                       "descriptive_only": bool(len(g) < ca.MIN_DECISION)}
    write_json(out_dir / "c15_c14_link.json", out)
    print(json.dumps(out, indent=1, default=float))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["dataset", "diagnose", "models", "valid", "c14link"])
    p.add_argument("--data-2025", type=Path)
    p.add_argument("--data-2026", type=Path)
    p.add_argument("--dataset", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--limit", type=int, default=0, help="rows per symbol/block (speed test only)")
    a = p.parse_args()
    if a.command in ("diagnose", "models", "valid"):
        if not a.dataset:
            raise SystemExit("--dataset is required")
        {"diagnose": diagnose, "models": models, "valid": valid}[a.command](a)
        return
    if not (a.data_2025 and a.data_2026):
        raise SystemExit("--data-2025 and --data-2026 are required")
    if a.command == "c14link":
        if not a.dataset:
            raise SystemExit("--dataset is required")
        c14link(a)
        return
    dataset(a)


if __name__ == "__main__":
    sys.exit(main())

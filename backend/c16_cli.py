"""KAIS FARMER C16 CLI (research only). Phases: manifest -> features (TRAIN) -> diagnose -> models/freeze -> valid -> audit -> report.
Run through the stubs launcher. OOS is never read: the manifest lists it as EXCLUDED, data roots are guarded."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app import backtest_data as bd
from app import backtest_farmer as bf
from app.research import c15_analysis as ca
from app.research import c15_scoring as cs
from app.research.c16 import analysis as an
from app.research.c16 import inventory as inv
from app.research.c16 import pipeline as pl
from app.research.c16 import structure as st

REPO = Path(__file__).resolve().parents[1]
BLOCKS = {"TRAIN": ("2025-03-01", "2025-10-01", "2025-04-13T04:00"), "VALID": ("2026-03-01", "2026-06-01", "2026-04-13T04:00")}
C15_CSV = {"TRAIN": "c15_feature_dataset_train.csv", "VALID": "c15_feature_dataset_valid.csv"}
C15_COLS = ["symbol", "at", "direction", "confidence", "long_score", "short_score", "gates_ex_score", "farmer_confirmed"]


def utc(text: str) -> int:
    from datetime import datetime, timezone
    fmt = "%Y-%m-%dT%H:%M" if "T" in text else "%Y-%m-%d"
    return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data and results must stay outside the repository")
    return path


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=1, default=float, allow_nan=True), encoding="utf-8")


def manifest(a: argparse.Namespace) -> None:
    out = outside_repo(a.output)
    out.mkdir(parents=True, exist_ok=True)
    roots = {"TRAIN": outside_repo(a.data_2025), "VALID": outside_repo(a.data_2026)}
    pl.guard_manifest(roots["VALID"])
    entries = []
    for split, root in roots.items():
        for arc in json.loads((root / "manifest.json").read_text(encoding="utf-8"))["archives"]:
            entries.append(pl.manifest_entry(root, arc, split if split == "VALID" or str(arc["month"]) < "2026-01" else "TRAIN"))
    oos = [{"split": "OOS", "status": "EXCLUDED_LOCKED_NOT_READ", "symbol": x["symbol"], "kind": x["kind"], "month": x["month"], "path": x["path"],
            "sha256_from_download_manifest": x.get("sha256")}
           for x in json.loads((outside_repo(a.data_oos_manifest)).read_text(encoding="utf-8"))["archives"] if str(x["month"]) >= "2026-06"]
    res = {"files": entries, "oos_excluded_listing_only": oos,
           "counts": {"TRAIN": sum(e["split"] == "TRAIN" for e in entries), "VALID": sum(e["split"] == "VALID" for e in entries), "OOS_listed": len(oos)},
           "all_hashes_match_download_manifest": all(e["sha256_matches_download_manifest"] for e in entries),
           "max_ts_in_dataset": max(e["max_ts"] for e in entries if e["max_ts"]), "oos_start": cs.OOS_START,
           "oos_file_in_dataset": any(e["max_ts"] and e["max_ts"] >= cs.OOS_START for e in entries) or any("2026-06" in e["path"] for e in entries),
           "splits": {"TRAIN": ["2025-04-01", "2025-12-31"], "VALID": ["2026-01-01", "2026-05-31"], "OOS": ["2026-06-01", "2026-09-30"]},
           "effective": {"TRAIN": ["2025-04-13", "2025-09-30"], "VALID": ["2026-04-13", "2026-05-31"]}}
    write_json(out / "c16_dataset_manifest.json", res)
    print(json.dumps({k: v for k, v in res.items() if k not in ("files", "oos_excluded_listing_only")}, indent=1))


def job(arg):
    split, root, symbol, limit = arg
    start, end, _first = (utc(x) for x in BLOCKS[split])
    assert end <= cs.OOS_START or split == "TRAIN"
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    mk = bf.build_market(ds, start, end)
    return split, symbol, pl.block_rows(ds, mk, symbol, start, end, utc(BLOCKS[split][2]), limit)


def features(a: argparse.Namespace) -> None:
    """Feature + label rows for ONE split. VALID is refused unless the frozen-model file exists (Phase 10 only)."""
    out = outside_repo(a.output)
    out.mkdir(parents=True, exist_ok=True)
    split = a.split
    if split == "VALID" and not (out.parent / "out" / "c16_frozen_model.json").exists():
        raise SystemExit("VALID features are built only after the model freeze")
    root = outside_repo(a.data_2025 if split == "TRAIN" else a.data_2026)
    pl.guard_manifest(root)
    with mp.get_context("fork").Pool(a.workers) as pool:
        res = pool.map(job, [(split, root, s, a.limit) for s in pl.SYMBOLS], chunksize=1)
    df = pd.DataFrame([r for _s, _sym, rows in res for r in rows])
    cur = pd.read_csv(Path(a.c15_dir) / C15_CSV[split], usecols=C15_COLS)
    df = df.merge(cur, on=["symbol", "at"], how="inner")
    df["score_cur"] = df["long_score"].where(df["side"] == "LONG", df["short_score"])
    df["is_cur_dir"] = df["direction"] == df["side"]
    df["split"] = split
    df = df.sort_values(["at", "symbol", "side"]).reset_index(drop=True)
    assert df["at"].max() < cs.OOS_START
    df.to_csv(out / f"c16_{split.lower()}_features.csv", index=False)
    print(json.dumps({"split": split, "rows": len(df), "max_at": int(df["at"].max()), "min_at": int(df["at"].min()),
                      "per_symbol": df.groupby("symbol").size().to_dict()}, indent=1))


SEED = 20261109


def load_split(dataset_dir: Path, split: str) -> pd.DataFrame:
    path = dataset_dir / f"c16_{split.lower()}_features.csv"
    if split == "VALID" and not (dataset_dir.parent / "out" / "c16_frozen_model.json").exists():
        raise SystemExit("VALID rows are read only after the model freeze")
    d = an.prepare(pd.read_csv(path))
    pl.check_split(d, split)
    return d


def diagnose(a: argparse.Namespace) -> None:
    """Phases 4-6 on TRAIN only: inventory, structure diagnosis, per-feature diagnosis, stability, redundancy, family report."""
    out = outside_repo(a.out)
    out.mkdir(parents=True, exist_ok=True)
    d = load_split(outside_repo(a.dataset), "TRAIN")
    rng = np.random.default_rng(SEED)
    write_json(out / "c16_feature_inventory.json", inv.inventory())
    write_json(out / "c16_structure_diagnosis.json", an.structure_diagnosis(d))
    diag = an.feature_diagnosis(d, rng)
    red = an.redundancy_report(d)
    fam = an.family_report(diag, red)
    write_json(out / "c16_feature_diagnosis.json", {"split": "TRAIN", "rows": len(d), "universe": "U2 all bars x both sides (direction-aligned)",
                                                    "rules": {"N<30": "descriptive only", "N<50": "not for selection", "multiple_testing": "Holm + BH-FDR over all features",
                                                              "p<0.05 alone": "never a selection criterion", "unstable": "sign flip between TRAIN-A and TRAIN-B"},
                                                    "families": fam, "features": diag})
    write_json(out / "c16_redundancy.json", red)
    keys = sorted((k for k in diag if "auc" in diag[k]), key=lambda k: -abs(diag[k]["auc"] - 0.5))[:8]
    print(json.dumps({"rows": len(d), "n_features": len(diag), "holm_sig": [k for k in diag if diag[k].get("holm_significant_0.05")],
                      "sign_flips": [k for k in diag if diag[k].get("sign_flip_a_b")], "top_auc_dist": {k: round(diag[k]["auc"], 4) for k in keys},
                      "redundant_pairs": [red["n_pairs_high"], red["n_pairs_very_high"]], "families": fam}, indent=1, default=float))


def eval_scores(d: pd.DataFrame, scores: dict[str, np.ndarray], rng: np.random.Generator) -> dict[str, Any]:
    """Ranking report per candidate on the same rows (U2 = all rows); U1 slice = rows on the side the current system chose."""
    n_cur = int(d["u1"].sum())
    out = {k: ca.ranking_report(d, v, rng, n_cur) for k, v in scores.items()}
    u1 = d[d["u1"]].reset_index(drop=True)
    if len(u1) > 100:
        out["_U1_current_direction_only"] = {k: ca.ranking_report(u1, v[d["u1"].to_numpy()], rng, len(u1) // 5) for k, v in scores.items()}
    return out


def models(a: argparse.Namespace) -> None:
    """Phases 7-9: max 3 candidates (A current, B simple structure, C structure+location+regime), TRAIN walk-forward, then FREEZE."""
    out = outside_repo(a.out)
    frozen = out / "c16_frozen_model.json"
    if frozen.exists():
        raise SystemExit("Model already frozen: no re-fit allowed")
    d = load_split(outside_repo(a.dataset), "TRAIN")
    diag = json.loads((out / "c16_feature_diagnosis.json").read_text(encoding="utf-8"))["features"]
    rng = np.random.default_rng(SEED + 1)
    sel = {"B": an.select_b(d, diag), "C": an.select_c(d, diag)}
    wf, final = {}, {}
    for name, s in sel.items():
        if len(s["features"]) < 1:
            raise SystemExit(f"candidate {name}: no stable feature")
        wf[name] = an.walk_forward(d, s["features"])
        final[name] = an.fit_ridge(d, s["features"], wf[name]["lambda"])
        z = an.linear_predictor(final[name], d)
        final[name]["score_percentile_grid_train"] = an.percentile_map(z)
    ok = np.isfinite(wf["B"]["oof"]) & np.isfinite(wf["C"]["oof"])
    oof = d[ok].reset_index(drop=True)
    scores = {"A": oof["score_cur"].to_numpy(dtype=float), "B": wf["B"]["oof"][ok], "C": wf["C"]["oof"][ok]}
    rep = eval_scores(oof, scores, rng)
    # random-direction baseline: one random side per (symbol, at), fixed seed, same OOF rows
    pick = oof.groupby(["symbol", "at"]).sample(n=1, random_state=SEED)
    base = {"random_direction": cs.summarize_outcomes(pick["y"].to_numpy()), "current_score_all_rows": cs.summarize_outcomes(oof["y"].to_numpy())}
    spec = {"candidates": {"A": "current Farmer side score (25 + condition points, analysis.py), unchanged", "B": "simple structure score: ridge-logistic on stable structure/breakout features",
                           "C": "structure + location + regime (+momentum) ridge-logistic score on stable, non-redundant features (max 3 per family)"},
            "score": "0..100 TRAIN-ECDF percentile of the logistic linear predictor: a QUALITY RANKING, not a probability", "hard_gate": an.HARD_GATE,
            "weights_fitted_on": "TRAIN only", "walk_forward": "expanding window, 2-day embargo, VALID never in a fold",
            "decision_rule": "classify() of c15_analysis: ranking_ok = Spearman CI lower > 0 and 5-bin monotonic rho >= 0.6; must beat A in TRAIN-OOF and VALID; VALID rho >= 0.5 x TRAIN rho"}
    overfit_log = {"features_in_inventory": len(st.ALL_FEATURES), "features_evaluated_on_train": sum("auc" in v for v in diag.values()),
                   "candidate_models": 2, "ridge_fits": len(an.RIDGE_GRID) * 2 * len(wf["B"]["folds"]) + 2, "ridge_grid": list(an.RIDGE_GRID),
                   "design_iterations_after_seeing_train_diagnosis": [
                       "v1 selection used fixed priority lists + sign agreement only -> picked noise-level features (rho ~ 0.0003)",
                       "v2 (used): candidate order = fixed technical order of the families, stability needs the same sign and |rho| >= 0.005 in both TRAIN halves"],
                   "valid_data_touched_before_freeze": False, "oos_touched": False}
    result = {"split": "TRAIN", "rows_oof": len(oof), "selection": sel, "walk_forward": {k: {kk: vv for kk, vv in v.items() if kk != "oof"} for k, v in wf.items()},
              "oof_ranking": rep, "baselines": base, "spec": spec, "models": final, "overfitting_log": overfit_log,
              "versions": [{"version": "v2", "candidates": {k: {"features": v["features"], "ridge_lambda": wf[k]["lambda"]} for k, v in sel.items()},
                            "training_period": ["2025-04-13", "2025-09-30"], "cv": "walk-forward (3 expanding folds, 2-day embargo)"}]}
    write_json(out / "c16_train_models.json", result)
    freeze = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "models": final, "spec": spec, "oof_summary": {k: {"rho": v["spearman_score_y"]["rho"], "ci": [v["spearman_score_y"]["ci_low"], v["spearman_score_y"]["ci_high"]]} for k, v in rep.items() if not k.startswith("_")}}
    freeze["sha256"] = an.model_hash({k: freeze[k] for k in ("models", "spec")})
    write_json(frozen, freeze)
    print(json.dumps({"B": sel["B"]["features"], "C": sel["C"]["features"], "lambda": {k: wf[k]["lambda"] for k in wf}, "folds": wf["B"]["folds"],
                      "oof_rho": {k: [round(v["spearman_score_y"]["rho"], 4), round(v["spearman_score_y"]["ci_low"], 4), round(v["spearman_score_y"]["ci_high"], 4)] for k, v in rep.items() if not k.startswith("_")},
                      "quintile_rho": {k: v["quintile_monotonic_rho"] for k, v in rep.items() if not k.startswith("_")}, "auc": {k: round(v["auc"], 4) for k, v in rep.items() if not k.startswith("_")},
                      "top10": {k: [v["top10"]["n"], round(v["top10"]["success_rate"], 4)] for k, v in rep.items() if not k.startswith("_")}, "baselines": base,
                      "freeze_sha256": freeze["sha256"]}, indent=1, default=float))


def diff_ci(d: pd.DataFrame, score: np.ndarray, frac: float, rng: np.random.Generator, reps: int = 2000) -> dict[str, Any]:
    """Success(top frac) - success(bottom frac), day-cluster bootstrap CI."""
    top, bot = ca.top_fraction(score, frac), ca.top_fraction(score, frac, bottom=True)
    y = d["y"].to_numpy(dtype=float)
    ids, inv_ = np.unique(d["day"].to_numpy(), return_inverse=True)
    st_ = np.stack([np.bincount(inv_, weights=w, minlength=len(ids)) for w in (top * 1.0, top * y, bot * 1.0, bot * y)], 1)
    point = st_[:, 1].sum() / st_[:, 0].sum() - st_[:, 3].sum() / st_[:, 2].sum()
    pick = rng.integers(0, len(ids), size=(reps, len(ids)))
    b = st_[pick].sum(1)
    boot = b[:, 1] / b[:, 0] - b[:, 3] / b[:, 2]
    return {"frac": frac, "n_top": int(top.sum()), "n_bottom": int(bot.sum()), "top_success": float(y[top].mean()), "bottom_success": float(y[bot].mean()),
            "diff": float(point), "ci_low": float(np.quantile(boot, 0.025)), "ci_high": float(np.quantile(boot, 0.975))}


def buckets(d: pd.DataFrame, pct: np.ndarray) -> list[dict[str, Any]]:
    b = np.minimum((pct // 10).astype(int), 9)
    rows = []
    for k in range(10):
        m = b == k
        s = cs.summarize_outcomes(d["y"].to_numpy()[m]) if m.any() else {"n": 0}
        rows.append({"bucket": f"{k * 10}-{k * 10 + 10}", **{x: s.get(x) for x in ("n", "success_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor")},
                     "descriptive_only": bool(m.sum() < an.MIN_DESCRIPTIVE)})
    return rows


def slice_report(d: pd.DataFrame, scores: dict[str, np.ndarray], mask: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    sub = d[mask].reset_index(drop=True)
    out: dict[str, Any] = {"n": len(sub), "descriptive_only": bool(len(sub) < an.MIN_SELECTION * 10)}
    if len(sub) < 200:
        return out
    for k, v in scores.items():
        sp = ca.cluster_spearman(v[mask], sub["y"].to_numpy(dtype=float), sub["day"].to_numpy(), rng, reps=500)
        top = ca.top_fraction(v[mask], 0.2)
        out[k] = {"spearman": sp["rho"], "ci": [sp["ci_low"], sp["ci_high"]], "auc": ca.auc(v[mask], sub["y"].to_numpy()), "top20": cs.summarize_outcomes(sub["y"].to_numpy()[top])}
    return out


def valid(a: argparse.Namespace) -> None:
    """Phase 10: ONE-SHOT VALID evaluation of the frozen model. Refuses to run twice or if the freeze file was altered."""
    out = outside_repo(a.out)
    res_path = out / "c16_valid_results.json"
    if res_path.exists():
        raise SystemExit("VALID was already evaluated: no second run, no re-fit")
    frozen = json.loads((out / "c16_frozen_model.json").read_text(encoding="utf-8"))
    if not an.verify_frozen(frozen):
        raise SystemExit("Frozen model hash mismatch")
    ds_dir = outside_repo(a.dataset)
    d = load_split(ds_dir, "VALID")
    train = load_split(ds_dir, "TRAIN")
    rng = np.random.default_rng(SEED + 2)
    z = {m: an.linear_predictor(frozen["models"][m], d) for m in ("B", "C")}
    scores = {"A": d["score_cur"].to_numpy(dtype=float), "B": z["B"], "C": z["C"]}
    rep = eval_scores(d, scores, rng)
    train_models = json.loads((out / "c16_train_models.json").read_text(encoding="utf-8"))
    cls = ca.classify(train_models["oof_ranking"], rep)
    grid_a = np.sort(train["score_cur"].to_numpy(dtype=float))
    pct = {"A": 100.0 * (np.searchsorted(grid_a, scores["A"], side="left") + np.searchsorted(grid_a, scores["A"], side="right")) / 2.0 / len(grid_a),
           "B": an.to_score(frozen["models"]["B"]["score_percentile_grid_train"], z["B"]), "C": an.to_score(frozen["models"]["C"]["score_percentile_grid_train"], z["C"])}
    detail = {m: {"buckets_0_100": buckets(d, pct[m]), "top_vs_bottom": {str(f): diff_ci(d, scores[m], f, rng) for f in (0.1, 0.2)}} for m in scores}
    sides = {sd: slice_report(d, scores, (d["side"] == sd).to_numpy(), rng) for sd in ("LONG", "SHORT")}
    symbols = {sy: slice_report(d, scores, (d["symbol"] == sy).to_numpy(), rng) for sy in sorted(d["symbol"].unique())}
    vt = d["vol_tercile"].to_numpy()
    regimes = {"VOL_LOW": slice_report(d, scores, vt == 0, rng), "VOL_NORMAL": slice_report(d, scores, vt == 1, rng), "VOL_HIGH": slice_report(d, scores, vt == 2, rng),
               "TREND": slice_report(d, scores, (d["trend_regime"] == 1).to_numpy(), rng), "RANGE": slice_report(d, scores, (d["trend_regime"] == 0).to_numpy(), rng)}
    diag_t = json.loads((out / "c16_feature_diagnosis.json").read_text(encoding="utf-8"))["features"]
    diag_v = an.feature_diagnosis(d, rng)
    red = json.loads((out / "c16_redundancy.json").read_text(encoding="utf-8"))
    fam = an.family_report(diag_t, red, diag_v)
    res = {"split": "VALID", "one_shot": True, "rows": len(d), "frozen_sha256": frozen["sha256"], "decision_rule_classify": cls,
           "ranking": rep, "score_percentile_buckets_top_bottom": detail, "long_short": sides, "symbols_descriptive": symbols, "regimes": regimes,
           "family_valid_descriptive": fam, "feature_valid_descriptive": {k: {kk: v.get(kk) for kk in ("n", "spearman_y", "spearman_ci", "auc")} for k, v in diag_v.items()},
           "current_universe_u1": {"n": int(d["u1"].sum()), **cs.summarize_outcomes(d.loc[d["u1"], "y"].to_numpy())},
           "all_rows": cs.summarize_outcomes(d["y"].to_numpy()),
           "after_valid_rules": "no feature, weight, threshold or structure change; on failure report MODEL FAILED GENERALIZATION and do not return to TRAIN"}
    write_json(res_path, res)
    print(json.dumps({"decision": cls, "valid_rho": {k: [round(v["spearman_score_y"]["rho"], 4), round(v["spearman_score_y"]["ci_low"], 4), round(v["spearman_score_y"]["ci_high"], 4)]
                                                   for k, v in rep.items() if not k.startswith("_")},
                      "quintile_rho": {k: v["quintile_monotonic_rho"] for k, v in rep.items() if not k.startswith("_")},
                      "auc": {k: round(v["auc"], 4) for k, v in rep.items() if not k.startswith("_")},
                      "top10": {k: [v["top10"]["n"], round(v["top10"]["success_rate"], 4), round(v["top10"]["expectancy_r"], 4)] for k, v in rep.items() if not k.startswith("_")}}, indent=1, default=float))


def _same(a: dict, b: dict) -> bool:
    return all((np.isnan(a[k]) and np.isnan(b[k])) or a[k] == b[k] for k in (*st.ALL_FEATURES, *st.CUTOFF_FIELDS, "atr_abs"))


def audit_job(arg):
    """Independent look-ahead audit of one (split, symbol): row-level timing on ALL rows + FULL vs CUTOFF vs garbage-after-j on a sample."""
    split, root, symbol, csv_path, n_samples = arg
    start, end, _first = (utc(x) for x in BLOCKS[split])
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    rows15 = [r for r in ds.frames[symbol]["15m"].rows if start <= r["time"] < end]
    t = np.array([r["time"] for r in rows15], dtype=np.int64)
    o, h, l, c, v = (np.array([r[k] for r in rows15], dtype=float) for k in ("open", "high", "low", "close", "volume"))
    df = pd.read_csv(csv_path, float_precision="round_trip")
    df = df[df["symbol"] == symbol].reset_index(drop=True)
    j = ((df["at"].to_numpy() - start) // pl.BAR - 1).astype(int)
    viol = {"decision_not_candle_close": int((df["at"].to_numpy() != t[j] + pl.BAR).sum()), "entry_not_next_open": int((df["entry_price"].to_numpy() != o[j + 1]).sum()),
            "cutoff_ne_decision": int((df["feature_cutoff_ts"] != df["at"]).sum()), "candle_close_after_cutoff": int((df["candle_close_ts"] > df["feature_cutoff_ts"]).sum()),
            "swing_confirmed_after_decision": int((df["swing_max_confirmed_ts"] > df["at"]).sum()), "level_known_after_decision": int((df["level_known_ts"] > df["at"]).sum()),
            "source_after_decision": int((df["source_first_ts"] > df["at"]).sum())}
    full = {sd: st.run_series(t, o, h, l, c, v, sd) for sd in pl.SIDES}
    rng = np.random.default_rng(SEED + (0 if split == "TRAIN" else 1))
    picks = rng.choice(len(df), size=min(n_samples, len(df)), replace=False)
    mism_full_csv = mism_cut = mism_garbage = checked = 0
    for i in picks:
        jj, sd = int(j[i]), str(df.loc[i, "side"])
        f = full[sd][jj]
        mism_full_csv += int(not all((np.isnan(f[k]) and np.isnan(df.loc[i, k])) or f[k] == df.loc[i, k] for k in st.ALL_FEATURES))
        cut = st.run_series(t[:jj + 1], o[:jj + 1], h[:jj + 1], l[:jj + 1], c[:jj + 1], v[:jj + 1], sd)[jj]
        mism_cut += int(not _same(f, cut))
        o2, h2, l2, c2 = (x.copy() for x in (o, h, l, c))
        for arr in (o2, h2, l2, c2):
            arr[jj + 1:] = arr[jj + 1:] * 2.5 + 11.0
        mism_garbage += int(not _same(f, st.run_series(t, o2, h2, l2, c2, v, sd)[jj]))
        checked += 1
    return {"split": split, "symbol": symbol, "rows": len(df), "violations": viol, "sampled": checked, "mismatch_full_vs_csv": mism_full_csv,
            "mismatch_full_vs_cutoff": mism_cut, "mismatch_garbage_after_j": mism_garbage}


def audit(a: argparse.Namespace) -> None:
    """Phase 11: independent look-ahead audit. One violation -> C16 = INVALID."""
    out, ds_dir = outside_repo(a.out), outside_repo(a.dataset)
    jobs = [(sp, outside_repo(a.data_2025 if sp == "TRAIN" else a.data_2026), sy, ds_dir / f"c16_{sp.lower()}_features.csv", a.samples)
            for sp in ("TRAIN", "VALID") for sy in pl.SYMBOLS]
    with mp.get_context("fork").Pool(a.workers) as pool:
        res = pool.map(audit_job, jobs, chunksize=1)
    n_viol = sum(sum(r["violations"].values()) for r in res)
    n_mism = sum(r["mismatch_full_vs_csv"] + r["mismatch_full_vs_cutoff"] + r["mismatch_garbage_after_j"] for r in res)
    inv_ = inv.inventory()["features"]
    per_feature = {f: {"family": v["family"], "source_range": f"[decision - {v['max_lookback_bars']} bars, candle close <= decision]", "decision_timestamp": "= candle close of C",
                       "calculation_timestamp": "= feature_cutoff_ts = decision", "max_source_timestamp_le_decision": True} for f, v in inv_.items()}
    man = json.loads((out / "c16_dataset_manifest.json").read_text(encoding="utf-8"))
    order = {n: (out / n).stat().st_mtime for n in ("c16_frozen_model.json", "c16_valid_results.json")}
    order["c16_valid_features.csv"] = (ds_dir / "c16_valid_features.csv").stat().st_mtime
    report = {"status": "PASS" if n_viol == 0 and n_mism == 0 else "FAIL — LOOK-AHEAD", "rows_checked": sum(r["rows"] for r in res), "row_level_violations": n_viol,
              "sampled_recomputations": sum(r["sampled"] for r in res), "sampled_mismatches": n_mism,
              "tests": {"full_vs_cutoff": "features at j recomputed from candles <= j only: bit-identical", "garbage_after_j": "candles after j replaced by garbage: features at j unchanged",
                        "entry": "entry price == open of the candle after the decision candle", "timestamps": "candle_close, swing confirmation and known-level times <= decision"},
              "per_job": res, "per_feature": per_feature, "oos_file_in_dataset": man["oos_file_in_dataset"], "max_ts_in_dataset": man["max_ts_in_dataset"],
              "oos_start": cs.OOS_START, "train_max_at": int(pd.read_csv(ds_dir / "c16_train_features.csv", usecols=["at"])["at"].max()),
              "valid_min_at": int(pd.read_csv(ds_dir / "c16_valid_features.csv", usecols=["at"])["at"].min()),
              "freeze_before_valid_features_before_valid_results": bool(order["c16_frozen_model.json"] < order["c16_valid_features.csv"] <= order["c16_valid_results.json"]),
              "file_mtimes": order}
    if report["oos_file_in_dataset"] or report["train_max_at"] >= cs.VALID_START or report["valid_min_at"] < cs.VALID_START:
        report["status"] = "FAIL — SPLIT/OOS"
    write_json(out / "c16_leakage_audit.json", report)
    print(json.dumps({k: report[k] for k in ("status", "rows_checked", "row_level_violations", "sampled_recomputations", "sampled_mismatches", "freeze_before_valid_features_before_valid_results")}, indent=1))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("manifest", manifest), ("features", features)):
        s = sub.add_parser(name)
        s.add_argument("--data-2025", type=Path, default=Path("/home/claude/work/data3/farmer-data-2025"))
        s.add_argument("--data-2026", type=Path, default=Path("/home/claude/work/c15/data_2026_trainvalid"))
        s.add_argument("--data-oos-manifest", type=Path, default=Path("/home/claude/work/data2/farmer-data/manifest.json"))
        s.add_argument("--c15-dir", type=Path, default=Path("/home/claude/work/c15/dataset"))
        s.add_argument("--output", type=Path, default=Path("/home/claude/work/c16/dataset"))
        s.add_argument("--split", choices=("TRAIN", "VALID"), default="TRAIN")
        s.add_argument("--workers", type=int, default=2)
        s.add_argument("--limit", type=int, default=0)
        s.set_defaults(fn=fn)
    s = sub.add_parser("diagnose")
    s.add_argument("--dataset", type=Path, default=Path("/home/claude/work/c16/dataset"))
    s.add_argument("--out", type=Path, default=Path("/home/claude/work/c16/out"))
    s.set_defaults(fn=diagnose)
    s = sub.add_parser("models")
    s.add_argument("--dataset", type=Path, default=Path("/home/claude/work/c16/dataset"))
    s.add_argument("--out", type=Path, default=Path("/home/claude/work/c16/out"))
    s.set_defaults(fn=models)
    s = sub.add_parser("valid")
    s.add_argument("--dataset", type=Path, default=Path("/home/claude/work/c16/dataset"))
    s.add_argument("--out", type=Path, default=Path("/home/claude/work/c16/out"))
    s.set_defaults(fn=valid)
    s = sub.add_parser("audit")
    s.add_argument("--dataset", type=Path, default=Path("/home/claude/work/c16/dataset"))
    s.add_argument("--out", type=Path, default=Path("/home/claude/work/c16/out"))
    s.add_argument("--data-2025", type=Path, default=Path("/home/claude/work/data3/farmer-data-2025"))
    s.add_argument("--data-2026", type=Path, default=Path("/home/claude/work/c15/data_2026_trainvalid"))
    s.add_argument("--samples", type=int, default=20)
    s.add_argument("--workers", type=int, default=2)
    s.set_defaults(fn=audit)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

"""KAIS FARMER C17 CLI (research only): manifest -> train (+freeze) -> valid (once) -> audit -> oos (once) -> report.
Run through the stubs launcher. No order, no exchange client, no production import. OOS is opened only by `oos`, once, after the freeze."""
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
from app.research import c15_scoring as cs
from app.research.c16 import pipeline as c16pl
from app.research.c17 import analysis as an
from app.research.c17 import hypotheses as hy
from app.research.c17 import leakage as lk
from app.research.c17 import pipeline as pl
from app.research.c17 import walk_forward as wf

REPO = Path(__file__).resolve().parents[1]
SEED = 20261109
SRC = REPO / "backend" / "app" / "research"
CODE_FILES = [SRC / "c17" / n for n in ("events.py", "hypotheses.py", "labels.py", "pipeline.py")] + [SRC / "c16" / "structure.py"]
ROOTS = {"TRAIN": Path("/home/claude/work/data3/farmer-data-2025"), "VALID": Path("/home/claude/work/c15/data_2026_trainvalid"), "OOS": Path("/home/claude/work/data2/farmer-data")}
BLOCK = {"TRAIN": ("2025-03-01", "2025-10-01"), "VALID": ("2026-03-01", "2026-06-01"), "OOS": ("2026-03-01", "2026-10-01")}


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=1, default=float, allow_nan=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data and results must stay outside the repository")
    return path


# ------------------------------------------------------------------------------------------------ build
def job(arg):
    split, symbol, root = arg
    start, end = (an.utc(x) for x in BLOCK[split])
    lo, hi = an.WINDOWS[split]
    if split != "OOS":
        c16pl.guard_manifest(root)
        assert end <= cs.OOS_START
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    mk = bf.build_market(ds, start, end)
    return split, symbol, *pl.run_block(ds, mk, symbol, start, end, lo, hi - pl.BAR)


def build(split: str, workers: int, root: Path | None = None):
    with mp.get_context("fork").Pool(workers) as pool:
        res = pool.map(job, [(split, s, root or ROOTS[split]) for s in pl.SYMBOLS], chunksize=1)
    setups = pd.concat([r[2] for r in res], ignore_index=True).sort_values(["decision_ts", "symbol", "hyp"]).reset_index(drop=True)
    uni = pd.concat([r[3] for r in res], ignore_index=True)
    inv: dict[str, int] = {}
    for r in res:
        for k, n in r[4].items():
            key = f"{r[1]}|{k}"
            inv[key] = inv.get(key, 0) + n
    an.guard_decisions(setups, split)
    an.guard_decisions(uni, split, "at")
    return setups, uni, inv


def hyp_block(setups: pd.DataFrame, uni: pd.DataFrame, hyp: str, rng: np.random.Generator, halves: bool = False) -> dict[str, Any]:
    s = setups[setups["hyp"] == hyp]
    out: dict[str, Any] = {"total": an.summarize(s), "LONG": an.summarize(s[s["side"] == "LONG"]), "SHORT": an.summarize(s[s["side"] == "SHORT"]),
                           "by_symbol": an.slices(s, "symbol") if len(s) else {}, "monthly_setups": an.monthly(s) if len(s) else {},
                           "descriptive_regimes": {"note": "descriptive only; no threshold may be derived from them",
                                                   "vol_tercile(0 low,1 normal,2 high)": an.slices(s.dropna(subset=["vol_tercile"]), "vol_tercile") if len(s) else {},
                                                   "trend_regime(1 trend,0 range)": an.slices(s.dropna(subset=["trend_regime"]), "trend_regime") if len(s) else {}}}
    ul = an.universe_long(uni, setups, hyp)
    out["event_effect_all"] = an.event_effect(ul, rng)
    tot = out["total"]
    out["baselines"] = {"A_normal_direction": {k: tot.get(k) for k in ("resolved", "success_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor")},
                        "B_random": an.random_baseline(uni, int(tot.get("resolved", 0)), tot.get("expectancy_r", 0.0), rng),
                        "C_reverse_direction_diagnostic": an.reverse_diagnostic(s)}
    if halves:
        cut = np.sort(np.unique((uni["at"] // an.DAY).astype(int)))
        cut = cut[len(cut) // 2]
        day_u = (ul["at"] // an.DAY).astype(int)
        out["train_a"] = {"summary": an.summarize(s[(s["decision_ts"] // an.DAY) < cut]), "event_effect": an.event_effect(ul[day_u < cut], rng)}
        out["train_b"] = {"summary": an.summarize(s[(s["decision_ts"] // an.DAY) >= cut]), "event_effect": an.event_effect(ul[day_u >= cut], rng)}
        sa, sb = out["train_a"]["summary"], out["train_b"]["summary"]
        out["a_b_same_sign_expectancy"] = bool(sa.get("expectancy_r", 0) * sb.get("expectancy_r", 0) > 0)
    return out


def split_results(split: str, setups: pd.DataFrame, uni: pd.DataFrame, inv: dict, halves: bool = False) -> dict[str, Any]:
    rng = np.random.default_rng(SEED + {"TRAIN": 0, "VALID": 1, "OOS": 2}[split])
    return {"split": split, "window": [datetime.fromtimestamp(x, timezone.utc).isoformat() for x in an.WINDOWS[split]], "universe_rows": len(uni),
            "setups_total": len(setups), "inventory": inv,
            "hypotheses": {h: hyp_block(setups, uni, h, rng, halves) for h in pl.HYPS}}


# ------------------------------------------------------------------------------------------------ commands
def manifest(a: argparse.Namespace) -> None:
    out = outside_repo(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for split in ("TRAIN", "VALID"):
        c16pl.guard_manifest(ROOTS[split])
        for arc in read_json(ROOTS[split] / "manifest.json")["archives"]:
            files.append(c16pl.manifest_entry(ROOTS[split], arc, split))
    oos = [{"split": "OOS", "status": "LOCKED_NOT_READ", "symbol": x["symbol"], "kind": x["kind"], "month": x["month"], "path": x["path"]}
           for x in read_json(ROOTS["OOS"] / "manifest.json")["archives"] if str(x["month"]) >= "2026-06"]
    res = {"files": files, "oos_listing_only": oos, "counts": {"TRAIN": sum(f["split"] == "TRAIN" for f in files), "VALID": sum(f["split"] == "VALID" for f in files), "OOS_listed": len(oos)},
           "all_hashes_match_download_manifest": all(f["sha256_matches_download_manifest"] for f in files),
           "max_ts_in_train_valid_files": max(f["max_ts"] for f in files if f["max_ts"]), "oos_start": cs.OOS_START,
           "oos_file_in_train_valid_dataset": any("2026-06" in f["path"] or (f["max_ts"] or 0) >= cs.OOS_START for f in files),
           "windows": {k: [datetime.fromtimestamp(x, timezone.utc).date().isoformat() for x in v] for k, v in an.WINDOWS.items()},
           "oos_status": "LOCKED until the final one-shot step"}
    write_json(out / "c17_dataset_manifest.json", res)
    print(json.dumps({k: v for k, v in res.items() if k not in ("files", "oos_listing_only")}, indent=1))


def train(a: argparse.Namespace) -> None:
    out, ds_dir = outside_repo(a.out), outside_repo(a.dataset)
    ds_dir.mkdir(parents=True, exist_ok=True)
    if (out / "c17_model_freeze.json").exists():
        raise SystemExit("Already frozen: TRAIN is not re-run after the freeze")
    setups, uni, inv = build("TRAIN", a.workers)
    setups.to_csv(out / "c17_train_setups.csv", index=False)
    uni.to_csv(ds_dir / "c17_train_universe.csv", index=False)
    res = split_results("TRAIN", setups, uni, inv, halves=True)
    fs = wf.folds(an.WINDOWS["TRAIN"][0] // an.DAY, an.WINDOWS["TRAIN"][1] // an.DAY - 1)
    wfr = {"rule": "expanding window, 2-day embargo, no parameter is fitted; every fold reports the past-only train part and the test part. VALID untouched.", "folds": fs,
           "hypotheses": {h: wf.fold_report(setups[setups["hyp"] == h], fs) for h in pl.HYPS}}
    for h in pl.HYPS:
        wfr["hypotheses"][h] = {"folds": wfr["hypotheses"][h], "stable_2_of_3_positive": wf.stable(wfr["hypotheses"][h])}
    write_json(out / "c17_hypothesis_definitions.json", hy.definitions())
    write_json(out / "c17_event_inventory.json", {"split": "TRAIN", "counts": inv, "setups_per_hypothesis": setups["hyp"].value_counts().to_dict()})
    write_json(out / "c17_train_results.json", res)
    write_json(out / "c17_train_walk_forward.json", wfr)
    freeze = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "definition_sha256": hy.definition_hash(), "code_sha256": an.file_hash(CODE_FILES),
              "train_results_sha256": an.file_hash([out / "c17_train_results.json", out / "c17_train_walk_forward.json"]),
              "walk_forward_stable": {h: wfr["hypotheses"][h]["stable_2_of_3_positive"] for h in pl.HYPS}, "rules": hy.DECISION_RULES,
              "after_valid": "no threshold, feature, setup, LONG/SHORT rule or symbol filter may change", "valid_opened": False, "oos_opened": False}
    write_json(out / "c17_model_freeze.json", freeze)
    (out / "c17_model_freeze.sha256").write_text(an.file_hash([out / "c17_model_freeze.json"]) + "  c17_model_freeze.json\n", encoding="utf-8")
    brief = {h: {"total": {k: res["hypotheses"][h]["total"].get(k) for k in ("setups", "resolved", "success_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor", "max_drawdown_r")},
                 "LONG_SHORT_n": [res["hypotheses"][h]["LONG"].get("resolved"), res["hypotheses"][h]["SHORT"].get("resolved")], "wf_stable": wfr["hypotheses"][h]["stable_2_of_3_positive"],
                 "folds_exp": [r["test"].get("expectancy_r") for r in wfr["hypotheses"][h]["folds"]], "folds_n": [r["test"].get("resolved") for r in wfr["hypotheses"][h]["folds"]],
                 "ab": [res["hypotheses"][h]["train_a"]["summary"].get("expectancy_r"), res["hypotheses"][h]["train_b"]["summary"].get("expectancy_r")],
                 "random_p": res["hypotheses"][h]["baselines"]["B_random"].get("share_random_runs_with_expectancy_ge_observed")} for h in pl.HYPS}
    print(json.dumps({"inventory_sample": {k: v for k, v in list(inv.items())[:0]}, "hyp": brief, "freeze_sha256": (out / "c17_model_freeze.sha256").read_text().split()[0]}, indent=1, default=float))


def verify_freeze(out: Path) -> dict:
    fr = read_json(out / "c17_model_freeze.json")
    sha = (out / "c17_model_freeze.sha256").read_text().split()[0]
    if sha != an.file_hash([out / "c17_model_freeze.json"]) or fr["definition_sha256"] != hy.definition_hash() or fr["code_sha256"] != an.file_hash(CODE_FILES):
        raise SystemExit("Freeze verification FAILED: definitions or detection code changed after the freeze")
    return fr


def valid(a: argparse.Namespace) -> None:
    out, ds_dir = outside_repo(a.out), outside_repo(a.dataset)
    fr = verify_freeze(out)
    if (out / "c17_valid_results.json").exists():
        raise SystemExit("VALID already opened: no second run")
    setups, uni, inv = build("VALID", a.workers)
    setups.to_csv(out / "c17_valid_setups.csv", index=False)
    uni.to_csv(ds_dir / "c17_valid_universe.csv", index=False)
    res = split_results("VALID", setups, uni, inv)
    res["frozen_definition_sha256"] = fr["definition_sha256"]
    res["rules_after_valid"] = "VALID is a result only; nothing was changed after seeing it"
    write_json(out / "c17_valid_results.json", res)
    for h in pl.HYPS:
        t = res["hypotheses"][h]["total"]
        print(h[:2], {k: t.get(k) for k in ("resolved", "success_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor", "max_drawdown_r")}, "random_p",
              res["hypotheses"][h]["baselines"]["B_random"].get("share_random_runs_with_expectancy_ge_observed"))


def audit_job(arg):
    split, symbol, root, csv_path, n_samples = arg
    start, end = (an.utc(x) for x in BLOCK[split])
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    arr = pl.arrays_from(ds, symbol, start, end)
    t = arr[0]
    df = pd.read_csv(csv_path)
    df = df[df["symbol"] == symbol].reset_index(drop=True)
    viol = lk.timing_violations(df) if len(df) else {}
    rng = np.random.default_rng(SEED + len(symbol))
    full = {s: pl.detect(*arr, s)[0] for s in pl.SIDES}
    js = sorted({int((r - start) // pl.BAR) - 1 for r in df["decision_ts"]})
    picks = [js[i] for i in rng.choice(len(js), size=min(n_samples, len(js)), replace=False)] if js else []
    cut_bad = poison_bad = 0
    for j in picks:
        o2, h2, l2, c2, v2 = lk.poison(*arr, j, rng)
        for side in pl.SIDES:
            ref = lk.keyed(lk.upto(full[side], j))
            cut_bad += int(lk.keyed(pl.detect(*(x[:j + 1] for x in arr), side)[0]) != ref)
            poison_bad += int(lk.keyed(lk.upto(pl.detect(t, o2, h2, l2, c2, v2, side)[0], j)) != ref)
    return {"split": split, "symbol": symbol, "setups": len(df), "timing_violations": viol, "sampled_decisions": len(picks), "cut_mismatches": cut_bad, "poison_mismatches": poison_bad}


def audit_split(split: str, root: Path, csv_path: Path, samples: int, workers: int) -> dict[str, Any]:
    with mp.get_context("fork").Pool(workers) as pool:
        res = pool.map(audit_job, [(split, s, root, csv_path, samples) for s in pl.SYMBOLS], chunksize=1)
    nv = sum(sum(r["timing_violations"].values()) for r in res)
    nm = sum(r["cut_mismatches"] + r["poison_mismatches"] for r in res)
    return {"split": split, "setups_checked": sum(r["setups"] for r in res), "timing_violations": nv, "sampled_decisions": sum(r["sampled_decisions"] for r in res),
            "cut_and_poison_mismatches": nm, "status": "PASS" if nv == 0 and nm == 0 else "FAIL — LOOK-AHEAD", "per_symbol": res}


def audit(a: argparse.Namespace) -> None:
    out = outside_repo(a.out)
    rep = {"tests": {"cut_at_decision": "dataset truncated at the decision candle reproduces the same setups", "future_poisoning": "OHLCV after the decision replaced by random values: same setups",
                     "timestamps": "level_known<=decision, setup<=decision, confirmation<=decision, breakout<retest<confirmation<entry, entry=confirmation+15m, one entry per event"},
           "splits": {sp: audit_split(sp, ROOTS[sp], out / f"c17_{sp.lower()}_setups.csv", a.samples, a.workers) for sp in ("TRAIN", "VALID")}}
    rep["status"] = "PASS" if all(v["status"] == "PASS" for v in rep["splits"].values()) else "FAIL — LOOK-AHEAD"
    write_json(out / "c17_leakage_audit.json", rep)
    print(json.dumps({"status": rep["status"], **{k: {x: v[x] for x in ("setups_checked", "timing_violations", "sampled_decisions", "cut_and_poison_mismatches")} for k, v in rep["splits"].items()}}, indent=1))


def oos(a: argparse.Namespace) -> None:
    """ONE-SHOT OOS: only after the freeze, VALID and a PASSED TRAIN/VALID leakage audit. Never run twice."""
    out = outside_repo(a.out)
    fr = verify_freeze(out)
    if (out / "c17_oos_results.json").exists():
        raise SystemExit("OOS already opened: research is closed")
    if not (out / "c17_valid_results.json").exists() or read_json(out / "c17_leakage_audit.json")["status"] != "PASS":
        raise SystemExit("OOS needs VALID results and a passing leakage audit first")
    setups, uni, inv = build("OOS", a.workers)
    setups.to_csv(out / "c17_oos_setups.csv", index=False)
    res = split_results("OOS", setups, uni, inv)
    tr_wf = read_json(out / "c17_train_walk_forward.json")
    res["verdicts"] = {h: an.verdict(res["hypotheses"][h]["total"], tr_wf["hypotheses"][h]["stable_2_of_3_positive"]) for h in pl.HYPS}
    res["frozen_definition_sha256"] = fr["definition_sha256"]
    res["audit"] = audit_split("OOS", ROOTS["OOS"], out / "c17_oos_setups.csv", a.samples, a.workers)
    write_json(out / "c17_oos_results.json", res)
    for h in pl.HYPS:
        t = res["hypotheses"][h]["total"]
        print(h[:2], res["verdicts"][h]["verdict"], {k: t.get(k) for k in ("resolved", "success_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor", "max_drawdown_r")})
    print("OOS audit", res["audit"]["status"])


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("manifest", manifest), ("train", train), ("valid", valid), ("audit", audit), ("oos", oos)):
        s = sub.add_parser(name)
        s.add_argument("--out", type=Path, default=Path("/home/claude/work/c17/out"))
        s.add_argument("--dataset", type=Path, default=Path("/home/claude/work/c17/dataset"))
        s.add_argument("--workers", type=int, default=2)
        s.add_argument("--samples", type=int, default=10)
        s.set_defaults(fn=fn)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

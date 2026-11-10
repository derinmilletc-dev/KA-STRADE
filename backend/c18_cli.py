"""C18 CLI (research only; no orders, no production code). Stages: build -> audit -> train (writes the freeze) -> valid (one shot).
Pre-registration: backend/studies/c18-turev-veri-onkayit.md. The VALID command refuses to run twice or without a matching freeze."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from app.research.c18 import features as ft
from app.research.c18 import leakage, pipeline, stats

HERE = Path(__file__).parent
PREREG = HERE / "studies" / "c18-turev-veri-onkayit.md"
CODE = [HERE / "app/research/c18" / f for f in ("features.py", "pipeline.py", "stats.py", "leakage.py")] + [Path(__file__)]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
ROOTS = {"TRAIN": ("c16_train_features.csv", "data3/farmer-data-2025"), "VALID": ("c16_valid_features.csv", "c15/data_2026_trainvalid")}
SEED = 20261109


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def jdump(o, p: Path) -> None:
    p.write_text(json.dumps(o, indent=1, default=float), encoding="utf-8")


def definition() -> dict:
    return {"prereg_sha256": sha(PREREG), "BASE": ft.BASE, "G1": ft.G1, "G2": ft.G2, "lambda": stats.LAM, "bootstrap_B": stats.B, "seed": SEED,
            "code": {c.name: sha(c) for c in CODE}}


def cmd_build(a) -> None:
    out, work = Path(a.out), Path(a.work)
    for split, (csv, kl) in ROOTS.items():
        d = pipeline.build(work / "c16/dataset" / csv, Path(a.data) / "raw", work / kl / "klines")
        d.to_csv(out / f"c18_{split.lower()}_dataset.csv", index=False)
        g1, g2 = np.isfinite(d[ft.G1].to_numpy(float)).all(1).mean(), np.isfinite(d[ft.G2].to_numpy(float)).all(1).mean()
        print(split, d.shape, f"G1 complete {g1:.4f} G2 complete {g2:.4f}")


def cmd_audit(a) -> None:
    out, work = Path(a.out), Path(a.work)
    res = []
    for split, (csv, kl) in ROOTS.items():
        base = pd.read_csv(work / "c16/dataset" / csv)
        longs = base[base["side"] == "LONG"]
        for sym in SYMBOLS:
            m = ft.load_metrics(Path(a.data) / "raw", sym)
            fts, fr = ft.load_funding(Path(a.data) / "raw", sym)
            qv = pipeline.quote_volume_24h(work / kl / "klines", sym)
            g = longs[longs["symbol"] == sym].sort_values("at")
            at = g["at"].to_numpy(np.int64)
            for q in (0.35, 0.7):
                res.append({"split": split, "symbol": sym, **leakage.audit(m, fts, fr, qv, at, g["atr_pct"].to_numpy(float), np.sign(g["roc16_atr"].to_numpy(float)), int(at[int(len(at) * q)]))})
    ok = all(r["passed"] for r in res)
    jdump({"passed": ok, "checks": res}, out / "c18_leakage_audit.json")
    print("leakage audit:", "PASS" if ok else "FAIL", len(res), "checks")
    if not ok:
        raise SystemExit("leakage audit failed: the study stops (pre-registration section 5)")


def groups(target: str) -> dict:
    return {"G1": (ft.BASE, ft.G1), "G2": (ft.BASE, ft.G2), "G2_over_G1": (ft.BASE + ft.G1, ft.G2)}


def cmd_train(a) -> None:
    out = Path(a.out)
    if not (out / "c18_leakage_audit.json").exists() or not json.loads((out / "c18_leakage_audit.json").read_text())["passed"]:
        raise SystemExit("run `audit` first and it must pass")
    rng = np.random.default_rng(SEED)
    df = pd.read_csv(out / "c18_train_dataset.csv")
    res: dict = {"definition": definition(), "targets": {}}
    for target in ("direction", "move"):
        d = stats.prep(df, target)
        t: dict = {"rows": len(d), "base_rate": float(d["yt"].mean()), "scan": {}, "compare": {}}
        t["scan"] = stats.scan(d, ft.G1 + ft.G2, rng)
        for name, (base, extra) in groups(target).items():
            t["compare"][name] = stats.compare_wf(d, base, extra, rng)
        res["targets"][target] = t
        print(target, {k: (round(v["improvement"], 5), [round(x, 5) for x in v["ci95"]], v["folds_positive"]) for k, v in t["compare"].items()})
    jdump(res, out / "c18_train_results.json")
    freeze = {"definition": res["definition"], "train_results_sha256": sha(out / "c18_train_results.json"), "train_dataset_sha256": sha(out / "c18_train_dataset.csv")}
    jdump(freeze, out / "c18_model_freeze.json")
    (out / "c18_model_freeze.sha256").write_text(sha(out / "c18_model_freeze.json") + "\n")
    print("freeze:", (out / "c18_model_freeze.sha256").read_text().strip())


def cmd_valid(a) -> None:
    out = Path(a.out)
    marker = out / "c18_valid_RAN"
    if marker.exists():
        raise SystemExit("VALID was already run once; no second run (pre-registration section 6)")
    fz = json.loads((out / "c18_model_freeze.json").read_text())
    if sha(out / "c18_model_freeze.json") != (out / "c18_model_freeze.sha256").read_text().strip() or fz["definition"] != definition() \
            or fz["train_results_sha256"] != sha(out / "c18_train_results.json"):
        raise SystemExit("freeze mismatch: definition, code or TRAIN results changed after the freeze")
    marker.write_text("ran\n")
    rng = np.random.default_rng(SEED + 1)
    tr, va = pd.read_csv(out / "c18_train_dataset.csv"), pd.read_csv(out / "c18_valid_dataset.csv")
    if int(va["at"].min()) < 1767225600 or int(va["at"].max()) >= pipeline.OOS_START:
        raise SystemExit("VALID window violated")
    res: dict = {"targets": {}}
    for target in ("direction", "move"):
        dt, dv = stats.prep(tr, target), stats.prep(va, target)
        res["targets"][target] = {name: {k: v for k, v in stats.valid_check(dt, dv, b, e, rng).items() if k != "models"} for name, (b, e) in groups(target).items()}
        print(target, {k: (round(v["improvement"], 5), [round(x, 5) for x in v["ci975"]]) for k, v in res["targets"][target].items()})
    jdump(res, out / "c18_valid_results.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "audit", "train", "valid"])
    ap.add_argument("--out", default="/home/claude/work/c18/out")
    ap.add_argument("--data", default="/home/claude/work/c18/data")
    ap.add_argument("--work", default="/home/claude/work")
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    {"build": cmd_build, "audit": cmd_audit, "train": cmd_train, "valid": cmd_valid}[a.cmd](a)

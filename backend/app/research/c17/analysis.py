"""C17 analysis (research only): split windows and guards, setup metrics, TRAIN-A/B stability, baselines, verdict. Statistics reuse C15/C16 code."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.research import c15_analysis as ca
from app.research import c15_scoring as cs
from app.research.c17.hypotheses import DECISION_RULES

DAY = cs.DAY


def utc(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# decision_ts windows [from, to): the real usable data of each split (spec section 1)
WINDOWS = {"TRAIN": (utc("2025-04-13"), utc("2025-10-01")), "VALID": (utc("2026-04-13"), utc("2026-06-01")), "OOS": (utc("2026-06-01"), utc("2026-10-01"))}
MIN_N, MIN_DESCRIPTIVE = DECISION_RULES["min_n"], 30


def guard_decisions(df: pd.DataFrame, split: str, col: str = "decision_ts") -> None:
    if len(df) == 0:
        return
    lo, hi = WINDOWS[split]
    if df[col].min() < lo or df[col].max() >= hi:
        raise ValueError(f"{split}: decision outside its window")
    if split != "OOS" and df[col].max() >= cs.OOS_START:
        raise ValueError("OOS timestamp in a TRAIN/VALID frame")


def file_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    """Metrics of one set of setups. Unresolved outcomes (no +-2 ATR touch before the data ends) are dropped from the rates and counted."""
    n_all = len(df)
    d = df[df["y"].notna()].sort_values(["decision_ts", "symbol"])
    out: dict[str, Any] = {"setups": n_all, "resolved": len(d), "unresolved": n_all - len(d)}
    if len(d) == 0:
        return out
    y = d["y"].to_numpy(dtype=int)
    out.update(cs.summarize_outcomes(y))
    out["max_drawdown_r"] = cs.max_drawdown_r(np.where(y == 1, 1.0, -1.0))
    out["median_hold_candles"] = float(d["hold"].median())
    out["mean_mfe_r"], out["mean_mae_r"] = float(d["mfe_r"].mean()), float(d["mae_r"].mean())
    net = np.where(y == 1, 1.0, -1.0) - d["cost_r"].to_numpy(dtype=float)
    out["net_expectancy_r_fee5bps_funding"] = float(np.nanmean(net))
    y1 = d["y1"].dropna().to_numpy(dtype=int)
    out["first_touch_plus1_share"] = float(y1.mean()) if len(y1) else None
    out["first_touch_minus1_share"] = float(1 - y1.mean()) if len(y1) else None
    out["descriptive_only"] = bool(len(d) < MIN_DESCRIPTIVE)
    return out


def slices(df: pd.DataFrame, by: str) -> dict[str, Any]:
    return {str(k): summarize(g) for k, g in df.groupby(by)}


def monthly(df: pd.DataFrame) -> dict[str, int]:
    m = pd.to_datetime(df["decision_ts"], unit="s", utc=True).dt.strftime("%Y-%m")
    return {k: int(v) for k, v in m.value_counts().sort_index().items()}


def cohen_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def universe_long(uni: pd.DataFrame, setups: pd.DataFrame, hyp: str) -> pd.DataFrame:
    """Every (symbol, decision, side) with its label; `event` = a completed setup of `hyp` on that side at that decision."""
    parts = []
    for side in ("LONG", "SHORT"):
        key = set(zip(setups.loc[(setups["hyp"] == hyp) & (setups["side"] == side), "symbol"], setups.loc[(setups["hyp"] == hyp) & (setups["side"] == side), "decision_ts"]))
        u = uni[["symbol", "at", f"y_{side.lower()}"]].rename(columns={f"y_{side.lower()}": "y"}).copy()
        u["side"] = side
        u["event"] = [(s, a) in key for s, a in zip(u["symbol"], u["at"])]
        parts.append(u)
    out = pd.concat(parts, ignore_index=True)
    out = out[out["y"].notna()].copy()
    out["day"] = (out["at"] // DAY).astype(int)
    return out


def event_effect(ul: pd.DataFrame, rng: np.random.Generator) -> dict[str, Any]:
    """Does the outcome distribution after the event differ from the same-side universe? Spearman(event, y) with day-cluster CI, effect size h, success difference."""
    ev, rest = ul[ul["event"]], ul[~ul["event"]]
    if len(ev) < 10:
        return {"n_event": len(ev), "n_rest": len(rest), "status": "TOO_FEW"}
    sp = ca.cluster_spearman(ul["event"].to_numpy(dtype=float), ul["y"].to_numpy(dtype=float), ul["day"].to_numpy(), rng, reps=1000)
    return {"n_event": len(ev), "n_rest": len(rest), "success_event": float(ev["y"].mean()), "success_rest": float(rest["y"].mean()),
            "diff": float(ev["y"].mean() - rest["y"].mean()), "cohen_h": cohen_h(float(ev["y"].mean()), float(rest["y"].mean())),
            "spearman": sp["rho"], "spearman_ci": [sp["ci_low"], sp["ci_high"]], "p_cluster": sp["p"], "descriptive_only": bool(len(ev) < MIN_DESCRIPTIVE)}


def halves_of(days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ds = np.sort(np.unique(days))
    cut = ds[len(ds) // 2]
    return days < cut, days >= cut


def random_baseline(uni: pd.DataFrame, n: int, observed_expectancy: float, rng: np.random.Generator, reps: int = 500) -> dict[str, Any]:
    """N-matched random entries: random decision (symbol, time) and random side from the same universe, fixed seed."""
    u = uni[["y_long", "y_short"]].to_numpy(dtype=float)
    if n == 0 or len(u) == 0:
        return {}
    exp, succ = [], []
    for _ in range(reps):
        idx = rng.integers(0, len(u), n)
        side = rng.integers(0, 2, n)
        y = u[idx, side]
        y = y[np.isfinite(y)]
        succ.append(y.mean())
        exp.append(2 * y.mean() - 1)
    exp, succ = np.array(exp), np.array(succ)
    return {"n": n, "reps": reps, "mean_success": float(succ.mean()), "mean_expectancy_r": float(exp.mean()), "p05_expectancy_r": float(np.quantile(exp, 0.05)),
            "p95_expectancy_r": float(np.quantile(exp, 0.95)), "share_random_runs_with_expectancy_ge_observed": float((exp >= observed_expectancy).mean())}


def reverse_diagnostic(df: pd.DataFrame) -> dict[str, Any]:
    d = df[df["y_reverse"].notna()]
    if len(d) == 0:
        return {}
    s = cs.summarize_outcomes(d["y_reverse"].to_numpy(dtype=int))
    return {"note": "DIAGNOSTIC ONLY - never a production rule", "n": s["n"], "success_rate": s["success_rate"], "ci_low": s["ci_low"], "ci_high": s["ci_high"], "expectancy_r": s["expectancy_r"]}


def verdict(oos: dict[str, Any], wf_stable: bool) -> dict[str, Any]:
    """PASS / FAIL / INCONCLUSIVE per the locked rule (section 15)."""
    n = oos.get("resolved", 0)
    if n < MIN_N:
        return {"verdict": "INCONCLUSIVE", "reason": f"OOS N={n} < {MIN_N}"}
    checks = {"wilson_lower_gt_50pct": oos["ci_low"] > 0.5, "expectancy_gt_0.05R": oos["expectancy_r"] > 0.05, "pf_ge_1.10": oos["profit_factor"] >= 1.10,
              "walk_forward_stable": bool(wf_stable)}
    ok = all(checks.values())
    return {"verdict": "PASS" if ok else "FAIL", "checks": checks, "label": "PROMISING — REQUIRES SEPARATE VALIDATION" if ok else None}

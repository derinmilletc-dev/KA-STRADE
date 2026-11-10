"""C18 statistics (research only): single-feature scan, walk-forward baseline-vs-extended comparison, frozen VALID check.
Fixed choices (pre-registered, never tuned): ridge lambda = 100, walk-forward = the C16 expanding window with a 2-day embargo,
complete rows only (no imputation), bootstrap over days (B = 2000)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.research import c15_analysis as ca
from app.research.c16 import analysis as an
from app.research.c18 import features as ft

LAM = 100.0
B = 2000
DAY = 86400
SIGNED_ALL = ft.SIGNED_G + ft.SIGNED_BASE


def prep(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """direction: resolved rows of both sides (features already side-oriented). move: LONG rows, every signed feature as |x| (direction-free)."""
    if target == "direction":
        d = df[df["y"].notna()].copy()
        d["yt"] = d["y"].astype(int)
    else:
        d = df[df["side"] == "LONG"].copy()
        d[SIGNED_ALL] = d[SIGNED_ALL].abs()
        d["yt"] = d["y_move"].astype(int)
    d["day"] = (d["at"] // DAY).astype(int)
    return d.reset_index(drop=True)


def _std(x: np.ndarray, mu=None, sd=None):
    mu = x.mean(0) if mu is None else mu
    sd = (x.std(0) + 1e-12) if sd is None else sd
    return (x - mu) / sd, mu, sd


def fit(d: pd.DataFrame, feats: list[str]) -> dict:
    x, mu, sd = _std(d[feats].to_numpy(dtype=float))
    w, b = ca._logistic_ridge(x, d["yt"].to_numpy(dtype=float), LAM)
    return {"features": feats, "mean": mu.tolist(), "std": sd.tolist(), "coef": w.tolist(), "intercept": float(b), "lambda": LAM}


def predict(m: dict, d: pd.DataFrame) -> np.ndarray:
    x, *_ = _std(d[m["features"]].to_numpy(dtype=float), np.array(m["mean"]), np.array(m["std"]))
    return 1 / (1 + np.exp(-(x @ np.array(m["coef"]) + m["intercept"])))


def logloss_rows(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def day_bootstrap(diff: np.ndarray, days: np.ndarray, rng: np.random.Generator, alpha: float = 0.05) -> tuple[float, float]:
    """95% interval of the mean per-row improvement, resampling whole days (rows of one day are correlated)."""
    u, inv = np.unique(days, return_inverse=True)
    s, n = np.bincount(inv, weights=diff), np.bincount(inv)
    idx = rng.integers(0, len(u), size=(B, len(u)))
    est = s[idx].sum(1) / n[idx].sum(1)
    return float(np.quantile(est, alpha / 2)), float(np.quantile(est, 1 - alpha / 2))


def complete(d: pd.DataFrame, feats: list[str]) -> pd.DataFrame:
    return d[np.isfinite(d[feats].to_numpy(dtype=float)).all(1)].reset_index(drop=True)


def compare_wf(d: pd.DataFrame, base: list[str], extra: list[str], rng: np.random.Generator) -> dict:
    """Walk-forward on identical complete rows: baseline vs baseline+extra. Improvement = baseline logloss - extended logloss (>0 = better)."""
    c = complete(d, base + extra)
    y = c["yt"].to_numpy(dtype=float)
    folds = an.wf_folds(c)
    pb, pe = np.full(len(c), np.nan), np.full(len(c), np.nan)
    per = []
    for tr, te in folds:
        if tr.sum() < 200 or te.sum() < 50:
            continue
        pb[te], pe[te] = predict(fit(c[tr], base), c[te]), predict(fit(c[tr], base + extra), c[te])
        diff = logloss_rows(pb[te], y[te]) - logloss_rows(pe[te], y[te])
        per.append({"test_rows": int(te.sum()), "improvement": float(diff.mean()), "train_rows": int(tr.sum())})
    ok = np.isfinite(pb)
    diff = logloss_rows(pb[ok], y[ok]) - logloss_rows(pe[ok], y[ok])
    lo, hi = day_bootstrap(diff, c["day"].to_numpy()[ok], rng)
    return {"rows_complete": len(c), "rows_scored": int(ok.sum()), "base_rate": float(y.mean()), "n_extra": len(extra),
            "improvement": float(diff.mean()), "ci95": [lo, hi], "folds": per, "folds_positive": int(sum(f["improvement"] > 0 for f in per)),
            "auc_base": float(ca.auc(pb[ok], y[ok])), "auc_extended": float(ca.auc(pe[ok], y[ok])),
            "lift_top10": _lift(pe[ok], y[ok]), "lift_top10_base": _lift(pb[ok], y[ok])}


def _lift(p: np.ndarray, y: np.ndarray) -> float:
    q = np.quantile(p, 0.9)
    return float(y[p >= q].mean() / y.mean())


def scan(d: pd.DataFrame, feats: list[str], rng: np.random.Generator) -> dict:
    """Descriptive single-feature scan with day-cluster Spearman, Holm and BH-FDR; TRAIN-A/B sign stability."""
    y, days = d["yt"].to_numpy(dtype=float), d["day"].to_numpy()
    ud = np.sort(np.unique(days))
    half = days >= ud[len(ud) // 2]
    out, ps = {}, []
    for f in feats:
        x = d[f].to_numpy(dtype=float)
        ok = np.isfinite(x)
        if ok.sum() < 200 or np.std(x[ok]) == 0:
            out[f] = {"n": int(ok.sum()), "status": "DEGENERATE"}
            continue
        sp = ca.cluster_spearman(x, y, days, rng, reps=1000)
        ra = _rho(x[ok & ~half], y[ok & ~half])
        rb = _rho(x[ok & half], y[ok & half])
        out[f] = {"n": int(ok.sum()), "rho": sp["rho"], "p": sp["p"], "auc": float(ca.auc(x[ok], y[ok])), "rho_a": ra, "rho_b": rb,
                  "sign_flip": bool(ra * rb < 0), "stable": bool(ra * rb > 0 and min(abs(ra), abs(rb)) >= 0.005)}
        ps.append(f)
    p = [out[f]["p"] if np.isfinite(out[f]["p"]) else 1.0 for f in ps]
    for f, h, q in zip(ps, ca.holm(p), an.bh_fdr(p)):
        out[f]["p_holm"], out[f]["q_bh"], out[f]["holm_significant"] = h, q, bool(h < 0.05)
    return out


def _rho(x: np.ndarray, y: np.ndarray) -> float:
    return float(ca.cs.spearman(x, y)) if len(x) > 50 and np.std(x) > 0 else float("nan")


def valid_check(train: pd.DataFrame, valid: pd.DataFrame, base: list[str], extra: list[str], rng: np.random.Generator) -> dict:
    """Frozen model: fit once on all complete TRAIN rows, score VALID once. Improvement = baseline logloss - extended logloss."""
    tr, va = complete(train, base + extra), complete(valid, base + extra)
    mb, me = fit(tr, base), fit(tr, base + extra)
    y = va["yt"].to_numpy(dtype=float)
    pb, pe = predict(mb, va), predict(me, va)
    diff = logloss_rows(pb, y) - logloss_rows(pe, y)
    lo, hi = day_bootstrap(diff, va["day"].to_numpy(), rng)
    lo2, hi2 = day_bootstrap(diff, va["day"].to_numpy(), rng, alpha=0.025)      # two groups => Holm-safe 97.5% interval
    return {"ci975": [lo2, hi2], "train_rows": len(tr), "valid_rows": len(va), "improvement": float(diff.mean()), "ci95": [lo, hi],
            "auc_base": float(ca.auc(pb, y)), "auc_extended": float(ca.auc(pe, y)), "lift_top10": _lift(pe, y), "lift_top10_base": _lift(pb, y),
            "models": {"base": mb, "extended": me}}

"""KAIS FARMER C16 analysis helpers (research only): TRAIN diagnosis, stability, redundancy, candidate scores, walk-forward, freeze.
Statistics are reused from the C15 modules (day-cluster Spearman, Wilson, Holm, ridge-logistic, ranking report)."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from app.research import c15_analysis as ca
from app.research import c15_scoring as cs
from app.research.c16 import structure as st

MIN_DESCRIPTIVE, MIN_SELECTION = 30, 50
REDUNDANT, VERY_REDUNDANT = 0.70, 0.85
RIDGE_GRID = (1.0, 10.0, 100.0, 1000.0)
EMBARGO_DAYS = 2
STABLE_MIN_RHO = 0.005        # noise floor of the half-split Spearman; below it a feature is indistinguishable from zero

# Candidate feature order = the fixed technical order of `structure.FAMILIES` (never an outcome ranking). A feature is eligible only if it is
# STABLE on TRAIN (same Spearman sign in TRAIN-A and TRAIN-B, both |rho| >= STABLE_MIN_RHO, N >= 50 in each half) and not redundant (|rho| < 0.70)
# with a feature already kept. B = structure families (A, B) only, C = all families with at most 3 per family.
B_FAMILIES = ("A_STRUCTURE", "B_BREAKOUT_RETEST")
CAP_B, CAP_C, CAP_C_PER_FAMILY = 5, 9, 3
HARD_GATE = "NONE (C16 ranks quality only; the existing Farmer gates are reported separately and never mixed into the score)"


def family_of() -> dict[str, str]:
    return {f: fam for fam, fs in st.FAMILIES.items() for f in fs}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with a resolved label only. `direction` = the side being scored (C15 helpers expect that name)."""
    d = df.rename(columns={"direction": "cur_direction"}).copy()
    d = d[d["y"].notna()].copy()
    d["y"] = d["y"].astype(int)
    d["direction"] = d["side"]
    d["day"] = (d["at"] // cs.DAY).astype(int)
    d["ret8_d"] = d["ret8_d"].astype(float)
    d["u1"] = d["cur_direction"] == d["side"]
    return d.reset_index(drop=True)


def halves(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    days = np.sort(d["day"].unique())
    cut = days[len(days) // 2]
    return (d["day"].to_numpy() < cut), (d["day"].to_numpy() >= cut)


def _edges(x: np.ndarray) -> np.ndarray:
    u = np.unique(x)
    return u if len(u) <= 8 else np.unique(np.quantile(x, [0.2, 0.4, 0.6, 0.8]))


def _rho(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x)
    return cs.spearman(x[ok], y[ok].astype(float)) if ok.sum() > 10 and np.std(x[ok]) > 0 else float("nan")


def bh_fdr(p: list[float]) -> list[float]:
    n = len(p)
    order = np.argsort(p)
    q = np.empty(n)
    run = 1.0
    for rank, i in zip(range(n, 0, -1), order[::-1]):
        run = min(run, p[i] * n / rank)
        q[i] = run
    return q.tolist()


def feature_diagnosis(d: pd.DataFrame, rng: np.random.Generator, features: list[str] | None = None) -> dict[str, Any]:
    features = features or st.ALL_FEATURES
    fam = family_of()
    y, days = d["y"].to_numpy(), d["day"].to_numpy()
    ha, hb = halves(d)
    out: dict[str, Any] = {}
    for name in features:
        x = d[name].to_numpy(dtype=float)
        ok = np.isfinite(x)
        xs, ys = x[ok], y[ok]
        row: dict[str, Any] = {"family": fam[name], "n": int(ok.sum()), "missing_rate": float(1 - ok.mean())}
        if ok.sum() < 10 or np.std(xs) == 0:
            row.update(status="DEGENERATE", descriptive_only=True, not_for_selection=True)
            out[name] = row
            continue
        row.update(mean=float(xs.mean()), std=float(xs.std()), min=float(xs.min()), max=float(xs.max()), success_rate_all=float(ys.mean()))
        sp = ca.cluster_spearman(x, y.astype(float), days, rng, reps=1000)
        row.update(spearman_y=sp["rho"], spearman_ci=[sp["ci_low"], sp["ci_high"]], p_cluster=sp["p"], auc=ca.auc(x, y), mutual_info_bits=ca.mutual_info(x, y))
        m1, m0 = xs[ys == 1], xs[ys == 0]
        pooled = math.sqrt(((len(m1) - 1) * m1.var(ddof=1) + (len(m0) - 1) * m0.var(ddof=1)) / max(1, len(m1) + len(m0) - 2))
        row["effect_size_d"] = float((m1.mean() - m0.mean()) / pooled) if pooled > 0 else float("nan")
        med = np.median(xs)
        hi = xs > med if (xs > med).any() else xs >= med
        row["mean_outcome_diff_hi_minus_lo"] = float(ys[hi].mean() - ys[~hi].mean()) if hi.any() and (~hi).any() else float("nan")
        bins, mono = ca.bin_table(xs, ys, _edges(xs))
        row["bins"], row["monotonic_rho"] = bins, mono
        q80, q20 = np.quantile(xs, 0.8), np.quantile(xs, 0.2)
        fav_hi = sp["rho"] > 0
        sel = (xs >= q80) if fav_hi else (xs <= q20)
        if sel.sum() == 0:
            sel = xs == (xs.max() if fav_hi else xs.min())
        row["directional_success_favoured_tail"] = {"side_of_tail": "high" if fav_hi else "low", "n": int(sel.sum()),
                                                      "success_rate": float(ys[sel].mean()) if sel.sum() else float("nan")}
        r_a, r_b = _rho(np.where(ha, x, np.nan), y), _rho(np.where(hb, x, np.nan), y)
        row.update(rho_train_a=r_a, rho_train_b=r_b, auc_train_a=ca.auc(x[ha & ok], y[ha & ok]) if (ha & ok).sum() > 10 else float("nan"),
                   auc_train_b=ca.auc(x[hb & ok], y[hb & ok]) if (hb & ok).sum() > 10 else float("nan"),
                   n_a=int((ha & ok).sum()), n_b=int((hb & ok).sum()))
        row["sign_flip_a_b"] = bool(np.isfinite(r_a) and np.isfinite(r_b) and r_a * r_b < 0)
        row["descriptive_only"] = bool(ok.sum() < MIN_DESCRIPTIVE)
        row["not_for_selection"] = bool(ok.sum() < MIN_SELECTION or row["n_a"] < MIN_SELECTION or row["n_b"] < MIN_SELECTION)
        out[name] = row
    names = [k for k, v in out.items() if "p_cluster" in v]
    p = [out[k]["p_cluster"] if np.isfinite(out[k]["p_cluster"]) else 1.0 for k in names]
    for k, a, q in zip(names, ca.holm(p), bh_fdr(p)):
        out[k]["p_holm"], out[k]["q_fdr_bh"] = a, q
        out[k]["holm_significant_0.05"] = bool(a < 0.05)
    return out


def redundancy_report(d: pd.DataFrame, features: list[str] | None = None) -> dict[str, Any]:
    features = features or st.ALL_FEATURES
    x = d[features]
    sp, pe = x.corr(method="spearman"), x.corr(method="pearson")
    fam = family_of()
    pairs = []
    for i, a in enumerate(features):
        for b in features[i + 1:]:
            r_s, r_p = sp.loc[a, b], pe.loc[a, b]
            if np.isfinite(r_s) and (abs(r_s) >= REDUNDANT or (np.isfinite(r_p) and abs(r_p) >= REDUNDANT)):
                pairs.append({"a": a, "b": b, "spearman": float(r_s), "pearson": float(r_p) if np.isfinite(r_p) else None,
                              "level": "VERY_HIGH" if max(abs(r_s), abs(r_p) if np.isfinite(r_p) else 0) >= VERY_REDUNDANT else "HIGH",
                              "same_family": fam[a] == fam[b]})
    pairs.sort(key=lambda r: -abs(r["spearman"]))
    per_family = {f: {"n_features": len(fs), "n_high_pairs_within": sum(1 for p in pairs if p["same_family"] and fam[p["a"]] == f)} for f, fs in st.FAMILIES.items()}
    return {"threshold_high": REDUNDANT, "threshold_very_high": VERY_REDUNDANT, "n_pairs_high": len(pairs),
            "n_pairs_very_high": sum(p["level"] == "VERY_HIGH" for p in pairs), "pairs": pairs, "per_family": per_family}


def family_report(diag: dict[str, Any], red: dict[str, Any], valid_diag: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {}
    for fam, fs in st.FAMILIES.items():
        rows = [diag[f] for f in fs if "auc" in diag[f]]
        st_ok = [r for r in rows if np.isfinite(r["rho_train_a"]) and np.isfinite(r["rho_train_b"])]
        entry = {"n_features": len(fs), "n_redundant_pairs_within": red["per_family"][fam]["n_high_pairs_within"],
                 "max_abs_auc_minus_half": max((abs(r["auc"] - 0.5) for r in rows), default=float("nan")),
                 "n_holm_significant": sum(bool(r.get("holm_significant_0.05")) for r in rows),
                 "stable_share_same_sign_a_b": float(np.mean([r["rho_train_a"] * r["rho_train_b"] > 0 for r in st_ok])) if st_ok else float("nan"),
                 "n_sign_flip": sum(bool(r.get("sign_flip_a_b")) for r in rows)}
        if valid_diag is not None:
            vr = [valid_diag[f] for f in fs if f in valid_diag and "auc" in valid_diag[f]]
            same = [np.sign(valid_diag[f]["spearman_y"]) == np.sign(diag[f]["spearman_y"]) for f in fs if "auc" in diag[f] and f in valid_diag and "auc" in valid_diag[f]]
            entry["valid_descriptive"] = {"max_abs_auc_minus_half": max((abs(r["auc"] - 0.5) for r in vr), default=float("nan")),
                                          "share_same_sign_as_train": float(np.mean(same)) if same else float("nan"), "n_features": len(vr)}
        out[fam] = entry
    return out


# ------------------------------------------------------------------------------------------------ structure engine description
def structure_diagnosis(d: pd.DataFrame) -> dict[str, Any]:
    """Descriptive: how often the structure events occur and how the +-2 ATR outcome looks conditional on them. No selection is made here."""
    y = d["y"].to_numpy()
    base = cs.summarize_outcomes(y)
    events = {"bos_with": d["bos_with"] == 1, "choch_with": d["choch_with"] == 1, "bo_fresh": d["bo_fresh"] == 1, "bo_active": d["bo_active"] == 1,
              "rt_touched": d["rt_touched"] == 1, "rt_confirmed": d["rt_confirmed"] == 1, "rt_fail_active": d["rt_fail_active"] == 1,
              "struct_up(+2)": d["struct_score"] == 2, "struct_down(-2)": d["struct_score"] == -2, "struct_mixed(0)": d["struct_score"] == 0}
    out = {"baseline_all_rows": base, "events": {}}
    for k, m in events.items():
        m = m.to_numpy()
        s = cs.summarize_outcomes(y[m])
        s.update(share_of_rows=float(m.mean()), descriptive_only=bool(m.sum() < MIN_DESCRIPTIVE), not_for_selection=bool(m.sum() < MIN_SELECTION),
                 per_symbol_n={sym: int((m & (d["symbol"].to_numpy() == sym)).sum()) for sym in sorted(d["symbol"].unique())})
        out["events"][k] = s
    out["engine_constants"] = {"swing_fractal_K": st.K, "range_swings": st.RANGE_SWINGS, "breakout_window_bars": st.W, "retest_zone_atr": st.ZONE,
                               "atr_n": st.ATR_N, "rsi_n": st.RSI_N, "adx_n": st.ADX_N, "slope_lag": st.SLOPE, "recent_bars": st.RECENT,
                               "atr_percentile_window": st.PCT_WIN, "displacement_body_atr": st.DISP,
                               "compression_below": st.COMPRESSION, "expansion_above": st.EXPANSION, "all_fixed_a_priori": True}
    return out


# ------------------------------------------------------------------------------------------------ feature selection (outcome-light)
def _stable(diag: dict[str, Any], f: str) -> tuple[bool, str]:
    r = diag.get(f, {})
    if "auc" not in r:
        return False, "degenerate"
    if r["not_for_selection"]:
        return False, "n<50"
    ra, rb = r["rho_train_a"], r["rho_train_b"]
    if not (np.isfinite(ra) and np.isfinite(rb)) or ra * rb <= 0:
        return False, "sign_flip_between_train_a_b"
    if min(abs(ra), abs(rb)) < STABLE_MIN_RHO:
        return False, "effect_below_noise_floor_in_one_half"
    return True, ""


def _select(d: pd.DataFrame, diag: dict[str, Any], order: list[tuple[str, str]], cap: int, per_family_cap: int | None) -> dict[str, Any]:
    kept: list[str] = []
    log = []
    count: dict[str, int] = {}
    for fam, f in order:
        if len(kept) >= cap:
            log.append({"feature": f, "action": "skipped", "reason": "cap reached"})
            continue
        ok, why = _stable(diag, f)
        if not ok:
            log.append({"feature": f, "action": "rejected", "reason": why})
            continue
        if per_family_cap and count.get(fam, 0) >= per_family_cap:
            log.append({"feature": f, "action": "rejected", "reason": "family cap"})
            continue
        clash = [k for k in kept if abs(ca.cs.spearman(d[f].to_numpy(dtype=float), d[k].to_numpy(dtype=float))) >= REDUNDANT]
        if clash:
            log.append({"feature": f, "action": "rejected", "reason": f"redundant with {clash[0]} (|rho|>=0.70)"})
            continue
        kept.append(f)
        count[fam] = count.get(fam, 0) + 1
        log.append({"feature": f, "action": "kept", "reason": "priority list, stable, not redundant"})
    return {"features": kept, "log": log}


def select_b(d: pd.DataFrame, diag: dict[str, Any]) -> dict[str, Any]:
    return _select(d, diag, [(fam, f) for fam in B_FAMILIES for f in st.FAMILIES[fam]], CAP_B, None)


def select_c(d: pd.DataFrame, diag: dict[str, Any]) -> dict[str, Any]:
    return _select(d, diag, [(fam, f) for fam, fs in st.FAMILIES.items() for f in fs], CAP_C, CAP_C_PER_FAMILY)


# ------------------------------------------------------------------------------------------------ models (ridge-logistic, TRAIN only)
def _matrix(d: pd.DataFrame, feats: list[str], med: np.ndarray | None = None, mu: np.ndarray | None = None, sd: np.ndarray | None = None):
    x = d[feats].to_numpy(dtype=float)
    med = np.nanmedian(x, axis=0) if med is None else med
    x = np.where(np.isfinite(x), x, med)
    mu = x.mean(0) if mu is None else mu
    sd = (x.std(0) + 1e-12) if sd is None else sd
    return (x - mu) / sd, med, mu, sd


def fit_ridge(d: pd.DataFrame, feats: list[str], lam: float) -> dict[str, Any]:
    xs, med, mu, sd = _matrix(d, feats)
    w, b = ca._logistic_ridge(xs, d["y"].to_numpy(dtype=float), lam)
    return {"features": feats, "median": med.tolist(), "mean": mu.tolist(), "std": sd.tolist(), "coef": w.tolist(), "intercept": b, "ridge_lambda": lam}


def linear_predictor(model: dict[str, Any], d: pd.DataFrame) -> np.ndarray:
    xs, *_ = _matrix(d, model["features"], np.array(model["median"]), np.array(model["mean"]), np.array(model["std"]))
    return xs @ np.array(model["coef"]) + model["intercept"]


def wf_folds(d: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding window on TRAIN days with an embargo of 2 days between the training end and the test start. VALID is never part of a fold."""
    days = np.sort(d["day"].unique())
    n = len(days)
    cuts = [round(n * 0.41), round(n * 0.585), round(n * 0.79), n]
    dd = d["day"].to_numpy()
    folds = []
    for a, b in itertools.pairwise(cuts):
        tr = dd < days[a]
        te = (dd >= days[min(a + EMBARGO_DAYS, n - 1)]) & (dd <= days[b - 1])
        folds.append((tr & (dd < days[a - EMBARGO_DAYS]), te))
    return folds


def walk_forward(d: pd.DataFrame, feats: list[str]) -> dict[str, Any]:
    folds = wf_folds(d)
    y = d["y"].to_numpy(dtype=float)
    res: dict[float, dict[str, Any]] = {}
    for lam in RIDGE_GRID:
        oof = np.full(len(d), np.nan)
        for tr, te in folds:
            m = fit_ridge(d[tr], feats, lam)
            oof[te] = linear_predictor(m, d[te])
        ok = np.isfinite(oof)
        pr = np.clip(1 / (1 + np.exp(-oof[ok])), 1e-6, 1 - 1e-6)
        res[lam] = {"oof": oof, "logloss": float(-(y[ok] * np.log(pr) + (1 - y[ok]) * np.log(1 - pr)).mean())}
    best = min(res, key=lambda k: res[k]["logloss"])
    return {"lambda": best, "oof": res[best]["oof"], "logloss_by_lambda": {str(k): v["logloss"] for k, v in res.items()},
            "folds": [{"train_days": len(np.unique(d["day"][tr])), "test_days": len(np.unique(d["day"][te])),
                       "train_end_day": int(d["day"][tr].max()), "test_start_day": int(d["day"][te].min())} for tr, te in folds]}


def percentile_map(z_train: np.ndarray) -> list[float]:
    """1001-point quantile grid of the TRAIN linear predictor; a score 0..100 = ECDF percentile (not a probability)."""
    return np.quantile(z_train, np.linspace(0, 1, 1001)).tolist()


def to_score(grid: list[float], z: np.ndarray) -> np.ndarray:
    g = np.asarray(grid)
    return 100.0 * (np.searchsorted(g, z, side="left") + np.searchsorted(g, z, side="right")) / 2.0 / (len(g))


def model_hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode()).hexdigest()


def verify_frozen(frozen: dict[str, Any]) -> bool:
    """True only if the stored hash matches the stored models + spec (any change after the freeze is detected)."""
    return frozen.get("sha256") == model_hash({k: frozen[k] for k in ("models", "spec")})

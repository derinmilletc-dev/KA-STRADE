"""KAIS FARMER C15 analysis helpers (research only). Operates on the TRAIN/VALID feature snapshot; never touches OOS data.

Everything here is deterministic. Statistics that respect overlap: consecutive 15m decisions share most of their outcome window, so
significance is judged with a DAY-CLUSTER bootstrap (resample calendar days), never with row-level standard errors.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from . import c15_scoring as cs

REPS = 2000
MIN_DESCRIPTIVE, MIN_DECISION, MIN_STRONG = 30, 50, 100        # subgroup thresholds from the C15 spec

FEATURES: dict[str, str] = {
    "ema20_dist_d": "trend", "ema20_50_gap_d": "trend", "ema50_200_gap_d": "trend", "trend_votes": "trend",
    "macd_d": "momentum", "rsi_d": "momentum", "bb_pos_d": "momentum",
    "adx": "strength", "atr_pct": "volatility", "bb_width_pct": "volatility", "volume_ratio": "volume",
    "room_atr": "structure", "near_atr": "structure", "range_pos_d": "structure",
    "trap_score": "radar", "breakout_quality": "radar",
    "mtf_alignment": "mtf", "mtf_agree": "mtf", "conf_1h_same": "mtf", "conf_4h_same": "mtf",
    "confidence": "score", "score_gap_d": "score", "pts_trend": "score", "pts_momentum": "score", "pts_volume": "score",
}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Directional rows only, direction-aligned features, one outcome column `y` (1 = +2 ATR first, 0 = -2 ATR first), unresolved dropped."""
    d = df[df["direction"].isin(["LONG", "SHORT"])].copy()
    s = np.where(d["direction"] == "LONG", 1.0, -1.0)
    d["sgn"] = s
    d["y"] = np.where(d["direction"] == "LONG", d["y_long"], d["y_short"])
    d = d[d["y"].notna()].copy()
    s = d["sgn"].to_numpy()
    d["day"] = (d["at"] // cs.DAY).astype(int)
    d["ema20_dist_d"], d["ema20_50_gap_d"], d["ema50_200_gap_d"] = (s * d["ema20_dist_atr"], s * d["ema20_50_gap_atr"], s * d["ema50_200_gap_atr"])
    d["macd_d"], d["bb_pos_d"] = s * d["macd_hist_atr"], s * d["bb_pos"]
    d["rsi_d"] = np.where(s > 0, d["rsi"], 100 - d["rsi"])
    d["range_pos_d"] = np.where(s > 0, d["range_pos"], 1 - d["range_pos"])
    d["room_atr"] = np.where(s > 0, d["resistance_dist_atr"], d["support_dist_atr"])
    d["near_atr"] = np.where(s > 0, d["support_dist_atr"], d["resistance_dist_atr"])
    d["trend_votes"] = d["c_price_gt_ema20"] + d["c_ema20_gt_ema50"] + d["c_ema50_gt_ema200"]
    d["mtf_agree"] = (d["dir_1h"] == d["direction"]).astype(int) + (d["dir_4h"] == d["direction"]).astype(int)
    d["conf_1h_same"] = np.where(d["dir_1h"] == d["direction"], d["conf_1h"], 0.0)
    d["conf_4h_same"] = np.where(d["dir_4h"] == d["direction"], d["conf_4h"], 0.0)
    d["score_gap_d"] = s * (d["long_score"] - d["short_score"])
    for k in (1, 2, 4, 8):
        d[f"ret{k}_d"] = s * d[f"ret_{k}_atr"]
    d["u_all"] = True
    d["u_gated"] = d["gates_ex_score"].astype(bool)
    d["u_farmer"] = d["farmer_confirmed"].astype(bool)
    return d.reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ rank statistics with day clusters
def _rank(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank(method="average").to_numpy()


def cluster_spearman(x: np.ndarray, y: np.ndarray, days: np.ndarray, rng: np.random.Generator, reps: int = REPS) -> dict[str, float]:
    """Spearman rho with a day-cluster bootstrap CI and two-sided bootstrap p (sufficient statistics per day, vectorised)."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y, days = x[ok], y[ok], days[ok]
    n = len(x)
    if n < 10 or np.std(x) == 0 or np.std(y) == 0:
        return {"n": n, "rho": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "p": float("nan")}
    rx, ry = _rank(x), _rank(y)
    ids, inv = np.unique(days, return_inverse=True)
    stat = np.zeros((len(ids), 6))
    for j, v in enumerate((np.ones(n), rx, ry, rx * rx, ry * ry, rx * ry)):
        stat[:, j] = np.bincount(inv, weights=v, minlength=len(ids))

    def rho(s: np.ndarray) -> np.ndarray:
        cnt, sx, sy, sxx, syy, sxy = (s[..., i] for i in range(6))
        cov = sxy / cnt - (sx / cnt) * (sy / cnt)
        vx, vy = sxx / cnt - (sx / cnt) ** 2, syy / cnt - (sy / cnt) ** 2
        return cov / np.sqrt(np.maximum(vx * vy, 1e-300))

    point = float(rho(stat.sum(0)))
    pick = rng.integers(0, len(ids), size=(reps, len(ids)))
    boot = rho(stat[pick].sum(1))
    boot = boot[np.isfinite(boot)]
    p = 2 * min((boot <= 0).mean(), (boot >= 0).mean())
    return {"n": n, "rho": point, "ci_low": float(np.quantile(boot, 0.025)), "ci_high": float(np.quantile(boot, 0.975)), "p": float(max(p, 1 / reps))}


def auc(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x)
    x, y = x[ok], y[ok].astype(int)
    n1, n0 = int(y.sum()), int((1 - y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = _rank(x)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def mutual_info(x: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """MI (bits) between a quantile-binned feature and the binary outcome."""
    ok = np.isfinite(x)
    x, y = x[ok], y[ok].astype(int)
    if len(x) < 50 or len(np.unique(x)) < 2:
        return float("nan")
    b = pd.qcut(pd.Series(x).rank(method="first"), q=min(bins, len(np.unique(x))), labels=False, duplicates="drop").to_numpy() if len(np.unique(x)) > bins else pd.factorize(x)[0]
    joint = pd.crosstab(b, y).to_numpy().astype(float)
    joint /= joint.sum()
    px, py = joint.sum(1, keepdims=True), joint.sum(0, keepdims=True)
    nz = joint > 0
    return float((joint[nz] * np.log2(joint[nz] / (px @ py)[nz])).sum())


def holm(p: list[float]) -> list[float]:
    order = np.argsort(p)
    m, out, prev = len(p), [0.0] * len(p), 0.0
    for rank, i in enumerate(order):
        prev = max(prev, min(1.0, (m - rank) * p[i]))
        out[i] = prev
    return out


# ------------------------------------------------------------------------------------------------ feature quality (spec 4, 5, 6)
def feature_quality(d: pd.DataFrame, rng: np.random.Generator, edges: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    y, days = d["y"].to_numpy(), d["day"].to_numpy()
    out: dict[str, Any] = {}
    plist, names = [], []
    for name, group in FEATURES.items():
        x = d[name].to_numpy(dtype=float)
        ok = np.isfinite(x)
        xs, ys = x[ok], y[ok]
        row: dict[str, Any] = {"group": group, "n": int(ok.sum()), "missing_rate": float(1 - ok.mean()), "mean": float(np.mean(xs)), "std": float(np.std(xs)),
                               "min": float(np.min(xs)), "max": float(np.max(xs)), "success_rate_all": float(ys.mean()),
                               "mean_future_ret8_atr": float(np.nanmean(d.loc[ok, "ret8_d"])), "median_future_ret8_atr": float(np.nanmedian(d.loc[ok, "ret8_d"]))}
        row["pearson_y"] = float(np.corrcoef(xs, ys)[0, 1]) if np.std(xs) > 0 else float("nan")
        sp = cluster_spearman(x, y.astype(float), days, rng)
        row.update(spearman_y=sp["rho"], spearman_ci=[sp["ci_low"], sp["ci_high"]], p_cluster=sp["p"])
        row["spearman_ret8"] = cluster_spearman(x, d["ret8_d"].to_numpy(dtype=float), days, rng, reps=500)["rho"]
        row["auc"] = auc(x, y)
        row["mutual_info_bits"] = mutual_info(x, y)
        m1, m0 = xs[ys == 1], xs[ys == 0]
        pooled = math.sqrt(((len(m1) - 1) * m1.var(ddof=1) + (len(m0) - 1) * m0.var(ddof=1)) / max(1, len(m1) + len(m0) - 2)) if len(m1) > 1 and len(m0) > 1 else float("nan")
        row["effect_size_d"] = float((m1.mean() - m0.mean()) / pooled) if pooled and pooled > 0 else float("nan")
        # monotonicity over pre-defined quantile bins (5 bins, or every distinct value for a discrete feature)
        edge = edges[name] if edges and name in edges else None
        row["bins"], row["monotonic_rho"] = bin_table(xs, ys, edge)
        out[name] = row
        names.append(name)
        plist.append(row["p_cluster"] if np.isfinite(row["p_cluster"]) else 1.0)
    adj = holm(plist)
    for nm, a in zip(names, adj):
        out[nm]["p_holm"] = a
    return out


def make_edges(d: pd.DataFrame) -> dict[str, np.ndarray]:
    """Quantile edges from the TRAIN distribution of each feature (outcome-blind)."""
    edges = {}
    for name in FEATURES:
        v = d[name].to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        if len(np.unique(v)) <= 8:
            edges[name] = np.unique(v)
        else:
            edges[name] = np.unique(np.quantile(v, [0.2, 0.4, 0.6, 0.8]))
    return edges


def bin_table(x: np.ndarray, y: np.ndarray, edge: np.ndarray | None) -> tuple[list[dict[str, Any]], float]:
    if edge is None:
        edge = np.unique(np.quantile(x, [0.2, 0.4, 0.6, 0.8]))
    idx = np.searchsorted(edge, x, side="left")           # a value equal to an edge falls in the lower bin
    rows = []
    for b in np.unique(idx):
        m = idx == b
        yy = y[m]
        lo, hi = cs.wilson(int(yy.sum()), int(m.sum()))
        rows.append({"bin": int(b), "x_min": float(x[m].min()), "x_max": float(x[m].max()), "n": int(m.sum()), "success_rate": float(yy.mean()), "ci_low": lo, "ci_high": hi,
                     "descriptive_only": bool(m.sum() < MIN_DESCRIPTIVE), "not_for_decisions": bool(m.sum() < MIN_DECISION)})
    rates = [r["success_rate"] for r in rows if r["n"] >= MIN_DESCRIPTIVE]
    mono = cs.spearman(np.arange(len(rates), dtype=float), np.array(rates)) if len(rates) >= 3 else float("nan")
    return rows, mono


# ------------------------------------------------------------------------------------------------ score calibration (spec 9)
BANDS = [("<=84", -1, 84), ("85-89", 85, 89), ("90-91", 90, 91), ("92-93", 92, 93), ("94-95", 94, 95)]


def band_table(d: pd.DataFrame, rng: np.random.Generator, col: str = "confidence", bands=BANDS) -> list[dict[str, Any]]:
    rows = []
    for label, lo, hi in bands:
        m = (d[col] >= lo) & (d[col] <= hi)
        sub = d[m]
        if sub.empty:
            rows.append({"band": label, "n": 0})
            continue
        y = sub["y"].to_numpy()
        st = cs.summarize_outcomes(y.astype(int))
        ci = cs.day_cluster_ci(y, sub["day"].to_numpy(), rng, reps=1000)
        st.update(band=label, day_cluster_ci=list(ci), max_drawdown_r=cs.max_drawdown_r(y.astype(int)[np.argsort(sub["at"].to_numpy(), kind="stable")]),
                  days=int(sub["day"].nunique()), long_n=int((sub["direction"] == "LONG").sum()), short_n=int((sub["direction"] == "SHORT").sum()),
                  descriptive_only=bool(len(sub) < MIN_DESCRIPTIVE), not_for_decisions=bool(len(sub) < MIN_DECISION))
        rows.append(st)
    return rows


def thin_by_hold(d: pd.DataFrame) -> pd.DataFrame:
    """Non-overlapping subsample: per symbol, take a decision, then skip every decision up to and including its exit candle."""
    hold = np.where(d["direction"] == "LONG", d["hold_long"], d["hold_short"])
    d = d.assign(_hold=hold)
    keep = []
    for _sym, g in d.sort_values("at").groupby("symbol"):
        free_at = -1
        for idx, at, h in zip(g.index, g["at"], g["_hold"], strict=False):
            if at < free_at or not np.isfinite(h):
                continue
            keep.append(idx)
            free_at = at + int(h) * cs.BAR
    return d.loc[keep].drop(columns="_hold")


# ------------------------------------------------------------------------------------------------ redundancy (spec 7) and decomposition (spec 8)
PAIRS = [("rsi_d", "macd_d"), ("rsi_d", "mom_match"), ("macd_d", "mom_match"), ("trend_votes", "mtf_agree"), ("trend_votes", "mtf_alignment"),
         ("ema20_50_gap_d", "ema50_200_gap_d"), ("ema20_dist_d", "ema20_50_gap_d"), ("atr_pct", "bb_width_pct"), ("breakout_quality", "ema20_dist_d"),
         ("breakout_quality", "adx"), ("breakout_quality", "volume_ratio"), ("breakout_quality", "mom_match"), ("trap_score", "breakout_quality"),
         ("confidence", "mtf_alignment"), ("confidence", "breakout_quality"), ("confidence", "trend_votes"), ("confidence", "trap_score")]
CLUSTER_RHO = 0.6                      # pre-specified redundancy threshold


def add_aux(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["mom_match"] = np.where(d["direction"] == "LONG", d["momentum"] == "Pozitif", d["momentum"] == "Negatif").astype(float)
    return d


def redundancy(d: pd.DataFrame) -> dict[str, Any]:
    d = add_aux(d)
    cols = list(FEATURES) + ["mom_match"]
    ranks = pd.DataFrame({c: _rank(d[c].fillna(d[c].median()).to_numpy(dtype=float)) for c in cols})
    corr = ranks.corr()
    pearson = d[cols].corr()
    pairs = [{"a": a, "b": b, "spearman": float(corr.loc[a, b]), "pearson": float(pearson.loc[a, b])} for a, b in PAIRS]
    parent = {c: c for c in cols}

    def find(c: str) -> str:
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c
    strong = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if abs(corr.loc[a, b]) >= CLUSTER_RHO:
                parent[find(a)] = find(b)
                strong.append({"a": a, "b": b, "spearman": float(corr.loc[a, b])})
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(find(c), []).append(c)
    eig = np.sort(np.linalg.eigvalsh(corr.to_numpy()))[::-1]
    return {"threshold_abs_spearman": CLUSTER_RHO, "requested_pairs": pairs, "strong_pairs": sorted(strong, key=lambda r: -abs(r["spearman"])),
            "clusters": [v for v in groups.values()], "n_features": len(cols), "n_clusters": len(groups),
            "effective_dimension_participation_ratio": float(eig.sum() ** 2 / (eig ** 2).sum()),
            "components_for_90pct_variance": int(np.searchsorted(np.cumsum(eig) / eig.sum(), 0.9) + 1),
            "clusters_note": "features connected by |spearman| >= threshold form one cluster (they carry largely the same information)"}


COND = [k for k, _p in cs.LONG_POINTS]


def decomposition(d: pd.DataFrame) -> dict[str, Any]:
    d = add_aux(d)
    hi = d[d["confidence"] >= cs.MIN_FARMER_SCORE]
    pts = dict(cs.LONG_POINTS)
    trend_group = ["price_gt_ema20", "ema20_gt_ema50", "ema50_gt_ema200", "adx_trend", "price_gt_bb_mid"]
    out: dict[str, Any] = {"max_points": sum(pts.values()), "trend_position_group": trend_group,
                           "trend_group_share_of_max_points": sum(pts[k] for k in trend_group) / sum(pts.values())}
    for label, sub in (("all_directional", d), ("score_ge_90", hi)):
        flags = sub[[f"c_{k}" for k in COND]]
        share = {k: float(sub[f"c_{k}"].mean()) for k in COND}
        pts_by_block = {b: float(sub[f"pts_{b}"].mean()) for b in ("trend", "momentum", "volume")}
        tot = sum(pts_by_block.values())
        out[label] = {"n": len(sub), "p_condition_true": share, "mean_points_by_block": pts_by_block,
                      "block_share_of_points": {b: v / tot for b, v in pts_by_block.items()} if tot else None,
                      "mean_conditions_true_of_8": float(flags.sum(1).mean()),
                      "blocks_with_points_distribution": sub[["pts_trend", "pts_momentum", "pts_volume"]].gt(0).sum(1).value_counts(normalize=True).sort_index().to_dict(),
                      "phi_between_conditions": {f"{a}|{b}": float(np.corrcoef(sub[f"c_{a}"], sub[f"c_{b}"])[0, 1]) for i, a in enumerate(COND) for b in COND[i + 1:]
                                                 if sub[f"c_{a}"].std() > 0 and sub[f"c_{b}"].std() > 0}}
    x = np.column_stack([d[f"c_{k}"] for k in COND] + [d["mom_match"], d["trap_score"], np.ones(len(d))])
    for target in ("breakout_quality", "trap_score"):
        yv = d[target].to_numpy(dtype=float)
        use = [i for i in range(x.shape[1]) if not (target == "trap_score" and i == len(COND) + 1)]
        beta, *_ = np.linalg.lstsq(x[:, use], yv, rcond=None)
        resid = yv - x[:, use] @ beta
        out[f"r2_{target}_explained_by_score_conditions"] = float(1 - resid.var() / yv.var())
    return out


# ------------------------------------------------------------------------------------------------ regimes (spec 10)
def add_regimes(d: pd.DataFrame, vol_edges: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """Vol regime from per-symbol TRAIN tertiles of ATR% (outcome-blind); trend regime from the code's own ADX >= 20 test."""
    d = d.copy()
    lo = d["symbol"].map(lambda s: vol_edges[s][0])
    hi = d["symbol"].map(lambda s: vol_edges[s][1])
    d["vol_regime"] = np.where(d["atr_pct"] <= lo, "LOW_VOL", np.where(d["atr_pct"] <= hi, "NORMAL_VOL", "HIGH_VOL"))
    d["trend_regime"] = np.where(d["adx"] >= 20, "TRENDING", "RANGING")
    return d


def vol_edges_from(train: pd.DataFrame) -> dict[str, tuple[float, float]]:
    return {s: (float(g["atr_pct"].quantile(1 / 3)), float(g["atr_pct"].quantile(2 / 3))) for s, g in train.groupby("symbol")}


def regime_table(d: pd.DataFrame, rng: np.random.Generator, key_features: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in ("vol_regime", "trend_regime"):
        for val, sub in d.groupby(col):
            y = sub["y"].to_numpy()
            row = cs.summarize_outcomes(y.astype(int))
            sp = cluster_spearman(sub["confidence"].to_numpy(dtype=float), y.astype(float), sub["day"].to_numpy(), rng, reps=1000)
            row.update(spearman_confidence_y=sp["rho"], spearman_ci=[sp["ci_low"], sp["ci_high"]], descriptive_only=bool(len(sub) < MIN_DESCRIPTIVE),
                       auc_by_feature={f: auc(sub[f].to_numpy(dtype=float), y) for f in key_features})
            out[f"{col}={val}"] = row
    return out


# ------------------------------------------------------------------------------------------------ current score diagnosis (spec 2, 6, 9)
def current_score_diagnosis(d: pd.DataFrame, rng: np.random.Generator) -> dict[str, Any]:
    out: dict[str, Any] = {"definition": "confidence = min(95, 25 + condition points) on the winning side; directional decisions only; y = +2 ATR before -2 ATR (mark), gross"}
    for name, col in (("all_directional", "u_all"), ("quality_gates_passed_any_score", "u_gated"), ("farmer_confirmed_score_ge_90", "u_farmer")):
        sub = d[d[col]]
        sp = cluster_spearman(sub["confidence"].to_numpy(dtype=float), sub["y"].to_numpy(dtype=float), sub["day"].to_numpy(), rng)
        thin = thin_by_hold(sub)
        out[name] = {"n_rows": len(sub), "days": int(sub["day"].nunique()), "overall": cs.summarize_outcomes(sub["y"].to_numpy().astype(int)),
                     "spearman_confidence_y": sp, "bands": band_table(sub, rng),
                     "non_overlapping_subsample": {"n": len(thin), "overall": cs.summarize_outcomes(thin["y"].to_numpy().astype(int)),
                                                   "bands": band_table(thin, rng), "spearman_confidence_y": cluster_spearman(
                                                       thin["confidence"].to_numpy(dtype=float), thin["y"].to_numpy(dtype=float), thin["day"].to_numpy(), rng)},
                     "by_direction": {s: {"n": int((sub["direction"] == s).sum()), "bands": band_table(sub[sub["direction"] == s], rng)} for s in ("LONG", "SHORT")}}
    f = d[d["u_farmer"]]
    prof_cols = ["pts_trend", "pts_momentum", "pts_volume", "adx", "atr_pct", "volume_ratio", "trap_score", "breakout_quality", "mtf_alignment", "ema20_dist_d", "near_atr", "room_atr", "rsi_d"]
    out["band_profile_farmer_confirmed"] = {label: {c: float(f.loc[(f["confidence"] >= lo) & (f["confidence"] <= hi), c].mean()) for c in prof_cols}
                                            for label, lo, hi in BANDS if label not in ("<=84", "85-89")}
    return out


# ------------------------------------------------------------------------------------------------ candidate scores (spec 11-13): at most 3
# A = the current confidence (unchanged). B = percentile-rank evidence score. C = ridge-logistic score. B and C are fitted on TRAIN only.
SELECT_P = 0.10                   # pre-registered: unadjusted TRAIN day-cluster p below this AND same sign in both TRAIN halves
RIDGE_GRID = (1.0, 10.0, 100.0, 1000.0)
C_MAX_FEATURES, C_PRUNE_RHO = 10, 0.6
RAW_POOL = [k for k, g in FEATURES.items() if g != "score"]


def split_halves(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    mid = np.median(d["at"].to_numpy())
    return d["at"].to_numpy() <= mid, d["at"].to_numpy() > mid


def select_for_b(d: pd.DataFrame, quality: dict[str, Any]) -> list[dict[str, Any]]:
    """TRAIN evidence only. Sign comes from the TRAIN Spearman with the outcome; one representative per redundant pair (|rho| >= 0.6)."""
    first, second = split_halves(d)
    y = d["y"].to_numpy(dtype=float)
    cand = []
    for k in RAW_POOL:
        q = quality[k]
        if not np.isfinite(q["p_cluster"]) or q["p_cluster"] >= SELECT_P:
            continue
        s1 = np.sign(cs.spearman(d.loc[first, k].fillna(d[k].median()).to_numpy(dtype=float), y[first]))
        s2 = np.sign(cs.spearman(d.loc[second, k].fillna(d[k].median()).to_numpy(dtype=float), y[second]))
        if s1 == s2 == np.sign(q["spearman_y"]):
            cand.append({"feature": k, "sign": int(np.sign(q["spearman_y"])), "auc": q["auc"], "p_cluster": q["p_cluster"], "p_holm": q["p_holm"],
                         "half_signs": [int(s1), int(s2)]})
    cand.sort(key=lambda r: -abs(r["auc"] - 0.5))
    kept: list[dict[str, Any]] = []
    for c in cand:
        if all(abs(cs.spearman(d[c["feature"]].to_numpy(dtype=float), d[k["feature"]].to_numpy(dtype=float))) < C_PRUNE_RHO for k in kept):
            kept.append(c)
    return kept


def fit_b(d: pd.DataFrame, selected: list[dict[str, Any]]) -> dict[str, Any]:
    grid = np.linspace(0, 1, 101)
    return {"name": "B_simplified_evidence_score", "features": [{"feature": s["feature"], "sign": s["sign"],
                                                                  "quantiles": np.quantile(d[s["feature"]].dropna().to_numpy(dtype=float), grid).tolist()} for s in selected]}


def score_b(model: dict[str, Any], d: pd.DataFrame) -> np.ndarray:
    """Mean TRAIN-percentile of each selected feature (sign-aligned, 0..100). Missing feature -> neutral 50 for that component."""
    if not model["features"]:
        return np.full(len(d), 50.0)
    parts = []
    for f in model["features"]:
        x = d[f["feature"]].to_numpy(dtype=float)
        pct = np.searchsorted(np.array(f["quantiles"]), x, side="right") / 101 * 100
        pct = np.where(np.isfinite(x), np.clip(pct, 0, 100), 50.0)
        parts.append(pct if f["sign"] > 0 else 100 - pct)
    return np.mean(parts, axis=0)


def _logistic_ridge(x: np.ndarray, y: np.ndarray, lam: float, iters: int = 50) -> tuple[np.ndarray, float]:
    p = x.shape[1]
    w, b = np.zeros(p), float(np.log(y.mean() / (1 - y.mean())))
    for _ in range(iters):
        z = x @ w + b
        pr = 1 / (1 + np.exp(-z))
        g = np.append(x.T @ (pr - y) + lam * w, (pr - y).sum())
        wt = pr * (1 - pr) + 1e-9
        h = np.zeros((p + 1, p + 1))
        h[:p, :p] = (x * wt[:, None]).T @ x + lam * np.eye(p)
        h[:p, p] = h[p, :p] = (x * wt[:, None]).sum(0)
        h[p, p] = wt.sum()
        step = np.linalg.solve(h, g)
        w, b = w - step[:p], b - step[p]
        if np.abs(step).max() < 1e-8:
            break
    return w, float(b)


def prune_for_c(d: pd.DataFrame, quality: dict[str, Any]) -> list[str]:
    order = sorted(RAW_POOL, key=lambda k: -abs(quality[k]["auc"] - 0.5))
    kept: list[str] = []
    for k in order:
        if len(kept) >= C_MAX_FEATURES:
            break
        if all(abs(cs.spearman(d[k].to_numpy(dtype=float), d[o].to_numpy(dtype=float))) < C_PRUNE_RHO for o in kept):
            kept.append(k)
    return kept


def fit_c(d: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    x = d[features].to_numpy(dtype=float)
    med = np.nanmedian(x, axis=0)
    x = np.where(np.isfinite(x), x, med)
    mu, sd = x.mean(0), x.std(0) + 1e-12
    xs, y = (x - mu) / sd, d["y"].to_numpy(dtype=float)
    folds = np.array_split(np.unique(d["day"].to_numpy()), 5)            # contiguous day blocks (time-ordered), no shuffling
    cv = {}
    for lam in RIDGE_GRID:
        ll = []
        for f in folds:
            te = np.isin(d["day"].to_numpy(), f)
            w, b = _logistic_ridge(xs[~te], y[~te], lam)
            pr = np.clip(1 / (1 + np.exp(-(xs[te] @ w + b))), 1e-6, 1 - 1e-6)
            ll.append(float(-(y[te] * np.log(pr) + (1 - y[te]) * np.log(1 - pr)).mean()))
        cv[lam] = float(np.mean(ll))
    lam = min(cv, key=cv.get)
    w, b = _logistic_ridge(xs, y, lam)
    return {"name": "C_calibrated_logistic_score", "features": features, "median": med.tolist(), "mean": mu.tolist(), "std": sd.tolist(), "coef": w.tolist(),
            "intercept": b, "ridge_lambda": lam, "cv_logloss_by_lambda": {str(k): v for k, v in cv.items()}, "cv_folds": "5 contiguous day blocks on TRAIN",
            "constant_logloss": float(-(y.mean() * np.log(y.mean()) + (1 - y.mean()) * np.log(1 - y.mean())))}


def score_c(model: dict[str, Any], d: pd.DataFrame) -> np.ndarray:
    x = d[model["features"]].to_numpy(dtype=float)
    x = np.where(np.isfinite(x), x, np.array(model["median"]))
    z = ((x - np.array(model["mean"])) / np.array(model["std"])) @ np.array(model["coef"]) + model["intercept"]
    return 1 / (1 + np.exp(-z))


def top_fraction(score: np.ndarray, frac: float, rng_seed: int = 7, bottom: bool = False) -> np.ndarray:
    """Boolean mask of the top (or bottom) `frac` by score. Ties are broken with a fixed pseudo-random key, never with the outcome."""
    n = len(score)
    k = max(1, round(frac * n))
    key = np.random.default_rng(rng_seed).random(n)
    order = np.lexsort((key, score if bottom else -score))
    mask = np.zeros(n, dtype=bool)
    mask[order[:k]] = True
    return mask


def ranking_report(d: pd.DataFrame, score: np.ndarray, rng: np.random.Generator, n_current: int) -> dict[str, Any]:
    y = d["y"].to_numpy().astype(int)
    days = d["day"].to_numpy()
    order_t = np.argsort(d["at"].to_numpy(), kind="stable")
    out: dict[str, Any] = {"spearman_score_y": cluster_spearman(score, y.astype(float), days, rng), "auc": auc(score, y)}
    for label, frac in (("top10", 0.1), ("top20", 0.2), ("top30", 0.3), ("top40", 0.4), ("all", 1.0)):
        m = top_fraction(score, frac)
        out[label] = _slice_stats(d, m, y, order_t, rng)
    for label, frac in (("bottom10", 0.1), ("bottom30", 0.3)):
        out[label] = _slice_stats(d, top_fraction(score, frac, bottom=True), y, order_t, rng)
    m = top_fraction(score, n_current / len(score))
    out["equal_count_as_current"] = _slice_stats(d, m, y, order_t, rng)
    # monotonicity over 5 equal-count score bins
    ranks = _rank(score) / len(score)
    bins = np.minimum((ranks * 5).astype(int), 4)
    rates = [float(y[bins == b].mean()) for b in range(5) if (bins == b).any()]
    out["quintile_success_rates"] = rates
    out["quintile_monotonic_rho"] = cs.spearman(np.arange(len(rates), dtype=float), np.array(rates)) if len(rates) >= 3 else float("nan")
    return out


def _slice_stats(d: pd.DataFrame, mask: np.ndarray, y: np.ndarray, order_t: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    sel = y[mask]
    st = cs.summarize_outcomes(sel)
    sub_order = np.argsort(d["at"].to_numpy()[mask], kind="stable")
    st["max_drawdown_r"] = cs.max_drawdown_r(sel[sub_order])
    st["day_cluster_ci"] = list(cs.day_cluster_ci(sel, d["day"].to_numpy()[mask], rng, reps=1000))
    st["long_share"] = float((d["direction"].to_numpy()[mask] == "LONG").mean())
    st["by_symbol_n"] = {s: int((d["symbol"].to_numpy()[mask] == s).sum()) for s in sorted(d["symbol"].unique())}
    return st


# ------------------------------------------------------------------------------------------------ pre-registered decision rule (spec 18)
def ranking_ok(rep: dict[str, Any]) -> bool:
    """A score ranks quality only if its Spearman with the outcome has a day-cluster CI above 0 AND its 5 equal-count bins rise (rho >= 0.6)."""
    sp = rep["spearman_score_y"]
    return bool(np.isfinite(sp["ci_low"]) and sp["ci_low"] > 0 and np.isfinite(rep["quintile_monotonic_rho"]) and rep["quintile_monotonic_rho"] >= 0.6)


def classify(train: dict[str, Any], valid: dict[str, Any]) -> dict[str, Any]:
    """A = PROMISING, B = NO MATERIAL IMPROVEMENT, C = OVERFIT / NO GENERALIZATION, D = INCONCLUSIVE. Written before VALID was opened."""
    verdicts = {}
    for m in ("B", "C"):
        t_ok, v_ok = ranking_ok(train[m]), ranking_ok(valid[m])
        better = (valid[m]["spearman_score_y"]["rho"] > valid["A"]["spearman_score_y"]["rho"]
                  and train[m]["spearman_score_y"]["rho"] > train["A"]["spearman_score_y"]["rho"])
        t_rho, v_rho = train[m]["spearman_score_y"]["rho"], valid[m]["spearman_score_y"]["rho"]
        degraded = not (np.isfinite(t_rho) and t_rho > 0 and v_rho >= 0.5 * t_rho)
        verdicts[m] = {"train_ranking_ok": t_ok, "valid_ranking_ok": v_ok, "better_than_current_in_both": bool(better), "extreme_degradation": bool(degraded)}
    if any(v["train_ranking_ok"] and v["valid_ranking_ok"] and v["better_than_current_in_both"] and not v["extreme_degradation"] for v in verdicts.values()):
        decision = "A — PROMISING (REQUIRES INDEPENDENT OOS)"
    elif any(v["train_ranking_ok"] and not v["valid_ranking_ok"] for v in verdicts.values()):
        decision = "C — OVERFIT / NO GENERALIZATION"
    elif not any(v["train_ranking_ok"] or v["valid_ranking_ok"] for v in verdicts.values()):
        decision = "B — NO MATERIAL IMPROVEMENT"
    else:
        decision = "D — INCONCLUSIVE"
    return {"per_model": verdicts, "decision": decision}

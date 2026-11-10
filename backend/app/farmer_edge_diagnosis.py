"""KAIS FARMER edge diagnosis (C13). Research only: nothing here is imported by the app, orders or any live gate.

Pure helpers on top of the C12 simulator (`backtest_farmer`): per-signal trade evaluation with MFE/MAE, cost layers, segment bucketing
with TRAIN-defined edges, cluster-robust statistics and multiple-testing correction. The real Farmer evaluator is only called by the CLI;
everything in this module is deterministic and unit-testable on synthetic fixtures (fixtures are for tests only, never for results).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import backtest_farmer as bf
from .farmer_engine.engine import farmer_spec, levels_from_avg

MIN_N = 30                                  # minimum trades for a segment to be judged at all ("insufficient sample" below)
DAY = 86400
NA = None


# ------------------------------------------------------------------------------------------------ single signal evaluation
def single_trade(mk: bf.Market, cand: bf.Candidates, d: int, sym: str, *, side: str | None = None, margin: float, leverage: float,
                 target: float, max_loss: float, slippage_bps: float, dead_score: float | None, intrabar: str = "STOP_FIRST") -> dict[str, Any] | None:
    """One signal evaluated on its own (no slot / symbol occupancy). Same sizing, fills, exits and costs as `bf.simulate`.
    `side` overrides the candidate direction (used by the direction control). Returns None when the position cannot be prepared."""
    slip = slippage_bps / 10_000.0
    row = cand.rows[sym][d]
    side = side or row["direction"]
    sign = 1 if side == "LONG" else -1
    rules = mk.rules[sym]
    try:
        spec = farmer_spec("bt", sym, side, float(row["entry"]), margin=margin, leverage=leverage, target_usdt=target,
                           max_loss_usdt=max_loss, tick=rules["tickSize"], step=rules["stepSize"], buffer_usdt=bf.BUFFER_USDT)
    except ValueError:
        return None
    qty = spec.levels[0].qty
    if qty < rules["minQty"] or qty * float(row["entry"]) < rules["minNotional"] or d >= mk.n:
        return None
    at = mk.t0 + d * bf.BAR
    fill = float(mk.copen[sym][d]) * (1 + sign * slip)
    tp, stop = levels_from_avg(side, fill, qty, target_usdt=target, max_loss_usdt=max_loss, tick=rules["tickSize"], buffer_usdt=bf.BUFFER_USDT)
    entry_fee = qty * fill * bf.TAKER_FEE
    atr, planned = row.get("atr"), float(row["entry"])
    atr_pct = atr / planned * 100 if atr and atr > 0 and planned > 0 else None
    meta = spec.meta
    pos = {"symbol": sym, "side": side, "d": d, "entry_ts": at, "qty": qty, "entry_fill": fill, "tp": tp, "stop": stop, "margin": margin,
           "entry_fee": entry_fee, "confidence": row["confidence"], "atr": atr, "atr_pct": atr_pct, "planned": planned,
           "target_pct": meta["target_pct"], "stop_pct": meta["stop_pct"],
           "target_over_atr": meta["target_pct"] / atr_pct if atr_pct else None,
           "stop_over_atr": meta["stop_pct"] / atr_pct if atr_pct else None,
           "cost_over_target": meta["est_cost_usdt"] / target if target > 0 else None,
           "trap": row.get("trap"), "breakout": row.get("breakout"), "momentum": row.get("momentum"),
           "mtf_permission": row.get("mtf_permission"), "mtf_alignment": row.get("mtf_alignment"), "mtf_direction": row.get("mtf_direction"),
           "sf_accepted": row.get("sf_accepted"), "sf_present": row.get("sf_present")}
    exit_info = bf._find_exit(mk, cand, pos, sign, slip, dead_score, intrabar)
    if exit_info is None:
        pos["status"] = "OPEN"
        return pos
    ts = exit_info["ts"]
    gross = sign * (exit_info["fill"] - fill) * qty
    commission = entry_fee + qty * exit_info["fill"] * bf.TAKER_FEE
    funding = bf._funding(mk, sym, sign, qty, at, ts)
    pos.update(status="CLOSED", reason=exit_info["kind"], exit_ts=ts, exit_fill=exit_info["fill"], gross=gross, commission=commission,
               funding=funding, net=gross - commission + funding, hold_bars=max(1, (ts - at) // bf.BAR), ambiguous=exit_info["ambiguous"])
    pos.update(excursions(mk, pos))
    return pos


def excursions(mk: bf.Market, pos: dict[str, Any]) -> dict[str, float | None]:
    """MFE / MAE on MARK prices between the entry bar and the exit (price terms, USDT and ATR multiples). Gap/dead exits happen at a bar
    OPEN, so that bar contributes only its open price; range exits include the whole exit bar."""
    sym, sign, fill, qty = pos["symbol"], (1 if pos["side"] == "LONG" else -1), pos["entry_fill"], pos["qty"]
    j = pos["d"]
    rel = pos["exit_ts"] - mk.t0
    e = rel // bf.BAR
    at_open = rel % bf.BAR == 0
    last = e - 1 if at_open else e
    hi = float(mk.mhigh[sym][j:last + 1].max()) if last >= j else fill
    lo = float(mk.mlow[sym][j:last + 1].min()) if last >= j else fill
    if at_open and e < mk.n:
        px = float(mk.mopen[sym][e])
        hi, lo = max(hi, px), min(lo, px)
    fav = max(0.0, (hi - fill) if sign == 1 else (fill - lo))
    adv = max(0.0, (fill - lo) if sign == 1 else (hi - fill))
    atr = pos.get("atr")
    return {"mfe": fav, "mae": adv, "mfe_usdt": fav * qty, "mae_usdt": adv * qty,
            "mfe_atr": fav / atr if atr and atr > 0 else None, "mae_atr": adv / atr if atr and atr > 0 else None}


# ------------------------------------------------------------------------------------------------ feature snapshot (pure, from evaluator dicts)
def extract_features(legacy_analysis: dict[str, Any], mtf: dict[str, Any]) -> dict[str, Any]:
    """Entry-time snapshot from the REAL evaluator output. Missing fields stay None (reported as NA, never invented)."""
    ema = legacy_analysis.get("ema") or {}
    radar = legacy_analysis.get("radar") or {}
    tfs = (mtf or {}).get("timeframes") or {}
    return {"rsi": legacy_analysis.get("rsi"), "macd_hist": legacy_analysis.get("macd"), "adx": legacy_analysis.get("adx"),
            "volume_ratio": legacy_analysis.get("volume_ratio"), "trend": legacy_analysis.get("trend"),
            "ema20": ema.get("ema20"), "ema50": ema.get("ema50"), "ema200": ema.get("ema200"),
            "trap_level": radar.get("trap_level"), "squeeze": radar.get("squeeze"), "fomo_risk": radar.get("fomo_risk"),
            "dir_15m": (tfs.get("15m") or {}).get("direction"), "dir_1h": (tfs.get("1h") or {}).get("direction"),
            "dir_4h": (tfs.get("4h") or {}).get("direction"), "mtf_verdict": (mtf or {}).get("verdict")}


# ------------------------------------------------------------------------------------------------ segment definitions
def mtf_class(side: str, d15: str | None, d1h: str | None, d4h: str | None) -> str:
    """Full: 15m = 1h = 4h. Partial: 15m = 1h, 4h different. Weak: only 15m is the trade side. Other: everything else."""
    if None in (d15, d1h, d4h):
        return "NA"
    if d15 == d1h == d4h:
        return "FULL"
    if d15 == d1h and d4h != d15:
        return "PARTIAL"
    if d15 == side and d1h != side and d4h != side:
        return "WEAK"
    return "OTHER"


def momentum_class(momentum: str | None) -> str:
    return {"Pozitif": "POSITIVE", "Negatif": "NEGATIVE", "Nötr": "NEUTRAL"}.get(momentum or "", "NA")


def time_bucket(ts: int) -> str:
    h = (ts % DAY) // 3600
    return f"{h // 6 * 6:02d}-{h // 6 * 6 + 6:02d} UTC"


def confidence_bucket(conf: float | None) -> str:
    if conf is None:
        return "NA"
    c = math.floor(conf)
    if c <= 91:
        return "90-91"
    if c <= 93:
        return "92-93"
    if c <= 95:
        return "94-95"
    return "96+"


def quantile_edges(train_values: list[float | None], k: int = 3) -> list[float]:
    """Bin edges from TRAIN values only. Duplicate edges are removed (discrete scores) so a bucket is never empty by construction."""
    v = np.array([x for x in train_values if x is not None], dtype=float)
    if len(v) < k:
        return []
    qs = np.quantile(v, [i / k for i in range(1, k)])
    return sorted({float(q) for q in qs})


def bucket(value: float | None, edges: list[float], labels: tuple[str, ...] = ("LOW", "MID", "HIGH")) -> str:
    if value is None:
        return "NA"
    i = int(np.searchsorted(np.array(edges), value, side="left"))   # value <= edge belongs to the lower bucket (discrete scores)
    if len(edges) + 1 == len(labels):
        return labels[i]
    return f"B{i + 1}of{len(edges) + 1}"


# ------------------------------------------------------------------------------------------------ statistics
def norm_sf2(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def cluster_stat(values: np.ndarray, clusters: np.ndarray) -> dict[str, float]:
    """Mean with a cluster-robust standard error (clusters = UTC day, because crypto positions on the same day are correlated)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "se": float("nan"), "t": float("nan"), "p": float("nan")}
    mean = float(values.mean())
    if n < 2:
        return {"n": n, "mean": mean, "se": float("nan"), "t": float("nan"), "p": float("nan")}
    ids, inv = np.unique(clusters, return_inverse=True)
    resid = np.bincount(inv, weights=values - mean, minlength=len(ids))
    se = math.sqrt(float((resid ** 2).sum())) / n
    t = mean / se if se > 0 else float("nan")
    return {"n": n, "mean": mean, "se": se, "t": t, "p": norm_sf2(t) if not math.isnan(t) else float("nan")}


def boot_ci(values: np.ndarray, clusters: np.ndarray, rng: np.random.Generator, reps: int = 2000) -> tuple[float, float]:
    """95% CI of the mean by resampling whole UTC days."""
    if len(values) < 2:
        return float("nan"), float("nan")
    ids, inv = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inv, weights=values, minlength=len(ids))
    cnt = np.bincount(inv, minlength=len(ids)).astype(float)
    k = len(ids)
    pick = rng.integers(0, k, size=(reps, k))
    means = sums[pick].sum(axis=1) / cnt[pick].sum(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def holm(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (NaN stays NaN and is not counted)."""
    idx = [i for i, p in enumerate(pvals) if not math.isnan(p)]
    m = len(idx)
    order = sorted(idx, key=lambda i: pvals[i])
    adj = [float("nan")] * len(pvals)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def sign_flip_p(deltas: np.ndarray, clusters: np.ndarray, rng: np.random.Generator, reps: int = 20000) -> dict[str, float]:
    """Direction-information test. delta = outcome(actual direction) - outcome(opposite direction) per signal. If direction carries no
    information, the sign of the cluster sum is symmetric around zero. Two-sided p from random sign flips of whole day clusters."""
    ids, inv = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inv, weights=deltas, minlength=len(ids))
    stat = float(sums.sum())
    flips = rng.choice([-1.0, 1.0], size=(reps, len(ids)))
    null = flips @ sums
    p = float((np.abs(null) >= abs(stat) - 1e-12).mean())
    return {"n": len(deltas), "mean_delta": float(deltas.mean()) if len(deltas) else float("nan"), "sum_delta": stat, "p": p}


# ------------------------------------------------------------------------------------------------ tables
def period_of(ts: int, segments: dict[str, tuple[int, int]]) -> str | None:
    for name, (lo, hi) in segments.items():
        if name != "ALL" and lo <= ts < hi:
            return name
    return None


def describe(trades: list[dict[str, Any]], *, gross_key: str = "g0", net_key: str = "n2") -> dict[str, Any]:
    """Statistics of one trade group. gross_key/net_key select the cost layer (see `attach_layers`)."""
    if not trades:
        return {"n": 0}
    g = np.array([t[gross_key] for t in trades], dtype=float)
    n = np.array([t[net_key] for t in trades], dtype=float)
    out = {"n": len(trades), "gross_mean": float(g.mean()), "net_mean": float(n.mean()), "net_sum": float(n.sum()), "gross_sum": float(g.sum()),
           "net_median": float(np.median(n)), "win": float((n > 0).mean()),
           "stop_rate": float(np.mean([t["reason"] == "STOP" for t in trades])), "target_rate": float(np.mean([t["reason"] == "TARGET" for t in trades])),
           "dead_rate": float(np.mean([t["reason"] == "DEAD_CLOSE" for t in trades]))}
    for k in ("mfe_atr", "mae_atr"):
        vals = [t[k] for t in trades if t.get(k) is not None]
        out[k] = float(np.mean(vals)) if vals else None
    return out


def attach_layers(base: list[dict[str, Any]], l0: list[dict[str, Any]], l5: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Join the realistic (2 bps) trades with the 0 bps and 5 bps runs of the same signals by (symbol, entry time).
    Layer fields: g0 gross, f0 funding, c0 commission, n0 net at 0 bps; n2 net at base slippage; n5 net at 5 bps; slip_cost = n0 - n2."""
    k0 = {(t["symbol"], t["entry_ts"]): t for t in l0}
    k5 = {(t["symbol"], t["entry_ts"]): t for t in l5}
    out: list[dict[str, Any]] = []
    for t in base:
        key = (t["symbol"], t["entry_ts"])
        if key not in k0 or key not in k5:
            continue
        a, b = k0[key], k5[key]
        row = dict(t)
        row.update(g0=a["gross"], f0=a["funding"], c0=a["commission"], n0=a["net"], n5=b["net"], n2=t["net"], slip_cost=a["net"] - t["net"],
                   g2=t["gross"], reason0=a["reason"])
        out.append(row)
    return out, {"base": len(base), "matched": len(out), "dropped": len(base) - len(out)}


def cost_layers(trades: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    def agg(vals: list[float]) -> dict[str, float]:
        a = np.array(vals, dtype=float)
        return {"sum": float(a.sum()), "mean": float(a.mean()) if len(a) else float("nan"), "win": float((a > 0).mean()) if len(a) else float("nan")}
    return {"L1_gross": agg([t["g0"] for t in trades]),
            "L2_gross_plus_funding": agg([t["g0"] + t["f0"] for t in trades]),
            "L3_fee_adjusted": agg([t["n0"] for t in trades]),
            "L4_realistic_2bps": agg([t["n2"] for t in trades]),
            "L5_stress_5bps": agg([t["n5"] for t in trades])}


def fee_dominance(trades: list[dict[str, Any]], target: float) -> dict[str, Any]:
    """Which trades are gross winners that costs turn into net losers. Costs at the realistic layer (2 bps)."""
    gpos = [t for t in trades if t["g0"] > 0]
    gneg = [t for t in trades if t["g0"] < 0]
    gp_nn = [t for t in gpos if t["n2"] < 0]
    gn_nn = [t for t in gneg if t["n2"] < 0]
    ratios = [t["c0"] / t["g0"] for t in gpos]
    return {"n": len(trades), "gross_pos": len(gpos), "gross_neg": len(gneg), "gross_pos_net_neg": len(gp_nn), "gross_neg_net_neg": len(gn_nn),
            "share_gross_pos_turned_negative": len(gp_nn) / len(gpos) if gpos else float("nan"),
            "fee_over_gross_median": float(np.median(ratios)) if ratios else float("nan"),
            "fee_over_target_mean": float(np.mean([t["c0"] / target for t in trades])) if trades else float("nan"),
            "sum_commission": float(sum(t["c0"] for t in trades)), "sum_funding": float(sum(t["f0"] for t in trades)),
            "sum_slippage": float(sum(t["slip_cost"] for t in trades)), "sum_gross": float(sum(t["g0"] for t in trades)),
            "sum_net_2bps": float(sum(t["n2"] for t in trades))}

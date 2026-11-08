"""KAIS FARMER C15: analysis and scoring research. Research only: no order, no exchange client, no live gate, no production import.

Pure helpers: feature snapshot from the REAL Original V2 evaluator output (`legacy.analysis` / `legacy.mtf`), exact score decomposition
(reproduces `analysis.analyze` confidence), look-ahead-free labels, TRAIN/VALID split guard and OOS protection. The OOS period
(2026-06-01 and later) is never loaded: `assert_not_oos` is called wherever a timestamp enters this module.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

BAR = 900
DAY = 86400


def _ts(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


# Split locked by C14 (protocol hash c29ad788...). C15 may use TRAIN and VALID only.
TRAIN_START, VALID_START, OOS_START = _ts("2025-04-01"), _ts("2026-01-01"), _ts("2026-06-01")

# Fixed, documented constants of the CURRENT scoring code (analysis.py). Verified by `reconstruct_score` against stored confidence.
BASE_SCORE, SCORE_CAP, DIRECTION_GAP = 25, 95, 10
LONG_POINTS = (("price_gt_ema20", 10), ("ema20_gt_ema50", 12), ("ema50_gt_ema200", 14), ("macd_pos", 10), ("rsi_zone", 9),
               ("price_gt_bb_mid", 6), ("adx_trend", 8), ("volume_ok", 6))
COMPONENT_BLOCKS = {"price_gt_ema20": "trend", "ema20_gt_ema50": "trend", "ema50_gt_ema200": "trend", "adx_trend": "trend",
                    "macd_pos": "momentum", "rsi_zone": "momentum", "price_gt_bb_mid": "momentum", "volume_ok": "volume"}
MAX_TRAP, MIN_BREAKOUT, MIN_FARMER_SCORE = 35, 50, 90      # confirm.py / main.py thresholds (read from code, not tuned)


def assert_not_oos(ts: int) -> None:
    if ts >= OOS_START:
        raise ValueError("OOS timestamp refused: C15 must not read the locked OOS period")


def period_of(ts: int) -> str | None:
    assert_not_oos(ts)
    if TRAIN_START <= ts < VALID_START:
        return "TRAIN"
    if VALID_START <= ts < OOS_START:
        return "VALID"
    return None


# ------------------------------------------------------------------------------------------------ score reconstruction
def score_flags(a: dict[str, Any]) -> dict[str, dict[str, bool]]:
    """The 8 boolean conditions of `analysis.analyze`, rebuilt from its own output (no new indicator)."""
    price, ema = a["entry"], a["ema"]
    mid, rsi, adx, vol, macd = a["bollinger"]["middle"], a["rsi"], a["adx"], a["volume_ratio"], a["macd"]
    long = {"price_gt_ema20": price > ema["ema20"], "ema20_gt_ema50": ema["ema20"] > ema["ema50"], "ema50_gt_ema200": ema["ema50"] > ema["ema200"],
            "macd_pos": macd > 0, "rsi_zone": 52 <= rsi <= 72, "price_gt_bb_mid": price > mid, "adx_trend": bool(adx >= 20 and ema["ema20"] > ema["ema50"]),
            "volume_ok": vol >= 1.05}
    short = {"price_gt_ema20": price < ema["ema20"], "ema20_gt_ema50": ema["ema20"] < ema["ema50"], "ema50_gt_ema200": ema["ema50"] < ema["ema200"],
             "macd_pos": macd < 0, "rsi_zone": 28 <= rsi <= 48, "price_gt_bb_mid": price < mid, "adx_trend": bool(adx >= 20 and ema["ema20"] < ema["ema50"]),
             "volume_ok": vol >= 1.05}
    return {"LONG": long, "SHORT": short}


def reconstruct_score(a: dict[str, Any]) -> dict[str, Any]:
    flags = score_flags(a)
    ls = BASE_SCORE + sum(p for k, p in LONG_POINTS if flags["LONG"][k])
    ss = BASE_SCORE + sum(p for k, p in LONG_POINTS if flags["SHORT"][k])
    diff = ls - ss
    direction = "LONG" if diff >= DIRECTION_GAP else "SHORT" if diff <= -DIRECTION_GAP else "BEKLE"
    conf = min(SCORE_CAP, max(ls, ss)) if direction != "BEKLE" else min(69, 50 + abs(diff))
    return {"long_score": ls, "short_score": ss, "direction": direction, "confidence": conf, "flags": flags}


def block_points(flags: dict[str, bool]) -> dict[str, int]:
    out = {"trend": 0, "momentum": 0, "volume": 0}
    for k, p in LONG_POINTS:
        if flags[k]:
            out[COMPONENT_BLOCKS[k]] += p
    return out


# ------------------------------------------------------------------------------------------------ feature snapshot
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def snapshot_row(symbol: str, at: int, legacy: dict[str, Any]) -> dict[str, Any]:
    """One decision-time feature row from the REAL evaluator output. Uses only candles closed before `at`. Missing -> None."""
    assert_not_oos(at)
    a, mtf = legacy.get("analysis") or {}, legacy.get("mtf") or {}
    radar, ema, bb = a.get("radar") or {}, a.get("ema") or {}, a.get("bollinger") or {}
    tfs = mtf.get("timeframes") or {}
    sf = mtf.get("short_filter_v2") if isinstance(mtf.get("short_filter_v2"), dict) else None
    price, atr = _f(a.get("entry")), _f(a.get("atr"))
    row: dict[str, Any] = {"symbol": symbol, "at": at, "period": period_of(at), "direction": a.get("direction"), "confidence": _f(a.get("confidence")),
                           "price": price, "atr": atr, "atr_pct": atr / price * 100 if atr and price else None, "rsi": _f(a.get("rsi")),
                           "macd_hist": _f(a.get("macd")), "adx": _f(a.get("adx")), "volume_ratio": _f(a.get("volume_ratio")),
                           "trend": a.get("trend"), "momentum": a.get("momentum"), "trap_score": _f(radar.get("trap_score")),
                           "breakout_quality": _f(radar.get("breakout_quality")), "entry_timing": radar.get("entry_timing"),
                           "squeeze": radar.get("squeeze"), "fomo_risk": radar.get("fomo_risk"), "wick_signal": radar.get("wick_signal"),
                           "hour_utc": (at % DAY) // 3600, "dow": (at // DAY + 4) % 7}
    if price and atr and atr > 0:
        for name in ("ema20", "ema50", "ema200"):
            e = _f(ema.get(name))
            row[f"{name}_dist_atr"] = (price - e) / atr if e is not None else None
        e20, e50, e200 = (_f(ema.get(k)) for k in ("ema20", "ema50", "ema200"))
        row["ema20_50_gap_atr"] = (e20 - e50) / atr if e20 is not None and e50 is not None else None
        row["ema50_200_gap_atr"] = (e50 - e200) / atr if e50 is not None and e200 is not None else None
        up, lo, mid = (_f(bb.get(k)) for k in ("upper", "lower", "middle"))
        row["bb_width_pct"] = (up - lo) / mid * 100 if None not in (up, lo, mid) and mid else None
        row["bb_pos"] = (price - mid) / (up - mid) if None not in (up, mid) and up != mid else None
        sup, res = _f(a.get("support")), _f(a.get("resistance"))
        row["support_dist_atr"] = (price - sup) / atr if sup is not None else None
        row["resistance_dist_atr"] = (res - price) / atr if res is not None else None
        row["range_pos"] = (price - sup) / (res - sup) if None not in (sup, res) and res > sup else None
        row["macd_hist_atr"] = _f(a.get("macd")) / atr if _f(a.get("macd")) is not None else None
    for tf in ("1h", "4h"):
        row[f"dir_{tf}"] = (tfs.get(tf) or {}).get("direction")
        row[f"conf_{tf}"] = _f((tfs.get(tf) or {}).get("confidence"))
    row.update(mtf_alignment=_f(mtf.get("alignment")), mtf_permission=bool(mtf.get("entry_permission")), mtf_direction=mtf.get("direction"),
               mtf_verdict=mtf.get("verdict"), sf_present=sf is not None, sf_accepted=(sf or {}).get("accepted") if sf else None,
               sf_extension_atr=_f((sf or {}).get("extension_atr")) if sf else None)
    if a.get("entry") is not None and a.get("ema") and a.get("bollinger"):
        rec = reconstruct_score(a)
        row.update(long_score=rec["long_score"], short_score=rec["short_score"], score_reproduced=bool(rec["direction"] == a["direction"] and rec["confidence"] == a["confidence"]))
        d = a["direction"] if a["direction"] in ("LONG", "SHORT") else ("LONG" if rec["long_score"] >= rec["short_score"] else "SHORT")
        for k, _p in LONG_POINTS:
            row[f"c_{k}"] = int(rec["flags"][d][k])
        for blk, pts in block_points(rec["flags"][d]).items():
            row[f"pts_{blk}"] = pts
    d = row["direction"]
    row["gate_trap"] = bool(row["trap_score"] is not None and row["trap_score"] <= MAX_TRAP)
    row["gate_breakout"] = bool(row["breakout_quality"] is not None and row["breakout_quality"] >= MIN_BREAKOUT)
    row["gate_mtf"] = row["mtf_permission"]
    row["gate_short_filter"] = True if d != "SHORT" else bool(row["sf_accepted"]) if row["sf_present"] else True
    want = {"LONG": "Pozitif", "SHORT": "Negatif"}.get(d)
    row["gate_momentum"] = bool(want and row["momentum"] == want)
    row["gates_ex_score"] = bool(d in ("LONG", "SHORT") and row["gate_trap"] and row["gate_breakout"] and row["gate_mtf"] and row["gate_short_filter"])
    mtf_ok = bool(row["mtf_permission"] and row["mtf_direction"] == d)
    base = bool(d in ("LONG", "SHORT") and (row["confidence"] or 0) >= MIN_FARMER_SCORE and row["gate_momentum"] and mtf_ok and row["gate_short_filter"])
    row["farmer_confirmed"] = base                                                  # CONFIDENCE variant (confirm.py): score, momentum, MTF, SHORT filter
    row["farmer_confirmed_quality"] = bool(base and row["gate_trap"] and row["gate_breakout"])    # STRATEGY_QUALITY variant adds trap and breakout
    return row


# ------------------------------------------------------------------------------------------------ labels (outcome only: never a feature)
def first_barrier(mark_high: np.ndarray, mark_low: np.ndarray, entry_idx: int, entry: float, atr: float, side: str, k: float = 2.0) -> dict[str, Any]:
    """+k ATR before -k ATR on MARK high/low from the entry candle on; both in one candle -> STOP. Same rule as the C14 label."""
    if side == "LONG":
        tgt, stp = mark_high[entry_idx:] >= entry + k * atr, mark_low[entry_idx:] <= entry - k * atr
    else:
        tgt, stp = mark_low[entry_idx:] <= entry - k * atr, mark_high[entry_idx:] >= entry + k * atr
    hit = tgt | stp
    if not hit.any():
        return {"result": None, "exit_idx": None, "ambiguous": False}
    i = int(np.argmax(hit))
    return {"result": 0 if stp[i] else 1, "exit_idx": entry_idx + i, "ambiguous": bool(stp[i] and tgt[i])}


def label_row(mark_high: np.ndarray, mark_low: np.ndarray, mark_close: np.ndarray, entry_idx: int, entry: float, atr: float,
              fund_t: np.ndarray, fund_r: np.ndarray, t0: int, horizon: int = 32) -> dict[str, Any]:
    """Labels for BOTH sides at one decision. entry_idx = the 15m candle that opens right after the decision candle closed."""
    out: dict[str, Any] = {}
    n = len(mark_high)
    for side in ("LONG", "SHORT"):
        lab = first_barrier(mark_high, mark_low, entry_idx, entry, atr, side)
        key = side.lower()
        out[f"y_{key}"], out[f"exit_idx_{key}"], out[f"amb_{key}"] = lab["result"], lab["exit_idx"], lab["ambiguous"]
        if lab["exit_idx"] is not None:
            t_in, t_out = t0 + entry_idx * BAR, t0 + (lab["exit_idx"] + 1) * BAR
            lo, hi = np.searchsorted(fund_t, t_in, side="right"), np.searchsorted(fund_t, t_out, side="right")
            out[f"fund_sum_{key}"] = float(fund_r[lo:hi].sum())
            out[f"hold_{key}"] = lab["exit_idx"] - entry_idx + 1
    end = min(n, entry_idx + horizon)
    if end - entry_idx == horizon:
        hi_w, lo_w = mark_high[entry_idx:end].max(), mark_low[entry_idx:end].min()
        out["mfe_long_r"], out["mae_long_r"] = (hi_w - entry) / (2 * atr), (entry - lo_w) / (2 * atr)
        out["mfe_short_r"], out["mae_short_r"] = out["mae_long_r"], out["mfe_long_r"]
    for k in (1, 2, 4, 8):
        j = entry_idx + k - 1
        out[f"ret_{k}_atr"] = float((mark_close[j] - entry) / atr) if j < n else None        # LONG-signed; SHORT = -value
    return out


# ------------------------------------------------------------------------------------------------ statistics
def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p, den = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def pf(wins: int, losses: int) -> float:
    return float("inf") if losses == 0 and wins else (wins / losses if losses else float("nan"))


def summarize_outcomes(y: np.ndarray) -> dict[str, Any]:
    """y in {0,1}: +1R / -1R (symmetric +-2 ATR barriers). Gross, per trade."""
    n = len(y)
    if n == 0:
        return {"n": 0}
    k = int(y.sum())
    lo, hi = wilson(k, n)
    z = (k - n / 2) / math.sqrt(n / 4)
    return {"n": n, "wins": k, "success_rate": k / n, "ci_low": lo, "ci_high": hi, "expectancy_r": (2 * k - n) / n, "profit_factor": pf(k, n - k),
            "z": z, "p_two_sided": math.erfc(abs(z) / math.sqrt(2))}


def day_cluster_ci(y: np.ndarray, days: np.ndarray, rng: np.random.Generator, reps: int = 2000) -> tuple[float, float]:
    """Bootstrap by calendar day: consecutive 15m decisions overlap, so row-level intervals are too narrow."""
    if len(y) < 2:
        return float("nan"), float("nan")
    ids, inv = np.unique(days, return_inverse=True)
    wins, cnt = np.bincount(inv, weights=y.astype(float), minlength=len(ids)), np.bincount(inv, minlength=len(ids)).astype(float)
    pick = rng.integers(0, len(ids), size=(reps, len(ids)))
    rate = wins[pick].sum(1) / cnt[pick].sum(1)
    return float(np.quantile(rate, 0.025)), float(np.quantile(rate, 0.975))


def max_drawdown_r(results: np.ndarray) -> float:
    """Max drawdown of the cumulative +1/-1 R series in the given order."""
    if len(results) == 0:
        return 0.0
    c = np.cumsum(np.where(results == 1, 1.0, -1.0))
    return float(np.max(np.maximum.accumulate(np.concatenate([[0.0], c]))[1:] - c))


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    rx, ry = _rank(x), _rank(y)
    if rx.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a))
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2
        i = j + 1
    return ranks
